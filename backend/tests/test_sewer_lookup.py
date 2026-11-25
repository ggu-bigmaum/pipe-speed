from contextlib import closing
import sqlite3
from pathlib import Path
import sys
from flask import Flask
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sewer_comparison import create_sewer_comparison_blueprint

@pytest.fixture
def client(tmp_path):
    path=tmp_path/'test.sqlite'
    with sqlite3.connect(path) as c:
        c.executescript('CREATE TABLE pipes(dataset,id,original_id,role,rounded_geometry_match,precision_review); CREATE TABLE pairs(kict_id,seoul_id);')
        c.executemany('INSERT INTO pipes VALUES(?,?,?,?,?,?)',[('kict',1,'K1','original',1,0),('kict',2,None,'inferred',0,0),('kict',3,None,'inferred',None,1)]+[('seoul',n,'S'+str(n),'original',1,0)for n in range(1,26)])
        c.executemany('INSERT INTO pairs VALUES(?,?)',[(1,n)for n in range(1,26)])
    class Package:
        def verified(self):return dict(version='test')
        def connect(self):
            c=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row;return closing(c)
    app=Flask(__name__);app.register_blueprint(create_sewer_comparison_blueprint(package=Package()))
    return app.test_client()

def test_paging_reciprocal_and_not_business_status(client):
    def req(layer,id,**kw):return client.post('/comparison/sewer/lookup',json=dict(layer=layer,properties={'id':id},**kw)).get_json()
    a=req('kict_sewer_seoul',1)
    assert a['pipe']['rounded_geometry_match'] and a['pipe']['comparison_status'] is None
    assert len(a['counterparts']['items'])==20 and a['counterparts']['next_offset']==20
    assert len(req('kict_sewer_seoul',1,offset=20)['counterparts']['items'])==5
    assert req('sb001',2)['counterparts']['items'][0]['original_id']=='K1'
    assert req('kict_sewer_seoul',2)['pipe']['rounded_geometry_match'] is False
    assert req('kict_sewer_seoul',3)['pipe']['rounded_geometry_match'] is None
    assert req('kict_sewer_seoul',3)['pipe']['precision_review']

@pytest.mark.parametrize('body',[{},[],{'layer':'sb001','properties':{'id':True}},{'layer':'sb001','properties':{'id':1},'source_fid':2},{'layer':'sb001','properties':{'id':1},'offset':-1}])
def test_invalid_request(client,body):assert client.post('/comparison/sewer/lookup',json=body).status_code==400

def test_unknown_object_and_wrong_layer(client):
    assert client.post('/comparison/sewer/lookup',json={'layer':'sb001','properties':{'id':999}}).status_code==404
    assert client.post('/comparison/sewer/lookup',json={'layer':'sa001','properties':{'id':1}}).status_code==404
