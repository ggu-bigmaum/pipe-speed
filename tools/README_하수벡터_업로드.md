# 하수 벡터타일 2종 서버 반영

`/app/` 왼쪽 벡터타일 목록에 다음 두 레이어가 등록되어 있다. 기존 상수와 TIF 레이어는 그대로 사용한다.

| 화면 이름 | 데이터 폴더 | 줌 | 타일 파일 수 |
| --- | --- | --- | ---: |
| 건기연 하수(서울·보정본) | `backend/data/tiles_full/kict_sewer_seoul` | 6~17 | 11,309 |
| 서울시 하수관망 | `backend/data/tiles_full/sb001` | 10~17 | 11,289 |

건기연 하수는 녹색, 서울시 하수는 보라색이다. 선택한 레이어는 범례와 속도 측정에 함께 표시된다. 서버에 데이터가 없으면 해당 선택칸에 `자료 없음 · 서버 데이터 연결 필요`가 표시된다.

타일은 `{레이어}/{줌}/{x}/{y}.pbf` 구조다. 기존 하수 TIF의 `backend/data/distance/kict_sewer_seoul`과 별도 위치다. 데이터는 Git에 포함하지 않는다.

## 1. Windows PowerShell에서 데이터 전송

두 폴더를 묶은 전송 파일 `sewer_vector_tiles_20261118.tar`가 로컬 `backend/data`에 준비되어 있다. 크기는 258,698,752 bytes이며 원본 GPKG는 포함하지 않는다.

```powershell
scp "C:\Users\admin\Desktop\빅마음\03_Dev\02_Web\pipe-speed\backend\data\sewer_vector_tiles_20261118.tar" "bigmaum99@220.95.232.173:/home/bigmaum99/demos/pipe-speed/backend/data/"
```

## 2. 서버 터미널에서 코드와 데이터 반영

```bash
cd /home/bigmaum99/demos/pipe-speed &&
git pull --ff-only origin main &&
mkdir -p backend/data/tiles_full &&
tar -xf backend/data/sewer_vector_tiles_20261118.tar -C backend/data/tiles_full &&
sudo systemctl restart pipe-speed &&
systemctl status pipe-speed --no-pager
```

아카이브에는 `kict_sewer_seoul/`과 `sb001/`만 들어 있으므로 해당 두 폴더에 타일을 배치한다. 같은 경로에 동명 파일이 있으면 이번 변환본으로 덮어쓴다. 기존 상수 및 거리 래스터 폴더는 건드리지 않는다. 별도 패키지 설치나 프런트엔드 빌드는 필요하지 않다.

## 3. 표시 확인

브라우저의 `/app/`에서 `Ctrl+Shift+R`로 새로고침한 뒤 두 하수 선택칸을 각각 켠다.

- `/tiles/kict_sewer_seoul/meta.json`: `minzoom=6`, `maxzoom=17`, `count=409087`
- `/tiles/sb001/meta.json`: `minzoom=10`, `maxzoom=17`, `count=406651`

타일 파일 수 확인:

```bash
find backend/data/tiles_full/kict_sewer_seoul -type f -name '*.pbf' | wc -l
find backend/data/tiles_full/sb001 -type f -name '*.pbf' | wc -l
```

각각 `11309`, `11289`가 나와야 한다. 원본 DB나 GPKG는 서버에서 읽지 않으며 지도는 변환된 PBF 타일 파일을 사용한다.
