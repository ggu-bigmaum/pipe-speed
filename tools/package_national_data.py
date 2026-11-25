"""Create server-transfer archives, retaining the backend/data directory layout."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import tarfile
import time


def package(repo, destination, filename, roots):
    target=destination/filename
    if target.exists(): raise FileExistsError(target)
    if not roots or any(not root.exists() for root in roots):
        raise FileNotFoundError('One or more required data roots are absent')
    members=[]; total=0; started=time.monotonic()
    with target.with_suffix(target.suffix+'.partial').open('wb') as raw:
        with gzip.GzipFile(filename='',fileobj=raw,mode='wb',compresslevel=1,mtime=0) as gz:
            with tarfile.open(fileobj=gz,mode='w|',format=tarfile.PAX_FORMAT,dereference=True) as tar:
                for root in roots:
                    files=sorted(root.rglob('*')) if root.is_dir() else [root]
                    count=0
                    for file in files:
                        if not file.is_file(): continue
                        if file.name.endswith(('.building.sqlite','.tmp','.partial')): raise ValueError('Unfinished input: '+str(file))
                        rel=file.relative_to(repo).as_posix()
                        info=tar.gettarinfo(str(file),arcname=rel)
                        if not info.isfile(): raise ValueError('Nonregular member')
                        info.uid=info.gid=0; info.uname=info.gname=''; info.mode=0o644
                        with file.open('rb') as f: tar.addfile(info,f)
                        members.append(rel); total+=info.size; count+=1
                    if count == 0:
                        raise ValueError('Required data root contains no files: '+str(root))
                    print(f'PACKED {root.name}: {count:,} files',flush=True)
    partial=target.with_suffix(target.suffix+'.partial')
    h=hashlib.sha256()
    with partial.open('rb') as f:
        for chunk in iter(lambda:f.read(4*1024*1024),b''): h.update(chunk)
    # Read the full stream to check gzip CRC and tar member names/counts.
    verified=[]
    with tarfile.open(partial,'r|gz') as tar:
        for member in tar:
            if not member.isfile() or '..' in Path(member.name).parts or not member.name.startswith('backend/data/'):
                raise ValueError('Unsafe archive member '+member.name)
            verified.append(member.name)
    if verified!=members: raise ValueError('Archive member verification mismatch')
    partial.replace(target)
    print(f'ARCHIVE READY {filename}: {target.stat().st_size:,} bytes ({time.monotonic()-started:.1f}s)',flush=True)
    return {'file':filename,'files':len(members),'uncompressed_bytes':total,'bytes':target.stat().st_size,'sha256':h.hexdigest()}


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--repo',required=True,type=Path);ap.add_argument('--destination',required=True,type=Path);a=ap.parse_args()
    a.destination.mkdir(parents=True,exist_ok=True)
    data=a.repo/'backend'/'data'
    results=[]
    results.append(package(a.repo,a.destination,'pipe-national-sewer-tiles-20261125.tar.gz',
                           [data/'tiles_sewer_national',data/'tiles_full'/'kict_sewer_seoul']))
    results.append(package(a.repo,a.destination,'pipe-national-rasters-20261125.tar.gz',
                           [data/'distance'/'kict_water_national',data/'distance'/'kict_sewer_national',data/'national_distance_ready.json']))
    (a.destination/'SHA256SUMS').write_text(''.join(f'{r["sha256"]}  {r["file"]}\n' for r in results),encoding='ascii')
    (a.destination/'archive-manifest.json').write_text(json.dumps(results,indent=2),encoding='utf-8')


if __name__=='__main__': main()
