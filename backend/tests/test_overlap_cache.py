"""Persistent display decisions must survive process restarts, not change originals."""
import base64
import gzip
import sqlite3
from test_comparison_tiles import write_tile, attrs, setup
from comparison_tiles import ComparisonTileSidecars
from test_comparison_mvt import field


def kict_water_tile(ids):
    from test_comparison_tiles import tile
    from comparison_mvt import _fields
    layer = bytes(next(_fields(memoryview(tile([attrs(i) for i in ids]))))[2])
    for before, after in [(b'sa001',b'kict_national'), (b'PLINE_ID',b'FTR_IDN'),
                          (b'PLINE_CALB',b'STD_DIP'), (b'PLINE_MTRQ',b'MOP_CDE')]:
        number = 1 if before == b'sa001' else 3
        layer = layer.replace(field(number,before),field(number,after))
    return field(3, layer)


def test_kict_water_requires_a_drawable_exact_counterpart(setup):
    data, fixture = setup
    from comparison import ComparisonPackage
    path = data/'tiles_national/kict_national/11/1746/793.pbf'
    path.parent.mkdir(parents=True); path.write_bytes(kict_water_tile([100,7,9]))
    service = ComparisonTileSidecars(data,ComparisonPackage(fixture.directory),layer='kict_national')
    # The S1 group includes a non-strict duplicate, so do not hide KICT 100.
    assert service.get(11,1746,793)['matched_count'] == 0
    with sqlite3.connect(fixture.path) as db:
        db.execute('UPDATE relations SET whole_geometry_equal=1,same_original_id=1 WHERE kict_fid=101 AND seoul_fid=3')
    fixture.finish()
    assert base64.b64decode(service.get(11,1746,793)['bits_base64']) == b'\x01'


def test_national_mbtiles_uses_same_xyz_rows_as_tile_server(setup):
    data, fixture = setup
    from comparison import ComparisonPackage
    with sqlite3.connect(data/'kict_national.mbtiles') as db:
        db.execute('CREATE TABLE tiles(zoom_level INTEGER,tile_column INTEGER,tile_row INTEGER,tile_data BLOB)')
        db.execute('INSERT INTO tiles VALUES(11,1746,793,?)',(gzip.compress(kict_water_tile([9])),))
    result = ComparisonTileSidecars(data,ComparisonPackage(fixture.directory),layer='kict_national').get(11,1746,793)
    assert result['feature_count'] == 1 and result['matched_count'] == 0


def test_cache_survives_new_service_without_decoding(setup, monkeypatch):
    data, fixture = setup
    from comparison import ComparisonPackage
    package = ComparisonPackage(fixture.directory)
    path, _ = write_tile(data, [attrs('S3')])
    original = path.read_bytes()
    first = ComparisonTileSidecars(data, package).get(10, 873, 396)
    restarted = ComparisonTileSidecars(data, package)
    def forbidden(*args):
        raise AssertionError('Persisted tile was decoded again')
    monkeypatch.setattr(restarted, '_build', forbidden)
    assert restarted.get(10, 873, 396) == first
    cache = data / 'comparison' / 'water' / 'tile_cache.sqlite'
    with sqlite3.connect(cache) as db:
        assert db.execute('SELECT COUNT(*) FROM tile_flags').fetchone()[0] == 1
    assert path.read_bytes() == original


def test_kict_sewer_flags_use_the_kict_keys(tmp_path):
    from test_sewer_sidecar import StrictPackage, sewer_tile
    package = StrictPackage(tmp_path / 'rules.sqlite')
    with sqlite3.connect(package.path) as db:
        db.executemany('INSERT INTO tile_groups VALUES(?,?,?)', [('kict','["1"]',0),('kict','["2"]',1)])
    from comparison_mvt import _fields
    layer = bytes(next(_fields(memoryview(sewer_tile([1,2]))))[2])
    raw = field(3, layer.replace(field(1,b'sb001'), field(1,b'kict_sewer_seoul')))
    path = tmp_path/'tiles_full/kict_sewer_seoul/12/3492/1586.pbf'
    path.parent.mkdir(parents=True); path.write_bytes(gzip.compress(raw))
    service = ComparisonTileSidecars(tmp_path, package, comparison='sewer', layer='kict_sewer_seoul')
    result = service.get(12,3492,1586)
    assert result['layer'] == 'kict_sewer_seoul'
    assert base64.b64decode(result['bits_base64']) == b'\x02'
