// 설정 분리 파일(전국 상수관망 화면). Client Secret 은 지도 JS 렌더링에 쓰지 않으므로 넣지 않는다.
window.APP_CONFIG = {
  NAVER_CLIENT_ID: 'YOUR_NAVER_CLIENT_ID',   // 네이버 지도 Client ID (서비스 URL: http://localhost, http://localhost:8000 등록)
  API_BASE: '',                    // '' = 페이지와 같은 오리진(http://localhost:8000). 백엔드가 다른 곳이면 전체 URL
  // 전국 상수관망 1종. 색 = 파랑 파스텔톤(Open Color blue 4 #4dabf7, 사용자 요청 2026-11-02), 투명도 없음
  LAYERS: [
    { id: 'kict_national', label: '건기연 상수관망', color: '#4dabf7' }
  ],
  TILE_SIZE: 512,                  // MapLibre 소스 tileSize (512: 요청 z = 네이버zoom-1, 256: 요청 z = 네이버zoom)
  DEFAULT_CENTER: [35.8310707, 127.7764620], // [lat, lng] 전국 범위 중심(app.js NATION 기본값 기준). 실제 첫 화면은 app.js 가 meta bounds 중심으로 설정
  DEFAULT_ZOOM: 7                  // S_auto(전국 전체 줌)를 계산하지 못했을 때만 사용
};
