// 관망 지도 설정. Client Secret 은 지도 JS 렌더링에 쓰지 않으므로 넣지 않는다.
window.APP_CONFIG = {
  NAVER_CLIENT_ID: 'YOUR_NAVER_CLIENT_ID',   // 네이버 지도 Client ID (서비스 URL: http://localhost, http://localhost:8000 등록)
  API_BASE: '',                    // '' = 페이지와 같은 오리진(http://localhost:8000). 백엔드가 다른 곳이면 전체 URL
  // 타일 파일은 backend/data 아래에 별도 배치한다. 데이터가 없으면 선택칸이 비활성화된다.
  LAYERS: [
    { id: 'kict_national', label: '건기연 상수관망', color: '#4dabf7' },
    { id: 'kict_sewer_national', label: '건기연 하수(전국·보정본)', note: '서울 포함 · 전남 제외', color: '#168574' },
    { id: 'sa001', label: '서울시 상수관망', color: '#df7c24' },
    { id: 'kict_sewer_seoul', label: '건기연 하수(서울·보정본)', color: '#168574' },
    { id: 'sb001', label: '서울시 하수관망', color: '#9355b5' }
  ],
  TILE_SIZE: 512,                  // MapLibre 소스 tileSize (512: 요청 z = 네이버zoom-1, 256: 요청 z = 네이버zoom)
  DEFAULT_CENTER: [35.8310707, 127.7764620], // [lat, lng] 전국 범위 중심(app.js NATION 기본값 기준). 실제 첫 화면은 app.js 가 meta bounds 중심으로 설정
  DEFAULT_ZOOM: 7                  // S_auto(전국 전체 줌)를 계산하지 못했을 때만 사용
};
