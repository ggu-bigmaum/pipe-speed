# 하수 2종 벡터타일 변환

2026-11-17 사용자 확정 범위:

| 자료 | 입력 | 출력 | 줌 | 원본 피처 |
|---|---|---|---|---:|
| 건기연 서울 하수 보정본 | `backend/data/source/kict_sewer_seoul/SWL_PIPE_LM.gpkg`, `swl_pipe_lm` | `backend/data/tiles_full/kict_sewer_seoul` | 6~17 | 409,087 |
| 서울시 하수 | `backend/data/source/seoul_sewer/SB001.gpkg`, `sb001` | `backend/data/tiles_full/sb001` | 10~17 | 406,651 |

건기연 입력은 `public.swl_pipe_lm_11_roadtypes_final_20261117`의 전체 내보내기다. 서울시 입력은 `public."SB001"`의 64개 속성을 모두 보존한 내보내기다. SB001 DB 좌표는 EPSG:32625로 저장되어 있어, 이전에 검증한 내보내기 방식과 동일하게 `ST_Transform(geom,32652)`를 사용했다. DB와 입력 GPKG를 수정하지 않는다.

## 표시 정책

전달받은 `convert_mvt_full.py`의 `inflate()` 알고리즘을 사용자의 명시적 요청으로 유지한다. 해당 줌에서 너무 작은 선은 외접 사각형 중심을 기준으로 시작→끝 방향의 최소 표시 길이 직선으로 치환한다. 원본 GPKG 형상은 유지되며 타일 표시 형상에만 적용된다. `W=40075016.686`, `SMALL_UNITS=2`, `MIN_LEN_UNITS=3`, `EXTENT=4096`, `BUFFER=80`을 유지한다.

## 타일 속성

- 건기연: `id`, `org_idn`, `original_pipe_id`, `feature_role`, `correction_stage`, `roadtypes_stage`, `treatment_code`, `std_dip`, `std_hol`, `std_vel`
- 서울시: `id`, `IDN`, `TY_JL_CDE`, `MOP_CDE`, `HOL_STD`, `VEL_STD`, `SAT_MHE`, `END_MHE`
- 나머지 전체 속성은 입력 GPKG에 보존한다. `id`는 각 입력의 유일 행 ID이며 타일 경계에서 같은 ID가 여러 번 등장할 수 있다.

## 완료와 재개

- 각 줌의 모든 MVT에서 ID 합집합을 읽어 입력 ID 전체와 비교한다. 누락·추가 ID가 있으면 완료 게시 전에 중단한다.
- GDAL MVT reader는 압축해제 크기 10MiB 초과 타일을 건너뛸 수 있어 검증에는 공식 MVT protobuf 스키마(`mapbox_vector_tile.Mapbox.vector_tile_pb2`)를 사용한다.
- 각 줌 폴더 안의 `_complete.json`에 ID 검증 결과·치환 피처 수·파일 수·해시를 기록한다.
- 모든 줌이 끝나면 레이어 폴더에 `conversion_complete.json`을 기록한다. 생성 중인 임시 파일은 `backend/data/conversion_work/sewer_20261117` 안에만 존재한다.
- 재실행은 입력·정책·타일 해시가 일치하는 완료 줌만 건너뛴다. 무표식 기존 출력 폴더는 덮어쓰지 않는다.
- 실행 상태와 로그는 `backend/data/conversion_runs/sewer_20261117/`에 있다. `batch_status.json`의 `status=complete`와 두 레이어의 완료 파일을 확인한다.

## 로컬 실행 환경

Python 3.12, pyogrio 0.13.0 (GDAL 3.12.4), NumPy 2.5.4, Shapely 2.2.0, pyproj 3.8.0, mapbox-vector-tile 2.2.0, protobuf 6.33.6을 사용했다. 변환기는 Python 3.11 이상이 필요하다. 서버의 Python 3.9 서비스 환경과 별개인 로컬 오프라인 도구다.

`convert_sewer_mvt.py --help`에서 입력·출력 옵션을 확인할 수 있다. 데이터 파일은 Git 제외이며, 웹의 레이어 등록과 서버 데이터 전송은 별도 단계다.
