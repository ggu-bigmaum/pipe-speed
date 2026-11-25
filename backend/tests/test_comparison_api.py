"""Read-only API contracts over small, real comparison SQLite packages."""
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import sys

from flask import Flask
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'backend'))
from tools.comparison.build_package import SCHEMA
from comparison_keys import tile_key


def properties(identifier=100):
    return {'FTR_IDN': identifier, 'STD_DIP': 100, 'MOP_CDE': 'MOP001'}


class Package:
    def __init__(self, directory):
        self.directory = directory
        directory.mkdir()
        self.path = directory / 'water_comparison.sqlite'
        with sqlite3.connect(self.path) as c:
            c.executescript(SCHEMA)
            c.executescript('CREATE INDEX pipes_tile_key ON pipes(dataset,tile_key); CREATE INDEX relations_seoul ON relations(seoul_fid,kict_fid);')
        self.pipe('kict', 101, 'same', properties())
        self.pipe('kict', 102, 'duplicate-A', properties(7))
        self.pipe('kict', 103, 'duplicate-B', properties(7), strict=False)
        self.pipe('kict', 104, 'outside', properties(9), region='26', status='NOT_COMPARED', strict=False)
        for fid in range(1, 9):
            self.pipe('seoul', fid, 'same' if fid == 1 else 'S' + str(fid),
                      {'PLINE_ID': 'S' + str(fid), 'PLINE_CALB': 100, 'PLINE_MTRQ': 'MOP001'})
        for fid, values in [(1, dict(whole_geometry_equal=1, same_original_id=1)),
                            (2, dict(whole_geometry_equal=1)),
                            (3, dict(sustained_decision='sustained_correspondence', covered_k_m=4, covered_s_m=4)),
                            (4, dict(sustained_decision='direction_or_distance_rejected', covered_k_m=0, covered_s_m=0)),
                            (5, dict(same_original_id=1)),
                            (6, dict(baseline_geometry_equal=1)),
                            (7, dict(sustained_decision='short_continuous_overlap_review', covered_k_m=2, covered_s_m=2)),
                            (8, dict(sustained_decision='sustained_correspondence', covered_k_m=2, covered_s_m=0))]:
            self.relation(101, fid, **values)
        self.finish()

    def pipe(self, dataset, fid, original_id, attrs, *, region='11', status='MATCH', strict=True):
        with sqlite3.connect(self.path) as c:
            c.execute('''INSERT INTO pipes(dataset,source_fid,original_id,tile_key,region,
              comparison_status,reason_code,exact_geometry_match,exact_same_id_geometry,
              coverage_ratio,length_m,regression_review_required,attribute_flags_json)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                      (dataset, fid, original_id, tile_key(dataset, attrs), region, status,
                       'whole_source_equal' if region == '11' else 'reference_unavailable',
                       int(strict), int(strict), .8 if region == '11' else None,
                       10 if region == '11' else None, 0, '{"diameter_value_diff":0}'))

    def relation(self, kict_fid, seoul_fid, **values):
        with sqlite3.connect(self.path) as c:
            fields = ['kict_fid', 'seoul_fid'] + list(values)
            c.execute('INSERT INTO relations(' + ','.join(fields) + ') VALUES(' + ','.join('?' for _ in fields) + ')',
                      [kict_fid, seoul_fid] + list(values.values()))

    def finish(self):
        with sqlite3.connect(self.path) as c:
            counts = {table: c.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                      for table in ['pipes', 'relations', 'tile_groups']}
            for name, value in {'schema_version': 1, 'rule_version': 'fixture-rule-v1', 'counts': counts}.items():
                c.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)', (name, json.dumps(value)))
        self.sha = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.manifest = dict(status='complete', schema_version=1, rule_version='fixture-rule-v1',
                             counts=counts, scope='Seoul supplied water comparison',
                             output=dict(sha256=self.sha, bytes=self.path.stat().st_size,
                                         path='must-not-follow-this-path.sqlite'))
        (self.directory / 'manifest.json').write_text(json.dumps(self.manifest), encoding='utf-8')


@pytest.fixture
def package(tmp_path):
    return Package(tmp_path / 'comparison')


def client(directory):
    assert (ROOT / 'backend/comparison.py').exists(), 'Read-only comparison API is not implemented'
    module = importlib.import_module('comparison')
    app = Flask(__name__)
    app.register_blueprint(module.create_comparison_blueprint(directory))
    return app.test_client()


def lookup(c, **changes):
    return c.post('/comparison/water/lookup', json=dict({'layer': 'kict_national', 'properties': properties()}, **changes))


def test_available_metadata_has_stable_version_without_local_paths(package):
    r = client(package.directory).get('/comparison/water/metadata')
    assert r.status_code == 200
    data = r.get_json()
    assert data['available'] is True and package.sha in data['version']
    assert data['counts']['pipes'] == 12
    assert 'must-not-follow' not in r.get_data(as_text=True)


def test_missing_package_is_explicit_and_does_not_invent_business_state(tmp_path):
    c = client(tmp_path)
    for r in [c.get('/comparison/water/metadata'), lookup(c)]:
        assert r.status_code == 503
        assert r.get_json()['available'] is False
        assert 'NOT_COMPARED' not in r.get_data(as_text=True)


def test_unique_pipe_reports_original_and_separate_strict_scope(package):
    r = lookup(client(package.directory))
    assert r.status_code == 200
    d = r.get_json()
    assert d['mapping'] == {'member_count': 1, 'ambiguous': False,
                            'members': [{'source_fid': 101, 'original_id': 'same', 'region': '11'}]}
    assert d['pipe']['source_fid'] == 101 and d['pipe']['exact_same_id_geometry'] is True
    assert d['pipe']['length_scope'] == 'seoul_boundary_clip'
    assert d['pipe']['attribute_flags']['diameter_value_diff'] == 0
    assert d['pipe']['reason']


def test_duplicate_group_requires_explicit_member_choice(package):
    c = client(package.directory)
    d = lookup(c, properties=properties(7)).get_json()
    assert d['mapping']['ambiguous'] and d['mapping']['member_count'] == 2
    assert d['pipe'] is None and [m['source_fid'] for m in d['mapping']['members']] == [102, 103]
    chosen = lookup(c, properties=properties(7), source_fid=103).get_json()
    assert chosen['pipe']['source_fid'] == 103 and chosen['pipe']['exact_same_id_geometry'] is False
    wrong = lookup(c, properties=properties(7), source_fid=101)
    assert wrong.status_code == 400 and wrong.get_json()['error'] == 'source_not_in_mapping'


def test_missing_key_and_sql_injection_do_not_select_any_other_source(package):
    c = client(package.directory)
    for identifier in [999999, "0' OR 1=1 --"]:
        r = lookup(c, properties=properties(identifier))
        assert r.status_code == 404
        assert r.get_json()['error'] == 'mapping_not_found'
        assert 'NOT_COMPARED' not in r.get_data(as_text=True)


def test_accepted_relations_exclude_rejected_and_clipped_only_evidence(package):
    d = lookup(client(package.directory)).get_json()['counterparts']
    assert (d['total'], d['accepted']) == (8, 3)
    items = {item['source_fid']: item for item in d['items']}
    assert {fid: items[fid]['relationship'] for fid in items} == {
        1: 'exact_same_id', 2: 'exact_other_id', 3: 'sustained',
        4: 'candidate_rejected', 5: 'id_only', 6: 'legacy_exact_only',
        7: 'candidate_rejected', 8: 'candidate_rejected'}
    assert all(items[fid]['accepted'] is (fid in [1, 2, 3]) for fid in items)


def test_pair_coverage_never_reuses_a_counterparts_overall_ratio(package):
    with sqlite3.connect(package.path) as db:
        db.execute("UPDATE pipes SET length_m=20,coverage_ratio=.9 WHERE dataset='seoul' AND source_fid=3")
    package.finish()
    items = {item['source_fid']: item for item in lookup(client(package.directory)).get_json()['counterparts']['items']}
    assert items[1]['selected_coverage_ratio'] == items[1]['counterpart_coverage_ratio'] == 1
    assert items[1]['coverage_scope'] == 'full_original'
    assert items[3]['selected_coverage_ratio'] == .4
    assert items[3]['counterpart_coverage_ratio'] == .2
    assert items[3]['coverage_scope'] == 'seoul_boundary_clip'
    assert items[4]['selected_coverage_ratio'] == items[4]['counterpart_coverage_ratio'] == 0
    assert items[5]['selected_coverage_ratio'] is None
    assert 'coverage_ratio' not in items[3] and 'comparison_status' not in items[3]
    assert items[3]['counterpart_comparison_status'] == 'MATCH'


def test_all_partners_are_reachable_with_bounded_stable_pages(package):
    for fid in range(20, 50):
        package.pipe('seoul', fid, 'extra' + str(fid), {'PLINE_ID': str(fid)}, strict=False)
        package.relation(101, fid, same_original_id=1)
    package.finish()
    c = client(package.directory)
    first = lookup(c).get_json()['counterparts']
    second = lookup(c, offset=first['next_offset']).get_json()['counterparts']
    assert (first['total'], first['accepted'], len(first['items']), first['offset']) == (38, 3, 20, 0)
    assert len(second['items']) == 18 and second['next_offset'] is None
    assert len({x['source_fid'] for x in first['items'] + second['items']}) == 38
    assert lookup(c, offset=39).status_code == 400


def test_reverse_seoul_lookup_uses_kict_counterpart(package):
    r = lookup(client(package.directory), layer='sa001',
               properties={'PLINE_ID': 'S1', 'PLINE_CALB': 100, 'PLINE_MTRQ': 'MOP001'})
    d = r.get_json()
    assert d['pipe']['dataset'] == 'seoul'
    assert d['counterparts']['items'][0]['source_fid'] == 101


@pytest.mark.parametrize('changes', [
    {'source_fid': True}, {'source_fid': 0}, {'source_fid': -1}, {'source_fid': '101'},
    {'source_fid': 2**54}, {'offset': True}, {'offset': -1}, {'offset': 1.5},
    {'offset': 2**40}, {'properties': []}, {'properties': {'FTR_IDN': []}},
    {'properties': {'FTR_IDN': True}}, {'properties': {'FTR_IDN': 10**500}},
    {'properties': dict(properties(), geometry='anything')},
    {'properties': {'FTR_IDN': 'x' * 1000}}, {'unexpected': 'field'},
])
def test_invalid_input_is_bounded_and_explicit(package, changes):
    r = lookup(client(package.directory), **changes)
    assert r.status_code == 400 and r.is_json


def test_unsupported_layer_has_json_404(package):
    r = lookup(client(package.directory), layer='sb001')
    assert r.status_code == 404 and r.get_json()['error'] == 'unsupported_layer'


def test_hash_change_and_incomplete_manifest_fail_closed(package):
    c = client(package.directory)
    assert lookup(c).status_code == 200
    with sqlite3.connect(package.path) as db:
        db.execute("UPDATE pipes SET original_id='changed' WHERE dataset='kict' AND source_fid=101")
    assert lookup(c).status_code == 503
    package.finish()
    assert lookup(c).status_code == 200
    package.manifest['status'] = 'building'
    (package.directory / 'manifest.json').write_text(json.dumps(package.manifest), encoding='utf-8')
    assert lookup(c).status_code == 503


@pytest.mark.parametrize('change', [{'output': []}, {'schema_version': True}, {'schema_version': 2}])
def test_malformed_manifest_fields_are_json_unavailable(package, change):
    package.manifest.update(change)
    (package.directory / 'manifest.json').write_text(json.dumps(package.manifest), encoding='utf-8')
    r = lookup(client(package.directory))
    assert r.status_code == 503 and r.get_json()['available'] is False


def test_connections_close_on_success_and_corrupt_stored_value(package, monkeypatch):
    c = client(package.directory)
    module = importlib.import_module('comparison')
    connect = module.sqlite3.connect
    connections = []

    def tracked(*args, **kwargs):
        db = connect(*args, **kwargs)
        connections.append(db)
        assert 'mode=ro' in args[0]
        return db

    monkeypatch.setattr(module.sqlite3, 'connect', tracked)
    assert lookup(c).status_code == 200
    for db in connections:
        with pytest.raises(sqlite3.ProgrammingError):
            db.execute('SELECT 1')
    monkeypatch.undo()
    with sqlite3.connect(package.path) as db:
        db.execute("UPDATE pipes SET attribute_flags_json='invalid' WHERE dataset='kict' AND source_fid=101")
    package.finish()
    monkeypatch.setattr(module.sqlite3, 'connect', tracked)
    assert lookup(c).status_code == 503
    for db in connections:
        with pytest.raises(sqlite3.ProgrammingError):
            db.execute('SELECT 1')


def test_requests_leave_package_bytes_unchanged(package):
    before = package.path.read_bytes()
    c = client(package.directory)
    assert lookup(c).status_code == 200
    assert lookup(c, properties=properties(9)).get_json()['pipe']['length_scope'] is None
    assert package.path.read_bytes() == before


def test_hash_is_cached_for_each_successful_or_failed_file_revision(package, monkeypatch):
    c = client(package.directory)
    module = importlib.import_module('comparison')
    real_hash = module._sha256
    reads = []

    def counted(path):
        reads.append(path)
        return real_hash(path)

    monkeypatch.setattr(module, '_sha256', counted)
    assert lookup(c).status_code == lookup(c).status_code == 200
    assert len(reads) == 1
    package.manifest['output']['sha256'] = '0' * 64
    (package.directory / 'manifest.json').write_text(json.dumps(package.manifest), encoding='utf-8')
    assert lookup(c).status_code == lookup(c).status_code == 503
    assert len(reads) == 2


def test_oversized_member_group_is_not_silently_truncated(package):
    with sqlite3.connect(package.path) as db:
        for fid in range(200, 299):
            db.execute('''INSERT INTO pipes SELECT dataset,?,original_id,tile_key,region,
                comparison_status,reason_code,candidate_status,exact_geometry_match,exact_same_id_geometry,
                baseline_id,coverage_ratio,length_m,regression_review_required,attribute_flags_json,
                fully_inside,supporting_count,competing_overlap_m,baseline_status,previous_coverage_ratio
                FROM pipes WHERE dataset='kict' AND source_fid=102''', (fid,))
    package.finish()
    response = lookup(client(package.directory), properties=properties(7))
    assert response.status_code == 503 and response.get_json()['available'] is False


def test_app_registers_comparison_routes():
    from app import app
    routes = {rule.rule for rule in app.url_map.iter_rules()}
    assert '/comparison/water/metadata' in routes
    assert '/comparison/water/lookup' in routes


@pytest.mark.parametrize('dataset,source_fid', [('kict', 101), ('seoul', 1)])
def test_counterpart_plan_looks_up_only_relations_of_selected_source(package, dataset, source_fid):
    client(package.directory)
    module = importlib.import_module('comparison')
    with sqlite3.connect(package.path) as db:
        db.row_factory = sqlite3.Row
        statements = []
        db.set_trace_callback(statements.append)
        module._counterparts(db, dataset, source_fid, 0, 10)
        db.set_trace_callback(None)
        query = next(query for query in statements if 'counterpart_length' in query)
        plan = [row[3] for row in db.execute('EXPLAIN QUERY PLAN ' + query)]
    assert plan[0].startswith('SEARCH r '), plan
    assert any('SEARCH p ' in step and 'dataset=? AND source_fid=?' in step for step in plan), plan
    assert not any(step.startswith('SCAN ') for step in plan), plan
