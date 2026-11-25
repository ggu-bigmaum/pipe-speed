"""Copy verified regional products and build bounded, read-only web lookup indexes.

Run locally/offline; server only needs stdlib sqlite3 to read the prepared indexes.
Original TIFFs and CSVs are copied byte-for-byte. No distance calculation changes.
"""
import argparse
import csv
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import time

FILES = ('pipe_case1_m.tif', 'pipe_case2_m.tif', 'pipe_dist_result.csv', 'grid_index.json')
REGIONS = dict(zip(('11','21','22','23','24','25','26','29','31','32','33','34','35','36','37','38','39'),
                   ('서울','부산','대구','인천','광주','대전','울산','세종','경기','강원','충북','충남','전북','전남','경북','경남','제주')))


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(4*1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def json_write(path, data):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def prepare(source, dest, utility, code, expected=None, data_policy=None):
    import numpy as np
    from PIL import Image
    started = time.monotonic()
    hashes = {name: digest(source/name) for name in FILES}
    if expected:
        for name in FILES:
            if hashes[name] != expected[name]:
                raise ValueError('Published source hash mismatch: ' + str(source/name))
    dest.mkdir(parents=True, exist_ok=True)
    oldmeta = dest/'dataset.json'
    if oldmeta.exists():
        old = json.loads(oldmeta.read_text(encoding='utf-8'))
        if data_policy and old.get('data_policy') != data_policy:
            raise ValueError('Existing dataset policy differs; stage a new directory: ' + str(dest))
        if all(old.get('source_files', {}).get(f, {}).get('sha256') == hashes[f] for f in FILES):
            if all((dest/f).is_file() and digest(dest/f) == hashes[f] for f in FILES) and (dest/'cells.sqlite').is_file() and digest(dest/'cells.sqlite') == old.get('provenance',{}).get('index_sha256'):
                with sqlite3.connect((dest/'cells.sqlite').as_uri() + '?mode=ro', uri=True) as con:
                    if con.execute('select count(*) from cells').fetchone()[0] == old['result_count'] and con.execute('pragma quick_check').fetchone()[0] == 'ok':
                        print(f'REUSE {utility}/{code}: {old["result_count"]:,}', flush=True)
                        return old
        raise ValueError('Existing product differs; do not overwrite automatically: ' + str(dest))
    for name in FILES:
        shutil.copy2(source/name, dest/name)
        if digest(dest/name) != hashes[name]:
            raise ValueError('Copy verification failed: ' + name)
    index = json.loads((dest/'grid_index.json').read_text(encoding='utf-8'))
    if index['crs'] != 'EPSG:32652' or index['pixel_size'] != [100,100]:
        raise ValueError('Unexpected grid CRS/pixel size')
    cells = index.pop('cells')
    index.pop('columns', None)
    w,h = index['width'],index['height']
    sx,sy = index['pixel_size']; ox,oy = index['origin']
    locations = {str(gid): (int(col),int(row)) for gid,col,row in cells}
    if len(locations) != len(cells) or len(cells) != index['result_count']:
        raise ValueError('Duplicate/missing grid IDs')
    expected_count = len(cells)
    del cells
    arrays = []
    for name in FILES[:2]:
        with Image.open(dest/name) as im:
            keys = im.tag_v2.get(34735,())
            epsg = next((keys[i+3] for i in range(4,len(keys),4) if keys[i] == 3072), None)
            if im.size != (w,h) or epsg != 32652 or tuple(im.tag_v2[33550][:2]) != (sx,sy) or tuple(im.tag_v2[33922][3:5]) != (ox,oy):
                raise ValueError('Raster/grid geometry mismatch')
            if float(im.tag_v2[42113]) != index['nodata']:
                raise ValueError('NoData mismatch')
            a = np.asarray(im).copy()
            if int(np.count_nonzero(a != index['nodata'])) != expected_count:
                raise ValueError('Valid pixel count mismatch')
            arrays.append(a)
    tempdb = dest/'cells.building.sqlite'
    if tempdb.exists():
        # Only this script's unfinished derived index; source files untouched.
        tempdb.unlink()
    con = sqlite3.connect(tempdb)
    try:
        con.executescript('''PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA cache_size=-65536;
        CREATE TABLE cells(col INTEGER NOT NULL,row INTEGER NOT NULL,grid_id TEXT NOT NULL,
        case1_m REAL NOT NULL,case2_m REAL NOT NULL,pass_flag INTEGER NOT NULL,
        pipe_id TEXT NOT NULL,near_x REAL NOT NULL,near_y REAL NOT NULL,near_lon REAL NOT NULL,near_lat REAL NOT NULL,
        PRIMARY KEY(col,row)) WITHOUT ROWID;''')
        batch=[]; total=0; max_error=0.0; samples=[]
        def flush():
            nonlocal total,max_error
            if not batch: return
            cols=np.asarray([v[0] for v in batch]); rows=np.asarray([v[1] for v in batch])
            for n in range(2):
                actual=arrays[n][rows,cols]
                expected_values=np.asarray([v[3+n] for v in batch],dtype=np.float64)
                if not np.allclose(actual,expected_values,rtol=2e-6,atol=.001):
                    raise ValueError('Raster/CSV values mismatch')
                max_error=max(max_error,float(np.max(np.abs(actual-expected_values))))
            con.executemany('INSERT INTO cells VALUES (?,?,?,?,?,?,?,?,?,?,?)',batch)
            total+=len(batch); batch.clear()
        with (dest/FILES[2]).open(encoding='utf-8-sig',newline='') as f:
            for record in csv.DictReader(f):
                gid=record['grid_id'].strip(); col,row=locations.pop(gid)
                if not (0<=col<w and 0<=row<h): raise ValueError('Out-of-range cell')
                v=[float(record[k]) for k in ('case1_m','case2_m','near_x','near_y','near_lon','near_lat')]
                if not all(map(math.isfinite,v)) or v[0]<0 or v[1]<0: raise ValueError('Invalid numeric values')
                flag=int(record['pass_flag'])
                if flag not in (0,1) or v[1] != (0.0 if flag else v[0]): raise ValueError('Case2/pass mismatch')
                pid=record['pipe_id'].strip()
                if not pid: raise ValueError('Empty pipe ID')
                batch.append((col,row,gid,v[0],v[1],flag,pid,*v[2:]))
                if len(samples)<3: samples.append({'col':col,'row':row,'grid_id':gid,'pipe_id':pid,'case1_m':v[0],'case2_m':v[1]})
                if len(batch)>=10000: flush()
        flush()
        if locations or total != expected_count: raise ValueError('Grid/result completeness mismatch')
        con.executescript('CREATE UNIQUE INDEX cells_grid_id ON cells(grid_id); CREATE INDEX cells_row_col ON cells(row,col);')
        con.commit()
        if con.execute('pragma quick_check').fetchone()[0] != 'ok': raise ValueError('SQLite quick_check failed')
    finally:
        con.close()
    tempdb.replace(dest/'cells.sqlite')
    original = utility == 'sewer' and code != '11' and data_policy == 'original-outside-seoul-v1'
    kind='상수' if utility=='water' else '하수·원본' if original else '하수·보정본'
    label=f'건기연 {kind}({REGIONS[code]})'
    source_files={name:{'bytes':(dest/name).stat().st_size,'sha256':hashes[name]} for name in FILES}
    meta={**index,'dataset_id':f'kict_{utility}_national_{code}','label':label,'result_filename':FILES[2],
          'grid_mode':'viewport','grid_cell_limit':12000,'source_files':source_files,
          'cases':[{'id':f'case{i+1}','filename':name,'bytes':(dest/name).stat().st_size,
                    'label':('Case 1 · 중심점부터 관로까지','Case 2 · 관로 통과 격자는 0')[i]} for i,name in enumerate(FILES[:2])],
          'provenance':{'source_directory':str(source),'source_hashes_verified':True,
                        'all_csv_grid_raster_values_verified':True,'max_float32_rounding_m':max_error,
                        'index_sha256':digest(dest/'cells.sqlite'),
                        'pipe_id_meaning':('기존 서울 하수 보정본 관로 ID' if utility=='sewer' and code=='11' else
                                           '전국 상수 원본 FID' if utility=='water' else
                                           '원본 관로 고유 ID; pipe_identity.csv로 원본 SHP FID·ORG_IDN 확인' if original else
                                           '전국 하수 보정본 통합 관로 ID')},
          'verification_samples':samples}
    if data_policy:
        meta['data_policy'] = data_policy
    json_write(oldmeta,meta)
    print(f'READY {utility}/{code} {REGIONS[code]}: {total:,} cells, {time.monotonic()-started:.1f}s',flush=True)
    del arrays,locations
    gc.collect()
    return meta


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--outputs',type=Path,required=True); ap.add_argument('--data',type=Path,required=True)
    ap.add_argument('--utilities',nargs='+',default=['water','sewer']); args=ap.parse_args()
    reports=[]
    for utility in args.utilities:
        source_root=args.outputs/f'national_{utility}_raster_20261124'
        if not (source_root/'COMPLETE.json').is_file(): raise ValueError('Source not marked complete')
        published=json.loads((source_root/'file_hash_manifest.json').read_text(encoding='utf-8'))
        if utility=='sewer': published={v['path'].replace('\\','/'):v['sha256'] for v in published['files']}
        for region in sorted((source_root/'regions').iterdir()):
            if not region.is_dir(): continue
            code=region.name.split('_')[0]
            expected=None
            if utility=='water': expected={f:published['sha256'][f'regions/{region.name}/{f}'] for f in FILES}
            else: expected={f:published[f'regions/{region.name}/{f}'] for f in FILES}
            reports.append(prepare(region,args.data/'distance'/f'kict_{utility}_national'/code,utility,code,expected))
        if utility=='sewer':
            reports.append(prepare(args.data/'distance'/'kict_sewer_seoul',args.data/'distance'/'kict_sewer_national'/'11','sewer','11'))
    json_write(args.data/'national_distance_ready.json',{'datasets':len(reports),'grid_cells':sum(r['result_count'] for r in reports),
        'datasets_verified':[{'id':r['dataset_id'],'rows':r['result_count'],'source_files':r['source_files'],'index_sha256':r['provenance']['index_sha256']} for r in reports]})


if __name__=='__main__': main()
