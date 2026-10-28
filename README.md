# 서울 상수관로 화면속도 시연 소스

네이버 지도 위에 상수관로 2종(서울시, 건기연)을 벡터타일로 올리고, 지도를 움직일 때 관로가 그려지는 시간을 재는 웹 화면입니다.

## 구성

| 파일 | 역할 |
|---|---|
| `frontend/index.html` | 화면 |
| `frontend/app.js` | 지도·관로 표시, 시간 측정, 결과 표, 엑셀·CSV 저장 |
| `frontend/style.css` | 화면 스타일 |
| `frontend/config.js` | 설정: 네이버 Client ID, 서버 주소, 관로 목록·색 |
| `backend/app.py` | 서버. 화면(`/app/`)과 타일(`/tiles/`)을 같은 주소로 제공 |
| `backend/start_server.ps1` | Windows 서버 시작 |
| `backend/stop_server.ps1` | Windows 서버 종료 |
| `backend/start_server.sh` | 리눅스 서버 시작 |
| `backend/requirements-server.txt` | 서버 실행에 필요한 패키지 (Flask, flask-cors, waitress) |
| `backend/requirements.lock.txt` | 작성 PC 전체 패키지 목록 (참고용) |
| `backend/data/tiles_full/` | 관로 벡터타일 (552 MiB) |
| `tools/convert_mvt_full.py` | gpkg → 벡터타일 변환 스크립트 |
| `tools/README_타일변환.md` | 변환 방법 |
| `info.txt` | 네이버 지도 Client ID와 등록된 서비스 URL |

## 설치 순서

### ① 저장소 받기

```
git clone <저장소 주소> 상수관로_화면속도
cd 상수관로_화면속도
```

### ② 타일 데이터 확인

타일은 저장소에 들어 있습니다. 아래 구조인지 확인만 합니다.

```
backend/data/tiles_full/{layer}/{z}/{x}/{y}.pbf
```

- `{layer}` = `sa001`(서울시), `wtl_pipe_lm`(건기연)

### ③ Python 패키지 설치

Python 3.13에서 확인했습니다.

Windows (`start_server.ps1`은 `backend\.venv`의 Python을 씁니다)

```
cd backend
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-server.txt
```

리눅스

```
cd backend
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-server.txt
```

### ④ config.js에 네이버 Client ID 넣기

`frontend/config.js`

```
NAVER_CLIENT_ID: 'YOUR_NAVER_CLIENT_ID',
API_BASE: '',
```

- `NAVER_CLIENT_ID`: 저장소 맨 위 `info.txt`의 Client ID를 넣습니다. Client Secret은 넣지 않습니다.
- 네이버 클라우드 콘솔의 Maps Application에 **Web 서비스 URL**로 접속 주소를 등록해야 지도가 뜹니다. 예: `http://서버주소:8000`. 등록은 키 소유 계정에서만 할 수 있습니다.
- `API_BASE`: 비워 두면 화면과 같은 주소의 서버에서 타일을 받습니다. 타일 서버가 따로 있을 때만 전체 주소를 넣습니다.

### ⑤ 서버 실행

Windows (PowerShell)

```
powershell -NoProfile -File backend\start_server.ps1
powershell -NoProfile -File backend\stop_server.ps1     # 종료
```

- 이 PC에서만 접속됩니다(127.0.0.1). 포트 변경: `start_server.ps1 -Port 8001`

리눅스 (`backend` 폴더, 가상환경을 켠 상태)

```
bash start_server.sh
# 또는
PYTHONUTF8=1 BACKEND_HOST=0.0.0.0 BACKEND_PORT=8000 python app.py
```

- `start_server.sh`는 기본으로 `0.0.0.0:8000`에서 받아 다른 PC에서도 접속됩니다.
- `BACKEND_HOST`를 지정하지 않고 `python app.py`로 실행하면 이 PC에서만 접속됩니다.

### ⑥ 브라우저 접속

```
http://localhost:8000/app/
```

서버에 올린 경우 `http://서버주소:8000/app/`

## 사용법

1. **관로 선택**: 상단 관로 목록에서 볼 관로를 켜고 끕니다. 처음에는 두 관로가 모두 켜져 있습니다. 창 크기를 바꿨으면 `서울 전체 범위 맞추기`를 한 번 누릅니다.
2. **실시간 측정**: 지도를 확대·축소·이동하면 `관로 표시 시간` 카드에 바로 결과가 나옵니다.
3. **자동 비교**: `반복 횟수`를 정하고, 필요하면 `캐시 초기화`를 켠 뒤 `▶ 시나리오 실행`을 누릅니다. `중단`으로 멈출 수 있습니다.
   - 시나리오: 서울 전체를 100%로 하여 200·400·800·1600% 순서로 확대한 뒤 역순으로 축소하고, 400%에서 강북구·강남구·강동구·강서구·서울 중앙으로 이동합니다.
   - 캐시 초기화를 켜면 단계마다 관로 타일을 새로 요청합니다.
4. **결과 보기**: 오른쪽 아래 `결과 보기`를 누르면 `개별 기록`(최근 100건)과 `자동 비교 기록` 탭이 나옵니다.
5. **엑셀·CSV**: 각 표의 `엑셀`, `CSV` 버튼으로 화면의 표를 그대로 저장합니다. `기록 비우기`로 기록을 지웁니다.

## 결과 표 열

개별 기록 (8열)

| 열 | 뜻 |
|---|---|
| 번호 | 기록 순번 |
| 동작 | 확대·축소·이동 |
| 배율 | 서울 전체 화면 = 100% 기준 배율 |
| 관로 | 측정한 관로 |
| 캐시 초기화 | 측정 전에 관로 타일 캐시를 비웠는지 |
| 관로 표시 시간 ms | 지도 조작이 끝난 뒤부터 관로가 다 그려질 때까지 |
| 요청 수 | 관로 타일 요청 건수. 빈 타일 포함 |
| 전송 용량 MiB | 관로 타일 압축 전송량 |

자동 비교 기록 (8열)

| 열 | 뜻 |
|---|---|
| 단계 | 시나리오 단계 (배율·이동 위치) |
| 관로 | 측정한 관로 |
| 캐시 초기화 | 캐시 초기화 여부 |
| 반복 | 평균에 들어간 횟수 |
| 관로 표시 시간 평균 ms | 관로 표시 시간의 평균 |
| 최소~최대 ms | 관로 표시 시간의 최솟값과 최댓값 |
| 요청 수 | 요청 수 평균. 빈 타일 포함 |
| 전송 용량 MiB | 압축 전송량 평균 |
