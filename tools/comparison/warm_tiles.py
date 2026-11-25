"""Precompute only the two Seoul comparison pairs, preserving source tiles."""
import argparse
import json
from pathlib import Path
import sys
import time

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
    args = parser.parse_args()
    cls = ComparisonPackage if args.pair == 'water' else SewerPackage
    package = cls(args.data / 'comparison' / args.pair)
    layers = ['sa001', 'kict_national'] if args.pair == 'water' else ['sb001', 'kict_sewer_seoul']
    services = {layer: ComparisonTileSidecars(args.data, package, comparison=args.pair, layer=layer) for layer in layers}
    coords = set()
    for path in (args.data / 'tiles_full' / layers[0]).glob('*/*/*.pbf'):
        z,x,y = int(path.parent.parent.name), int(path.parent.name), int(path.stem)
        if args.min_zoom <= z <= args.max_zoom:
            coords.add((z,x,y))
    report = dict(pair=args.pair, version=package.verified()['version'], total_coordinates=len(coords),
                  completed=0, missing=[], errors=[], feature_instances=0, matched_instances=0, started=time.time())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    for coord in sorted(coords):
        for layer, service in services.items():
            try:
                result = service.get(*coord)
                report['completed'] += 1
                report['feature_instances'] += result['feature_count']
                report['matched_instances'] += result['matched_count']
            except MissingTile:
                report['missing'].append([layer,*coord])
            except Exception as error:
                report['errors'].append(dict(tile=[layer,*coord], error=str(error)))
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
