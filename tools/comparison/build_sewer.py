"""Build separate per-object sewer evidence; source geometry is read-only."""
import argparse, hashlib, json, sqlite3
from pathlib import Path
import numpy as np
import shapely

RULE = 'sewer-whole-round3-half-up-v1'

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        while block:=f.read(8*1024*1024): h.update(block)
    return h.hexdigest()

def rounded(geoms):
    return shapely.transform(geoms, lambda xy: np.copysign(np.floor(np.abs(xy)*1000+.5),xy)/1000)

def matched_pairs(a,b):
    a,b=rounded(a),rounded(b)
    av=shapely.is_valid(a)&(~shapely.is_empty(a))&(shapely.length(a)>0)
    bv=shapely.is_valid(b)&(~shapely.is_empty(b))&(shapely.length(b)>0)
    index={}
    for j,box in enumerate(shapely.bounds(b)):
        if bv[j]: index.setdefault(tuple(box),[]).append(j)
    ai=[];bi=[]
    for i,box in enumerate(shapely.bounds(a)):
        if av[i]:
            for j in index.get(tuple(box),[]): ai.append(i);bi.append(j)
    ai=np.asarray(ai,dtype=np.int64);bi=np.asarray(bi,dtype=np.int64)
    keep=shapely.equals(a[ai],b[bi])
    return ai[keep],bi[keep],av,bv

def read_source(data,layer,filename,table,oid,role):
    path=data/'source'/layer/filename
    sha=digest(path)
    marker=json.loads((data/'tiles_full'/('sb001' if layer=='seoul_sewer' else layer)/'conversion_complete.json').read_text(encoding='utf-8'))
    if marker['source_sha256']!=sha: raise ValueError('Tile source hash differs: '+layer)
    db=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True)
    assert db.execute('SELECT srs_id FROM gpkg_geometry_columns WHERE table_name=?',(table,)).fetchone()[0]==32652
    rows=db.execute(f'SELECT id,"{oid}",{role},geom FROM "{table}" ORDER BY id').fetchall();db.close()
    assert len({r[0] for r in rows})==len(rows)==marker['source_features']
    def wkb(v): return v[8+{0:0,1:32,2:48,3:48,4:64}[(v[3]>>1)&7]:]
    geoms=shapely.from_wkb([wkb(r[3]) for r in rows])
    return rows,geoms,dict(path=str(path),sha256=sha,layer=layer)

def build(data,out):
    data=Path(data).resolve();out=Path(out).resolve();out.mkdir(parents=True,exist_ok=True)
    path=out/'sewer_comparison.sqlite'
    if path.exists(): raise ValueError('Output already exists; use a new directory')
    kr,kg,km=read_source(data,'kict_sewer_seoul','SWL_PIPE_LM.gpkg','swl_pipe_lm','org_idn','feature_role')
    sr,sg,sm=read_source(data,'seoul_sewer','SB001.gpkg','sb001','IDN',"'municipal_original'")
    a,b,av,bv=matched_pairs(kg,sg);ks=set(map(int,a));ss=set(map(int,b))
    db=sqlite3.connect(path)
    db.executescript('''CREATE TABLE pipes(dataset TEXT,id INTEGER,original_id TEXT,role TEXT,rounded_geometry_match INTEGER,precision_review INTEGER,PRIMARY KEY(dataset,id));
    CREATE TABLE pairs(kict_id INTEGER,seoul_id INTEGER,PRIMARY KEY(kict_id,seoul_id));
    CREATE INDEX pairs_seoul ON pairs(seoul_id,kict_id);
    CREATE TABLE tile_groups(dataset TEXT,tile_key TEXT,all_strict INTEGER,PRIMARY KEY(dataset,tile_key));
    CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);''')
    counts={}
    for dataset,rows,valid,matched in [('kict',kr,av,ks),('seoul',sr,bv,ss)]:
        db.executemany('INSERT INTO pipes VALUES(?,?,?,?,?,?)',((dataset,int(r[0]),None if r[1] is None else str(r[1]),r[2],int(i in matched) if valid[i] else None,int(not valid[i]))for i,r in enumerate(rows)))
        db.executemany('INSERT INTO tile_groups VALUES(?,?,?)',((dataset,json.dumps([str(r[0])],separators=(',',':')),int(i in matched))for i,r in enumerate(rows)))
        counts[dataset]=dict(total=len(rows),matched=len(matched),precision_review=int((~valid).sum()))
    db.executemany('INSERT INTO pairs VALUES(?,?)',((int(kr[i][0]),int(sr[j][0]))for i,j in zip(a,b)))
    counts['pairs']=len(a)
    for key,value in [('rule_version',RULE),('counts',counts)]:db.execute('INSERT INTO metadata VALUES(?,?)',(key,json.dumps(value)))
    assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    assert db.execute('SELECT COUNT(*) FROM pairs').fetchone()[0]==len(a)
    assert db.execute('SELECT COUNT(*) FROM pairs p LEFT JOIN pipes k ON k.dataset=\'kict\' AND k.id=p.kict_id LEFT JOIN pipes s ON s.dataset=\'seoul\' AND s.id=p.seoul_id WHERE k.id IS NULL OR s.id IS NULL').fetchone()[0]==0
    db.commit();db.close()
    for meta in (km,sm): assert digest(Path(meta['path']))==meta['sha256']
    manifest=dict(status='complete',schema_version=1,rule_version=RULE,counts=counts,sources=[km,sm],crs=32652,original_id_condition=False,geometry_changed=False,rounding='XY half-away-from-zero to 3 decimals, whole-feature GEOS equals; invalid/zero-length rounded geometries require review; six business states unchanged',output=dict(bytes=path.stat().st_size,sha256=digest(path)))
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(manifest,ensure_ascii=False))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',required=True);p.add_argument('--out',required=True);x=p.parse_args();build(x.data,x.out)
