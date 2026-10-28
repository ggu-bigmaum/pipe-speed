"""상수관로 벡터타일 서버 (기본 http://localhost:8000). 규약은 backend/HANDOFF.md 참조.
 GET /tiles/{layer}/{z}/{x}/{y}.pbf   (XYZ, EPSG:3857, gzip 사전압축 저장, 타일 없으면 204)
 GET /tiles/{layer}/meta.json
 GET /app/*                           (frontend 정적 서빙)
 GET /health

실행: PYTHONUTF8=1 [BACKEND_HOST=0.0.0.0] [BACKEND_PORT=8000] python app.py     (의존: Flask, flask-cors, waitress)
 - BACKEND_HOST 기본 127.0.0.1(이 PC 전용, 가능하면 ::1 도 함께 수신). 외부 접속을 허용하려면 0.0.0.0.
 - 타일 폴더: data/tiles_full/{layer}/{z}/{x}/{y}.pbf 하나만 있어도 동작. (선택) data/tiles, data/tiles_low 가 있으면 tiles_full 에
   없는 줌에 한해 기존 타일을 서빙하며, 없으면 해당 줌은 204(빈 타일).
 - 프론트 폴더: frontend/ (환경변수 FRONTEND_DIR 로 지정 가능, 기본은 backend/frontend 또는 ../frontend)
"""
import os, json, gzip, time, threading, socket
from pathlib import Path
from flask import Flask, Response, abort, request, jsonify, send_from_directory
from flask_cors import CORS

ROOT = Path(__file__).resolve().parent
TILES_FULL = ROOT / "data" / "tiles_full"  # 전량 보존 타일(줌별 폴더 존재 여부로 완전 줌 판단)
TILES = ROOT / "data" / "tiles"            # (선택) 기존 z12~17, 일부 선 누락
TILES_LOW = ROOT / "data" / "tiles_low"    # (선택) 기존 z0~11, 일부 선 누락
LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)
LAYERS = {"sa001": "SA001.gpkg/_", "wtl_pipe_lm": "WTL_PIPE_LM.gpkg/wtl_pipe_lm"}
FEATURE_COUNT = {"sa001": 1444804, "wtl_pipe_lm": 1445440}  # sqlite count(*) 로 확인한 원본 건수
# meta.json 값(타일 폴더에 의존하지 않도록 고정). bounds: WGS84 [west,south,east,north]
BOUNDS = {"sa001": [126.7954714, 37.4338554, 127.2225354, 37.6939493],
          "wtl_pipe_lm": [126.7984784, 37.4338554, 127.2225354, 37.6939493]}
FIELDS = {"sa001": {"PLINE_ID": "String", "PLINE_CALB": "Number", "PLINE_MTRQ": "String"},
          "wtl_pipe_lm": {"FTR_IDN": "Number", "STD_DIP": "Number", "MOP_CDE": "String"}}
ZOOMS = range(0, 18)

_fe = os.environ.get("FRONTEND_DIR")
FRONTEND = Path(_fe) if _fe else (ROOT / "frontend" if (ROOT / "frontend").is_dir() else ROOT.parent / "frontend")

app = Flask(__name__)
CORS(app, expose_headers=["Server-Timing", "X-Server-Ms", "X-Tile-Bytes", "X-Tile-Set", "Content-Length"])
_log_lock = threading.Lock()
_log_f = open(LOGS / "requests.csv", "a", encoding="utf-8", buffering=1)
if _log_f.tell() == 0:
    _log_f.write("ts_iso,method,path,status,bytes,server_ms,note\n")


def log_req(path, status, nbytes, ms, note=""):
    with _log_lock:
        _log_f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')},{request.method},{path},{status},{nbytes},{ms:.3f},{note}\n")


def _zoom_dirs(layer):
    full = [z for z in ZOOMS if (TILES_FULL / layer / str(z)).is_dir()]
    legacy = [z for z in ZOOMS if z not in full and ((TILES if z >= 12 else TILES_LOW) / layer / str(z)).is_dir()]
    return full, legacy


def _meta(layer):
    if layer not in LAYERS:
        abort(404)
    full, legacy = _zoom_dirs(layer)
    have = sorted(full + legacy)
    if not have:
        abort(404)
    return {
        "layer": layer,
        "source": LAYERS[layer],
        "bounds": BOUNDS[layer],
        "minzoom": have[0],
        "maxzoom": have[-1],
        "count": FEATURE_COUNT[layer],
        "tile_url": f"{request.host_url.rstrip('/')}/tiles/{layer}/{{z}}/{{x}}/{{y}}.pbf",
        "scheme": "xyz",
        "tile_extent": 4096,
        "vector_layer_id": layer,
        "fields": FIELDS[layer],
        # 전량 보존 타일이 존재하는 줌(고유 ID 100% 검증) / 그 외 줌(기존 타일이 있으면 일부 선 누락, 없으면 빈 타일)
        "full_zooms": full,
        "incomplete_zooms": [z for z in ZOOMS if z not in full],
    }


@app.get("/health")
def health():
    return jsonify(ok=True, pid=os.getpid(), layers=list(LAYERS))


@app.get("/tiles/<layer>/meta.json")
def meta(layer):
    t0 = time.perf_counter()
    r = jsonify(_meta(layer))
    ms = (time.perf_counter() - t0) * 1000
    r.headers["X-Server-Ms"] = f"{ms:.3f}"
    log_req(request.path, 200, len(r.get_data()), ms)
    return r


@app.get("/tiles/<layer>/<int:z>/<int:x>/<int:y>.pbf")
def tile(layer, z, x, y):
    t0 = time.perf_counter()
    if layer not in LAYERS:
        abort(404)
    # tiles_full 에 해당 줌 폴더가 있으면 그것을, 없으면 (있다면) 기존 타일(일부 선 누락)을, 둘 다 없으면 204.
    full = (TILES_FULL / layer / str(z)).is_dir()
    p = (TILES_FULL if full else (TILES if z >= 12 else TILES_LOW)) / layer / str(z) / str(x) / f"{y}.pbf"
    try:
        with open(p, "rb") as f:
            data = f.read()  # gzip 사전압축된 MVT
    except FileNotFoundError:
        ms = (time.perf_counter() - t0) * 1000
        log_req(request.path, 204, 0, ms, "empty")
        r = Response(status=204)
        r.headers["X-Server-Ms"] = f"{ms:.3f}"
        r.headers["Cache-Control"] = "no-store"
        return r
    t_read = time.perf_counter()
    if "gzip" in request.headers.get("Accept-Encoding", "") and request.args.get("identity") != "1":
        body, enc = data, "gzip"
    else:
        body, enc = gzip.decompress(data), None
    ms = (time.perf_counter() - t0) * 1000
    r = Response(body, mimetype="application/x-protobuf")
    if enc:
        r.headers["Content-Encoding"] = enc
    r.headers["Cache-Control"] = "no-store"  # 측정 왜곡 방지(브라우저 캐시 사용 안 함)
    r.headers["X-Server-Ms"] = f"{ms:.3f}"
    r.headers["X-Tile-Bytes"] = str(len(body))
    r.headers["X-Tile-Set"] = "full" if full else "legacy-incomplete"
    r.headers["Server-Timing"] = f"read;dur={(t_read-t0)*1000:.3f}, total;dur={ms:.3f}"
    log_req(request.path, 200, len(body), ms, f"read_ms={(t_read-t0)*1000:.3f};enc={enc or 'identity'}")
    return r


@app.get("/")
def root_redirect():
    return Response(status=302, headers={"Location": "/app/"})


@app.get("/app/", defaults={"fname": "index.html"})
@app.get("/app/<path:fname>")
def frontend_static(fname):
    """frontend/ 정적 서빙. 같은 포트에서 페이지와 타일을 함께 제공."""
    r = send_from_directory(FRONTEND, fname)
    r.headers["Cache-Control"] = "no-cache"
    return r


def _listen(host, port):
    if host != "127.0.0.1":
        return f"{host}:{port}"
    # 기본(루프백): Windows 에서 localhost 가 ::1 을 먼저 시도해 거부되면 ~2초 지연되므로 가능하면 IPv6 루프백도 함께 수신.
    try:
        s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        s.bind(("::1", 0))
        s.close()
        return f"127.0.0.1:{port} [::1]:{port}"
    except OSError:
        return f"127.0.0.1:{port}"


if __name__ == "__main__":
    try:
        (ROOT / "server.pid").write_text(str(os.getpid()))
    except OSError:
        pass
    from waitress import serve
    host = os.environ.get("BACKEND_HOST", "127.0.0.1")
    port = int(os.environ.get("BACKEND_PORT", "8000"))  # 네이버 키 등록 URL 때문에 로컬 운영은 8000 고정
    serve(app, listen=_listen(host, port), threads=16, channel_timeout=600)
