import json
from pathlib import Path
import sys
from flask import Flask

sys.path[:0] = [str(Path(__file__).parent), str(Path(__file__).parents[1])]
from test_national_backend import make_dataset, TILE_KEYS
from national_distance import create_national_distance_blueprint
from national_tiles import create_national_sewer_tiles_blueprint

POLICY = 'original-outside-seoul-v1'

def test_old_corrected_distance_is_not_served_as_original(tmp_path):
    root = make_dataset(tmp_path, 'sewer', '31')
    app = Flask(__name__)
    app.register_blueprint(create_national_distance_blueprint(tmp_path))
    client = app.test_client()
    assert client.get('/distance/national/sewer/31/meta').status_code == 503
    metadata = json.loads((root/'dataset.json').read_text())
    metadata['data_policy'] = POLICY
    (root/'dataset.json').write_text(json.dumps(metadata))
    assert client.get('/distance/national/sewer/31/meta').status_code == 200

def test_old_corrected_tiles_are_not_counted_as_original(tmp_path):
    from national_tiles import REGIONS
    for key, count in REGIONS:
        root = tmp_path/key
        for z in range(6, 18):
            (root/str(z)).mkdir(parents=True)
        metadata = {'name':key, 'bounds':'126,33,130,39',
                    'json':json.dumps({'vector_layers':[{'id':key, 'fields':{}}]})}
        (root/'metadata.json').write_text(json.dumps(metadata))
    app=Flask(__name__)
    app.register_blueprint(create_national_sewer_tiles_blueprint(tmp_path,tmp_path/'kict_sewer_seoul'))
    client=app.test_client()
    assert client.get('/tiles/kict_sewer_national/meta.json').get_json()['complete'] is False
    for key,count in REGIONS:
        if key=='kict_sewer_seoul': continue
        (tmp_path/key/'conversion_complete.json').write_text(json.dumps({
            'status':'complete','data_policy':POLICY,'source_features':count,
            'raw_originals_verified':True}))
    metadata=client.get('/tiles/kict_sewer_national/meta.json').get_json()
    assert metadata['complete'] is True
    assert metadata['expected_count']==3634657
    assert metadata['data_policy']==POLICY
    marker=tmp_path/TILE_KEYS[0]/'conversion_complete.json'
    value=json.loads(marker.read_text()); value['source_features']+=1
    marker.write_text(json.dumps(value))
    assert client.get('/tiles/kict_sewer_national/meta.json').get_json()['complete'] is False
