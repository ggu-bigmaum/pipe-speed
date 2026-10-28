# 상수관로 벡터타일 변환

상수관로 gpkg 2종을 지도용 벡터타일(MVT)로 바꾸는 스크립트입니다. 시연 화면이 쓰는 `tiles_full` 타일을 이 스크립트로 만들었습니다.

## 구성

| 파일 | 역할 |
|---|---|
| `tools/convert_mvt_full.py` | 변환 스크립트 |
| `README_타일변환.md` | 이 문서 |

## 입력

| 레이어 이름 | 파일 | 원본 건수 | 남기는 속성 |
|---|---|---|---|
| `sa001` (서울시 상수관로) | `SA001.gpkg` | 1,444,804 | PLINE_ID, PLINE_CALB, PLINE_MTRQ |
| `wtl_pipe_lm` (건기연 상수관로) | `WTL_PIPE_LM.gpkg` | 1,445,440 | FTR_IDN, STD_DIP, MOP_CDE |

- 원본 좌표계 EPSG:32652를 EPSG:3857로 바꿔 타일을 만듭니다.
- 기하는 선(MultiLineString)이며 단순화하지 않습니다.

## 도구

- Python 3.13
- pyogrio 0.13.0 (GDAL 3.12.4 포함, GDAL의 MVT 드라이버 사용), shapely 2.2.0, pyproj 3.8.0, numpy 2.5.4
- 별도 GDAL·tippecanoe 설치는 필요 없습니다.

```
python -m venv .venv
.venv\Scripts\python -m pip install pyogrio==0.13.0 shapely==2.2.0 pyproj==3.8.0 numpy==2.5.4
```

## 폴더 구조와 실행 위치

저장소에서는 스크립트가 `tools/`에 있으므로 **경로 옵션 3개를 모두 줍니다.** 저장소 맨 위에서 실행하고, gpkg 2개는 `상수샘플/`에 둡니다. gpkg는 저장소에 없습니다.

```
저장소/
├─ 상수샘플/              ← gpkg 2개를 여기에 둠
│   ├─ SA001.gpkg
│   └─ WTL_PIPE_LM.gpkg
├─ tools/convert_mvt_full.py
└─ backend/data/
    ├─ tiles_full/          ← 결과
    └─ tiles_full_work/     ← 작업용 임시 폴더
```

## 실행 예시

```
# 저장소 맨 위에서
python tools/convert_mvt_full.py sa001 10,11,12,13,14,15,16,17 --src-dir 상수샘플 --out-dir backend/data/tiles_full --work-dir backend/data/tiles_full_work
python tools/convert_mvt_full.py wtl_pipe_lm 10,11,12,13,14,15,16,17 --src-dir 상수샘플 --out-dir backend/data/tiles_full --work-dir backend/data/tiles_full_work

# 낮은 줌 (저장소 타일에 포함됨)
python tools/convert_mvt_full.py sa001 0,1,2,3,4,5,6 --src-dir 상수샘플 --out-dir backend/data/tiles_full --work-dir backend/data/tiles_full_work

# 도움말
python tools/convert_mvt_full.py --help
```

- 저장소에 이미 타일이 있으므로 다시 만들려면 다른 `--out-dir`을 쓰세요. 같은 줌 폴더가 있으면 건너뜁니다.

- 인자: `레이어 줌목록 [건수]`. 건수를 주면 앞쪽 N건만 시험 변환하고 기본 출력은 `tiles_full_test`가 됩니다.
- 결과 폴더에 그 줌 폴더가 이미 있으면 건너뜁니다. 덮어쓰지 않습니다.
- 레이어 하나를 메모리에 모두 올려 변환합니다.

## 출력

```
tiles_full/
├─ sa001/
│   ├─ {z}/{x}/{y}.pbf        gzip 압축된 MVT, XYZ 방식, EPSG:3857
│   └─ metadata_z{z}.json     줌별 GDAL 메타데이터
└─ wtl_pipe_lm/ (같은 구조)
```

- 타일 설정: 격자 4096, 버퍼 80, 타일 크기·건수 상한 사실상 없음
- MVT 레이어 이름 = 레이어 이름(`sa001`, `wtl_pipe_lm`)

## 줌별 소요 시간

작성 PC에서 두 레이어를 동시에 돌린 실측값입니다. 단위는 초이며 타일 쓰기 시간입니다.

| 줌 | sa001 | wtl_pipe_lm |
|---|---|---|
| 0 | 346 | 319 |
| 1 | 374 | 345 |
| 2 | 363 | 364 |
| 3 | 424 | 420 |
| 4 | 398 | 396 |
| 5 | 440 | 440 |
| 6 | 396 | 393 |
| 7~9 | 기록 없음 | 기록 없음 |
| 10 | 326 | 322 |
| 11 | 367 | 355 |
| 12 | 385 | 384 |
| 13 | 390 | 391 |
| 14 | 463 | 468 |
| 15 | 439 | 461 |
| 16 | 480 | 517 |
| 17 | 511 | 557 |

- 실행마다 원본 읽기와 좌표 변환에 9~32초가 더 걸렸습니다.
- 출처: 변환 로그 `full_sa001*.log`, `full_wtl*.log`

## 짧은 선 처리

- 줌마다 격자 1칸 크기가 정해집니다. 격자 2칸보다 짧은 선은 그대로 두면 한 점으로 뭉개져 타일에서 빠집니다.
- 이런 선은 같은 위치(중심)와 같은 방향으로 길이 3칸인 선으로 바꿔 남깁니다. 그래서 원본 선이 모두 타일에 들어갑니다.
- 바뀐 선은 화면에서 1픽셀 미만입니다.
- 바뀐 선은 원래 길이가 아니므로 **길이 계산에는 쓰지 마세요.**
- 그 밖의 선은 원본 모양 그대로입니다.

## 검증 결과

- 타일을 풀어 원본 ID와 비교했습니다. z10~z17 두 레이어 모두 원본 고유 ID 100% 포함, 누락 0건입니다. z0~z6도 같습니다.
- sa001은 ID가 비어 있는 원본 1행과 ID가 중복된 15쌍이 있어 ID로는 구분하지 못합니다.

## 만들지 않은 줌

- z7~z9는 만들지 않았습니다. 시연 화면의 최소 줌이 서울 전체 화면이라 이 줌은 요청되지 않습니다.
