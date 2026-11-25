# 서울시 거리 자료 연결

서울시 상수와 하수는 건기연 자료와 별도의 폴더·경로를 사용한다.

| 자료 | 폴더 | 조회 경로 | grid_index.json의 dataset_id |
| --- | --- | --- | --- |
| 서울시 상수 | `backend/data/distance/seoul_water/` | `/distance/seoul_water/` | `seoul_water` |
| 서울시 하수 | `backend/data/distance/seoul_sewer/` | `/distance/seoul_sewer/` | `seoul_sewer` |

각 폴더에는 `pipe_case1_m.tif`, `pipe_case2_m.tif`, `pipe_dist_result.csv`, `grid_index.json` 네 파일이 필요하다. 기존 건기연 자료를 복사하거나 이름만 바꾸어 대체하지 않는다. `dataset_id`는 확인된 서울시 원본으로 산출한 자료의 출처 식별자이며, 실제 원본 확인을 대신하지 않는다.

`grid_index.json`은 기존 서울 격자 형식을 사용하되 최상위에 위 표의 `dataset_id`를 반드시 넣는다. 필수 필드는 `crs`(`EPSG:32652`), `width`, `height`, `origin`(`[x, y]`), `pixel_size`(`[x, y]`), `nodata`, `cells`(`[["grid_id", col, row], ...]`)다. 두 TIF의 좌표계·크기·원점·픽셀 크기·NoData가 이 정보와 같아야 한다.

CSV는 UTF-8이며 필수 열은 `grid_id,case1_m,case2_m,pass_flag,pipe_id,near_x,near_y,near_lon,near_lat`다. `grid_id`와 `pipe_id`는 문자열 ID를 허용한다. CSV의 격자 ID·거리값·유효 셀 수가 두 TIF 및 격자 대응표와 일치해야 한다. `pass_flag`는 0 또는 1이다.

`/availability`는 누락 파일이 있으면 `waiting`(자료 대기), 출처·격자·result 검증 실패 시 `invalid`, 검증 성공 시 `ready`를 반환한다. 화면은 실패한 서울시 그룹의 Case만 비활성화한다. 첫 검증은 파일을 읽으며 같은 서버 프로세스 내 변경 없는 정상 파일은 크기·수정시간 확인만 수행한다. 별도 검증 캐시 파일은 만들지 않는다.

네 파일을 모두 준비하거나 교체한 뒤 화면을 새로고침한다. `자료 대기`는 파일 미배치, `확인 필요`는 화면에 표시된 검증 사유를 확인한다. 기존 `/distance/`의 건기연 서울 상수 XLSX 경로는 그대로 유지된다.
