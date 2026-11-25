"""Small real SQLite fixtures exercise package joins and evidence conservation."""
from contextlib import contextmanager
import hashlib
import importlib
import inspect
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


@contextmanager
def database(path):
    connection = sqlite3.connect(path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()

class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.paths = {k: self.folder / (k + '.sqlite')
                      for k in ('baseline', 'mapping', 'strict', 'keys', 'kict_source')}
        self.output = self.folder / 'output' / 'water_comparison.sqlite'
        self.create_fixture()

    def create_fixture(self):
        source_rows = [('kict', 101, 1, 'same'), ('kict', 102, 2, 'old-id'),
                       ('kict', 103, 3, 'review'), ('seoul', 1, 1, 'same'),
                       ('seoul', 2, 2, 'new-id'), ('seoul', 3, 3, 'review')]
        with database(self.paths['baseline']) as c:
            c.executescript('''
                CREATE TABLE source_keys(dataset,old_id,original_id,full_geometry_md5,PRIMARY KEY(dataset,old_id));
                CREATE TABLE pipes(dataset,id,org_idn,fully_inside,length_m,comparison_status,
                  exact_candidate_count,same_id_count,diameter_value_diff,type_code_diff,material_code_diff,
                  supporting_count,covered_length_1m,coverage_ratio_1m,competing_overlap_m,
                  previous_coverage_ratio_1m,regression_review_required,PRIMARY KEY(dataset,id));
                CREATE TABLE exact_pairs(kid,sid,id_match);
                CREATE TABLE sustained_pairs(kid,sid,created_batch,old_k_m,old_s_m,ranges_k,ranges_s,
                  covered_k_m,covered_s_m,direction_k_m,direction_s_m,longest_k_m,longest_s_m,
                  decision,whole_path_k,whole_path_s,id_match,geometry_equal,min_distance_m,hausdorff_m);
                CREATE TABLE id_pairs(kid,sid,geometry_equal,diameter_equal,type_equal,material_equal,
                  source_low_depth,reference_average_depth);
            ''')
            for dataset, fid, old, oid in source_rows:
                blob = (dataset + str(fid)).encode()
                c.execute('INSERT INTO source_keys VALUES(?,?,?,?)',
                          (dataset, old, oid, hashlib.md5(blob).hexdigest()))
                review = old == 3
                c.execute('INSERT INTO pipes VALUES(' + ','.join('?' for _ in range(17)) + ')',
                          (dataset, old, oid, 1, 10, 'spatial_partial_candidate' if review else 'exact_same_id',
                           0 if review else 1, 1, 0, 1, 0, 1, 4 if review else 10,
                           .4 if review else 1, 0, .96 if review else 1, int(review)))
            c.execute('INSERT INTO exact_pairs VALUES(1,1,1)')
            c.execute('INSERT INTO sustained_pairs VALUES(3,3,1,9.6,9.6,?, ?,4,4,4,4,4,4,?,0,0,1,0,.5,.9)',
                      ('{[0,4]}', '{[0,4]}', 'sustained_correspondence'))
            c.execute('INSERT INTO sustained_pairs VALUES(3,2,1,1,1,?, ?,0,0,0,0,0,0,?,0,0,0,0,0,10)',
                      ('{}', '{}', 'direction_or_distance_rejected'))
            # Same-ID relation outside the spatial pair set must not disappear.
            c.execute('INSERT INTO id_pairs VALUES(2,3,0,0,1,1,2,3)')
        with database(self.paths['mapping']) as c:
            c.execute('CREATE TABLE source_mapping(dataset,old_id,source_fid,match_method,PRIMARY KEY(dataset,old_id))')
            c.executemany('INSERT INTO source_mapping VALUES(?,?,?,?)',
                          [(d, old, f, 'verified') for d, f, old, _ in source_rows])
        with database(self.paths['strict']) as c:
            c.executescript('CREATE TABLE pipes(dataset,source_fid,original_id,geometry_wkb,exact_pair_count,PRIMARY KEY(dataset,source_fid)); CREATE TABLE exact_pairs(kict_fid,seoul_fid,same_original_id,PRIMARY KEY(kict_fid,seoul_fid));')
            c.executemany('INSERT INTO pipes VALUES(?,?,?,?,?)',
                          [(d, f, oid, (d + str(f)).encode(), int(old != 3)) for d, f, old, oid in source_rows])
            c.executemany('INSERT INTO exact_pairs VALUES(?,?,?)', [(101, 1, 1), (102, 2, 0)])
        with database(self.paths['keys']) as c:
            c.execute('CREATE TABLE tile_keys(dataset,source_fid,tile_key,region,PRIMARY KEY(dataset,source_fid))')
            c.executemany('INSERT INTO tile_keys VALUES(?,?,?,?)',
                          [(d, f, 'mixed' if d == 'kict' and f in (101, 103) else d + str(f), '11')
                           for d, f, _, _ in source_rows] + [('kict', 200, 'outside', '26')])
        with database(self.paths['kict_source']) as c:
            c.execute('CREATE TABLE _(fid INTEGER PRIMARY KEY,ORG_IDN TEXT)')
            c.executemany('INSERT INTO _ VALUES(?,?)', [(f, oid) for d, f, _, oid in source_rows if d == 'kict'] + [(200, 'outside-original')])
        sources = {'kict': {'sha256': hashlib.sha256(self.paths['kict_source'].read_bytes()).hexdigest()},
                   'seoul': {'sha256': 'a' * 64}}
        with database(self.paths['strict']) as c:
            c.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT)')
            c.execute('INSERT INTO metadata VALUES(?,?)', ('manifest', json.dumps({'status': 'complete', 'sources': sources})))
        with database(self.paths['keys']) as c:
            c.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT)')
            c.execute('INSERT INTO metadata VALUES(?,?)', ('source_manifest', json.dumps(sources)))
        self.refresh_trusted_manifest(sources)

    def refresh_trusted_manifest(self, sources=None):
        if sources is None:
            with database(self.paths['strict']) as c:
                sources = json.loads(c.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()[0])['sources']
        (self.folder / 'manifest.json').write_text(json.dumps({'status': 'complete', 'sources': sources,
            'outputs': {'strict.sqlite': {'sha256': hashlib.sha256(self.paths['strict'].read_bytes()).hexdigest()}}}))

    def build(self):
        if not (ROOT / 'tools/comparison/build_package.py').exists():
            self.fail('The offline comparison package builder is not implemented')
        module = importlib.import_module('tools.comparison.build_package')
        return module.build_package(**self.paths, output=self.output)

    def test_mapping_retains_source_ids_and_full_state_scope(self):
        result = self.build()
        with database(self.output) as c:
            self.assertEqual(c.execute("SELECT source_fid,baseline_id,original_id,comparison_status FROM pipes WHERE dataset='kict' ORDER BY source_fid").fetchall(),
                             [(101, 1, 'same', 'MATCH'), (102, 2, 'old-id', 'MATCH'),
                              (103, 3, 'review', 'CONFLICT'), (200, None, 'outside-original', 'NOT_COMPARED')])
        self.assertEqual(result['validation']['source_row_conservation'], True)
        self.assertEqual(result['counts']['pipes'], 7)

    def test_exact_flags_do_not_promote_mixed_tile_group(self):
        self.build()
        with database(self.output) as c:
            self.assertEqual(c.execute("SELECT exact_geometry_match,exact_same_id_geometry FROM pipes WHERE dataset='kict' AND source_fid=102").fetchone(), (1, 0))
            self.assertEqual(c.execute("SELECT member_count,all_strict FROM tile_groups WHERE dataset='kict' AND tile_key='mixed'").fetchone(), (2, 0))

    def test_all_relation_types_preserve_rejected_and_nonspatial_evidence(self):
        self.build()
        with database(self.output) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM relations').fetchone()[0], 5)
            self.assertEqual(c.execute('SELECT sustained_decision,covered_k_m,ranges_k FROM relations WHERE kict_fid=103 AND seoul_fid=2').fetchone(),
                             ('direction_or_distance_rejected', 0, '{}'))
            self.assertEqual(c.execute('SELECT diameter_equal,source_low_depth,reference_average_depth FROM relations WHERE kict_fid=102 AND seoul_fid=3').fetchone(), (0, 2, 3))

    def test_mapping_geometry_mismatch_is_rejected(self):
        with database(self.paths['strict']) as c:
            c.execute("UPDATE pipes SET geometry_wkb=x'00' WHERE dataset='kict' AND source_fid=103")
        # This test targets mapping validation after provenance has been verified.
        self.refresh_trusted_manifest()
        with self.assertRaisesRegex(ValueError, 'mapping.*geometry|geometry.*mapping'):
            self.build()

    def test_missing_relation_source_mapping_is_rejected(self):
        with database(self.paths['mapping']) as c:
            c.execute("DELETE FROM source_mapping WHERE dataset='seoul' AND old_id=3")
        with self.assertRaisesRegex(ValueError, 'mapping'):
            self.build()

    def test_input_hashes_and_existing_outputs_are_preserved(self):
        before = {k: hashlib.sha256(p.read_bytes()).hexdigest() for k, p in self.paths.items()}
        result = self.build()
        after = {k: hashlib.sha256(p.read_bytes()).hexdigest() for k, p in self.paths.items()}
        self.assertEqual(before, after)
        self.assertEqual(result['input_hashes'], before)
        with self.assertRaises(FileExistsError):
            self.build()

    def test_tile_source_provenance_mismatch_is_rejected(self):
        with database(self.paths['keys']) as c:
            sources = json.loads(c.execute("SELECT value FROM metadata WHERE key='source_manifest'").fetchone()[0])
            sources['seoul']['sha256'] = 'b' * 64
            c.execute("UPDATE metadata SET value=? WHERE key='source_manifest'", (json.dumps(sources),))
        with self.assertRaisesRegex(ValueError, 'provenance'):
            self.build()

    def test_strict_pair_artifact_tampering_is_rejected(self):
        with database(self.paths['strict']) as c:
            c.execute('INSERT INTO exact_pairs VALUES(103,2,0)')
        with self.assertRaisesRegex(ValueError, 'provenance|trusted.*hash'):
            self.build()

    def test_missing_trusted_manifest_is_rejected(self):
        (self.folder / 'manifest.json').unlink()
        with self.assertRaisesRegex(ValueError, 'provenance|manifest'):
            self.build()

    def test_relation_update_uses_bounded_source_key_lookups(self):
        module = importlib.import_module('tools.comparison.build_package')
        self.assertTrue(hasattr(module, 'RELATION_ID_UPDATE'), 'Relation-first update plan is not implemented')
        with database(self.folder / 'plan.sqlite') as c:
            c.executescript(module.SCHEMA)
            plan = [r[3] for r in c.execute('EXPLAIN QUERY PLAN ' + module.RELATION_ID_UPDATE)]
        self.assertTrue(any('SCAN r' in p for p in plan), plan)
        self.assertEqual(sum('SEARCH' in p and 'dataset=? AND source_fid=?' in p for p in plan), 2, plan)

    def test_resume_requires_matching_verified_pipe_checkpoint(self):
        result = self.build()
        staging = self.output.with_suffix('.building.sqlite')
        self.output.replace(staging)
        module = importlib.import_module('tools.comparison.build_package')
        self.assertIn('resume', inspect.signature(module.build_package).parameters, 'Verified checkpoint resume is not implemented')
        with self.assertRaisesRegex(ValueError, 'checkpoint'):
            module.build_package(**self.paths, output=self.output, resume=True)
        self.assertTrue(staging.exists())
        with database(staging) as c:
            c.execute('DELETE FROM relations')
            c.execute('DELETE FROM tile_groups')
            c.execute('DELETE FROM metadata')
            for name in ('pipes_tile_key', 'pipes_original_id', 'relations_seoul'):
                c.execute('DROP INDEX ' + name)
            c.execute('INSERT INTO metadata VALUES(?,?)', ('build_checkpoint', json.dumps({
                'input_hashes': result['input_hashes'], 'rule_version': result['rule_version'], 'pipes': 7})))
        resumed = module.build_package(**self.paths, output=self.output, resume=True)
        self.assertEqual(resumed['counts']['relations'], 5)
        self.assertTrue(resumed['validation']['resumed_from_verified_pipe_checkpoint'])


if __name__ == '__main__':
    unittest.main()
