"""Persistent display decisions must survive process restarts, not change originals."""
import base64
import gzip
import sqlite3
from test_comparison_tiles import write_tile, attrs, setup
from comparison_tiles import ComparisonTileSidecars
from test_comparison_mvt import field


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
