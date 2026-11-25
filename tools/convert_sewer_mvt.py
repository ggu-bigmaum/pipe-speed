"""GPKG -> verified MVT zoom folders, preserving the delivered inflate policy.

Each completed zoom is published atomically with its verification manifest.
No source file or database is modified. Incomplete work is never served.
"""
import argparse, gc, gzip, hashlib, json, os, re, shutil, time, uuid
from pathlib import Path
import numpy as np
import shapely
from pyproj import Transformer
from pyogrio.raw import read, write
from mapbox_vector_tile.Mapbox import vector_tile_pb2

W = 40075016.686
MIN_LEN_UNITS = 3.0
SMALL_UNITS = 2.0
POLICY_VERSION = 'delivered-inflate-v1-verified-output-v1'


def inflate(g, z):
    """Same algorithm as the delivered convert_mvt_full.py, explicitly approved."""
    unit = W / 2 ** z / 4096
    b = shapely.bounds(g)
    small = np.maximum(b[:, 2] - b[:, 0], b[:, 3] - b[:, 1]) < SMALL_UNITS * unit
    idx = np.where(small)[0]
    if len(idx) == 0:
        return g, 0
    sub = g[idx]
    c, ci = shapely.get_coordinates(sub, return_index=True)
    st = np.r_[0, np.where(np.diff(ci) != 0)[0] + 1]
    en = np.r_[st[1:] - 1, len(c) - 1]
    d = c[en] - c[st]
    nrm = np.hypot(d[:, 0], d[:, 1])
    d = np.where(nrm[:, None] > 1e-9, d / np.maximum(nrm, 1e-9)[:, None], np.array([1.0, 0.0]))
    bb = b[idx]
    ctr = np.column_stack(((bb[:, 0] + bb[:, 2]) / 2, (bb[:, 1] + bb[:, 3]) / 2))
    half = MIN_LEN_UNITS * unit / 2
    p0, p1 = ctr - d * half, ctr + d * half
    lines = shapely.linestrings(np.stack([p0, p1], axis=1))
    g = g.copy()
    g[idx] = shapely.multilinestrings(lines, indices=np.arange(len(lines)))
    return g, len(idx)


def file_hash(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def tile_digest(directory):
    digest=hashlib.sha256();total=0;count=0
    for path in sorted(directory.glob('*/*.pbf')):
        data=path.read_bytes()
        assert data[:2] == b'\x1f\x8b', f'Non-gzip tile: {path}'
        digest.update(path.relative_to(directory).as_posix().encode())
        digest.update(hashlib.sha256(data).digest())
        total+=len(data);count+=1
    assert count, f'No tiles: {directory}'
    return dict(tile_count=count,bytes=total,tile_sha256=digest.hexdigest())


def read_tile_ids(directory, key):
    """Decode every MVT without GDAL's 10 MiB decompressed read limit."""
    ids=[]
    for path in sorted(directory.glob('*/*.pbf')):
        tile=vector_tile_pb2.tile()
        tile.ParseFromString(gzip.decompress(path.read_bytes()))
        assert len(tile.layers)==1 and tile.layers[0].name==key, str(path)
        layer=tile.layers[0]
        assert layer.version==2 and layer.extent==4096
        id_key=list(layer.keys).index('id')
        for feature in layer.features:
            assert feature.type==2 and len(feature.geometry)>0, f'Empty/non-line feature: {path}'
            tags=feature.tags
            assert len(tags)%2==0
            found=False
            for i in range(0,len(tags),2):
                if tags[i]!=id_key:continue
                value=layer.values[tags[i+1]]
                for field in ('int_value','uint_value','sint_value'):
                    if value.HasField(field):
                        ids.append(int(getattr(value,field)));found=True;break
                assert found, f'Non-integer row ID: {path}'
                break
            assert found, f'Missing row ID: {path}'
    return np.unique(np.asarray(ids,dtype=np.int64))


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    for name in ('src','source-layer','key','zooms','fields','out-dir','work-dir'):
        ap.add_argument('--'+name,required=True)
    ap.add_argument('--expected-count',required=True,type=int)
    args=ap.parse_args()
    assert re.fullmatch('[a-z0-9_]+',args.key)
    zooms=[int(z) for z in args.zooms.split(',')]
    assert zooms==sorted(set(zooms)) and all(0<=z<=22 for z in zooms)
    fields=args.fields.split(',');assert 'id' in fields
    src=Path(args.src).resolve();out=Path(args.out_dir).resolve()/args.key
    work_root=Path(args.work_dir).resolve()
    out.mkdir(parents=True,exist_ok=True);work_root.mkdir(parents=True,exist_ok=True)
    original_hash=file_hash(src)
    signature=dict(source_sha256=original_hash,source_layer=args.source_layer,layer=args.key,fields=fields,policy=POLICY_VERSION)
    print(json.dumps(dict(event='START',layer=args.key,zooms=zooms,input=str(src)),ensure_ascii=False),flush=True)
    meta,_,wkb,data=read(src,layer=args.source_layer,columns=fields)
    fields=list(meta['fields'])
    assert len(wkb)==args.expected_count and set(fields)==set(args.fields.split(','))
    ids=np.asarray(data[fields.index('id')],dtype=np.int64)
    expected_ids=np.sort(ids)
    assert len(np.unique(ids))==len(ids), 'Source row IDs must be unique'
    g=shapely.from_wkb(wkb);del wkb
    assert not np.any(shapely.is_missing(g)|shapely.is_empty(g))
    types=shapely.get_type_id(g);assert np.all(np.isin(types,[1,5]))
    single=np.where(types==1)[0]
    if len(single):g[single]=shapely.multilinestrings(g[single],indices=np.arange(len(single)))
    assert meta['crs'], 'Missing input CRS'
    tr=Transformer.from_crs(meta['crs'],'EPSG:3857',always_xy=True)
    g=shapely.transform(g,lambda xy:np.column_stack(tr.transform(xy[:,0],xy[:,1])))
    assert np.isfinite(shapely.bounds(g)).all()
    results=[]
    for z in zooms:
        target=out/str(z);marker=target/'_complete.json'
        if target.exists():
            assert marker.is_file(), f'Incomplete output needs inspection: {target}'
            result=json.loads(marker.read_text(encoding='utf-8'))
            assert result['input_signature']==signature
            assert result['unique_ids']==len(ids) and result['missing_ids']==0
            assert tile_digest(target)=={k:result[k] for k in ('tile_count','bytes','tile_sha256')}
            results.append(result)
            print(json.dumps(dict(event='RESUMED_VERIFIED',layer=args.key,zoom=z)),flush=True)
            continue
        started=time.time()
        task=(work_root/f'{args.key}_z{z}_{uuid.uuid4().hex}').resolve()
        assert task.is_relative_to(work_root) and task!=work_root
        gz,inflated=inflate(g,z);shapes=shapely.to_wkb(gz)
        print(json.dumps(dict(event='WRITING',layer=args.key,zoom=z,features=len(ids),inflated_features=inflated)),flush=True)
        options=dict(MINZOOM=z,MAXZOOM=z,EXTENT=4096,BUFFER=80,MAX_SIZE=1000000000,MAX_FEATURES=100000000,TILING_SCHEME='EPSG:3857,-20037508.343,20037508.343,40075016.686',COMPRESS='YES',TYPE='overlay',NAME=args.key)
        write(task,shapes,data,fields,driver='MVT',layer=args.key,crs='EPSG:3857',geometry_type='MultiLineString',**{k:str(v) for k,v in options.items()})
        del gz,shapes;gc.collect()
        print(json.dumps(dict(event='VERIFYING',layer=args.key,zoom=z)),flush=True)
        actual=read_tile_ids(task/str(z),args.key)
        missing=np.setdiff1d(expected_ids,actual);extra=np.setdiff1d(actual,expected_ids)
        assert len(missing)==0 and len(extra)==0, f'z{z} ID mismatch: missing={missing[:20]} extra={extra[:20]}'
        stats=tile_digest(task/str(z))
        result=dict(input_signature=signature,zoom=z,source_features=len(ids),unique_ids=len(actual),missing_ids=len(missing),extra_ids=len(extra),inflated_features=inflated,elapsed_seconds=round(time.time()-started,2),**stats)
        (task/str(z)/'_complete.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        metadata=json.loads((task/'metadata.json').read_text(encoding='utf-8'))
        (out/f'metadata_z{z}.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
        metadata['minzoom']=min(zooms);metadata['maxzoom']=max(zooms)
        if isinstance(metadata.get('json'),str):
            nested=json.loads(metadata['json'])
            for layer in nested.get('vector_layers',[]):
                layer['minzoom']=min(zooms);layer['maxzoom']=max(zooms)
            metadata['json']=json.dumps(nested,ensure_ascii=False)
        (out/'metadata.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
        os.replace(task/str(z),target)
        assert task.is_relative_to(work_root) and task.name.startswith(args.key+'_z')
        shutil.rmtree(task)
        results.append(result)
        print(json.dumps(dict(event='ZOOM_COMPLETE',layer=args.key,**{k:v for k,v in result.items() if k!='input_signature'})),flush=True)
    assert file_hash(src)==original_hash, 'Input source changed during conversion'
    summary=dict(status='complete',layer=args.key,source=str(src),source_sha256=original_hash,source_features=len(ids),zooms=results,source_modified=False,display_policy='Legacy minimum-length line replacement retained by explicit user request')
    (out/'conversion_complete.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(event='COMPLETE',layer=args.key,source_features=len(ids),zooms=zooms)),flush=True)


if __name__=='__main__':main()
