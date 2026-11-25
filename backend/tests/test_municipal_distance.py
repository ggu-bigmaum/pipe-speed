"""Municipal results retain their own source, route and validation state."""
import csv
from html.parser import HTMLParser
import json
from pathlib import Path
import sys

from flask import Flask
from PIL import Image, TiffImagePlugin
import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from distance import HEADERS, create_distance_blueprint


def write_dataset(directory, dataset_id, distance=12.5, result_filename='pipe_dist_result.csv'):
    directory.mkdir(parents=True, exist_ok=True)
    index = dict(dataset_id=dataset_id, crs='EPSG:32652', width=2, height=1,
                 origin=[300000, 4200000], pixel_size=[100, 100], nodata=-9999,
                 cells=[['001', 0, 0]])
    (directory / 'grid_index.json').write_text(json.dumps(index), encoding='utf-8')
    for case, value in [('case1', distance), ('case2', 0)]:
        image = Image.new('F', (2, 1))
        image.putdata([value, -9999])
        tags = TiffImagePlugin.ImageFileDirectory_v2()
        tags[33550] = (100.0, 100.0, 0.0)
        tags[33922] = (0.0, 0.0, 0.0, 300000.0, 4200000.0, 0.0)
        tags[34735] = (1, 1, 0, 1, 3072, 0, 1, 32652)
        tags[42113] = '-9999'
        image.save(directory / f'pipe_{case}_m.tif', tiffinfo=tags)
    row = ['001', distance, 0, 1, str(dataset_id) + '-pipe', 300010, 4199990, 127, 37]
    if result_filename.endswith('.csv'):
        with (directory / result_filename).open('w', encoding='utf-8', newline='') as handle:
            csv.writer(handle).writerows([HEADERS, row])
    else:
        from openpyxl import Workbook
        workbook = Workbook()
        workbook.active.title = '격자별_거리'
        workbook.active.append(HEADERS)
        workbook.active.append(row)
        workbook.save(directory / result_filename)
        workbook.close()
    return directory


def municipal_app(tmp_path):
    app = Flask(__name__)
    for dataset_id, label in [('seoul_water', '서울시 상수'), ('seoul_sewer', '서울시 하수')]:
        app.register_blueprint(create_distance_blueprint(
            tmp_path / dataset_id, name=dataset_id, url_prefix='/distance/' + dataset_id,
            dataset_id=dataset_id, label=label, result_filename='pipe_dist_result.csv',
            require_dataset_id=True))
    app.register_blueprint(create_distance_blueprint(tmp_path / 'kict_water_seoul'))
    return app


def test_app_registers_two_dedicated_city_routes():
    from app import app
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    for dataset_id in ['seoul_water', 'seoul_sewer']:
        for endpoint in ['availability', 'meta', 'cell', 'gridlines', 'files/<path:filename>']:
            assert f'/distance/{dataset_id}/{endpoint}' in rules


def test_missing_municipal_files_leave_existing_xlsx_route_available(tmp_path):
    write_dataset(tmp_path / 'kict_water_seoul', 'kict_water_seoul', 5,
                  result_filename='pipe_dist_result.xlsx')
    client = municipal_app(tmp_path).test_client()
    for dataset_id, label in [('seoul_water', '서울시 상수'), ('seoul_sewer', '서울시 하수')]:
        response = client.get(f'/distance/{dataset_id}/availability')
        assert response.status_code == 200
        state = response.get_json()
        assert state['dataset_id'] == dataset_id and state['label'] == label
        assert state['available'] is False and state['status'] == 'waiting'
        assert '자료 대기' in state['reason']
        assert set(state['missing_files']) == {
            'pipe_case1_m.tif', 'pipe_case2_m.tif', 'pipe_dist_result.csv', 'grid_index.json'}
        assert client.get(f'/distance/{dataset_id}/meta').status_code == 503
    legacy = client.get('/distance/meta')
    assert legacy.status_code == 200
    assert legacy.get_json()['result_filename'] == 'pipe_dist_result.xlsx'


def test_city_cell_and_downloads_never_fall_back_to_kict_or_other_city(tmp_path):
    write_dataset(tmp_path / 'seoul_water', 'seoul_water', 12.5)
    write_dataset(tmp_path / 'seoul_sewer', 'seoul_sewer', 91.25)
    write_dataset(tmp_path / 'kict_water_seoul', 'kict_water_seoul', 5,
                  result_filename='pipe_dist_result.xlsx')
    client = municipal_app(tmp_path).test_client()
    for dataset_id, distance, label in [('seoul_water', 12.5, '서울시 상수'), ('seoul_sewer', 91.25, '서울시 하수')]:
        base = '/distance/' + dataset_id
        assert client.get(base + '/availability').get_json()['available'] is True
        meta = client.get(base + '/meta').get_json()
        assert (meta['dataset_id'], meta['label'], meta['result_filename']) == (dataset_id, label, 'pipe_dist_result.csv')
        cell = client.get(base + '/cell?col=0&row=0').get_json()
        assert cell['dataset_id'] == dataset_id
        assert cell['pipe_id'] == dataset_id + '-pipe'
        assert cell['case1_m'] == distance and cell['case2_m'] == 0
        for filename in ['pipe_case1_m.tif', 'pipe_case2_m.tif', 'pipe_dist_result.csv']:
            assert client.get(base + '/files/' + filename).data == (tmp_path / dataset_id / filename).read_bytes()
        assert client.get(base + '/files/pipe_dist_result.xlsx').status_code == 404
    assert client.get('/distance/cell?col=0&row=0').get_json()['case1_m'] == 5
    assert client.get('/distance/files/pipe_dist_result.csv').status_code == 404


def test_invalid_city_files_disable_only_that_dataset_and_reload_after_replacement(tmp_path):
    water = write_dataset(tmp_path / 'seoul_water', 'seoul_water')
    write_dataset(tmp_path / 'seoul_sewer', 'seoul_sewer')
    (water / 'pipe_dist_result.csv').write_text('wrong,columns\n1,2\n', encoding='utf-8')
    client = municipal_app(tmp_path).test_client()
    state = client.get('/distance/seoul_water/availability').get_json()
    assert state['available'] is False and state['status'] == 'invalid'
    assert '열' in state['reason']
    assert client.get('/distance/seoul_sewer/availability').get_json()['available'] is True
    write_dataset(water, 'seoul_water', 24)
    assert client.get('/distance/seoul_water/availability').get_json()['available'] is True
    assert client.get('/distance/seoul_water/cell?col=0&row=0').get_json()['case1_m'] == 24


@pytest.mark.parametrize('source_id', ['kict_water_seoul', 'seoul_sewer', None])
def test_city_rejects_missing_or_wrong_source_identity(tmp_path, source_id):
    directory = write_dataset(tmp_path / 'seoul_water', source_id)
    client = municipal_app(tmp_path).test_client()
    state = client.get('/distance/seoul_water/availability').get_json()
    assert state['available'] is False and state['status'] == 'invalid'
    assert 'dataset_id' in state['reason']
    assert client.get('/distance/seoul_water/meta').status_code == 503
    assert client.get('/distance/seoul_water/files/pipe_case1_m.tif').status_code == 503


@pytest.mark.parametrize('malformation', ['non_object_index', 'non_tiff_image', 'wrong_result_values'])
def test_malformed_city_data_returns_validation_status_instead_of_server_error(tmp_path, malformation):
    directory = write_dataset(tmp_path / 'seoul_water', 'seoul_water')
    if malformation == 'non_object_index':
        (directory / 'grid_index.json').write_text('[]')
    elif malformation == 'non_tiff_image':
        Image.new('RGB', (2, 1)).save(directory / 'pipe_case1_m.tif', format='PNG')
    else:
        result = directory / 'pipe_dist_result.csv'
        result.write_text(result.read_text().replace('12.5', '999'), encoding='utf-8')
    client = municipal_app(tmp_path).test_client()
    response = client.get('/distance/seoul_water/availability')
    assert response.status_code == 200
    state = response.get_json()
    assert state['available'] is False and state['status'] == 'invalid'
    assert state['reason']


def test_availability_reuses_validation_until_file_changes(tmp_path, monkeypatch):
    directory = write_dataset(tmp_path / 'seoul_water', 'seoul_water')
    client = municipal_app(tmp_path).test_client()
    assert client.get('/distance/seoul_water/availability').get_json()['available'] is True
    def unexpected_parse(*args, **kwargs):
        pytest.fail('Unchanged availability must not decode TIF pixels or parse result rows again')
    monkeypatch.setattr(Image, 'open', unexpected_parse)
    assert client.get('/distance/seoul_water/availability').get_json()['available'] is True
    (directory / 'pipe_case2_m.tif').unlink()
    state = client.get('/distance/seoul_water/availability').get_json()
    assert state['available'] is False and state['missing_files'] == ['pipe_case2_m.tif']


class GroupParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.details = []
        self.parents = {}
        self.attributes = {}
        self.raster_parents = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'details':
            group = attrs.get('id')
            self.parents[group] = self.details[-1] if self.details else None
            self.attributes[group] = attrs
            self.details.append(group)
        if tag == 'input' and 'data-raster' in attrs:
            self.raster_parents[attrs['id']] = self.details[-1]

    def handle_endtag(self, tag):
        if tag == 'details':
            self.details.pop()


def test_five_raster_groups_have_the_vector_source_order_and_old_seoul_is_nested():
    parser = GroupParser()
    parser.feed((BACKEND.parent / 'frontend/index.html').read_text(encoding='utf-8'))
    raster_groups = set(parser.raster_parents.values())
    top_level = [group for group, parent in parser.parents.items() if group in raster_groups and parent is None]
    assert top_level == ['nationalWaterGroup', 'legacySewerGroup', 'nationalSewerGroup', 'seoulWaterGroup', 'seoulSewerGroup']
    assert parser.parents['legacyWaterGroup'] == 'nationalWaterGroup'
    assert 'open' not in parser.attributes['legacyWaterGroup']
    assert parser.raster_parents['case1'] == parser.raster_parents['case2'] == 'legacyWaterGroup'
    for stem in ['seoulWater', 'seoulSewer']:
        assert parser.raster_parents[stem + 'Case1'] == parser.raster_parents[stem + 'Case2'] == stem + 'Group'
