# 하수 관로별 형상 비교

사용자가 승인한 기준: ID 동일 조건 없이 EPSG:32652의 비교용 XY를 소수점 셋째 자리로 반올림(half-away-from-zero)한 뒤 전체 선형 GEOS equals. 원본·보정본 좌표와 ID는 유지한다. 기존 상수 판정과 6개 업무 비교상태는 변경하지 않는다.

원본 객체 키는 자료명 + 해당 GPKG의 고유 id이며 타일 변환 manifest의 원본 SHA256을 검증한다. 원본 ORG_IDN/IDN은 속성으로 보존하고 여러 대응 관계를 모두 pairs에 저장한다. 원본 타일의 id로 결과를 조회하며 타일 선형으로 판정하지 않는다.

결과: 건기연409,087개 중383,537개, 서울시406,651개 중383,993개 일치. 대응384,095쌍. 반올림 후 무효·길이0 건기연59개는 별도 precision_review이며 일치여부NULL이다. 미일치는 KICT_ONLY/LOCAL_ONLY/CONFLICT로 변환하지 않는다.

재현: `tools/comparison/build_sewer.py --data <backend/data> --out <새 결과 폴더>` (분석환경의 numpy/shapely 필요). 운영 서버는 완성 SQLite와manifest만 사용하며 GIS 분석 라이브러리나 PostgreSQL은 필요하지 않다.

결과 파일: `backend/data/comparison/sewer/sewer_comparison.sqlite`, `manifest.json` (데이터는 Git 제외).
현재 생성본: C:/Users/admin/Documents/ChatGPT/도미온/outputs/sewer_comparison_package_20261126/
SQLite111,603,712bytes, SHA256 cd781fd1b3558588ab0845b58cd27e992d8b1ad338a0440ab88bd8e775db1800.
빌드 전후 두 원본 SHA256 동일, 관로수·참조 무결성 확인. 반대 방향·등가 분할·중복 대응·교차·부분 선형·반올림 경계·축약선 fixture 통과.

이 결과는 정밀도 통일 후 형상 비교이며 물리적 동일 시설이나 보정 정확도를 보증하는 비율이 아니다. 다른 PC 실행용 묶음은 만들지 않는다.

## 화면 및 검증

- 관로 클릭: 관로별 반올림 형상 일치 여부, 원본 ID, 상대 관로 ID, 판정 규칙을 조회한다. 대응이 여러 개면 20개씩 모두 조회한다. 비교값은 원본 객체 키로 연결한 별도 SQLite에 저장하며 원본 GPKG·MVT 좌표는 바꾸지 않는다.
- 건기연 서울 하수 보정본 + 서울시 하수를 함께 켰을 때만 일치 관로를 남색·흰 외곽선으로 강조한다. 한쪽 해제 시 강조·선택 해제. 서울 외 하수는 이 조합에 들어가지 않는다. 상수와 하수의 두 조합은 독립 동작한다.
- Python 기존·추가 회귀 200개와 패키지 무결성 3개, JS 44개 통과. 상수·하수 16개 레이어 조합, 지연 응답, 틀린 타일/버전/객체 수, 여러 대응, 무효 선, 교차·부분 선형을 검증했다.
- 실제 localhost 지도: 서울시 IDN 05-08463 ↔ 건기연 ORG_IDN 61434085 조회, 일치 강조 및 한쪽 끄기, 상수·하수 동시 표시 확인. 브라우저 error 로그 없음.
- 실제 API: 건기연 객체1 / ORG_IDN 61368515 ↔ 서울시 객체343557 / IDN 21-06732. 저장된 전체 집계와 metadata 일치.
- 화면 근거: C:/Users/admin/Documents/ChatGPT/도미온/outputs/sewer_comparison_package_20261126/sewer-click-highlight.png

## 단계별 커밋

1. 4402c24 — 비교용 반올림 계산·관로별 값·다중 대응 저장.
2. 41e5f68 — 원본 객체와 연결한 클릭 조회.
3. 이 문서를 포함하는 커밋 — 실제 타일 강조 활성화·회귀 및 화면 검증.

로컬 반영 대상: C:/Users/admin/Desktop/빅마음/03_Dev/02_Web/pipe-speed. 서버 배포·푸시는 별도이며 이번 작업에 포함하지 않는다. 서버에 적용할 때에는 코드 외에 backend/data/comparison/sewer의 SQLite와 manifest도 필요하다.
