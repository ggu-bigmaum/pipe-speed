import base64
from contextlib import closing
import gzip
import sqlite3
from pathlib import Path
import sys

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_comparison_mvt import field


class StrictPackage:
    def __init__(self, path):
        self.path = path
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE tile_groups(dataset TEXT,tile_key TEXT,all_strict INTEGER,PRIMARY KEY(dataset,tile_key))')
            db.executemany('INSERT INTO tile_groups VALUES(?,?,?)', [('seoul','["1"]',1),('seoul','["2"]',0)])

    def verified(self):
        return {'available':True, 'version':'sewer-test-v1'}

    def connect(self):
        db = sqlite3.connect(self.path.as_uri()+'?mode=ro&immutable=1', uri=True)
        db.row_factory = sqlite3.Row
        return closing(db)


def sewer_tile(ids):
    features = b''.join(field(2, field(2, bytes([0,i])) + field(3,2) + field(4,b'\x09\x02\x04\x0a\x02\x04')) for i in range(len(ids)))
    layer = field(1,b'sb001') + features + field(3,b'id')
    layer += b''.join(field(4,field(5,i)) for i in ids)
    return field(3, layer + field(5,4096) + field(15,2))


def test_sewer_route_uses_only_sb001_and_retained_original_id(tmp_path):
    from comparison_tiles import create_comparison_tiles_blueprint
    package = StrictPackage(tmp_path/'strict.sqlite')
    tilepath=tmp_path/'tiles_full/sb001/10/873/396.pbf'
    tilepath.parent.mkdir(parents=True)
    content=gzip.compress(sewer_tile([2,1,1]))
    tilepath.write_bytes(content)
    app=Flask(__name__)
    app.register_blueprint(create_comparison_tiles_blueprint(tmp_path, package=package, comparison='sewer'))
    client=app.test_client()
    response=client.get('/comparison/sewer/tiles/sb001/10/873/396')
    assert response.status_code==200
    body=response.get_json()
    assert body['layer']=='sb001' and body['feature_count']==3 and body['matched_count']==2
    assert base64.b64decode(body['bits_base64'])==b'\x06'
    assert client.get('/comparison/sewer/tiles/sa001/10/873/396').status_code==404
    assert client.get('/comparison/water/tiles/sb001/10/873/396').status_code==404
    assert tilepath.read_bytes()==content


@pytest.mark.parametrize('value',[None,True,-1,1.1,'001','1.0',2**53])
def test_invalid_retained_id_fails_closed(value):
    from comparison_keys import sewer_tile_key
    with pytest.raises(ValueError): sewer_tile_key('seoul',{'id':value})


def test_same_id_scalar_encoding_and_unknown_pair(tmp_path):
    from comparison_keys import sewer_tile_key
    from comparison_tiles import create_comparison_tiles_blueprint
    assert sewer_tile_key('kict',{'id':12}) == sewer_tile_key('seoul',{'id':12.0}) == '["12"]'
    with pytest.raises(ValueError): create_comparison_tiles_blueprint(tmp_path, comparison='mixed')
