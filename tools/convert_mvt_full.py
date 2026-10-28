"""전량 보존 MVT 변환 (tiles_full). 줌별로 GDAL MVT 드라이버를 따로 실행한다.
- 원본(EPSG:32652)을 EPSG:3857로 직접 변환한 뒤 GDAL에 3857로 전달(재투영 없음).
- 해당 줌의 4096 격자 1칸(unit)의 2배보다 작은 선(bbox 긴 변 < 2*unit)은 격자에서 한 점으로 뭉개져 GDAL이 버리므로,
  선의 중심에 시작→끝 방향으로 길이 3*unit 의 2점 선으로 치환해 남긴다. (그 외 선은 원본 그대로, 단순화 없음)
- MAX_SIZE / MAX_FEATURES 는 사실상 무제한. 속성은 기존과 동일(3개).
사용: convert_mvt_full.py <layer> <z,z,..> [limit] [--src-dir DIR] [--out-dir DIR] [--work-dir DIR]
   -> {out-dir}/{layer}/{z}/{x}/{y}.pbf, {out-dir}/{layer}/metadata_z{z}.json
   기본 경로(옵션 생략 시): 입력 <ROOT>/상수샘플/*.gpkg, 출력 <ROOT>/backend/data/tiles_full, 작업 <ROOT>/backend/data/tiles_full_work
   (<ROOT> = 이 파일 위치의 두 단계 위 = backend/scripts/ 의 상위 폴더)
"""
import sys, time, json, shutil, os, argparse
from pathlib import Path
import numpy as np, shapely
from shapely import from_wkb
from pyproj import Transformer
from pyogrio.raw import read, write

ROOT = Path(__file__).resolve().parents[2]
CFG = {
    "sa001": dict(file="SA001.gpkg", cols=["PLINE_ID", "PLINE_CALB", "PLINE_MTRQ"]),
    "wtl_pipe_lm": dict(file="WTL_PIPE_LM.gpkg", cols=["FTR_IDN", "STD_DIP", "MOP_CDE"]),
}
W = 40075016.686
MIN_LEN_UNITS = 3.0      # 치환 선 길이(격자 단위)
SMALL_UNITS = 2.0        # 이보다 작은 선 치환


def inflate(g, z):
    unit = W / 2 ** z / 4096
    b = shapely.bounds(g)
    small = np.maximum(b[:, 2] - b[:, 0], b[:, 3] - b[:, 1]) < SMALL_UNITS * unit
    idx = np.where(small)[0]
    if len(idx) == 0:
        return g, 0
    sub = g[idx]
    # 시작점/끝점: 첫 부분의 첫 점, 마지막 부분의 마지막 점
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


def main():
    ap = argparse.ArgumentParser(description="전량 보존 MVT 변환 (줌별 GDAL MVT 드라이버 실행)")
    ap.add_argument("layer", choices=list(CFG), help="sa001 | wtl_pipe_lm")
    ap.add_argument("zooms", help="쉼표로 구분한 줌 목록, 예: 10,11,12,13,14,15,16,17")
    ap.add_argument("limit", nargs="?", type=int, default=0, help="시험용 앞쪽 N건만 변환(0 또는 생략 = 전량). 지정 시 기본 출력은 tiles_full_test")
    ap.add_argument("--src-dir", default=str(ROOT / "상수샘플"), help="gpkg 폴더 (기본 <ROOT>/상수샘플)")
    ap.add_argument("--out-dir", default=None, help="출력 타일 폴더 (기본 <ROOT>/backend/data/tiles_full)")
    ap.add_argument("--work-dir", default=str(ROOT / "backend" / "data" / "tiles_full_work"), help="임시 작업 폴더")
    a = ap.parse_args()
    key = a.layer
    zooms = [int(v) for v in a.zooms.split(",")]
    limit = a.limit if a.limit > 0 else None
    c = CFG[key]
    t0 = time.time()
    meta, _, geom, fields = read(str(Path(a.src_dir) / c["file"]), columns=c["cols"], max_features=limit)
    g = from_wkb(geom)
    del geom
    tr = Transformer.from_crs("EPSG:32652", "EPSG:3857", always_xy=True)
    g = shapely.transform(g, lambda a: np.column_stack(tr.transform(a[:, 0], a[:, 1])))
    print(f"[{key}] read+project {len(g)} {time.time()-t0:.0f}s", flush=True)
    out_dir = Path(a.out_dir) if a.out_dir else ROOT / "backend" / "data" / ("tiles_full_test" if limit else "tiles_full")
    final_root = out_dir / key
    work_root = Path(a.work_dir)
    final_root.mkdir(parents=True, exist_ok=True)
    work_root.mkdir(parents=True, exist_ok=True)
    for z in zooms:
        zdir = final_root / str(z)
        if zdir.exists() and any(zdir.iterdir()):
            print(f"z{z}: 이미 있음 -> 건너뜀(덮어쓰기 금지)", flush=True)
            continue
        t1 = time.time()
        gz, n_inf = inflate(g, z)
        wkb = shapely.to_wkb(gz)
        out = work_root / f"{key}_z{z}"
        if out.exists():
            shutil.rmtree(out)  # 내가 만든 작업 폴더만
        opts = dict(MINZOOM=z, MAXZOOM=z, EXTENT=4096, BUFFER=80, MAX_SIZE=1000000000, MAX_FEATURES=100000000,
                    TILING_SCHEME="EPSG:3857,-20037508.343,20037508.343,40075016.686", COMPRESS="YES", TYPE="overlay", NAME=key)
        write(str(out), wkb, fields, meta["fields"], driver="MVT", layer=key, crs="EPSG:3857",
              geometry_type="MultiLineString", **{k: str(v) for k, v in opts.items()})
        shutil.move(str(out / str(z)), str(zdir))
        if z == zooms[-1] or not (final_root / "metadata.json").exists():
            shutil.copy(out / "metadata.json", final_root / f"metadata_z{z}.json")
        shutil.rmtree(out)
        tmpdb = work_root / f"{key}_z{z}.temp.db"
        if tmpdb.exists():
            tmpdb.unlink()
        print(f"z{z}: inflated {n_inf} lines, write {time.time()-t1:.0f}s", flush=True)
    print(f"[{key}] done {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
