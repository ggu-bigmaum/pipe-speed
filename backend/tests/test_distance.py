import hashlib
import json
from pathlib import Path
import sys
import pytest

BACKEND = Path(__file__).resolve().parents[1]
DATA = BACKEND / 'data/distance/kict_water_seoul'
sys.path.insert(0, str(BACKEND))
from app import app

@pytest.fixture(scope='module')
def client():
    return app.test_client()

def test_raster_metadata_and_grid_count(client):
    r=client.get('/distance/meta')
    assert r.status_code==200
    m=r.get_json()
    assert (m['width'],m['height'],m['crs'])==(370,303,'EPSG:32652')
    assert m['result_count']==60579
    assert [x['id'] for x in m['cases']]==['case1','case2']

@pytest.mark.parametrize('filename',['pipe_case1_m.tif','pipe_case2_m.tif','pipe_dist_result.xlsx'])
def test_serves_original_bytes_without_http_cache(client,filename):
    r=client.get('/distance/files/'+filename)
    assert r.status_code==200
    assert hashlib.sha256(r.data).digest()==hashlib.sha256((DATA/filename).read_bytes()).digest()
    assert 'no-store' in r.headers['Cache-Control']

def test_actual_clicked_cell_links_by_grid_id(client):
    r=client.get('/distance/cell?col=0&row=156')
    assert r.status_code==200
    d=r.get_json()
    assert d['matched'] is True
    assert d['grid_id']=='14914652'
    assert d['pipe_id']=='5396300000'
    assert d['case1_m']==pytest.approx(3411.445238340554)
    assert d['center']==[302650,4158850]
    assert d['near_x']==pytest.approx(305731.3998381217)

def test_nodata_is_unmatched_and_not_zero(client):
    idx=json.loads((DATA/'grid_index.json').read_text(encoding='utf-8'))
    occupied={(c,r) for _,c,r in idx['cells']}
    c,r=next((c,r) for r in range(303) for c in range(370) if (c,r) not in occupied)
    response=client.get(f'/distance/cell?col={c}&row={r}')
    assert response.status_code==200
    assert response.get_json()=={'matched':False,'reason':'nodata'}

@pytest.mark.parametrize('query',['col=-1&row=0','col=370&row=0','col=0&row=303','col=NaN&row=0','col=0.5&row=1','col=0'])
def test_bad_or_outside_pixel_is_rejected(client,query):
    assert client.get('/distance/cell?'+query).status_code==400

def test_valid_zero_is_returned(client):
    from openpyxl import load_workbook
    wb=load_workbook(DATA/'pipe_dist_result.xlsx',read_only=True,data_only=True)
    values=iter(wb['격자별_거리'].values); next(values)
    zero=next(row for row in values if row[2]==0)
    wb.close()
    cells=json.loads((DATA/'grid_index.json').read_text(encoding='utf-8'))['cells']
    _,col,row=next(x for x in cells if x[0]==str(zero[0]))
    d=client.get(f'/distance/cell?col={col}&row={row}').get_json()
    assert d['matched'] is True and d['case2_m']==0 and d['pass_flag']==1

@pytest.mark.parametrize('path',['.env','grid_index.json','../../app.py','other.tif'])
def test_download_allowlist(client,path):
    assert client.get('/distance/files/'+path).status_code==404

def test_existing_endpoints_remain_available(client):
    assert client.get('/health').status_code==200
    assert client.get('/app/').status_code==200
    assert client.get('/tiles/unknown/1/1/1.pbf').status_code==404

def test_missing_dataset_returns_actionable_json(tmp_path):
    from flask import Flask
    from distance import create_distance_blueprint
    isolated=Flask('missing-data-test')
    isolated.register_blueprint(create_distance_blueprint(tmp_path))
    response=isolated.test_client().get('/distance/meta')
    assert response.status_code==503
    assert '입력 파일' in response.get_json()['error']

@pytest.mark.parametrize('value,expected',[(123.0,'123'),('00123','00123'),('A-123','A-123')])
def test_identifier_preserves_meaning(value,expected):
    from distance import identifier
    assert identifier(value)==expected

def test_grid_overlay_exactly_covers_valid_cell_edges(client):
    response=client.get('/distance/gridlines')
    assert response.status_code==200
    body=response.get_json()
    assert body['crs']=='EPSG:32652'
    index=json.loads((DATA/'grid_index.json').read_text(encoding='utf-8'))
    expected=set()
    for _,c,r in index['cells']:
        expected.update(((c,r,'h'),(c,r+1,'h'),(c,r,'v'),(c+1,r,'v')))
    actual=set(); units=0
    ox,oy=index['origin'];sx,sy=index['pixel_size']
    for start,end in body['geometry']['coordinates']:
        c0,r0=(start[0]-ox)/sx,(oy-start[1])/sy
        c1,r1=(end[0]-ox)/sx,(oy-end[1])/sy
        assert all(v==int(v) for v in (c0,r0,c1,r1))
        c0,r0,c1,r1=map(int,(c0,r0,c1,r1))
        assert (c0==c1) != (r0==r1)
        edge={(c,r0,'h') for c in range(c0,c1)} if r0==r1 else {(c0,r,'v') for r in range(r0,r1)}
        actual.update(edge); units+=len(edge)
    assert actual==expected
    assert units==len(actual), 'shared edges must not be drawn twice'
