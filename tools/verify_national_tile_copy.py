"""Verify every copied tile byte against the completed conversion digests."""
import argparse
import hashlib
import json
from pathlib import Path

p=argparse.ArgumentParser(); p.add_argument('root',type=Path); p.add_argument('report',type=Path); args=p.parse_args()
reports=[]
for region in sorted(args.root.iterdir()):
    if not region.is_dir(): continue
    complete=json.loads((region/'conversion_complete.json').read_text(encoding='utf-8'))
    for z in range(6,18):
        directory=region/str(z)
        expected=json.loads((directory/'_complete.json').read_text(encoding='utf-8'))
        h=hashlib.sha256(); count=size=0
        for path in sorted(directory.glob('*/*.pbf')):
            data=path.read_bytes()
            assert data[:2]==b'\x1f\x8b'
            h.update(path.relative_to(directory).as_posix().encode())
            h.update(hashlib.sha256(data).digest()); count+=1; size+=len(data)
        assert (count,size,h.hexdigest())==(expected['tile_count'],expected['bytes'],expected['tile_sha256']), (region.name,z)
        reports.append({'region':region.name,'zoom':z,'tiles':count,'bytes':size,'sha256':h.hexdigest()})
    print('VERIFIED '+region.name,flush=True)
assert len(reports)==192
args.report.write_text(json.dumps({'complete':True,'regions':16,'tiles':sum(r['tiles'] for r in reports),'bytes':sum(r['bytes'] for r in reports),'checks':reports},ensure_ascii=False,indent=2),encoding='utf-8')
