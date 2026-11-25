"""National distance results backed by per-region, read-only SQLite indexes."""
import json
import math
import sqlite3
from contextlib import closing
from pathlib import Path

from flask import Blueprint, abort, jsonify, request, send_from_directory

REGIONS = (
    ('11', '서울특별시'), ('21', '부산광역시'), ('22', '대구광역시'),
    ('23', '인천광역시'), ('24', '광주광역시'), ('25', '대전광역시'),
    ('26', '울산광역시'), ('29', '세종특별자치시'), ('31', '경기도'),
    ('32', '강원특별자치도'), ('33', '충청북도'), ('34', '충청남도'),
    ('35', '전북특별자치도'), ('36', '전라남도'), ('37', '경상북도'),
    ('38', '경상남도'), ('39', '제주특별자치도'),
)
UTILITIES = {'water': '건기연 상수', 'sewer': '건기연 하수·보정본'}
FILES = ('pipe_case1_m.tif', 'pipe_case2_m.tif', 'pipe_dist_result.csv')
SOURCES = FILES + ('grid_index.json',)
GRID_CELL_LIMIT = 12000
PUBLIC_META = ('dataset_id', 'label', 'crs', 'width', 'height', 'origin',
               'pixel_size', 'nodata', 'bounds', 'bounds_wgs84', 'valid_count',
               'result_count', 'result_filename', 'cases')
DATA_ERRORS = (OSError, ValueError, KeyError, TypeError, sqlite3.Error)


class NationalDistanceData:
    def __init__(self, root, utility, code):
        if utility not in UTILITIES or code not in dict(REGIONS):
            abort(404)
        self.directory = Path(root) / ('kict_' + utility + '_national') / code
        self.dataset_id = 'kict_{}_national_{}'.format(utility, code)
        self.unavailable = utility == 'sewer' and code == '36'

    def metadata(self):
        """Read only the small derived header; never parse original CSV or grid."""
        if self.unavailable:
            raise ValueError('전라남도 하수 자료가 없습니다.')
        data = json.loads((self.directory / 'dataset.json').read_text(encoding='utf-8'))
        if data['dataset_id'] != self.dataset_id or data['result_filename'] != FILES[2]:
            raise ValueError('자료 식별자가 다릅니다.')
        if data['crs'] != 'EPSG:32652':
            raise ValueError('지원하지 않는 격자 좌표계입니다.')
        if any(type(data[k]) is not int or data[k] <= 0 for k in ('width', 'height')):
            raise ValueError('잘못된 격자 크기입니다.')
        for key in ('origin', 'pixel_size'):
            if len(data[key]) != 2 or not all(math.isfinite(float(v)) for v in data[key]):
                raise ValueError('잘못된 격자 위치입니다.')
        if any(v <= 0 for v in data['pixel_size']):
            raise ValueError('잘못된 격자 간격입니다.')
        required = SOURCES + ('cells.sqlite',)
        if any(not (self.directory / f).is_file() for f in required):
            raise ValueError('필수 자료 파일이 없습니다.')
        sizes = {f: (self.directory / f).stat().st_size for f in required}
        if not all(sizes.values()):
            raise ValueError('비어 있는 자료 파일이 있습니다.')
        cases = data['cases']
        if len(cases) != 2:
            raise ValueError('Case 1/2 정보가 필요합니다.')
        for i, case in enumerate(cases):
            if case['id'] != 'case' + str(i + 1) or case['filename'] != FILES[i] or case['bytes'] != sizes[FILES[i]]:
                raise ValueError('TIFF 파일 크기 또는 Case 식별자가 다릅니다.')
        for filename, information in data.get('source_files', {}).items():
            if filename in SOURCES:
                expected = information['bytes'] if isinstance(information, dict) else information
                if sizes[filename] != expected:
                    raise ValueError('원본 파일 크기가 검증 정보와 다릅니다.')
        # Never publish provenance paths, build diagnostics, hashes or sample rows.
        public = {key: data[key] for key in PUBLIC_META if key in data}
        public['cases'] = [{key: case[key] for key in ('id', 'filename', 'bytes', 'label')} for case in cases]
        public.update(grid_mode='viewport', grid_cell_limit=GRID_CELL_LIMIT)
        return public

    def connection(self):
        # closing() is necessary: sqlite's own context manager does not close it.
        connection = sqlite3.connect((self.directory / 'cells.sqlite').resolve().as_uri() + '?mode=ro', uri=True)
        connection.row_factory = sqlite3.Row
        return closing(connection)


def grid_geometry(cells, metadata):
    horizontal, vertical = {}, {}
    for col, row in cells:
        horizontal.setdefault(row, set()).add(col)
        horizontal.setdefault(row + 1, set()).add(col)
        vertical.setdefault(col, set()).add(row)
        vertical.setdefault(col + 1, set()).add(row)
    ox, oy = metadata['origin']
    sx, sy = metadata['pixel_size']
    lines = []
    for axis, edges in (('h', horizontal), ('v', vertical)):
        for fixed, positions in sorted(edges.items()):
            ordered = sorted(positions)
            start = previous = ordered[0]
            runs = []
            for position in ordered[1:]:
                if position != previous + 1:
                    runs.append((start, previous + 1))
                    start = position
                previous = position
            runs.append((start, previous + 1))
            for start, end in runs:
                if axis == 'h':
                    lines.append([[ox + start * sx, oy - fixed * sy], [ox + end * sx, oy - fixed * sy]])
                else:
                    lines.append([[ox + fixed * sx, oy - start * sy], [ox + fixed * sx, oy - end * sy]])
    return {'type': 'MultiLineString', 'coordinates': lines}


def create_national_distance_blueprint(root):
    bp = Blueprint('national_distance', __name__, url_prefix='/distance/national')

    def store_for(utility, code):
        return NationalDistanceData(root, utility, code)

    def data_error(error):
        return jsonify(error='거리 자료를 읽지 못했습니다. 지역 자료의 설치 및 검증 상태를 확인해 주세요.'), 503

    for error_type in DATA_ERRORS:
        bp.register_error_handler(error_type, data_error)

    @bp.after_request
    def no_cache(response):
        response.headers['Cache-Control'] = 'no-store'
        return response

    @bp.get('/catalog')
    def catalog():
        groups = []
        for utility, label in UTILITIES.items():
            regions = []
            for code, region_label in REGIONS:
                store = store_for(utility, code)
                entry = dict(code=code, label=region_label, dataset_id=store.dataset_id,
                             route='national/{}/{}/'.format(utility, code), available=True)
                try:
                    store.metadata()
                except DATA_ERRORS:
                    entry.update(available=False, reason=('원본 자료 없음' if store.unavailable else '자료 설치 또는 검증 필요'))
                regions.append(entry)
            groups.append(dict(id=utility, label=label, regions=regions))
        return jsonify(groups=groups)

    @bp.get('/<utility>/<code>/meta')
    def meta(utility, code):
        return jsonify(store_for(utility, code).metadata())

    @bp.get('/<utility>/<code>/files/<path:filename>')
    def original_file(utility, code, filename):
        store = store_for(utility, code)
        if filename not in FILES:
            abort(404)
        store.metadata()
        return send_from_directory(store.directory, filename, conditional=False, max_age=0)

    @bp.get('/<utility>/<code>/cell')
    def cell(utility, code):
        store = store_for(utility, code)
        metadata = store.metadata()
        try:
            col, row = int(request.args['col']), int(request.args['row'])
        except (KeyError, ValueError):
            return jsonify(error='정수 col과 row가 필요합니다.'), 400
        if not (0 <= col < metadata['width'] and 0 <= row < metadata['height']):
            return jsonify(error='자료 범위 밖입니다.'), 400
        with store.connection() as connection:
            record = connection.execute('SELECT grid_id, case1_m, case2_m, pass_flag, pipe_id, near_x, near_y, near_lon, near_lat FROM cells WHERE col=? AND row=?', (col, row)).fetchone()
        if record is None:
            return jsonify(matched=False, reason='nodata')
        ox, oy = metadata['origin']
        sx, sy = metadata['pixel_size']
        return jsonify(matched=True, dataset_id=store.dataset_id, col=col, row=row,
                       center=[ox + (col + .5) * sx, oy - (row + .5) * sy], **dict(record))

    @bp.get('/<utility>/<code>/gridlines')
    def gridlines(utility, code):
        store = store_for(utility, code)
        metadata = store.metadata()
        try:
            min_col, max_col, min_row, max_row = (int(request.args[key]) for key in ('min_col', 'max_col', 'min_row', 'max_row'))
        except (KeyError, ValueError):
            return jsonify(error='정수 min_col, max_col, min_row, max_row가 필요합니다.'), 400
        if not (0 <= min_col <= max_col < metadata['width'] and 0 <= min_row <= max_row < metadata['height']):
            return jsonify(error='격자 조회 범위가 올바르지 않습니다.'), 400
        result = dict(crs=metadata['crs'], pixel_size=metadata['pixel_size'])
        if (max_col - min_col + 1) * (max_row - min_row + 1) > GRID_CELL_LIMIT:
            result.update(omitted=True, reason='격자선을 보려면 지도를 확대해 주세요.',
                          geometry={'type': 'MultiLineString', 'coordinates': []})
        else:
            with store.connection() as connection:
                cells = connection.execute('SELECT col, row FROM cells WHERE row BETWEEN ? AND ? AND col BETWEEN ? AND ?',
                                           (min_row, max_row, min_col, max_col)).fetchall()
            result.update(omitted=False, geometry=grid_geometry(cells, metadata))
        return jsonify(result)

    return bp
