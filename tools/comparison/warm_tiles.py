"""Precompute only the two Seoul comparison pairs, preserving source tiles."""
import argparse
import json
from pathlib import Path
import sys
import time
import threading
import sqlite3
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from comparison import ComparisonPackage
from sewer_comparison import SewerPackage
from comparison_tiles import ComparisonTileSidecars, MissingTile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--pair', choices=['water', 'sewer'], required=True)
    parser.add_argument('--min-zoom', type=int, default=11)  # display zoom12, tileSize512
    parser.add_argument('--max-zoom', type=int, default=17)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    cls = ComparisonPackage if args.pair == 'water' else SewerPackage
    package = cls(args.data / 'comparison' / args.pair)
    initial_version = package.verified()['version']
    class WarmPackage:
        def verified(self):
            info = package.verified()
            if info['version'] != initial_version:
                raise ValueError('Comparison package changed; restart precomputation')
            return info
        @contextmanager
        def connect(self):
            if not hasattr(local, 'db'):
                local.db = sqlite3.connect(package.path.as_uri()+'?mode=ro&immutable=1',uri=True)
                local.db.row_factory = sqlite3.Row
            yield local.db
    warm_package = WarmPackage()
    layers = ['sa001', 'kict_national'] if args.pair == 'water' else ['sb001', 'kict_sewer_seoul']
    local = threading.local()
    def build(job):
        layer, coord = job
        if not hasattr(local, 'services'):
            local.services = {name: ComparisonTileSidecars(args.data, warm_package, comparison=args.pair, layer=name) for name in layers}
            for service in local.services.values():
                service.disk.keep_open = True
        try:
            return layer, coord, local.services[layer].get(*coord), None
        except MissingTile:
            return layer, coord, None, 'missing'
        except Exception as error:
            return layer, coord, None, str(error)
    coords = set()
    for path in (args.data / 'tiles_full' / layers[0]).glob('*/*/*.pbf'):
        z,x,y = int(path.parent.parent.name), int(path.parent.name), int(path.stem)
        if args.min_zoom <= z <= args.max_zoom:
            coords.add((z,x,y))
    report = dict(pair=args.pair, version=package.verified()['version'], total_coordinates=len(coords),
                  completed=0, missing=[], errors=[], feature_instances=0, matched_instances=0, started=time.time())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=max(1,min(args.workers,4))) as executor:
        jobs = [(layer,coord) for coord in sorted(coords) for layer in layers]
        for layer,coord,result,error in executor.map(build,jobs):
            if result is not None:
                report['completed'] += 1
                report['feature_instances'] += result['feature_count']
                report['matched_instances'] += result['matched_count']
            elif error == 'missing':
                report['missing'].append([layer,*coord])
            else:
                report['errors'].append(dict(tile=[layer,*coord], error=error))
            if report['completed'] % 200 == 0:
                args.report.write_text(json.dumps(report, indent=2), encoding='utf-8')
                print(json.dumps({k:report[k] for k in ['pair','completed','total_coordinates']}), flush=True)
    report['finished'] = time.time()
    report['status'] = 'complete' if not report['errors'] else 'review_required'
    args.report.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k:report[k] for k in ['pair','completed','total_coordinates','status']}), flush=True)
    return bool(report['errors'])


if __name__ == '__main__':
    raise SystemExit(main())
