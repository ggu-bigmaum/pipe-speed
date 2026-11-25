"""Compose verified regional sewer Tile messages without changing layer IDs.

Metadata remains inspectable during a partial installation. Tile requests fail
with 503 until every required region and zoom is present, so a partial network
cannot be mistaken for a complete national result. Jeonnam has no source data.
"""
import gzip
import io
import json
import math
from contextlib import ExitStack
from pathlib import Path
import time
import zlib

from flask import Blueprint, Response, abort, jsonify, request

LAYER = 'kict_sewer_national'
# Verified original SHP counts outside Seoul. Seoul corrected tiles have their own route.
DATA_POLICY = 'original-outside-seoul-v1'
REGIONS = (
    ('sewer_gangwon', 243135), ('sewer_gyeonggi_south', 683531),
    ('sewer_gyeonggi_north', 344491), ('sewer_gyeongnam', 391148),
    ('sewer_gyeongbuk', 373896), ('sewer_gwangju', 123984),
    ('sewer_daegu', 150510), ('sewer_daejeon', 78908),
    ('sewer_busan', 168064), ('sewer_sejong', 20538),
    ('sewer_ulsan', 111293), ('sewer_incheon', 141128),
    ('sewer_jeonbuk', 249324), ('sewer_jeju', 129909),
    ('sewer_chungnam', 256851), ('sewer_chungbuk', 167947),
)
ZOOMS = tuple(range(6, 18))
MAX_TILE_BYTES = 256 * 1024 * 1024


def create_national_sewer_tiles_blueprint(directory, seoul_directory, log_request=None):
    bp = Blueprint('national_sewer_tiles', __name__, url_prefix='/tiles/' + LAYER)
    # Keep the existing factory argument for callers; this composite excludes Seoul.
    directories = {key: Path(directory) / key for key, _ in REGIONS}

    def metadata():
        available, missing, bounds, fields = [], [], [], {}
        for key, count in REGIONS:
            root = directories[key]
            try:
                info = json.loads((root / 'metadata.json').read_text(encoding='utf-8'))
                if key != 'kict_sewer_seoul':
                    verified = json.loads((root / 'conversion_complete.json').read_text(encoding='utf-8'))
                    if (verified.get('status') != 'complete' or verified.get('data_policy') != DATA_POLICY
                            or verified.get('source_features') != count or verified.get('raw_originals_verified') is not True):
                        raise ValueError('Original-source tile verification is missing')
                layers = json.loads(info['json'])['vector_layers']
                if [layer['id'] for layer in layers] != [key] or any(not (root / str(z)).is_dir() for z in ZOOMS):
                    raise ValueError('Regional layer or zoom mismatch')
                extent = info['bounds']
                extent = [float(v) for v in extent.split(',')] if isinstance(extent, str) else extent
                if len(extent) != 4 or not all(math.isfinite(v) for v in extent) or not (-180 <= extent[0] < extent[2] <= 180 and -90 <= extent[1] < extent[3] <= 90):
                    raise ValueError('Invalid bounds')
                bounds.append(extent)
                fields.update(layers[0].get('fields', {}))
                available.append(key)
            except (OSError, ValueError, KeyError, TypeError):
                missing.append(key)
        combined_bounds = ([min(b[0] for b in bounds), min(b[1] for b in bounds),
                            max(b[2] for b in bounds), max(b[3] for b in bounds)] if bounds else None)
        return dict(layer=LAYER, source='건기연 하수 원본(서울 외)', scope='outside-seoul', data_policy=DATA_POLICY,
                    bounds=combined_bounds, minzoom=6, maxzoom=17,
                    count=sum(count for key, count in REGIONS if key in available),
                    expected_count=sum(count for _, count in REGIONS), complete=not missing,
                    missing_regions=missing, excluded_regions=['전라남도'],
                    tile_url=request.host_url.rstrip('/') + '/tiles/' + LAYER + '/{z}/{x}/{y}.pbf',
                    vector_layer_ids=available, scheme='xyz', tile_extent=4096,
                    fields=fields, full_zooms=list(ZOOMS) if not missing else [],
                    available_zooms=list(ZOOMS) if not missing else [])

    @bp.before_request
    def start_timer():
        request.national_tile_start = time.perf_counter()

    @bp.after_request
    def finish(response):
        ms = (time.perf_counter() - request.national_tile_start) * 1000
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Server-Ms'] = '{:.3f}'.format(ms)
        response.headers.setdefault('Server-Timing', 'total;dur={:.3f}'.format(ms))
        response.vary.add('Accept-Encoding')
        if log_request:
            log_request(request.path, response.status_code, response.calculate_content_length() or 0, ms, 'national-sewer')
        return response

    @bp.get('/meta.json')
    def meta():
        return jsonify(metadata())

    @bp.get('/<int:z>/<int:x>/<int:y>.pbf')
    def tile(z, x, y):
        if z not in ZOOMS or not (0 <= x < 2 ** z and 0 <= y < 2 ** z):
            abort(404)
        info = metadata()
        if not info['complete']:
            return jsonify(error='전국 하수 타일 설치가 완료되지 않았습니다.', missing_regions=info['missing_regions']), 503
        # A protobuf Tile consists of repeated layers. Concatenating complete
        # decompressed Tile messages preserves all regions and their layer IDs.
        # Compose in chunks, keeping only the negotiated output in memory. In
        # particular, gzip requests never retain a second, expanded national tile.
        # Bound each response and retain no process-wide tile or geometry cache.
        use_gzip = request.accept_encodings['gzip'] > 0 and request.args.get('identity') != '1'
        raw_size = 0
        try:
            with io.BytesIO() as output:
                with ExitStack() as stack:
                    sink = stack.enter_context(gzip.GzipFile(fileobj=output, mode='wb', compresslevel=1, mtime=0)) if use_gzip else output
                    for key in info['vector_layer_ids']:
                        path = directories[key] / str(z) / str(x) / (str(y) + '.pbf')
                        try:
                            handle = path.open('rb')
                        except FileNotFoundError:
                            continue
                        with handle, gzip.GzipFile(fileobj=handle) as compressed:
                            while True:
                                part = compressed.read(min(1024 * 1024, MAX_TILE_BYTES - raw_size + 1))
                                if not part:
                                    break
                                raw_size += len(part)
                                if raw_size > MAX_TILE_BYTES:
                                    raise ValueError('Tile response exceeds memory limit')
                                sink.write(part)
                body = output.getvalue()
        except (OSError, EOFError, ValueError, zlib.error):
            return jsonify(error='전국 하수 타일 파일을 읽지 못했습니다. 타일 검증 상태를 확인해 주세요.'), 503
        if not raw_size:
            return Response(status=204)
        t_read = time.perf_counter()
        response = Response(body, mimetype='application/x-protobuf')
        if use_gzip:
            response.headers['Content-Encoding'] = 'gzip'
        response.headers['X-Tile-Bytes'] = str(len(body))
        response.headers['X-Tile-Set'] = 'full'
        response.headers['X-Tile-Source'] = 'file'
        t0 = request.national_tile_start
        response.headers['Server-Timing'] = 'read;dur={:.3f}, total;dur={:.3f}'.format((t_read - t0) * 1000, (time.perf_counter() - t0) * 1000)
        return response

    return bp
