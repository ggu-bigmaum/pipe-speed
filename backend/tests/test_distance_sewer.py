"""The sewer files must never be presented or queried as the water dataset."""
import csv
import hashlib
import json
from pathlib import Path
import sys
import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from app import app

DATA = BACKEND / 'data/distance/kict_sewer_seoul'
PREFIX = '/distance/kict_sewer_seoul'
pytestmark = pytest.mark.skipif(
    not DATA.exists(), reason='Optional sewer integration data must be uploaded separately')

@pytest.fixture(scope='module')
def client():
    return app.test_client()

def test_sewer_metadata_identifies_the_dataset_and_csv(client):
    response = client.get(PREFIX + '/meta')
    assert response.status_code == 200
    meta = response.get_json()
    assert meta['dataset_id'] == 'kict_sewer_seoul'
    assert meta['label'] == '건기연 하수(서울·보정본)'
    assert meta['result_filename'] == 'pipe_dist_result.csv'
    assert (meta['width'], meta['height'], meta['result_count']) == (370, 303, 60579)

@pytest.mark.parametrize('filename', ['pipe_case1_m.tif', 'pipe_case2_m.tif', 'pipe_dist_result.csv'])
def test_sewer_download_preserves_the_original_file(client, filename):
    response = client.get(PREFIX + '/files/' + filename)
    assert response.status_code == 200
    assert hashlib.sha256(response.data).digest() == hashlib.sha256((DATA / filename).read_bytes()).digest()
    assert 'no-store' in response.headers['Cache-Control']

def test_identical_grid_location_queries_the_selected_network(client):
    with (DATA / 'pipe_dist_result.csv').open(encoding='utf-8-sig', newline='') as handle:
        expected = next(row for row in csv.DictReader(handle) if row['grid_id'] == '14914652')
    sewer = client.get(PREFIX + '/cell?col=0&row=156')
    assert sewer.status_code == 200
    sewer = sewer.get_json()
    water = client.get('/distance/cell?col=0&row=156').get_json()
    assert sewer['dataset_id'] == 'kict_sewer_seoul'
    assert sewer['grid_id'] == water['grid_id'] == '14914652'
    assert sewer['pipe_id'] == expected['pipe_id']
    for key in ('case1_m', 'case2_m', 'near_x', 'near_y', 'near_lon', 'near_lat'):
        assert sewer[key] == pytest.approx(float(expected[key]), abs=1e-8)
    assert sewer['case1_m'] != water['case1_m']
    assert sewer['pipe_id'] != water['pipe_id']

def test_sewer_gridlines_and_water_remain_separate(client):
    sewer = client.get(PREFIX + '/gridlines')
    assert sewer.status_code == 200
    assert sewer.get_json()['crs'] == 'EPSG:32652'
    assert client.get('/distance/meta').get_json()['dataset_id'] == 'kict_water_seoul'
    assert client.get(PREFIX + '/files/pipe_dist_result.xlsx').status_code == 404
    assert client.get('/distance/files/pipe_dist_result.csv').status_code == 404

def test_sewer_nodata_does_not_become_a_valid_zero(client):
    index = json.loads((DATA / 'grid_index.json').read_text(encoding='utf-8'))
    occupied = {(c, r) for _, c, r in index['cells']}
    col, row = next((c, r) for r in range(index['height']) for c in range(index['width']) if (c, r) not in occupied)
    response = client.get(f'{PREFIX}/cell?col={col}&row={row}')
    assert response.status_code == 200
    assert response.get_json() == {'matched': False, 'reason': 'nodata'}
