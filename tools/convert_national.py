"""전국 상수관망(건기연상수관망.gpkg) -> MVT 타일(tiles_national/kict_national). 한 번에 한 프로세스, 줌별·구역별로 GDAL 실행.
- z6~z10 : 간략화 구간. GDAL 기본 동작(격자에서 뭉개지는 짧은 선 탈락) + MAX_SIZE(압축 후) 상한으로 타일 1장 gzip <= 약 5MB.
- z11~z17: 원본 전량 보존. 해당 줌 격자 2칸 미만 선은 중심에서 방향 유지한 3칸 길이 2점 선으로 치환(convert_mvt_full.py 와 동일).
- 구역(chunk): z8 타일 단위. z>=8 타일은 정확히 한 구역에 속하므로, 구역의 bbox 와 겹치는 선을 읽어 변환한 뒤 그 구역에 속한 타일만 최종 폴더로 옮긴다
  (경계를 넘는 선은 이웃 구역에서도 읽히므로 타일에 모두 들어감).
- z<8 (z6, z7) : 전국 단위 한 번에. 메모리를 위해 격자 0.7칸 이상인 선만 읽음(그 미만은 어차피 GDAL 이 탈락시킴).
사용: convert_national.py <z,z,..> [chunks=all|x8/y8,x8/y8..] [--src GPKG] [--out-dir DIR] [--work-dir DIR]
   -> {out-dir}/{z}/{x}/{y}.pbf, 진행 로그 stdout
   기본 경로(옵션 생략 시): 입력 <ROOT>/건기연상수관망/건기연상수관망.gpkg, 출력 <ROOT>/backend/data/tiles_national/kict_national,
   작업 <ROOT>/backend/data/tiles_national_work  (<ROOT> = 이 파일 위치의 두 단계 위 = backend/scripts/ 의 상위 폴더)
"""
import sys, time, os, shutil, json, sqlite3, argparse
from pathlib import Path
import numpy as np, shapely
from shapely import from_wkb
from pyproj import Transformer
from pyogrio.raw import read, write

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "건기연상수관망" / "건기연상수관망.gpkg"
KEY = "kict_national"
OUT = ROOT / "backend" / "data" / "tiles_national" / KEY
WORK = ROOT / "backend" / "data" / "tiles_national_work"
COLS = ["FTR_IDN", "STD_DIP", "MOP_CDE"]
W = 40075016.686
SIMPLIFIED = (5, 6, 7, 8, 9, 10)
MAX_SIZE_SIMPLIFIED = 5_000_000
TR_FWD = Transformer.from_crs("EPSG:32652", "EPSG:3857", always_xy=True)
TR_INV = Transformer.from_crs("EPSG:3857", "EPSG:32652", always_xy=True)
MIN_LEN_UNITS, SMALL_UNITS = 3.0, 2.0


def unit_of(z):
    return W / 2 ** z / 4096


def inflate(g, z):
    unit = unit_of(z)
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
    lines = shapely.linestrings(np.stack([ctr - d * half, ctr + d * half], axis=1))
    g = g.copy()
    g[idx] = shapely.multilinestrings(lines, indices=np.arange(len(lines)))
    return g, len(idx)


def block_bbox_utm(bx, by, margin_frac=0.03):
    t = W / 2 ** 8
    x0, y1 = -W / 2 + bx * t, W / 2 - by * t
    m = t * margin_frac
    xs = np.linspace(x0 - m, x0 + t + m, 9)
    ys = np.linspace(y1 - t - m, y1 + m, 9)
    X, Y = np.meshgrid(xs, ys)
    u, v = TR_INV.transform(X.ravel(), Y.ravel())
    return float(np.min(u)), float(np.min(v)), float(np.max(u)), float(np.max(v))


def load_index():
    """rtree 로 선별: fid, 3857 bbox 중심 -> z8 블록. (rtree 는 float32)"""
    c = sqlite3.connect(f"file:{SRC}?mode=ro", uri=True)
    r = np.array(c.execute("select id,minx,maxx,miny,maxy from rtree___geom order by id").fetchall(), dtype=np.float64)
    c.close()
    fid = r[:, 0].astype(np.int64)
    cx, cy = TR_FWD.transform((r[:, 1] + r[:, 2]) / 2, (r[:, 3] + r[:, 4]) / 2)
    t = W / 2 ** 8
    bx = np.floor((np.asarray(cx) + W / 2) / t).astype(np.int64)
    by = np.floor((W / 2 - np.asarray(cy)) / t).astype(np.int64)
    w3857 = np.maximum(r[:, 2] - r[:, 1], r[:, 4] - r[:, 3]) * 1.26  # 대략(UTM->3857 축척 1/cos(lat))
    return fid, bx, by, w3857


def write_zoom(wkb, fields, fnames, z, outdir, simplified):
    opts = dict(MINZOOM=z, MAXZOOM=z, EXTENT=4096, BUFFER=80, COMPRESS="YES", TYPE="overlay", NAME=KEY,
                TILING_SCHEME="EPSG:3857,-20037508.343,20037508.343,40075016.686")
    if simplified:
        opts.update(MAX_SIZE=MAX_SIZE_SIMPLIFIED, MAX_FEATURES=100_000_000)
    else:
        opts.update(MAX_SIZE=1_000_000_000, MAX_FEATURES=100_000_000)
    write(str(outdir), wkb, fields, fnames, driver="MVT", layer=KEY, crs="EPSG:3857",
          geometry_type="MultiLineString", **{k: str(v) for k, v in opts.items()})


def move_tiles(src_root, z, owner):
    """owner=None: 전부 이동, (bx,by): z8 구역에 속한 타일만 이동. 같은 구역 재실행이면 덮어씀."""
    n = skipped = 0
    zdir = src_root / str(z)
    if not zdir.is_dir():
        return 0, 0
    sh = z - 8
    for xd in zdir.iterdir():
        x = int(xd.name)
        for f in xd.iterdir():
            y = int(f.stem)
            if owner is not None and not (x >> sh == owner[0] and y >> sh == owner[1]):
                skipped += 1
                continue
            dst = OUT / str(z) / str(x) / f.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.replace(f, dst)  # 소유 구역은 하나뿐이므로 재실행 시 덮어써도 안전
            n += 1
    return n, skipped


def run_chunk(z_list, owner, fid, bx, by, w3857):
    t0 = time.time()
    if owner is None:   # 전국 단위(z<8): 격자 1칸 이상 선만
        z = z_list[0]
        sel = fid[w3857 >= 0.7 * unit_of(min(z_list))]
        bbox = None
    else:
        sel, bbox = None, block_bbox_utm(*owner)
    kw = dict(columns=COLS)
    if sel is not None:
        meta, _, geom, fields = read(str(SRC), fids=sel, **kw)
    else:
        meta, _, geom, fields = read(str(SRC), bbox=bbox, **kw)
    if len(geom) == 0:
        return
    g = from_wkb(geom)
    del geom
    g = shapely.transform(g, lambda a: np.column_stack(TR_FWD.transform(a[:, 0], a[:, 1])))
    print(f"  chunk {owner}: {len(g)} features read {time.time()-t0:.0f}s", flush=True)
    for z in z_list:
        t1 = time.time()
        simplified = z in SIMPLIFIED
        gz, n_inf = (g, 0) if simplified else inflate(g, z)
        wkb = shapely.to_wkb(gz)
        wd = WORK / f"{KEY}_z{z}_{'nat' if owner is None else '%d_%d' % owner}"
        if wd.exists():
            shutil.rmtree(wd)
        write_zoom(wkb, fields, meta["fields"], z, wd, simplified)
        n, sk = move_tiles(wd, z, owner)
        shutil.rmtree(wd, ignore_errors=True)
        tmpdb = Path(str(wd) + ".temp.db")
        if tmpdb.exists():
            tmpdb.unlink()
        print(f"  z{z} chunk {owner}: inflated {n_inf}, tiles kept {n}, foreign tiles dropped {sk}, {time.time()-t1:.0f}s", flush=True)
    del g


def main():
    global SRC, OUT, WORK
    ap = argparse.ArgumentParser(description="전국 상수관망 MVT 변환 (z6~z10 간략화, z11~z17 원본 전량)")
    ap.add_argument("zooms", help="쉼표로 구분한 줌 목록, 예: 6,7,8,9,10,11,12")
    ap.add_argument("chunks", nargs="?", default="all", help="all(기본) 또는 z8 구역 x/y 목록, 예: 218/99,219/101")
    ap.add_argument("--src", default=str(SRC), help="입력 gpkg (기본 <ROOT>/건기연상수관망/건기연상수관망.gpkg)")
    ap.add_argument("--out-dir", default=str(OUT), help="출력 타일 폴더 (기본 <ROOT>/backend/data/tiles_national/kict_national)")
    ap.add_argument("--work-dir", default=str(WORK), help="임시 작업 폴더 (기본 <ROOT>/backend/data/tiles_national_work)")
    a = ap.parse_args()
    SRC, OUT, WORK = Path(a.src), Path(a.out_dir), Path(a.work_dir)
    zooms = [int(v) for v in a.zooms.split(",")]
    sel_chunks = None if a.chunks == "all" else [tuple(int(v) for v in s.split("/")) for s in a.chunks.split(",")]
    WORK.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    fid, bx, by, w3857 = load_index()
    print(f"index loaded {len(fid)} {time.time()-t0:.0f}s", flush=True)
    low = [z for z in zooms if z < 8]
    high = [z for z in zooms if z >= 8]
    # z<8 : 전국 한 번에
    for z in low:
        if (OUT / str(z)).is_dir():
            print(f"z{z}: 이미 있음 -> 건너뜀", flush=True)
            continue
        run_chunk([z], None, fid, bx, by, w3857)
    if high:
        blocks, counts = np.unique(np.column_stack([bx, by]), axis=0, return_counts=True)
        order = np.argsort(-counts)
        todo = [tuple(int(v) for v in blocks[i]) for i in order]
        if sel_chunks is not None:
            todo = [b for b in todo if b in sel_chunks]
        print(f"chunks {len(todo)} (features by center, top: {[(tuple(blocks[i]), int(counts[i])) for i in order[:5]]})", flush=True)
        for b in todo:
            done_marker = WORK / f"done_{'_'.join(map(str, high))}_{b[0]}_{b[1]}"
            if done_marker.exists():
                print(f"chunk {b}: 이미 완료 -> 건너뜀", flush=True)
                continue
            run_chunk(high, b, fid, bx, by, w3857)
            done_marker.write_text("ok")
    print(f"ALL DONE {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
