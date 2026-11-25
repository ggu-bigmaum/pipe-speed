"""Bounded, read-only access to the separately verified water comparison package."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import threading

from flask import Blueprint, jsonify, request

from comparison_keys import FIELDS, tile_key

LAYERS = {'kict_national': 'kict', 'sa001': 'seoul'}
PAGE_SIZE = 20
MAX_MEMBERS = 100
MAX_BODY = 8192
STATES = {'MATCH', 'PARTIAL_MATCH', 'KICT_ONLY', 'LOCAL_ONLY', 'CONFLICT', 'NOT_COMPARED'}
ACCEPTED_SQL = """(r.whole_geometry_equal=1 OR
    (r.sustained_decision IN ('sustained_correspondence','whole_path_correspondence')
     AND r.covered_k_m>0 AND r.covered_s_m>0))"""
REASONS = {
    'reference_unavailable': '해당 지역의 지자체 비교 자료가 없습니다.',
    'whole_source_equal': '두 원본 관로의 전체 위치와 형상이 같습니다.',
    'source_mapping_unverified': '기존 비교 결과와 현재 원본의 대응을 확정하지 못했습니다.',
    'boundary_scope_difference': '기존 비교의 서울 경계 절단 영향으로 전체 원본 판정을 보류했습니다.',
    'regression_review': '방향·연속 길이 보완 후 대응 비율이 감소한 검토 대상입니다.',
    'competing_correspondence': '같은 구간에 여러 상대 관로가 중복 대응하여 자동 확정을 보류했습니다.',
    'sustained_full': '방향·연속 길이 조건을 통과한 구간이 평가구간 길이의 95% 이상입니다.',
    'sustained_partial': '방향·연속 길이 조건을 통과한 일부 구간이 확인됩니다.',
    'absence_unconfirmed': '5m 이내 대응 후보가 없지만 한 자료에만 존재하는지는 추가 확인이 필요합니다.',
    'same_id_different_path': '같은 ID의 관로가 있으나 인정된 연속 대응 구간이 없습니다.',
    'no_sustained_path': '가까운 관로는 있으나 인정된 연속 대응 구간이 없습니다.',
}
REQUIRED_COLUMNS = {
    'pipes': {'dataset', 'source_fid', 'original_id', 'tile_key', 'region', 'comparison_status',
              'reason_code', 'candidate_status', 'exact_geometry_match', 'exact_same_id_geometry',
              'coverage_ratio', 'length_m', 'regression_review_required', 'attribute_flags_json'},
    'relations': {'kict_fid', 'seoul_fid', 'whole_geometry_equal', 'same_original_id',
                  'baseline_geometry_equal', 'sustained_decision', 'covered_k_m', 'covered_s_m'},
    'metadata': {'key', 'value'},
}


class RequestError(Exception):
    def __init__(self, code, message, status=400):
        self.code, self.message, self.status = code, message, status


def _sha256(path):
    value = hashlib.sha256()
    with path.open('rb') as source:
        while block := source.read(8 * 1024 * 1024):
            value.update(block)
    return value.hexdigest()


class ComparisonPackage:
    """Verify each installed file revision once; close every request connection."""
    def __init__(self, directory):
        self.root = Path(directory).resolve()
        self.path = self.root / 'water_comparison.sqlite'
        self.manifest_path = self.root / 'manifest.json'
        self._cached = None
        self._hash_result = None
        self._lock = threading.Lock()

    def connect(self):
        connection = sqlite3.connect(self.path.as_uri() + '?mode=ro&immutable=1', uri=True)
        connection.row_factory = sqlite3.Row
        return closing(connection)

    def _revision(self):
        data, manifest = self.path.stat(), self.manifest_path.stat()
        return data.st_size, data.st_mtime_ns, manifest.st_size, manifest.st_mtime_ns

    def verified(self):
        with self._lock:
            revision = self._revision()
            if self._cached and self._cached[0] == revision:
                return self._cached[1]
            # Never follow the output path stored in an external manifest.
            if revision[2] > 1024 * 1024:
                raise ValueError('Comparison manifest too large')
            manifest = json.loads(self.manifest_path.read_text(encoding='utf-8'))
            if (not isinstance(manifest, dict) or manifest.get('status') != 'complete'
                    or type(manifest.get('schema_version')) is not int or manifest['schema_version'] != 1):
                raise ValueError('Comparison package is incomplete or unsupported')
            output = manifest.get('output', {})
            if not isinstance(output, dict):
                raise ValueError('Comparison output identity is invalid')
            expected = output.get('sha256')
            rule = manifest.get('rule_version')
            if (not isinstance(expected, str) or len(expected) != 64
                    or any(char not in '0123456789abcdef' for char in expected)
                    or output.get('bytes') != revision[0]
                    or not isinstance(rule, str) or not rule or len(rule) > 128):
                raise ValueError('Comparison package identity is invalid')
            # Failed integrity checks are also cached for this file revision;
            # retries must not repeatedly hash a multi-gigabyte broken package.
            if not self._hash_result or self._hash_result[0] != revision:
                self._hash_result = revision, _sha256(self.path) == expected
            if not self._hash_result[1]:
                raise ValueError('Comparison package hash mismatch')
            with self.connect() as connection:
                for table, columns in REQUIRED_COLUMNS.items():
                    present = {row['name'] for row in connection.execute('PRAGMA table_info(' + table + ')')}
                    if not columns.issubset(present):
                        raise ValueError('Comparison schema is missing required columns')
                stored = {row['key']: json.loads(row['value']) for row in connection.execute(
                    "SELECT key,value FROM metadata WHERE key IN ('schema_version','rule_version','counts')")}
                if any(stored.get(key) != manifest.get(key) for key in ('schema_version', 'rule_version', 'counts')):
                    raise ValueError('Comparison manifest and database metadata differ')
            if revision != self._revision():
                raise ValueError('Comparison package changed during verification')
            info = dict(available=True, version=rule + ':' + expected, schema_version=1,
                        rule_version=rule, scope=manifest.get('scope', ''), counts=manifest['counts'],
                        layers=list(LAYERS))
            self._cached = revision, info
            return info


def _integer(value, name, minimum=0, maximum=2**53 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise RequestError('invalid_request', name + ' 값이 올바르지 않습니다.')
    return value


def _request_values():
    if not request.is_json or (request.content_length or 0) > MAX_BODY:
        raise RequestError('invalid_request', '요청 형식 또는 크기가 올바르지 않습니다.')
    raw = request.stream.read(MAX_BODY + 1)
    if len(raw) > MAX_BODY:
        raise RequestError('invalid_request', '요청 크기가 너무 큽니다.')
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeError):
        raise RequestError('invalid_request', 'JSON 요청을 읽을 수 없습니다.') from None
    if not isinstance(body, dict) or set(body) - {'layer', 'properties', 'source_fid', 'offset'}:
        raise RequestError('invalid_request', '요청 항목이 올바르지 않습니다.')
    layer = body.get('layer')
    if not isinstance(layer, str) or layer not in LAYERS:
        raise RequestError('unsupported_layer', '이 자료는 관로 비교 조회 대상이 아닙니다.', 404)
    dataset = LAYERS[layer]
    properties = body.get('properties')
    if not isinstance(properties, dict) or set(properties) - set(FIELDS[dataset]):
        raise RequestError('invalid_request', '타일 식별 속성이 올바르지 않습니다.')
    if any(isinstance(value, str) and len(value) > 256 for value in properties.values()):
        raise RequestError('invalid_request', '타일 식별 속성이 너무 깁니다.')
    try:
        key = tile_key(dataset, properties)
    except (ValueError, TypeError, OverflowError):
        raise RequestError('invalid_request', '타일 식별 속성은 검증 가능한 단일 값이어야 합니다.') from None
    selected = _integer(body['source_fid'], '원본 번호', 1) if 'source_fid' in body else None
    offset = _integer(body.get('offset', 0), '목록 위치', maximum=2**31 - 1)
    return dataset, key, selected, offset


def _pipe_view(row):
    if row['comparison_status'] not in STATES:
        raise ValueError('Unknown persisted business status')
    attributes = json.loads(row['attribute_flags_json'])
    if not isinstance(attributes, dict):
        raise ValueError('Invalid persisted attributes')
    return dict(dataset=row['dataset'], source_fid=row['source_fid'], original_id=row['original_id'],
                comparison_status=row['comparison_status'], reason_code=row['reason_code'],
                reason=REASONS.get(row['reason_code'], '저장된 판정 근거를 확인해 주세요.'),
                candidate_status=row['candidate_status'], coverage_ratio=row['coverage_ratio'],
                length_m=row['length_m'],
                length_scope='seoul_boundary_clip' if row['length_m'] is not None or row['coverage_ratio'] is not None else None,
                exact_same_id_geometry=bool(row['exact_same_id_geometry']),
                exact_geometry_match=bool(row['exact_geometry_match']),
                regression_review_required=bool(row['regression_review_required']), attribute_flags=attributes)


def _relationship(row):
    if row['whole_geometry_equal']:
        if row['same_original_id']:
            return 'exact_same_id', True, '원본 ID와 원본 전체 선형이 모두 정확히 같습니다.'
        return 'exact_other_id', True, '원본 전체 선형은 정확히 같고 원본 ID는 다릅니다.'
    if (row['sustained_decision'] in ('sustained_correspondence', 'whole_path_correspondence')
            and (row['covered_k_m'] or 0) > 0 and (row['covered_s_m'] or 0) > 0):
        return 'sustained', True, '기존 평가구간에서 양쪽의 방향·연속 길이 조건을 통과한 대응입니다.'
    if row['sustained_decision'] is not None:
        return 'candidate_rejected', False, '양쪽의 연속 대응으로 인정되지 않은 검토 후보입니다.'
    if row['baseline_geometry_equal']:
        return 'legacy_exact_only', False, '기존 서울 경계 내 구간의 일치 기록입니다. 원본 전체의 정확 일치는 아닙니다.'
    return 'id_only', False, '같은 ID로 연결된 검토 후보이며 공간 대응으로 확정하지 않았습니다.'


def _counterparts(connection, dataset, source_fid, offset, selected_length):
    # SQL column names come only from these fixed alternatives, never the request.
    own, other, other_dataset = ('kict_fid', 'seoul_fid', 'seoul') if dataset == 'kict' else ('seoul_fid', 'kict_fid', 'kict')
    total, accepted = connection.execute(
        f'SELECT COUNT(*),COALESCE(SUM(CASE WHEN {ACCEPTED_SQL} THEN 1 ELSE 0 END),0) FROM relations r WHERE r.{own}=?',
        (source_fid,)).fetchone()
    if offset > total:
        raise RequestError('invalid_request', '목록 위치가 전체 대응 관계 수를 넘었습니다.')
    # CROSS JOIN fixes the join order: the CASE sort otherwise makes SQLite
    # scan every counterpart pipe before applying the selected relation key.
    rows = connection.execute(f'''SELECT r.*,p.source_fid,p.original_id,p.comparison_status,p.length_m counterpart_length
        FROM relations r CROSS JOIN pipes p ON p.dataset=? AND p.source_fid=r.{other}
        WHERE r.{own}=? ORDER BY CASE WHEN r.whole_geometry_equal=1 THEN 0
        WHEN {ACCEPTED_SQL} THEN 1 ELSE 2 END,r.{other} LIMIT ? OFFSET ?''',
                              (other_dataset, source_fid, PAGE_SIZE, offset)).fetchall()
    items = []
    for row in rows:
        relationship, is_accepted, reason = _relationship(row)
        if row['whole_geometry_equal']:
            selected_coverage = counterpart_coverage = 1
            coverage_scope = 'full_original'
        else:
            own_covered, other_covered = ((row['covered_k_m'], row['covered_s_m']) if dataset == 'kict'
                                          else (row['covered_s_m'], row['covered_k_m']))
            selected_coverage = own_covered / selected_length if own_covered is not None and selected_length and selected_length > 0 else None
            counterpart_coverage = other_covered / row['counterpart_length'] if other_covered is not None and row['counterpart_length'] and row['counterpart_length'] > 0 else None
            coverage_scope = 'seoul_boundary_clip' if own_covered is not None or other_covered is not None else None
        items.append(dict(source_fid=row['source_fid'], original_id=row['original_id'],
                          relationship=relationship, accepted=is_accepted, reason=reason,
                          counterpart_comparison_status=row['comparison_status'],
                          selected_coverage_ratio=selected_coverage, counterpart_coverage_ratio=counterpart_coverage,
                          coverage_scope=coverage_scope,
                          exact_same_id_geometry=bool(row['whole_geometry_equal'] and row['same_original_id'])))
    if len(items) != min(PAGE_SIZE, total - offset):
        raise ValueError('Counterpart source relation is incomplete')
    return dict(total=total, accepted=accepted, items=items, offset=offset,
                next_offset=offset + len(items) if offset + len(items) < total else None)


def create_comparison_blueprint(directory, *, package=None):
    bp = Blueprint('water_comparison', __name__, url_prefix='/comparison/water')
    package = package if package is not None else ComparisonPackage(directory)

    @bp.errorhandler(RequestError)
    def invalid(error):
        return jsonify(available=True, error=error.code, message=error.message), error.status

    @bp.errorhandler(OSError)
    @bp.errorhandler(ValueError)
    @bp.errorhandler(sqlite3.Error)
    @bp.errorhandler(KeyError)
    @bp.errorhandler(TypeError)
    def unavailable(error):
        return jsonify(available=False, error='comparison_unavailable',
                       message='비교 자료를 확인할 수 없습니다.'), 503

    @bp.after_request
    def no_stale_response(response):
        response.headers['Cache-Control'] = 'no-store'
        return response

    @bp.get('/metadata')
    def metadata():
        return jsonify(package.verified())

    @bp.post('/lookup')
    def lookup():
        dataset, key, selected, offset = _request_values()
        info = package.verified()
        with package.connect() as connection:
            members = [dict(row) for row in connection.execute('''SELECT source_fid,original_id,region FROM pipes
                WHERE dataset=? AND tile_key=? ORDER BY source_fid LIMIT ?''', (dataset, key, MAX_MEMBERS + 1))]
            if not members:
                raise RequestError('mapping_not_found', '현재 타일 속성과 대응하는 원본 관로를 찾지 못했습니다.', 404)
            if len(members) > MAX_MEMBERS:
                raise ValueError('Original mapping group exceeds the verified bound')
            mapping = dict(member_count=len(members), members=members, ambiguous=len(members) > 1)
            if selected is not None and selected not in {member['source_fid'] for member in members}:
                raise RequestError('source_not_in_mapping', '선택한 원본 번호가 현재 타일의 원본 후보에 없습니다.')
            if selected is None and len(members) == 1:
                selected = members[0]['source_fid']
            pipe = None
            counterparts = dict(total=0, accepted=0, items=[], offset=0, next_offset=None)
            if selected is None:
                if offset:
                    raise RequestError('invalid_request', '원본 후보를 선택한 뒤 대응 목록을 조회해 주세요.')
            else:
                row = connection.execute('SELECT * FROM pipes WHERE dataset=? AND source_fid=?', (dataset, selected)).fetchone()
                pipe = _pipe_view(row)
                counterparts = _counterparts(connection, dataset, selected, offset, row['length_m'])
        return jsonify(available=True, version=info['version'], mapping=mapping, pipe=pipe, counterparts=counterparts)

    return bp
