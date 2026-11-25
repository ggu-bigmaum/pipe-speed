"""Build the offline water comparison package from immutable evidence.

No PostgreSQL connection or geometry mutation occurs here. Historical lengths
and ranges refer to the Seoul-boundary-clipped comparison, while exact display
flags come exclusively from the separate full-original equality relation.
"""
import argparse
import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from .rules import STATES, VERSION, classify

PIPE_COLUMNS = (
    'dataset', 'source_fid', 'original_id', 'tile_key', 'region',
    'comparison_status', 'reason_code', 'candidate_status',
    'exact_geometry_match', 'exact_same_id_geometry', 'baseline_id',
    'coverage_ratio', 'length_m', 'regression_review_required',
    'attribute_flags_json', 'fully_inside', 'supporting_count',
    'competing_overlap_m', 'baseline_status', 'previous_coverage_ratio',
)

SCHEMA = '''
CREATE TABLE pipes(
 dataset TEXT NOT NULL,source_fid INTEGER NOT NULL,original_id TEXT,
 tile_key TEXT NOT NULL,region TEXT,comparison_status TEXT NOT NULL,
 reason_code TEXT NOT NULL,candidate_status TEXT,
 exact_geometry_match INTEGER NOT NULL,exact_same_id_geometry INTEGER NOT NULL,
 baseline_id INTEGER,coverage_ratio REAL,length_m REAL,
 regression_review_required INTEGER NOT NULL,attribute_flags_json TEXT NOT NULL,
 fully_inside INTEGER,supporting_count INTEGER,competing_overlap_m REAL,
 baseline_status TEXT,previous_coverage_ratio REAL,
 PRIMARY KEY(dataset,source_fid)) WITHOUT ROWID;
CREATE TABLE relations(
 kict_fid INTEGER NOT NULL,seoul_fid INTEGER NOT NULL,
 whole_geometry_equal INTEGER NOT NULL DEFAULT 0,same_original_id INTEGER NOT NULL DEFAULT 0,
 baseline_geometry_equal INTEGER,baseline_id_match INTEGER,sustained_decision TEXT,
 old_k_m REAL,old_s_m REAL,covered_k_m REAL,covered_s_m REAL,ranges_k TEXT,ranges_s TEXT,
 direction_k_m REAL,direction_s_m REAL,longest_k_m REAL,longest_s_m REAL,
 whole_path_k INTEGER,whole_path_s INTEGER,min_distance_m REAL,hausdorff_m REAL,
 diameter_equal INTEGER,type_equal INTEGER,material_equal INTEGER,
 source_low_depth REAL,reference_average_depth REAL,
 PRIMARY KEY(kict_fid,seoul_fid)) WITHOUT ROWID;
CREATE TABLE tile_groups(dataset TEXT NOT NULL,tile_key TEXT NOT NULL,
 member_count INTEGER NOT NULL,all_strict INTEGER NOT NULL,
 PRIMARY KEY(dataset,tile_key)) WITHOUT ROWID;
CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL) WITHOUT ROWID;
'''

RELATION_ID_UPDATE = '''UPDATE relations AS r SET same_original_id=COALESCE((
    SELECT CASE WHEN k.original_id IS NOT NULL AND k.original_id<>''
        AND k.original_id=s.original_id THEN 1 ELSE 0 END
    FROM pipes k JOIN pipes s ON s.dataset='seoul' AND s.source_fid=r.seoul_fid
    WHERE k.dataset='kict' AND k.source_fid=r.kict_fid),0)'''


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as source:
        while block := source.read(8 * 1024 * 1024):
            h.update(block)
    return h.hexdigest()


def verify_provenance(paths, hashes=None):
    """Fail closed on a replaced pair file or keys from another source bundle."""
    paths = {name: Path(path).resolve() for name, path in paths.items()}
    hashes = hashes or {name: sha256(path) for name, path in paths.items()}
    try:
        strict_db = sqlite3.connect(paths['strict'].as_uri() + '?mode=ro', uri=True)
        try:
            internal = json.loads(strict_db.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()[0])
        finally:
            strict_db.close()
        key_db = sqlite3.connect(paths['keys'].as_uri() + '?mode=ro', uri=True)
        try:
            tile_sources = json.loads(key_db.execute("SELECT value FROM metadata WHERE key='source_manifest'").fetchone()[0])
        finally:
            key_db.close()
        trusted = json.loads(paths['strict'].with_name('manifest.json').read_text(encoding='utf-8'))
        expected = trusted['outputs'][paths['strict'].name]['sha256']
        if expected != hashes['strict']:
            raise ValueError('Strict artifact provenance differs from the trusted output hash')
        if internal['status'] != 'complete' or trusted['status'] != 'complete':
            raise ValueError('Strict comparison provenance is incomplete')
        for dataset in ('kict', 'seoul'):
            source_hash = internal['sources'][dataset]['sha256']
            if (len(source_hash) != 64 or any(char not in '0123456789abcdef' for char in source_hash)
                    or tile_sources[dataset]['sha256'] != source_hash
                    or trusted['sources'][dataset]['sha256'] != source_hash):
                raise ValueError(f'{dataset} tile/source provenance mismatch')
        if internal['sources']['kict']['sha256'] != hashes['kict_source']:
            raise ValueError('National source provenance does not match the strict comparison')
    except (OSError, sqlite3.Error, KeyError, TypeError, IndexError, json.JSONDecodeError) as exc:
        raise ValueError('Required comparison provenance metadata or manifest is missing or invalid') from exc
    return dict(verified=True, strict_artifact_sha256=hashes['strict'],
                trusted_manifest_sha256=sha256(paths['strict'].with_name('manifest.json')),
                sources=internal['sources'])


def _count(c, query):
    return c.execute(query).fetchone()[0]


def _require_zero(c, query, message):
    failures = _count(c, query)
    if failures:
        raise ValueError(f'{message}: {failures} rows')


def _validate_inputs(c):
    """Recheck every mapping using original IDs and full geometry bytes."""
    c.create_function('wkb_md5', 1, lambda blob: hashlib.md5(blob).hexdigest() if blob is not None else None,
                      deterministic=True)
    _require_zero(c, '''SELECT count(*) FROM mapping.source_mapping
        WHERE dataset NOT IN ('kict','seoul')''', 'Unsupported source mapping dataset')
    _require_zero(c, '''SELECT count(*) FROM (SELECT dataset,source_fid,count(*) n
        FROM mapping.source_mapping GROUP BY 1,2 HAVING n<>1)''', 'Nonunique source mapping')
    _require_zero(c, '''SELECT count(*) FROM strict.pipes p
        LEFT JOIN mapping.source_mapping m ON m.dataset=p.dataset AND m.source_fid=p.source_fid
        LEFT JOIN baseline.source_keys o ON o.dataset=m.dataset AND o.old_id=m.old_id
        WHERE m.old_id IS NULL OR o.old_id IS NULL OR p.original_id IS NOT o.original_id''',
                  'Incomplete or inconsistent source mapping')
    _require_zero(c, '''SELECT count(*) FROM mapping.source_mapping m
        LEFT JOIN strict.pipes p ON p.dataset=m.dataset AND p.source_fid=m.source_fid
        WHERE p.source_fid IS NULL''', 'Source mapping has unknown current rows')
    _require_zero(c, '''SELECT count(*) FROM baseline.source_keys o
        LEFT JOIN mapping.source_mapping m ON m.dataset=o.dataset AND m.old_id=o.old_id
        WHERE m.old_id IS NULL''', 'Original source mapping is incomplete')
    _require_zero(c, '''SELECT count(*) FROM strict.pipes p
        JOIN mapping.source_mapping m ON m.dataset=p.dataset AND m.source_fid=p.source_fid
        JOIN baseline.source_keys o ON o.dataset=m.dataset AND o.old_id=m.old_id
        WHERE wkb_md5(p.geometry_wkb) IS NOT o.full_geometry_md5''', 'Source mapping geometry mismatch')
    _require_zero(c, '''SELECT count(*) FROM baseline.pipes p
        LEFT JOIN mapping.source_mapping m ON m.dataset=p.dataset AND m.old_id=p.id
        WHERE m.old_id IS NULL''', 'Historical pipe source mapping is incomplete')
    _require_zero(c, '''SELECT count(*) FROM keys.tile_keys
        WHERE dataset NOT IN ('kict','seoul')''', 'Unsupported tile dataset')
    _require_zero(c, '''SELECT count(*) FROM strict.pipes p
        LEFT JOIN keys.tile_keys k ON k.dataset=p.dataset AND k.source_fid=p.source_fid
        WHERE k.source_fid IS NULL OR k.region IS NOT '11' ''', 'Current comparison source tile mapping missing')
    _require_zero(c, '''SELECT count(*) FROM keys.tile_keys k NOT INDEXED
        LEFT JOIN kict_source._ n ON n.fid=k.source_fid
        WHERE k.dataset='kict' AND n.fid IS NULL''', 'National original row missing')
    _require_zero(c, '''SELECT count(*) FROM kict_source._ n
        LEFT JOIN keys.tile_keys k ON k.dataset='kict' AND k.source_fid=n.fid
        WHERE k.source_fid IS NULL''', 'National tile keys are incomplete')
    _require_zero(c, '''SELECT count(*) FROM keys.tile_keys k
        LEFT JOIN strict.pipes p ON p.dataset=k.dataset AND p.source_fid=k.source_fid
        WHERE (k.dataset='seoul' OR k.region='11') AND p.source_fid IS NULL''',
                  'Seoul tile source is not present in strict comparison')
    _require_zero(c, '''SELECT count(*) FROM strict.pipes p INDEXED BY sqlite_autoindex_pipes_1 JOIN kict_source._ n ON n.fid=p.source_fid
        WHERE p.dataset='kict' AND p.original_id IS NOT n.ORG_IDN''', 'National source identifier mismatch')
    _require_zero(c, '''SELECT count(*) FROM strict.exact_pairs e
        LEFT JOIN strict.pipes k ON k.dataset='kict' AND k.source_fid=e.kict_fid
        LEFT JOIN strict.pipes s ON s.dataset='seoul' AND s.source_fid=e.seoul_fid
        WHERE k.source_fid IS NULL OR s.source_fid IS NULL OR
        e.same_original_id <> CASE WHEN k.original_id IS NOT NULL AND k.original_id<>''
                                  AND k.original_id=s.original_id THEN 1 ELSE 0 END''',
                  'Strict exact pair identifier mismatch')
    for table in ('exact_pairs', 'sustained_pairs', 'id_pairs'):
        _require_zero(c, f'''SELECT count(*) FROM baseline.{table} p
            LEFT JOIN mapping.source_mapping k ON k.dataset='kict' AND k.old_id=p.kid
            LEFT JOIN mapping.source_mapping s ON s.dataset='seoul' AND s.old_id=p.sid
            WHERE k.old_id IS NULL OR s.old_id IS NULL''', f'{table} relation source mapping missing')


def _build_pipes(c, progress):
    c.executescript('''CREATE TEMP TABLE strict_flags(dataset TEXT,source_fid INTEGER,strict INTEGER,
        PRIMARY KEY(dataset,source_fid)) WITHOUT ROWID;
        INSERT INTO strict_flags SELECT 'kict',kict_fid,max(same_original_id) FROM strict.exact_pairs GROUP BY kict_fid;
        INSERT INTO strict_flags SELECT 'seoul',seoul_fid,max(same_original_id) FROM strict.exact_pairs GROUP BY seoul_fid;''')
    source = c.execute('''SELECT t.dataset,t.source_fid,t.tile_key,t.region,
        CASE WHEN t.dataset='kict' THEN n.ORG_IDN ELSE current.original_id END original_id,
        m.old_id,b.id baseline_id,b.fully_inside,b.length_m,b.comparison_status,
        b.supporting_count,b.coverage_ratio_1m,b.competing_overlap_m,b.regression_review_required,
        b.diameter_value_diff,b.type_code_diff,b.material_code_diff,b.previous_coverage_ratio_1m,
        flags.source_fid exact_fid,flags.strict
        FROM keys.tile_keys t
        LEFT JOIN strict.pipes current ON current.dataset=t.dataset AND current.source_fid=t.source_fid
        LEFT JOIN kict_source._ n ON t.dataset='kict' AND n.fid=t.source_fid
        LEFT JOIN mapping.source_mapping m ON m.dataset=t.dataset AND m.source_fid=t.source_fid
        LEFT JOIN baseline.pipes b ON b.dataset=m.dataset AND b.id=m.old_id
        LEFT JOIN strict_flags flags ON flags.dataset=t.dataset AND flags.source_fid=t.source_fid''')
    statement = 'INSERT INTO pipes VALUES(' + ','.join('?' for _ in PIPE_COLUMNS) + ')'
    count = 0
    while rows := source.fetchmany(10000):
        result = []
        for row in rows:
            (dataset, fid, key, region, original, old, baseline_id, inside, length, status,
             support, coverage, competing, review, diameter, pipe_type, material, previous,
             exact_fid, strict) = row
            exact = exact_fid is not None
            decision = classify(dict(dataset=dataset, reference_available=region == '11',
                source_verified=old is not None, whole_geometry_equal=exact,
                same_id_geometry_equal=bool(strict), fully_inside=bool(inside),
                length_m=length, supporting_count=support or 0, coverage_ratio=coverage,
                competing_overlap_m=competing, regression_review_required=bool(review), previous_status=status))
            attribute_flags = json.dumps(dict(diameter_value_diff=diameter,
                type_code_diff=pipe_type, material_code_diff=material), separators=(',', ':'))
            result.append((dataset, fid, original, key, region, decision['status'],
                decision['reason_code'], decision['candidate_status'], int(exact),
                int(decision['exact_same_id_geometry']), baseline_id, coverage, length,
                int(bool(review)), attribute_flags, inside, support, competing, status, previous))
        c.executemany(statement, result)
        count += len(result)
        if count % 200000 == 0:
            c.commit()
            progress('pipes', count)
    c.commit()
    progress('pipes', count)


def _merge_relation(c, columns, query):
    updates = ','.join(f'{name}=excluded.{name}' for name in columns if name not in ('kict_fid', 'seoul_fid'))
    c.execute(f"INSERT INTO relations({','.join(columns)}) {query} WHERE 1 "
              f'ON CONFLICT(kict_fid,seoul_fid) DO UPDATE SET {updates}')


def _build_relations(c):
    _merge_relation(c, ['kict_fid', 'seoul_fid', 'whole_geometry_equal'],
                    'SELECT kict_fid,seoul_fid,1 FROM strict.exact_pairs')
    join = ''' CROSS JOIN mapping.source_mapping k ON k.dataset='kict' AND k.old_id=p.kid
               CROSS JOIN mapping.source_mapping s ON s.dataset='seoul' AND s.old_id=p.sid'''
    _merge_relation(c, ['kict_fid', 'seoul_fid', 'baseline_geometry_equal', 'baseline_id_match'],
                    'SELECT k.source_fid,s.source_fid,1,p.id_match FROM baseline.exact_pairs p' + join)
    fields = ['old_k_m', 'old_s_m', 'covered_k_m', 'covered_s_m', 'ranges_k', 'ranges_s',
              'direction_k_m', 'direction_s_m', 'longest_k_m', 'longest_s_m',
              'whole_path_k', 'whole_path_s', 'min_distance_m', 'hausdorff_m']
    _merge_relation(c, ['kict_fid', 'seoul_fid', 'sustained_decision'] + fields,
                    'SELECT k.source_fid,s.source_fid,p.decision,' + ','.join('p.' + f for f in fields) +
                    ' FROM baseline.sustained_pairs p' + join)
    fields = ['geometry_equal', 'diameter_equal', 'type_equal', 'material_equal',
              'source_low_depth', 'reference_average_depth']
    _merge_relation(c, ['kict_fid', 'seoul_fid', 'baseline_geometry_equal'] + fields[1:],
                    'SELECT k.source_fid,s.source_fid,' + ','.join('p.' + f for f in fields) +
                    ' FROM baseline.id_pairs p' + join)
    c.execute(RELATION_ID_UPDATE)
    c.commit()


def _validate_output(c):
    expected = _count(c, 'SELECT count(*) FROM keys.tile_keys')
    if _count(c, 'SELECT count(*) FROM pipes') != expected:
        raise ValueError('Source row conservation failed')
    _require_zero(c, '''SELECT count(*) FROM relations r
        LEFT JOIN pipes k ON k.dataset='kict' AND k.source_fid=r.kict_fid
        LEFT JOIN pipes s ON s.dataset='seoul' AND s.source_fid=r.seoul_fid
        WHERE k.source_fid IS NULL OR s.source_fid IS NULL''', 'Relation has missing original source')
    for table in ('exact_pairs', 'sustained_pairs', 'id_pairs'):
        _require_zero(c, f'''SELECT count(*) FROM baseline.{table} p
            CROSS JOIN mapping.source_mapping k ON k.dataset='kict' AND k.old_id=p.kid
            CROSS JOIN mapping.source_mapping s ON s.dataset='seoul' AND s.old_id=p.sid
            LEFT JOIN relations r ON r.kict_fid=k.source_fid AND r.seoul_fid=s.source_fid
            WHERE r.kict_fid IS NULL''', f'{table} relation conservation failed')
    _require_zero(c, '''SELECT count(*) FROM strict.exact_pairs e LEFT JOIN relations r
        ON r.kict_fid=e.kict_fid AND r.seoul_fid=e.seoul_fid
        WHERE r.kict_fid IS NULL OR r.whole_geometry_equal<>1 OR r.same_original_id<>e.same_original_id''',
                  'Whole-original exact relation conservation failed')
    _require_zero(c, '''SELECT count(*) FROM pipes WHERE exact_same_id_geometry=1
        AND (exact_geometry_match<>1 OR region<>'11')''', 'Invalid strict display flag')
    _require_zero(c, '''SELECT count(*) FROM pipes WHERE region='11' AND comparison_status='NOT_COMPARED' ''',
                  'Unmatched Seoul pipe mislabeled as reference unavailable')
    allowed = ','.join('?' for _ in STATES)
    if c.execute(f'SELECT count(*) FROM pipes WHERE comparison_status NOT IN ({allowed})', STATES).fetchone()[0]:
        raise ValueError('Unknown business comparison state')
    return dict(source_row_conservation=True, source_mapping_full_wkb_verified=True,
                relation_conservation=True, rejected_relations_preserved=True,
                strict_display_flag_separate=True,
                integrity_check=c.execute('PRAGMA main.integrity_check').fetchone()[0])


def build_package(*, baseline, mapping, strict, keys, kict_source, output, progress=None, resume=False):
    """Build once, refuse existing output, return the saved provenance manifest."""
    started = time.monotonic()
    progress = progress or (lambda *args: None)
    output = Path(output).resolve()
    staging = output.with_suffix('.building.sqlite')
    if output.exists() or (staging.exists() and not resume):
        raise FileExistsError(output if output.exists() else staging)
    if resume and not staging.exists():
        raise ValueError('No verified pipe checkpoint exists to resume')
    paths = {name: Path(path).resolve() for name, path in
             dict(baseline=baseline, mapping=mapping, strict=strict, keys=keys, kict_source=kict_source).items()}
    stats = {name: (p.stat().st_size, p.stat().st_mtime_ns) for name, p in paths.items()}
    hashes = {name: sha256(p) for name, p in paths.items()}
    provenance = verify_provenance(paths, hashes)
    progress('input_hashes', len(hashes))
    output.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(staging.as_uri(), uri=True)
    try:
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('PRAGMA synchronous=NORMAL')
        c.execute('PRAGMA cache_size=-131072')
        c.execute('PRAGMA temp_store=FILE')
        for name, path in paths.items():
            c.execute(f'ATTACH DATABASE ? AS {name}', (path.as_uri() + '?mode=ro',))
            c.execute(f'PRAGMA {name}.cache_size=-131072')
        checkpoint = dict(input_hashes=hashes, rule_version=VERSION,
                          pipes=_count(c, 'SELECT count(*) FROM keys.tile_keys'))
        if resume:
            saved = c.execute("SELECT value FROM metadata WHERE key='build_checkpoint'").fetchone()
            if not saved or json.loads(saved[0]) != checkpoint:
                raise ValueError('Verified pipe checkpoint is missing or inputs/rules changed')
            if _count(c, 'SELECT count(*) FROM pipes') != checkpoint['pipes'] or _count(c, 'SELECT count(*) FROM relations') != 0:
                raise ValueError('Verified pipe checkpoint row counts are inconsistent')
        _validate_inputs(c)
        progress('mapping_verified', _count(c, 'SELECT count(*) FROM mapping.source_mapping'))
        if not resume:
            c.executescript(SCHEMA)
            _build_pipes(c, progress)
            c.execute('INSERT INTO metadata VALUES(?,?)', ('build_checkpoint', json.dumps(checkpoint)))
            c.commit()
        else:
            progress('resumed_verified_pipes', checkpoint['pipes'])
        _build_relations(c)
        progress('relations', _count(c, 'SELECT count(*) FROM relations'))
        c.executescript('''CREATE INDEX pipes_tile_key ON pipes(dataset,tile_key);
            CREATE INDEX pipes_original_id ON pipes(dataset,original_id);
            CREATE INDEX relations_seoul ON relations(seoul_fid,kict_fid);
            INSERT INTO tile_groups SELECT dataset,tile_key,count(*),min(exact_same_id_geometry)
                FROM pipes GROUP BY dataset,tile_key;''')
        validation = _validate_output(c)
        validation['resumed_from_verified_pipe_checkpoint'] = bool(resume)
        if validation['integrity_check'] != 'ok':
            raise ValueError('Output SQLite integrity check failed')
        sources = provenance['sources']
        validation['provenance_verified'] = True
        manifest = dict(status='complete', schema_version=1, rule_version=VERSION,
            generated_at=datetime.now(timezone.utc).isoformat(),
            scope='KICT national water versus supplied Seoul municipal water. Municipal input unavailable outside Seoul source region 11.',
            geometry_policy='No source geometry edited. Historical sustained lengths/ranges are Seoul-boundary-clipped; strict flags use full original equality.',
            length_scope='pipes.length_m and coverage_ratio are historical Seoul-boundary-clipped comparison metrics; NULL outside historical scope.',
            business_states=list(STATES), input_hashes=hashes,
            provenance=provenance,
            inputs={k: str(p) for k, p in paths.items()}, source_manifest=sources,
            database_access='none in builder; baseline was extracted using read-only PostgreSQL transactions',
            counts={name: _count(c, f'SELECT count(*) FROM {name}') for name in ('pipes', 'relations', 'tile_groups')},
            per_dataset=[dict(zip(('dataset', 'count'), r)) for r in c.execute('SELECT dataset,count(*) FROM pipes GROUP BY dataset')],
            state_counts=[dict(zip(('dataset', 'state', 'count'), r)) for r in c.execute('SELECT dataset,comparison_status,count(*) FROM pipes GROUP BY 1,2')],
            reason_counts=[dict(zip(('dataset', 'reason', 'count'), r)) for r in c.execute('SELECT dataset,reason_code,count(*) FROM pipes GROUP BY 1,2')],
            strict_counts=[dict(zip(('dataset', 'strict_pipes', 'exact_pipes'), r)) for r in c.execute('SELECT dataset,sum(exact_same_id_geometry),sum(exact_geometry_match) FROM pipes GROUP BY 1')],
            relation_sources={name: _count(c, f'SELECT count(*) FROM baseline.{name}') for name in ('exact_pairs', 'sustained_pairs', 'id_pairs')},
            validation=validation)
        manifest['relation_sources']['whole_exact_pairs'] = _count(c, 'SELECT count(*) FROM strict.exact_pairs')
        for name, path in paths.items():
            if stats[name] != (path.stat().st_size, path.stat().st_mtime_ns):
                raise ValueError(f'Input changed during build: {name}')
        manifest['validation']['input_file_stats_unchanged'] = True
        c.execute("DELETE FROM metadata WHERE key='build_checkpoint'")
        c.executemany('INSERT INTO metadata VALUES(?,?)', [(k, json.dumps(v, ensure_ascii=False)) for k, v in manifest.items()])
        c.commit()
        c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    except Exception:
        c.close()
        if not resume:
            staging.unlink(missing_ok=True)
        raise
    else:
        c.close()
    staging.replace(output)
    manifest['output'] = dict(path=str(output), bytes=output.stat().st_size, sha256=sha256(output))
    manifest['elapsed_seconds'] = round(time.monotonic() - started, 2)
    output.with_name('manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('baseline', 'mapping', 'strict', 'keys', 'kict-source', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--validation-json', type=Path)
    parser.add_argument('--resume', action='store_true', help='Resume only a matching complete-pipe checkpoint with zero committed relations')
    args = vars(parser.parse_args())
    validation_path = args.pop('validation_json')
    result = build_package(**args, progress=lambda stage, count: print(json.dumps(dict(stage=stage, count=count)), flush=True))
    if validation_path:
        validation_path.parent.mkdir(parents=True, exist_ok=True)
        validation_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(status=result['status'], counts=result['counts'], elapsed_seconds=result['elapsed_seconds'])), flush=True)


if __name__ == '__main__':
    main()
