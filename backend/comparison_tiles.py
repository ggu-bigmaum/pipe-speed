"""Small strict-match bitsets for unchanged Seoul water vector tiles.

Only original MVT attributes are decoded. Full-source comparison decisions
already exist in the verified package; tile geometry never decides equality.
"""
import base64
from collections import OrderedDict
from pathlib import Path
import sqlite3
import threading
import zlib

from flask import Blueprint, jsonify

from comparison import ComparisonPackage
from comparison_keys import tile_key, sewer_tile_key
from comparison_mvt import read_attribute_tile

MAX_TILE_BYTES = 256 * 1024 * 1024
MAX_FEATURES = 4_000_000
CHUNK_SIZE = 500
MAX_CACHE_ENTRIES = 32
MAX_CACHE_BYTES = 16 * 1024 * 1024
MAX_CACHE_ENTRY_BYTES = 1024 * 1024
BUILD_WAIT_SECONDS = 60


class SidecarBusy(Exception):
    pass


class MissingTile(Exception):
    pass


def _lookup_flags(connection, keys):
    unique = list(dict.fromkeys(keys))
    if not unique:
        return {}
    marks = ','.join('?' for _ in unique)
    rows = connection.execute(f'''SELECT tile_key,all_strict FROM tile_groups
        WHERE dataset=? AND tile_key IN ({marks})''', ['seoul'] + unique)
    result = {row['tile_key']: row['all_strict'] for row in rows}
    if len(result) != len(unique) or any(value not in (0, 1) for value in result.values()):
        raise ValueError('Tile contains unknown or invalid comparison keys')
    return result


class ComparisonTileSidecars:
    def __init__(self, data_directory, package, *, comparison='water'):
        if comparison not in ('water', 'sewer'):
            raise ValueError('Unsupported comparison pair')
        self.layer = 'sa001' if comparison == 'water' else 'sb001'
        self.key_function = tile_key if comparison == 'water' else sewer_tile_key
        self.data = Path(data_directory).resolve()
        self.package = package
        self._cache = OrderedDict()
        self._cache_bytes = 0
        self._cache_lock = threading.Lock()
        self._build_slot = threading.BoundedSemaphore(1)

    def _path(self, z, x, y):
        full = self.data / 'tiles_full' / self.layer / str(z)
        base = self.data / ('tiles_full' if full.is_dir() else 'tiles' if z >= 12 else 'tiles_low')
        path = base / self.layer / str(z) / str(x) / (str(y) + '.pbf')
        if not path.is_file():
            raise MissingTile()
        return path

    @staticmethod
    def _fingerprint(path):
        stat = path.stat()
        return str(path), stat.st_size, stat.st_mtime_ns

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

    def _build(self, path, version):
        if path.stat().st_size > MAX_TILE_BYTES:
            raise ValueError('Compressed tile exceeds comparison size limit')
        with path.open('rb') as source:
            data = source.read(MAX_TILE_BYTES + 1)
        if len(data) > MAX_TILE_BYTES:
            raise ValueError('Tile changed beyond comparison size limit')
        tile = read_attribute_tile(data, self.layer, max_bytes=MAX_TILE_BYTES)
        del data
        bits, batch = bytearray(), []
        feature_count = matched_count = 0
        with self.package.connect() as connection:
            # The installed group index is larger than SQLite's default 2MB
            # cache. One bounded build holds at most this 64MB query cache.
            connection.execute('PRAGMA cache_size=-65536')

            def flush():
                flags = _lookup_flags(connection, [key for _, key in batch])
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
                batch.append((ordinal, self.key_function('seoul', properties)))
                feature_count += 1
                if len(batch) == CHUNK_SIZE:
                    matched_count += flush()
            if batch:
                matched_count += flush()
        return dict(available=True, schema_version=1, version=version, layer=self.layer,
                    sha256=tile.sha256, feature_count=feature_count, matched_count=matched_count,
                    bits_base64=base64.b64encode(bits).decode('ascii'))

    def get(self, z, x, y):
        info = self.package.verified()
        path = self._path(z, x, y)
        key = (info['version'],) + self._fingerprint(path)
        cached = self._cached(key)
        if cached is not None:
            return cached
        if not self._build_slot.acquire(timeout=BUILD_WAIT_SECONDS):
            raise SidecarBusy()
        try:
            # The tile or package may have changed while this request waited.
            info = self.package.verified()
            path = self._path(z, x, y)
            fingerprint = self._fingerprint(path)
            key = (info['version'],) + fingerprint
            cached = self._cached(key)
            if cached is not None:
                return cached
            payload = self._build(path, info['version'])
            if fingerprint != self._fingerprint(path) or self.package.verified()['version'] != info['version']:
                raise ValueError('Comparison inputs changed during sidecar construction')
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
    sidecars = ComparisonTileSidecars(data_directory, package, comparison=comparison)
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
        response.headers['Cache-Control'] = 'no-store'
        return response

    @bp.get('/tiles/<layer>/<int(signed=True):z>/<int(signed=True):x>/<int(signed=True):y>')
    def flags(layer, z, x, y):
        if layer != sidecars.layer or not 0 <= z <= 17 or not 0 <= x < 2**z or not 0 <= y < 2**z:
            return jsonify(available=False, error='unsupported_tile', message='강조 조회 대상 타일이 아닙니다.'), 404
        return jsonify(sidecars.get(z, x, y))

    @bp.get('/tiles/<path:invalid>')
    def invalid(invalid):
        return jsonify(available=False, error='unsupported_tile', message='타일 주소가 올바르지 않습니다.'), 404

    return bp
