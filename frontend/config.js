// 설정 분리 파일. Client Secret 은 지도 JS 렌더링에 쓰지 않으므로 넣지 않는다.
window.APP_CONFIG = {
  NAVER_CLIENT_ID: 'YOUR_NAVER_CLIENT_ID',   // 네이버 지도 Client ID (서비스 URL: http://localhost, http://localhost:8000 등록)
  API_BASE: '',                    // '' = 페이지와 같은 오리진(http://localhost:8000). 백엔드가 다른 곳이면 전체 URL
  // 목록 순서 = 화면 선택 목록 순서 = 지도 겹침 순서(위쪽 항목이 지도에서도 위에 그려짐, app.js addTileLayer)
  // 색: 네이버 기본 지도(도로 노랑·주황/흰, 녹지 초록, 물 하늘색)에 없는 빨강·남보라 한 쌍. 색상각 약 105° 이상 떨어져 서로 구분됨.
  LAYERS: [
    // 2026-10-28 파스텔: Open Color red 4 #ff8787(서울시)·violet 4 #9775fa(건기연), 투명도 없음(겹친 아래 선이 비치지 않게). 이전 색 이력은 CHANGELOG.md
    { id: 'sa001',       label: '서울시 상수관로',       color: '#ff8787' },
    { id: 'wtl_pipe_lm', label: '건기연 상수관로', color: '#9775fa' }
  ],
  TILE_SIZE: 512,                  // MapLibre 소스 tileSize (512: 요청 z = 네이버zoom-1, 256: 요청 z = 네이버zoom)
  DEFAULT_CENTER: [37.5665, 126.9780], // [lat, lng] 서울시청 부근
  DEFAULT_ZOOM: 12
};
