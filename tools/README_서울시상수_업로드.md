# 서울시 상수관망 벡터타일 추가

서울시 상수 `sa001`을 `/app/` 왼쪽 벡터타일 목록에 추가한다. 건기연 상수와 TIF 목록은 유지한다.

## 데이터 출처와 범위

- 전달받은 `수열관로테스트서버/seoul-water-pipe-speed-main/backend/data/tiles_full/sa001`의 기존 MVT 파일을 사용한다.
- 연속된 줌 10~17의 11,455개 파일을 그대로 복사한다. 좌표·속성·타일 내용을 재생성하거나 변경하지 않는다.
- 전달본에는 줌 0~6도 있지만 7~9가 없다. 중간에 빈 화면이 생기지 않도록 연속된 10~17 세트만 배치한다. 기존 API가 최소 줌 10을 반환하며, 현재 지도는 그보다 축소된 화면에서도 줌 10 타일을 표시한다. 서울 전체 표시에서는 파일 수신·해석이 오래 걸릴 수 있으므로 실제 표시 시간을 확인한다.
- 변환 코드는 원본 프로젝트의 `tools/convert_mvt_full.py`에 있다. 이 코드는 매우 짧은 선을 표시용 최소 길이로 늘리는 처리가 있으므로 향후 하수 변환에 그대로 적용하지 말고 검토한다.
- 이번 작업에서는 건기연 하수 보정본을 변환하지 않는다.
- 줌 10의 큰 타일에는 약 119만 피처가 들어 있다. 현재 지도에서 하나의 Canvas 그리기 경로로 처리하면 타일이 투명하게 빠지는 현상을 확인했다. 피처 16,384개씩 동일한 색·굵기로 나누어 그리도록 보완했다. 타일의 원본 좌표·속성·개수는 유지한다.
- `backend/data/`는 Git에 추가하지 않는다. 파일은 직접 전송한다.

## 서버 반영

Windows PowerShell에서:

```powershell
scp "C:\Users\admin\Desktop\빅마음\03_Dev\02_Web\pipe-speed\backend\data\sa001_tiles_z10_17_20261117.tar" "bigmaum99@220.95.232.173:/home/bigmaum99/demos/pipe-speed/backend/data/"
```

서버 터미널에서:

```bash
cd /home/bigmaum99/demos/pipe-speed &&
git pull --ff-only origin main &&
mkdir -p backend/data/tiles_full &&
tar --keep-old-files -xf backend/data/sa001_tiles_z10_17_20261117.tar -C backend/data/tiles_full &&
sudo systemctl restart pipe-speed &&
systemctl status pipe-speed --no-pager
```

`--keep-old-files`는 기존 동명 타일을 덮어쓰지 않는다. 기존 파일이 있다는 오류가 나면 출력 내용을 확인하고 중복 여부를 점검한다.

브라우저에서 `/app/`를 새로고침한 뒤 **서울시 상수관망**을 선택한다. `/tiles/sa001/meta.json`의 `minzoom=10`, `maxzoom=17` 및 실제 타일 표시를 확인한다. 관로가 겹쳐 보이지 않도록 처음 확인할 때는 다른 레이어를 꺼 둔다.
