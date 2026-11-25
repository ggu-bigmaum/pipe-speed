"""National routes use bounded local indexes and preserve original datasets."""
import gzip
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tracemalloc

from flask import Flask
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FILES = ('pipe_case1_m.tif', 'pipe_case2_m.tif', 'pipe_dist_result.csv', 'grid_index.json')


def make_dataset(root, utility='water', code='11', distance=12.5):
    directory = root / ('kict_' + utility + '_national') / code
    directory.mkdir(parents=True)
    for filename in FILES:
        (directory / filename).write_bytes(('original ' + filename).encode())
    metadata = dict(dataset_id='kict_' + utility + '_national_' + code,
                    label=utility + code, crs='EPSG:32652', width=200, height=200,
                    origin=[300000, 4200000], pixel_size=[100, 100], nodata=-9999,
                    result_count=3, result_filename=FILES[2], grid_mode='viewport',
                    grid_cell_limit=12000,
                    cases=[dict(id='case' + str(i + 1), filename=f,
                                bytes=(directory / f).stat().st_size, label='Case')
                           for i, f in enumerate(FILES[:2])],
                    source_files={f: {'bytes': (directory / f).stat().st_size} for f in FILES})
    (directory / 'dataset.json').write_text(json.dumps(metadata), encoding='utf-8')
    connection = sqlite3.connect(str(directory / 'cells.sqlite'))
    connection.executescript('''
        CREATE TABLE cells (col INTEGER, row INTEGER, grid_id TEXT UNIQUE,
            case1_m REAL, case2_m REAL, pass_flag INTEGER, pipe_id TEXT,
            near_x REAL, near_y REAL, near_lon REAL, near_lat REAL,
            PRIMARY KEY(col,row)) WITHOUT ROWID;
        CREATE INDEX cells_row_col ON cells(row,col);
    ''')
    connection.executemany('INSERT INTO cells VALUES (?,?,?,?,?,?,?,?,?,?,?)', [
        (0, 0, '001', distance, 0, 1, utility + '-001', 300005, 4199995, 127, 37),
        (2, 0, '003', distance + 2, distance + 2, 0, utility + '-003', 300205, 4199995, 127, 37),
        (0, 1, '004', distance + 3, distance + 3, 0, utility + '-004', 300005, 4199895, 127, 37),
    ])
    connection.commit()
    connection.close()
    return directory


@pytest.fixture
def distance_client(tmp_path):
    assert importlib.util.find_spec('national_distance'), 'National SQLite distance routes are not implemented'
    from national_distance import create_national_distance_blueprint
    water = make_dataset(tmp_path)
    sewer = make_dataset(tmp_path, 'sewer', distance=91.25)
    make_dataset(tmp_path, 'water', '21', distance=33)
    app = Flask(__name__)
    app.register_blueprint(create_national_distance_blueprint(tmp_path))
    return app.test_client(), water, sewer


def test_catalog_checks_actual_files_and_lists_missing_jeonnam(distance_client):
    client, water, _ = distance_client
    response = client.get('/distance/national/catalog')
    assert response.status_code == 200
    groups = response.get_json()['groups']
    assert [group['id'] for group in groups] == ['water', 'sewer']
    assert [r['code'] for r in groups[0]['regions']] == ['11', '21', '22', '23', '24', '25', '26', '29', '31', '32', '33', '34', '35', '36', '37', '38', '39']
    seoul = groups[0]['regions'][0]
    assert seoul['available'] and seoul['route'] == 'national/water/11/'
    assert seoul['dataset_id'] == 'kict_water_national_11'
    jeonnam = next(r for r in groups[1]['regions'] if r['code'] == '36')
    assert not jeonnam['available'] and jeonnam['reason']
    (water / FILES[0]).unlink()
    assert client.get('/distance/national/catalog').get_json()['groups'][0]['regions'][0]['available'] is False


def test_metadata_and_cells_never_read_huge_originals(distance_client, monkeypatch):
    client, water, _ = distance_client
    real_open = Path.open

    def guarded_open(path, *args, **kwargs):
        assert path.name not in FILES, 'The endpoint read an original raster, grid or CSV'
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', guarded_open)
    assert client.get('/distance/national/catalog').status_code == 200
    response = client.get('/distance/national/water/11/meta')
    assert response.status_code == 200
    metadata = response.get_json()
    assert metadata['grid_mode'] == 'viewport' and metadata['grid_cell_limit'] == 12000
    assert 'cells' not in metadata and 'columns' not in metadata
    assert metadata['result_count'] == 3
    assert client.get('/distance/national/water/11/cell?col=0&row=0').status_code == 200


def test_cell_isolation_valid_zero_and_nodata(distance_client):
    client, _, _ = distance_client
    for utility, code, distance in [('water', '11', 12.5), ('sewer', '11', 91.25), ('water', '21', 33)]:
        response = client.get('/distance/national/{}/{}/cell?col=0&row=0'.format(utility, code))
        assert response.status_code == 200
        cell = response.get_json()
        assert cell['dataset_id'] == 'kict_{}_national_{}'.format(utility, code)
        assert cell['grid_id'] == '001' and cell['pipe_id'] == utility + '-001'
        assert cell['case1_m'] == distance and cell['case2_m'] == 0 and cell['pass_flag'] == 1
        assert cell['center'] == [300050, 4199950]
    assert client.get('/distance/national/water/11/cell?col=1&row=0').get_json() == {'matched': False, 'reason': 'nodata'}


@pytest.mark.parametrize('query', ['col=-1&row=0', 'col=200&row=0', 'col=0&row=200', 'col=NaN&row=0', 'col=1.5&row=0', 'col=0'])
def test_cell_rejects_bad_coordinates(distance_client, query):
    assert distance_client[0].get('/distance/national/water/11/cell?' + query).status_code == 400


def test_grid_edges_only_cover_valid_cells_in_requested_viewport(distance_client):
    response = distance_client[0].get('/distance/national/water/11/gridlines?min_col=0&max_col=2&min_row=0&max_row=0')
    assert response.status_code == 200
    body = response.get_json()
    assert body['geometry']['type'] == 'MultiLineString'
    actual = set()
    for start, end in body['geometry']['coordinates']:
        c0, r0 = int((start[0] - 300000) / 100), int((4200000 - start[1]) / 100)
        c1, r1 = int((end[0] - 300000) / 100), int((4200000 - end[1]) / 100)
        edges = {(c, r0, 'h') for c in range(c0, c1)} if r0 == r1 else {(c0, r, 'v') for r in range(r0, r1)}
        assert not actual.intersection(edges)
        actual.update(edges)
    expected = set()
    for c, r in [(0, 0), (2, 0)]:
        expected.update(((c, r, 'h'), (c, r + 1, 'h'), (c, r, 'v'), (c + 1, r, 'v')))
    assert actual == expected


def test_grid_oversized_window_returns_omitted_without_database_query(distance_client, monkeypatch):
    client = distance_client[0]
    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **k: pytest.fail('oversized viewport queried SQLite'))
    response = client.get('/distance/national/water/11/gridlines?min_col=0&max_col=199&min_row=0&max_row=199')
    assert response.status_code == 200
    assert response.get_json()['omitted'] is True
    assert response.get_json()['geometry'] == {'type': 'MultiLineString', 'coordinates': []}


@pytest.mark.parametrize('query', ['', 'min_col=0&max_col=2&min_row=0', 'min_col=-1&max_col=2&min_row=0&max_row=0', 'min_col=2&max_col=1&min_row=0&max_row=0', 'min_col=0&max_col=200&min_row=0&max_row=0'])
def test_grid_rejects_invalid_windows(distance_client, query):
    assert distance_client[0].get('/distance/national/water/11/gridlines?' + query).status_code == 400


@pytest.mark.parametrize('filename', FILES[:3])
def test_downloads_original_bytes_and_disable_cache(distance_client, filename):
    client, water, _ = distance_client
    response = client.get('/distance/national/water/11/files/' + filename)
    assert response.status_code == 200
    assert response.data == (water / filename).read_bytes()
    assert response.headers['Cache-Control'] == 'no-store'


@pytest.mark.parametrize('suffix', ['files/grid_index.json', 'files/dataset.json', 'files/cells.sqlite', 'files/../dataset.json', 'files/..%5Cdataset.json', 'files/pipe_dist_result.xlsx'])
def test_internal_files_and_traversal_are_not_downloadable(distance_client, suffix):
    assert distance_client[0].get('/distance/national/water/11/' + suffix).status_code == 404


@pytest.mark.parametrize('route', ['gas/11', 'water/../../', 'water/99', 'water/011'])
def test_unknown_datasets_are_404(distance_client, route):
    assert distance_client[0].get('/distance/national/' + route + '/meta').status_code == 404


def test_missing_corrupt_or_replaced_datasets_fail_safely(distance_client):
    client, water, _ = distance_client
    assert client.get('/distance/national/sewer/36/meta').status_code == 503
    (water / FILES[0]).write_bytes(b'changed file')
    assert client.get('/distance/national/water/11/meta').status_code == 503
    metadata = json.loads((water / 'dataset.json').read_text())
    metadata['dataset_id'] = 'kict_sewer_national_11'
    (water / 'dataset.json').write_text(json.dumps(metadata))
    response = client.get('/distance/national/water/11/meta')
    assert response.status_code == 503 and response.headers['Cache-Control'] == 'no-store'
    assert str(water) not in response.get_json()['error']


def test_directory_cannot_substitute_for_the_sqlite_file(distance_client):
    client, water, _ = distance_client
    (water / 'cells.sqlite').unlink()
    (water / 'cells.sqlite').mkdir()
    assert client.get('/distance/national/water/11/meta').status_code == 503
    assert client.get('/distance/national/catalog').get_json()['groups'][0]['regions'][0]['available'] is False


def test_metadata_does_not_expose_build_paths_or_samples(distance_client):
    client, water, _ = distance_client
    metadata = json.loads((water / 'dataset.json').read_text())
    metadata['provenance'] = {'source_directory': 'PRIVATE_LOCAL_PATH'}
    metadata['verification_samples'] = [{'pipe_id': 'PRIVATE_SAMPLE'}]
    metadata['cases'][0]['source_path'] = 'PRIVATE_CASE_PATH'
    (water / 'dataset.json').write_text(json.dumps(metadata))
    response = client.get('/distance/national/water/11/meta')
    assert response.status_code == 200
    assert b'PRIVATE' not in response.data
    assert 'source_files' not in response.get_json()


def test_sqlite_is_readonly_closed_and_corruption_returns_503(distance_client):
    from national_distance import NationalDistanceData
    client, water, _ = distance_client
    store = NationalDistanceData(water.parents[1], 'water', '11')
    with store.connection() as connection:
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            connection.execute('DELETE FROM cells')
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        connection.execute('SELECT 1')
    (water / 'cells.sqlite').write_bytes(b'not SQLite')
    assert client.get('/distance/national/water/11/cell?col=0&row=0').status_code == 503


TILE_KEYS = ['sewer_gangwon', 'sewer_gyeonggi_south', 'sewer_gyeonggi_north', 'sewer_gyeongnam', 'sewer_gyeongbuk', 'sewer_gwangju', 'sewer_daegu', 'sewer_daejeon', 'sewer_busan', 'sewer_sejong', 'sewer_ulsan', 'sewer_incheon', 'sewer_jeonbuk', 'sewer_jeju', 'sewer_chungnam', 'sewer_chungbuk', 'kict_sewer_seoul']


def mvt_layer(name):
    # Tile.layers (field 3) containing Layer.name, extent and version.
    layer = b'\x0a' + bytes([len(name)]) + name.encode() + b'\x28\x80\x20\x78\x02'
    return b'\x1a' + bytes([len(layer)]) + layer


@pytest.fixture
def tile_client(tmp_path):
    assert importlib.util.find_spec('national_tiles'), 'National merged tile routes are not implemented'
    from national_tiles import create_national_sewer_tiles_blueprint, REGIONS, DATA_POLICY
    roots = {}
    for key in TILE_KEYS:
        directory = tmp_path / ('full' if key == 'kict_sewer_seoul' else 'national') / key
        roots[key] = directory
        for z in range(6, 18):
            (directory / str(z)).mkdir(parents=True)
        metadata = dict(name=key, bounds='126,33,130,39', minzoom=6, maxzoom=17,
                        json=json.dumps({'vector_layers': [{'id': key, 'fields': {'id': 'Number'}}]}))
        (directory / 'metadata.json').write_text(json.dumps(metadata))
        if key != 'kict_sewer_seoul':
            (directory / 'conversion_complete.json').write_text(json.dumps({
                'status':'complete', 'data_policy':DATA_POLICY,
                'source_features':dict(REGIONS)[key], 'raw_originals_verified':True}))
    for key in [TILE_KEYS[0], TILE_KEYS[1], TILE_KEYS[-1]]:
        path = roots[key] / '6' / '55' / '24.pbf'
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(gzip.compress(mvt_layer(key)))
    app = Flask(__name__)
    app.register_blueprint(create_national_sewer_tiles_blueprint(tmp_path / 'national', tmp_path / 'full' / 'kict_sewer_seoul'))
    return app.test_client(), roots


def test_national_tiles_metadata_includes_all_internal_layer_names(tile_client):
    response = tile_client[0].get('/tiles/kict_sewer_national/meta.json')
    assert response.status_code == 200
    metadata = response.get_json()
    assert metadata['vector_layer_ids'] == TILE_KEYS[:-1]
    assert metadata['count'] == 3634657 and metadata['complete'] is True
    assert metadata['scope'] == 'outside-seoul'
    assert metadata['bounds'] == [126, 33, 130, 39]
    assert (metadata['minzoom'], metadata['maxzoom']) == (6, 17)
    assert metadata['excluded_regions'] == ['전라남도']


def test_tiles_concatenate_layers_and_negotiate_gzip(tile_client):
    client, _ = tile_client
    expected = b''.join(mvt_layer(k) for k in [TILE_KEYS[0], TILE_KEYS[1]])
    for encoding, compressed in [('gzip', True), ('identity', False), ('gzip;q=0', False)]:
        response = client.get('/tiles/kict_sewer_national/6/55/24.pbf', headers={'Accept-Encoding': encoding})
        assert response.status_code == 200
        assert (gzip.decompress(response.data) if compressed else response.data) == expected
        assert ('Content-Encoding' in response.headers) is compressed
        assert response.headers['Cache-Control'] == 'no-store'
        assert response.headers['X-Tile-Set'] == 'full'
        assert response.headers['X-Tile-Source'] == 'file'
        assert int(response.headers['X-Tile-Bytes']) == len(response.data)
        assert 'Server-Timing' in response.headers and 'X-Server-Ms' in response.headers
    response = client.get('/tiles/kict_sewer_national/6/55/24.pbf?identity=1', headers={'Accept-Encoding': 'gzip'})
    assert response.data == expected and 'Content-Encoding' not in response.headers


def test_missing_tiles_are_empty_but_invalid_coordinates_are_rejected(tile_client):
    client = tile_client[0]
    assert client.get('/tiles/kict_sewer_national/6/1/1.pbf').status_code == 204
    for route in ['5/1/1', '18/1/1', '6/64/1', '6/1/64', '6/-1/1', '../1/1']:
        assert client.get('/tiles/kict_sewer_national/' + route + '.pbf').status_code == 404


def test_incomplete_tile_installation_reports_available_count_and_refuses_partial_tiles(tile_client):
    client, roots = tile_client
    (roots[TILE_KEYS[0]] / 'metadata.json').unlink()
    metadata = client.get('/tiles/kict_sewer_national/meta.json').get_json()
    assert metadata['count'] == 3634657 - 243135
    assert metadata['expected_count'] == 3634657 and metadata['complete'] is False
    assert TILE_KEYS[0] in metadata['missing_regions']
    assert TILE_KEYS[0] not in metadata['vector_layer_ids']
    assert client.get('/tiles/kict_sewer_national/6/55/24.pbf').status_code == 503


def test_corrupt_deflate_returns_safe_503(tile_client):
    client, roots = tile_client
    (roots[TILE_KEYS[0]] / '6/55/24.pbf').write_bytes(b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff\x07invalid')
    response = client.get('/tiles/kict_sewer_national/6/55/24.pbf')
    assert response.status_code == 503
    assert response.headers['Cache-Control'] == 'no-store'


def test_outside_seoul_does_not_depend_on_or_serve_the_seoul_layer(tile_client):
    client, roots = tile_client
    # Seoul is installed but belongs only to its separate selectable layer.
    (roots['kict_sewer_seoul'] / 'metadata.json').unlink()
    (roots['kict_sewer_seoul'] / '6/55/24.pbf').write_bytes(b'corrupt Seoul tile')
    metadata = client.get('/tiles/kict_sewer_national/meta.json').get_json()
    assert metadata['complete'] is True
    assert 'kict_sewer_seoul' not in metadata['vector_layer_ids']
    response = client.get('/tiles/kict_sewer_national/6/55/24.pbf')
    assert response.status_code == 200
    assert response.data == mvt_layer(TILE_KEYS[0]) + mvt_layer(TILE_KEYS[1])


def test_tile_decompression_is_bounded(tile_client, monkeypatch):
    import national_tiles
    monkeypatch.setattr(national_tiles, 'MAX_TILE_BYTES', 10)
    assert tile_client[0].get('/tiles/kict_sewer_national/6/55/24.pbf').status_code == 503


def test_gzip_response_does_not_hold_the_entire_raw_tile_in_memory(tile_client):
    client, roots = tile_client
    payload = mvt_layer(TILE_KEYS[0]) * 400000
    (roots[TILE_KEYS[0]] / '6/55/24.pbf').write_bytes(gzip.compress(payload))
    tracemalloc.start()
    try:
        response = client.get('/tiles/kict_sewer_national/6/55/24.pbf', headers={'Accept-Encoding': 'gzip'})
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert response.status_code == 200
    assert gzip.decompress(response.data).startswith(payload)
    assert peak < 8 * 1024 * 1024, 'gzip composition retained the full decompressed tile'


def test_routes_are_registered_without_replacing_existing_seoul_or_water():
    from app import app
    client = app.test_client()
    assert client.get('/distance/national/catalog').status_code == 200
    assert client.get('/tiles/kict_sewer_national/meta.json').status_code == 200
    assert client.get('/health').status_code == 200
    assert client.get('/tiles/unknown/6/1/1.pbf').status_code == 404
