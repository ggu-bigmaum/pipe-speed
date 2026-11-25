#!/usr/bin/env bash
# Run on the pipe server after uploading the two archives and pulling code.
# Keep previous directories intact in a dated backup; never merge tile trees.
set -euo pipefail
repo="$(cd "${1:-/home/bigmaum99/demos/pipe-speed}" && pwd -P)"
data="$repo/backend/data"
upload="$data/upload_original_sewer_20261125"
test -f "$repo/backend/national_tiles.py"
test -f "$upload/archive-manifest.json"
grep -q 'original-outside-seoul-v1' "$repo/backend/national_tiles.py"
(cd "$upload" && sha256sum --check SHA256SUMS)

incoming="$(mktemp -d "$data/.incoming-original-20261125.XXXXXX")"
tar -xzf "$upload/pipe-sewer-original-tiles-20261125.tar.gz" -C "$incoming"
tar -xzf "$upload/pipe-national-rasters-original-sewer-20261125.tar.gz" -C "$incoming"
fresh="$incoming/backend/data"
paths=(tiles_sewer_national tiles_full/kict_sewer_seoul distance/kict_water_national distance/kict_sewer_national national_distance_ready.json)
for rel in "${paths[@]}"; do
  test -e "$fresh/$rel"
  test ! -L "$data/$rel"
done
"$repo/.venv/bin/python" - "$fresh" <<'PY'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
tiles = list((p/'tiles_sewer_national').glob('*/conversion_complete.json'))
assert len(tiles) == 16, 'Expected 16 regional tile bundles'
for f in tiles:
    m = json.loads(f.read_text())
    assert m['status'] == 'complete' and m['data_policy'] == 'original-outside-seoul-v1'
    assert m['raw_originals_verified'] is True
    assert all((f.parent/str(z)).is_dir() for z in range(6, 18))
sewer = list((p/'distance/kict_sewer_national').glob('*/dataset.json'))
assert len(sewer) == 16, 'Expected Seoul + 15 original sewer regions'
for f in sewer:
    if f.parent.name != '11':
        assert json.loads(f.read_text())['data_policy'] == 'original-outside-seoul-v1'
assert len(list((p/'distance/kict_water_national').glob('*/dataset.json'))) == 17
print('Upload data validation passed')
PY

backup="$(mktemp -d "$data/.previous-original-20261125.XXXXXX")"
backed=()
installed=()
rollback() {
  code=$?
  trap - ERR
  set +e
  for ((i=${#installed[@]}-1; i>=0; i--)); do
    rel="${installed[i]}"
    mkdir -p "$(dirname "$fresh/$rel")"
    mv -- "$data/$rel" "$fresh/$rel"
  done
  for ((i=${#backed[@]}-1; i>=0; i--)); do
    rel="${backed[i]}"
    mkdir -p "$(dirname "$data/$rel")"
    mv -- "$backup/$rel" "$data/$rel"
  done
  sudo systemctl start pipe-speed.service
  printf 'Installation failed; previous data restored. Backup: %s\n' "$backup" >&2
  exit "$code"
}
sudo systemctl stop pipe-speed.service
trap rollback ERR
for rel in "${paths[@]}"; do
  mkdir -p "$(dirname "$data/$rel")" "$(dirname "$backup/$rel")"
  if test -e "$data/$rel"; then
    mv -- "$data/$rel" "$backup/$rel"
    backed+=("$rel")
  fi
  mv -- "$fresh/$rel" "$data/$rel"
  installed+=("$rel")
done
sudo systemctl start pipe-speed.service
sudo systemctl is-active --quiet pipe-speed.service
trap - ERR
printf 'Data installed. Previous data retained at: %s\n' "$backup"
printf 'Check https://pipe.domion-energy.com/app/ after a hard refresh.\n'
