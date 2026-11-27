"""Persistent strict-match bitsets for unchanged water and sewer vector tiles.

Only original MVT attributes are decoded. Full-source comparison decisions
already exist in the verified package; tile geometry never decides equality.
"""
import base64
import hashlib
import io
import os
from collections import OrderedDict
from pathlib import Path
import sqlite3
import threading
import zlib

from flask import Blueprint, jsonify, request

from comparison import ComparisonPackage
from comparison_keys import tile_key, sewer_tile_key
from comparison_mvt import read_attribute_tile, hash_tile_stream
from comparison_cache import TileFlagCache, content_key, valid_payload

MAX_TILE_BYTES = 256 * 1024 * 1024
MAX_FEATURES = 4_000_000
CHUNK_SIZE = 500
MAX_CACHE_ENTRIES = 32
MAX_CACHE_BYTES = 16 * 1024 * 1024
MAX_CACHE_ENTRY_BYTES = 1024 * 1024
BUILD_WAIT_SECONDS = 60
# Separate lightweight verification budget, shared across all four layers.
# MBTiles BLOBs are size-checked in SQL before allocation; at most two are held.
_HASH_SLOTS = threading.BoundedSemaphore(2)


class SidecarBusy(Exception):
    pass


class MissingTile(Exception):
    pass


def _lookup_flags(connection, keys, dataset='seoul'):
    unique = list(dict.fromkeys(keys))
    if not unique:
        return {}
    marks = ','.join('?' for _ in unique)
    rows = connection.execute(f'''SELECT tile_key,all_strict FROM tile_groups
        WHERE dataset=? AND tile_key IN ({marks})''', [dataset] + unique)
    result = {row['tile_key']: row['all_strict'] for row in rows}
    if len(result) != len(unique) or any(value not in (0, 1) for value in result.values()):
        raise ValueError('Tile contains unknown or invalid comparison keys')
    return result


class ComparisonTileSidecars:
    def __init__(self, data_directory, package, *, comparison='water', layer=None):
        if comparison not in ('water', 'sewer'):
            raise ValueError('Unsupported comparison pair')
        layers = ('kict_national', 'sa001') if comparison == 'water' else ('kict_sewer_seoul', 'sb001')
        self.layer = layer or layers[1]
        if self.layer not in layers:
            raise ValueError('Unsupported comparison layer')
        self.dataset = 'seoul' if self.layer == layers[1] else 'kict'
        self.comparison = comparison
        self.key_function = tile_key if comparison == 'water' else sewer_tile_key
        self.data = Path(data_directory).resolve()
        self.package = package
        self.disk = TileFlagCache(self.data / 'comparison' / comparison / 'tile_cache.sqlite')
        self._cache = OrderedDict()
        self._cache_bytes = 0
        self._cache_lock = threading.Lock()
        self._build_slot = threading.BoundedSemaphore(1)

    def _path(self, z, x, y):
        if self.layer == 'kict_national':
            mb = self.data / 'kict_national.mbtiles'
            mode = os.environ.get('TILE_SOURCE', 'auto').lower()
            path = mb if mode == 'mbtiles' or (mode == 'auto' and mb.is_file()) else self.data / 'tiles_national' / self.layer / str(z) / str(x) / (str(y) + '.pbf')
            if not path.is_file():
                raise MissingTile()
            return path
        full = self.data / 'tiles_full' / self.layer / str(z)
        base = self.data / ('tiles_full' if full.is_dir() else 'tiles' if z >= 12 else 'tiles_low')
        path = base / self.layer / str(z) / str(x) / (str(y) + '.pbf')
        if not path.is_file():
            raise MissingTile()
        return path

    @staticmethod
    def _fingerprint(path):
        stat = path.stat()
        # A process-local hint, never a portable cache identity. ctime/ino also
        # detect replacements preserving size and mtime on ordinary filesystems.
        return str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino

    def _peer_path(self, coord):
        if self.dataset != 'kict':
            return None
        peer = ComparisonTileSidecars(self.data, self.package, comparison=self.comparison)
        try:
            return peer._path(*coord)
        except MissingTile:
            return None

    def _peer_signature(self, coord):
        path = self._peer_path(coord)
        return self._fingerprint(path) if path else None

    def _cached(self, key):
        with self._cache_lock:
            if key not in self._cache:
                return None
            self._cache.move_to_end(key)
            return self._cache[key][0]

    def _remember(self, key, payload):
        # Cache only the small response, never the decoded tile or its features.
        size = len(payload['bits_base64']) + 512
        if size > MAX_CACHE_ENTRY_BYTES:
            return
        with self._cache_lock:
            previous = self._cache.pop(key, None)
            if previous:
                self._cache_bytes -= previous[1]
            self._cache[key] = payload, size
            self._cache_bytes += size
            while len(self._cache) > MAX_CACHE_ENTRIES or self._cache_bytes > MAX_CACHE_BYTES:
                _, (_, removed_size) = self._cache.popitem(last=False)
                self._cache_bytes -= removed_size

    def _read(self, path, tile_coord=None):
        if tile_coord is not None:
            from contextlib import closing
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
                row = db.execute('''SELECT length(tile_data), CASE WHEN length(tile_data)<=? THEN tile_data END
                    FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?''', (MAX_TILE_BYTES, *tile_coord)).fetchone()
            if row is None:
                raise MissingTile()
            if row[0] is None or row[0] > MAX_TILE_BYTES:
                raise ValueError('Compressed tile exceeds comparison size limit')
            data = bytes(row[1])
        elif path.stat().st_size > MAX_TILE_BYTES:
            raise ValueError('Compressed tile exceeds comparison size limit')
        else:
            with path.open('rb') as source:
                data = source.read(MAX_TILE_BYTES + 1)
        if len(data) > MAX_TILE_BYTES:
            raise ValueError('Tile changed beyond comparison size limit')
        return data

    def _content_sha(self, path, coord):
        if not _HASH_SLOTS.acquire(timeout=BUILD_WAIT_SECONDS):
            raise SidecarBusy()
        try:
            if path.suffix == '.mbtiles':
                with io.BytesIO(self._read(path, coord)) as source:
                    return hash_tile_stream(source, MAX_TILE_BYTES)
            if path.stat().st_size > MAX_TILE_BYTES:
                raise ValueError('Compressed tile exceeds comparison size limit')
            with path.open('rb') as source:
                return hash_tile_stream(source, MAX_TILE_BYTES)
        finally:
            _HASH_SLOTS.release()

    def _build(self, path, version, tile_coord=None):
        data = self._read(path, tile_coord)
        tile = read_attribute_tile(data, self.layer, max_bytes=MAX_TILE_BYTES)
        del data
        peer_keys, peer_sha = set(), None
        if self.dataset == 'kict':
            coord = tile_coord or (int(path.parent.parent.name),int(path.parent.name),int(path.stem))
            peer_path = self._peer_path(coord)
            if peer_path:
                with peer_path.open('rb') as source:
                    peer_data = source.read(MAX_TILE_BYTES + 1)
                peer = read_attribute_tile(peer_data, 'sa001' if self.comparison == 'water' else 'sb001', max_bytes=MAX_TILE_BYTES)
                del peer_data
                peer_keys = {self.key_function('seoul', props) for _,props in peer.features()}
                peer_sha = peer.sha256
                del peer
        bits, batch = bytearray(), []
        feature_count = matched_count = 0
        with self.package.connect() as connection:
            # The installed group index is larger than SQLite's default 2MB
            # cache. One bounded build holds at most this 64MB query cache.
            connection.execute('PRAGMA cache_size=-65536')

            def flush():
                keys = [key for _, key in batch]
                flags = _lookup_flags(connection, keys) if self.dataset == 'seoul' else _lookup_flags(connection, keys, self.dataset)
                if self.comparison == 'water' and self.dataset == 'kict':
                    # Only suppress a group when EVERY original has an exact
                    # same-ID counterpart whose municipal group is drawable.
                    strict_keys = [key for key, flag in flags.items() if flag]
                    if strict_keys:
                        marks = ','.join('?' for _ in strict_keys)
                        expected = {row['tile_key']:row['member_count'] for row in connection.execute(
                            f"SELECT tile_key,member_count FROM tile_groups WHERE dataset='kict' AND tile_key IN ({marks})",strict_keys)}
                        present = {key:set() for key in strict_keys}
                        # Preserve the indexed tile-key -> relation -> peer order.
                        # Otherwise SQLite can start with every municipal pipe.
                        rows = connection.execute(f'''SELECT k.tile_key,k.source_fid,s.tile_key AS peer_key FROM pipes k
                              CROSS JOIN relations r ON r.kict_fid=k.source_fid AND r.whole_geometry_equal=1 AND r.same_original_id=1
                              CROSS JOIN pipes s ON s.dataset='seoul' AND s.source_fid=r.seoul_fid
                              CROSS JOIN tile_groups g ON g.dataset='seoul' AND g.tile_key=s.tile_key AND g.all_strict=1
                              WHERE k.dataset='kict' AND k.tile_key IN ({marks})''', strict_keys)
                        for row in rows:
                            if row['peer_key'] in peer_keys:
                                present[row['tile_key']].add(row['source_fid'])
                        for key in strict_keys:
                            flags[key] = int(len(present[key]) == expected[key])
                elif self.dataset == 'kict':
                    import json
                    ids = {int(json.loads(key)[0]):key for key,flag in flags.items() if flag}
                    present = set()
                    if ids:
                        marks = ','.join('?' for _ in ids)
                        for row in connection.execute(f'SELECT kict_id,seoul_id FROM pairs WHERE kict_id IN ({marks})',list(ids)):
                            if self.key_function('seoul',{'id':row['seoul_id']}) in peer_keys:
                                present.add(row['kict_id'])
                    for object_id,key in ids.items():
                        flags[key] = int(object_id in present)
                matches = 0
                for ordinal, key in batch:
                    if flags[key]:
                        bits[ordinal >> 3] |= 1 << (ordinal & 7)
                        matches += 1
                batch.clear()
                return matches

            for ordinal, properties in tile.features():
                if ordinal != feature_count or feature_count >= MAX_FEATURES:
                    raise ValueError('Tile feature count exceeds verified sidecar bounds')
                if not ordinal & 7:
                    bits.append(0)
                batch.append((ordinal, self.key_function(self.dataset, properties)))
                feature_count += 1
                if len(batch) == CHUNK_SIZE:
                    matched_count += flush()
            if batch:
                matched_count += flush()
        return dict(available=True, schema_version=1, version=version, layer=self.layer,
                    sha256=tile.sha256, feature_count=feature_count, matched_count=matched_count,
                    bits_base64=base64.b64encode(bits).decode('ascii'), counterpart_sha256=peer_sha)

    def _probe(self, coord):
        """Hash only on a process-memory miss; persisted hits never decode MVT."""
        info = self.package.verified()
        path = self._path(*coord)
        fingerprint, peer_signature = self._fingerprint(path), self._peer_signature(coord)
        key = (info['version'],) + fingerprint + (*coord, peer_signature)
        cached = self._cached(key)
        if cached is not None:
            return cached, None
        sha = self._content_sha(path, coord)
        peer_sha = self._content_sha(Path(peer_signature[0]), coord) if peer_signature else None
        disk_key = content_key(self.comparison, self.layer, coord, info['version'], sha, peer_sha)
        payload, migrate = self.disk.get_verified(self.layer, coord, disk_key)
        snapshot = (path, fingerprint, peer_signature, key, disk_key)
        self._check_snapshot(coord, snapshot)
        if payload is not None:
            if migrate:
                self.disk.put(self.layer, coord, disk_key, payload)
            self._remember(key, payload)
        return payload, snapshot

    def _check_snapshot(self, coord, snapshot):
        path, fingerprint, peer_signature, _, disk_key = snapshot
        if (self._path(*coord) != path or fingerprint != self._fingerprint(path)
                or peer_signature != self._peer_signature(coord)
                or self.package.verified()['version'] != disk_key[2]):
            raise ValueError('Comparison inputs changed during sidecar construction')

    def get(self, z, x, y):
        coord = (z, x, y)
        # Disk hits (including legacy migration) must bypass the expensive-build
        # semaphore, so a busy uncached tile cannot stall known content.
        payload, _ = self._probe(coord)
        if payload is not None:
            return payload
        if not self._build_slot.acquire(timeout=BUILD_WAIT_SECONDS):
            raise SidecarBusy()
        try:
            payload, snapshot = self._probe(coord)
            if payload is not None:
                return payload
            path, _, _, key, disk_key = snapshot
            payload = self._build(path, disk_key[2], coord) if path.suffix == '.mbtiles' else self._build(path, disk_key[2])
            self._check_snapshot(coord, snapshot)
            if not valid_payload(payload, disk_key):
                raise ValueError('Built flags do not match verified tile content')
            self.disk.put(self.layer, coord, disk_key, payload)
            self._remember(key, payload)
            return payload
        finally:
            self._build_slot.release()


def create_comparison_tiles_blueprint(data_directory, *, package=None, comparison='water'):
    if comparison not in ('water', 'sewer'):
        raise ValueError('Unsupported comparison pair')
    if comparison == 'sewer' and package is None:
        raise ValueError('Sewer comparison requires its separately verified package')
    package = package if package is not None else ComparisonPackage(Path(data_directory) / 'comparison' / 'water')
    layers = ('kict_national', 'sa001') if comparison == 'water' else ('kict_sewer_seoul', 'sb001')
    sidecars = {layer: ComparisonTileSidecars(data_directory, package, comparison=comparison, layer=layer) for layer in layers}
    bp = Blueprint(comparison + '_comparison_tiles', __name__, url_prefix='/comparison/' + comparison)

    @bp.errorhandler(MissingTile)
    def missing(error):
        return jsonify(available=False, error='comparison_tile_missing', message='현재 관로 타일이 없습니다.'), 404

    @bp.errorhandler(SidecarBusy)
    def busy(error):
        response = jsonify(available=False, error='comparison_busy', message='강조 정보를 준비 중입니다. 잠시 후 다시 확인해 주세요.')
        response.headers['Retry-After'] = '1'
        return response, 503

    @bp.errorhandler(OSError)
    @bp.errorhandler(ValueError)
    @bp.errorhandler(sqlite3.Error)
    @bp.errorhandler(KeyError)
    @bp.errorhandler(TypeError)
    @bp.errorhandler(OverflowError)
    @bp.errorhandler(zlib.error)
    def unavailable(error):
        return jsonify(available=False, error='comparison_unavailable', message='현재 타일의 정확 일치 강조 정보를 확인할 수 없습니다.'), 503

    @bp.after_request
    def avoid_stale(response):
        if response.status_code == 200:
            # Revalidate before every reuse; changing inputs cannot serve stale
            # flags. Browser still verifies tile SHA before applying the bits.
            response.headers['Cache-Control'] = 'public, max-age=0, must-revalidate'
            response.set_etag(hashlib.sha256(response.get_data()).hexdigest())
            response.make_conditional(request)
        else:
            response.headers['Cache-Control'] = 'no-store'
        return response

    @bp.get('/tiles/<layer>/<int(signed=True):z>/<int(signed=True):x>/<int(signed=True):y>')
    def flags(layer, z, x, y):
        if layer not in sidecars or not 0 <= z <= 17 or not 0 <= x < 2**z or not 0 <= y < 2**z:
            return jsonify(available=False, error='unsupported_tile', message='강조 조회 대상 타일이 아닙니다.'), 404
        return jsonify(sidecars[layer].get(z, x, y))

    @bp.get('/tiles/<path:invalid>')
    def invalid(invalid):
        return jsonify(available=False, error='unsupported_tile', message='타일 주소가 올바르지 않습니다.'), 404

    return bp
