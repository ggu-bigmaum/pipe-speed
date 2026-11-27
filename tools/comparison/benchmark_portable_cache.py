"""Compare recomputation vs verified cache reuse on identical local tiles."""
import argparse
from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend'))
from comparison import ComparisonPackage
from sewer_comparison import SewerPackage
from comparison_tiles import ComparisonTileSidecars
from comparison_cache import TileFlagCache


def timed(function):
    start=time.perf_counter();value=function()
    return value,(time.perf_counter()-start)*1000


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    report={'scope':'local same-tile API logic timing, warm OS file cache; excludes rendering/network','rows':[]}
    for pair,cls,layers in [('water',ComparisonPackage,['sa001','kict_national']),('sewer',SewerPackage,['sb001','kict_sewer_seoul'])]:
        package=cls(args.data/'comparison'/pair)
        info,report[pair+'_package_ms']=timed(package.verified)
        for layer in layers:
            for z in [11,13,15]:
                x=int((126.98+180)/360*2**z)
                y=int((1-math.asinh(math.tan(math.radians(37.56)))/math.pi)/2*2**z)
                with closing(sqlite3.connect((args.data/'comparison'/pair/'tile_cache.sqlite').resolve().as_uri()+'?mode=ro',uri=True)) as db:
                    row=db.execute('SELECT z,x,y,input_key,payload FROM tile_flags WHERE layer=? AND z=? ORDER BY abs(x-?)+abs(y-?) LIMIT 1',(layer,z,x,y)).fetchone()
                if not row:continue
                coord=tuple(row[:3]);expected=json.loads(row[4])
                service=ComparisonTileSidecars(args.data,package,comparison=pair,layer=layer)
                path=service._path(*coord)
                rebuilt,build_ms=timed(lambda:service._build(path,info['version'],coord if path.suffix=='.mbtiles' else None))
                # Older municipal rows omit the optional null peer field.
                normalized=dict(expected);normalized.setdefault('counterpart_sha256',None)
                assert rebuilt==normalized,'Recomputed payload differs from stored proof'
                cache=TileFlagCache(args.output/(pair+'-benchmark-cache.sqlite'))
                cache.put(layer,coord,json.loads(row[3]),expected)
                service.disk=cache
                def forbidden(*a,**kw):raise AssertionError('Cache lookup unexpectedly rebuilt flags')
                service._build=forbidden
                reused,first_ms=timed(lambda:service.get(*coord));assert reused==expected
                repeated,repeat_ms=timed(lambda:service.get(*coord));assert repeated==expected
                restarted=ComparisonTileSidecars(args.data,package,comparison=pair,layer=layer)
                restarted.disk=TileFlagCache(cache.path);restarted._build=forbidden
                _,disk_ms=timed(lambda:restarted.get(*coord))
                result={'pair':pair,'layer':layer,'coord':coord,'features':expected['feature_count'],
                        'recompute_ms':build_ms,'legacy_migrate_ms':first_ms,'content_disk_hit_ms':disk_ms,'memory_hit_ms':repeat_ms}
                report['rows'].append(result);print(json.dumps(result),flush=True)
    report['status']='passed'
    (args.output/'benchmark.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
