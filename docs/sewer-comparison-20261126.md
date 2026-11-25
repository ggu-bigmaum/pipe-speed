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
