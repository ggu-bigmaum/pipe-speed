"""Verify migration on SQLite copies; never modify installed caches or source data."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from comparison import ComparisonPackage
from sewer_comparison import SewerPackage
from comparison_tiles import ComparisonTileSidecars
from comparison_cache import TileFlagCache


class VerificationCache(TileFlagCache):
    """Batch writes to the disposable verification copy, never installed data."""
    def __init__(self,path):
        super().__init__(path)
        self.keep_open=True
        self.pending=[]

    def put(self,layer,coord,key,payload):
        self.pending.append((layer,*coord,json.dumps(key),json.dumps(payload,separators=(',',':'))))
        if len(self.pending)>=250:self.flush()

    def flush(self):
        if self.pending:
            with self._session() as db,db:
                db.executemany('INSERT OR REPLACE INTO tile_flags VALUES(?,?,?,?,?,?)',self.pending)
            self.pending.clear()


def summarize(values):
    ordered = sorted(values)
    return dict(count=len(values), median_ms=statistics.median(values) if values else None,
                p95_ms=ordered[min(len(ordered)-1,int(len(ordered)*.95))] if values else None,
                max_ms=max(values) if values else None)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--limit',type=int,default=60,help='Per layer; 0 verifies every stored row')
    parser.add_argument('--resume',action='store_true',help='Reverify all rows using existing output copies')
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    report={'status':'running','scope':'copied caches; full original comparison DBs read-only','layers':{}}
    report_path=args.output/'verification.json'
    for pair,cls,layers in [('water',ComparisonPackage,['sa001','kict_national']),('sewer',SewerPackage,['sb001','kict_sewer_seoul'])]:
        original=args.data/'comparison'/pair/'tile_cache.sqlite'
        copy=args.output/(pair+'-verified-cache.sqlite')
        if copy.exists() and not args.resume: raise ValueError('Output already exists: '+str(copy))
        if not copy.exists():
            with closing(sqlite3.connect(original.resolve().as_uri()+'?mode=ro',uri=True)) as source, closing(sqlite3.connect(copy)) as dest:
                source.backup(dest)
        package=cls(args.data/'comparison'/pair)
        start=time.perf_counter();version=package.verified()['version']
        report[pair+'_package_verify_ms']=(time.perf_counter()-start)*1000
        for layer in layers:
            service=ComparisonTileSidecars(args.data,package,comparison=pair,layer=layer)
            service.disk=VerificationCache(copy)
            def forbidden(*a,**kw): raise AssertionError('Unexpected rebuild; stored proof did not transfer')
            service._build=forbidden
            with closing(sqlite3.connect(original.resolve().as_uri()+'?mode=ro',uri=True)) as db:
                rows=db.execute('SELECT z,x,y,payload FROM tile_flags WHERE layer=? ORDER BY z,x,y',(layer,)).fetchall()
            if args.limit and len(rows)>args.limit:
                rows=[rows[int(i*(len(rows)-1)/(args.limit-1))] for i in range(args.limit)] if args.limit>1 else rows[:1]
            first_times=[];repeat_times=[];errors=[]
            for index,(z,x,y,stored) in enumerate(rows):
                try:
                    start=time.perf_counter();result=service.get(z,x,y);first_times.append((time.perf_counter()-start)*1000)
                    if result!=json.loads(stored): raise AssertionError('Payload changed')
                    start=time.perf_counter();again=service.get(z,x,y);repeat_times.append((time.perf_counter()-start)*1000)
                    if again!=result: raise AssertionError('Repeat changed')
                except Exception as exc: errors.append({'coord':[z,x,y],'error':str(exc)})
                if (index+1)%1000==0: print(json.dumps({'layer':layer,'checked':index+1,'errors':len(errors)}),flush=True)
            service.disk.flush()
            if hasattr(service.disk._local,'db'): service.disk._local.db.close()
            report['layers'][layer]={'version':version,'rows':len(rows),'errors':errors,'first_content_verified':summarize(first_times),'memory_repeat':summarize(repeat_times)}
            report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'layer':layer,'rows':len(rows),'errors':len(errors),'first':summarize(first_times),'repeat':summarize(repeat_times)}),flush=True)
        with closing(sqlite3.connect(copy)) as db:
            report[pair+'_integrity']=db.execute('PRAGMA integrity_check').fetchone()[0]
            report[pair+'_new_keys']=db.execute("SELECT count(*) FROM tile_flags WHERE input_key LIKE '[\"tile-content-v1\"%'").fetchone()[0]
        if package.verified()['version']!=version: raise ValueError('Package changed during verification')
    report['status']='passed' if not any(v['errors'] for v in report['layers'].values()) and all(report[p+'_integrity']=='ok' for p in ['water','sewer']) else 'failed'
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report['status']!='passed'


if __name__=='__main__': raise SystemExit(main())
