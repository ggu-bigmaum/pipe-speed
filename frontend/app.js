/* 상수관로 화면속도 측정 — 네이버 지도 JS v3(베이스) + MapLibre GL(벡터타일 오버레이, 카메라 동기화)
 *
 * 측정 시점 정의 (NOTES.md 와 동일)
 *  t0   : 조작 시작. 수동 = 네이버 zoomstart/dragstart/pinchstart(없으면 첫 카메라 변경 이벤트),
 *         자동 = 시나리오가 morph/setCenter/setZoom 을 호출하기 직전.
 *  tN   : 네이버 'idle' (조작이 끝나 최종 카메라에 도달, 시나리오는 목표 뷰 도달까지 확인) 시각.
 *  tEnd : tN 이후 최종 카메라를 MapLibre 에 동기화하고, 그 뒤 MapLibre 가 렌더하고 map.loaded()==true
 *         (보이는 타일 전부 로드·파싱·그리기 완료, 'idle' 상당)가 된 첫 시점. 레이어 OFF 이면 tEnd=tN.
 *  총ms = tEnd-t0, 네이버ms = tN-t0, 오버레이ms = tEnd-tN.
 *  fps/드롭: t0~tEnd 구간 requestAnimationFrame 간격. 드롭 = Σ max(0, round(dt/16.67)-1) (60Hz 가정).
 *  타일 요청/전송량: 계측 프로토콜(mvt://) 경유 요청 중 t0 이후 시작한 것. 전송량은 응답 본문 바이트(압축 후 아님).
 */
(function () {
  'use strict';
  const CFG = window.APP_CONFIG;
  const $ = (id) => document.getElementById(id);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const MAX_RECORDS = 100;   // 보관·표시 기록 최대 건수(사용자 결정 2026-10-28). 자동 시나리오 1회(14단계)×반복 7회=98건까지 한 실행이 온전히 남음
  const ORIGIN = CFG.API_BASE || location.origin;

  const state = {
    nmap: null, ml: null, ready: false,
    layers: {},            // id -> {on, meta, srcLayer}
    nonce: 0,              // 캐시 무시용
    instr: true,
    records: [], seq: 0,
    session: null,
    tileLog: [],           // {t, bytes, ms, status}
    longtasks: [],
    hiddenSeen: false,     // 측정 중 탭이 숨김이었던 적 있는지(fps 유효성)
    rafProbe: null,        // 초기 rAF 빈도 점검 결과
    tileHeaders: null,     // 샘플 타일 응답 헤더(Cache-Control 등)
    scn: { running: false, stop: false },
    mlVersion: maplibregl.getVersion ? maplibregl.getVersion() : '?'
  };

  function setStatus(msg, cls) { const s = $('status'); s.textContent = msg; s.className = 'status ' + (cls || ''); }

  /* ---------- 네이버 지도 로드 ---------- */
  window.navermap_authFailure = function () {
    const m = '네이버 지도 인증 실패 (navermap_authFailure 콜백 호출됨). Client ID/서비스 URL 설정을 확인하세요. 현재 오리진: ' + location.origin;
    console.error(m);
    $('authmsg').hidden = false; $('authmsg').textContent = m;
    setStatus('네이버 지도 인증 실패', 'err');
  };
  function loadNaver() {
    return new Promise((res, rej) => {
      const s = document.createElement('script');
      s.src = 'https://openapi.map.naver.com/openapi/v3/maps.js?ncpKeyId=' + encodeURIComponent(CFG.NAVER_CLIENT_ID);
      s.onload = () => res(); s.onerror = () => rej(new Error('네이버 지도 스크립트 로드 실패'));
      document.head.appendChild(s);
    });
  }

  const tileZ = (url) => { const m = /\/tiles\/[^/]+\/(\d+)\//.exec(url); return m ? +m[1] : null; };
  /* ---------- 계측 프로토콜 (타일 요청 수·전송량) ---------- */
  maplibregl.addProtocol('mvt', async (params, abort) => {
    const url = params.url.replace(/^mvt:\/\//, '') + (state.nonce ? '?nc=' + state.nonce : '');
    const t = performance.now();
    const r = await fetch(url, { signal: abort.signal });
    if (r.status === 204 || r.status === 404) { state.tileLog.push({ t, url, z: tileZ(url), bytes: 0, ms: performance.now() - t, status: r.status }); return { data: new Uint8Array(0) }; }
    if (!r.ok) throw new Error('tile ' + r.status + ' ' + url);
    const buf = await r.arrayBuffer();
    state.tileLog.push({ t, url, z: tileZ(url), bytes: buf.byteLength, ms: performance.now() - t, status: r.status });
    return { data: buf };
  });

  /* ---------- MapLibre 오버레이 ---------- */
  function initOverlay() {
    state.ml = new maplibregl.Map({
      container: 'ml', style: { version: 8, sources: {}, layers: [] },
      center: [CFG.DEFAULT_CENTER[1], CFG.DEFAULT_CENTER[0]], zoom: CFG.DEFAULT_ZOOM - 1,
      interactive: false, attributionControl: false, fadeDuration: 0, renderWorldCopies: false,
      transformRequest: (url, type) => {
        if (type === 'Tile' && !url.startsWith('mvt://')) { state.tileLog.push({ t: performance.now(), url, z: tileZ(url), bytes: null, ms: null, status: 'n/a' }); if (state.nonce) return { url: url + (url.includes('?') ? '&' : '?') + 'nc=' + state.nonce }; }
        return undefined;
      }
    });
    state.ml.on('render', onMlRender);
    new ResizeObserver(() => state.ml.resize()).observe($('stage'));
  }
  const tileUrl = (id) => (state.instr ? 'mvt://' : '') + ORIGIN + '/tiles/' + id + '/{z}/{x}/{y}.pbf';
  function addTileLayer(id) {
    const L = state.layers[id], c = CFG.LAYERS.find((x) => x.id === id);
    const m = L.meta || {};
    if (state.ml.getLayer('lyr-' + id)) return;
    state.ml.addSource('src-' + id, {
      type: 'vector', tiles: [tileUrl(id)], tileSize: CFG.TILE_SIZE,
      minzoom: m.minzoom != null ? m.minzoom : 0, maxzoom: m.maxzoom != null ? m.maxzoom : 18
    });
    // 겹침 순서 = CFG.LAYERS 목록 순서(목록 위쪽 = 지도 위쪽). 켜는 순서·캐시 초기화 재생성과 무관하게,
    // 목록에서 바로 위에 있는(인덱스가 더 작은) 켜진 관로 레이어 아래로 넣는다. 없으면 맨 위(beforeId 없음).
    const idx = CFG.LAYERS.findIndex((x) => x.id === id);
    let beforeId;
    for (let i = idx - 1; i >= 0; i--) if (state.ml.getLayer('lyr-' + CFG.LAYERS[i].id)) { beforeId = 'lyr-' + CFG.LAYERS[i].id; break; }
    // 단일 line 레이어 유지(그리는 양 불변). 선명도: 굵기만 소폭 상향(이전 z8 0.6 → z16 2), 불투명 1 명시.
    state.ml.addLayer({
      id: 'lyr-' + id, type: 'line', source: 'src-' + id, 'source-layer': L.srcLayer,
      paint: { 'line-color': c.color, 'line-opacity': 1, 'line-width': ['interpolate', ['linear'], ['zoom'], 8, 1, 16, 2.4] }
    }, beforeId);
  }
  function removeTileLayer(id) {
    if (state.ml.getLayer('lyr-' + id)) state.ml.removeLayer('lyr-' + id);
    if (state.ml.getSource('src-' + id)) state.ml.removeSource('src-' + id);
  }
  function resetOverlay() { // MapLibre 타일 캐시 + HTTP 캐시 무효화(nonce)
    state.nonce++;
    for (const id of Object.keys(state.layers)) if (state.layers[id].on) { removeTileLayer(id); addTileLayer(id); }
  }
  function anyOverlayOn() { return Object.values(state.layers).some((l) => l.on); }

  /* ---------- 카메라 동기화 ---------- */
  let syncQueued = false;
  function syncNow() {
    const c = state.nmap.getCenter(), z = state.nmap.getZoom();
    // 네이버 zoom z 의 세계 폭=256*2^z, MapLibre zoom m 의 세계 폭=512*2^m → m=z-1
    state.ml.jumpTo({ center: [c.lng(), c.lat()], zoom: z - 1 });
  }
  function requestSync() { if (syncQueued) return; syncQueued = true; requestAnimationFrame(() => { syncQueued = false; syncNow(); }); }

  /* ---------- 측정 세션 ---------- */
  const mark = () => performance.now();
  function beginSession(trigger, target) {
    if (state.session) { state.session.events++; return state.session; }
    const n = state.nmap, c = n.getCenter();
    const s = state.session = {
      t0: mark(), wall: new Date().toISOString(), trigger, target: target || null, events: 1,
      from: { lat: c.lat(), lng: c.lng(), zoom: n.getZoom() }, tN: null, camSeen: false,
      tileStart: state.tileLog.length, frames: [], lastFrame: null, done: null, timer: null,
      armed: false, layers: Object.keys(state.layers).filter((k) => state.layers[k].on),
      zmin: n.getZoom(), zmax: n.getZoom(), hidden: document.visibilityState !== 'visible'
    };
    // 직접(수동) 조작 측정마다 타일 캐시 초기화(토글 optLiveCold): 세션 시작 시 1회, 자동 시나리오·S 탐색 중에는 무시
    const lc = document.getElementById('optLiveCold');
    s.liveCold = !!(lc && lc.checked && !state.scn.running && !state.inFindS && trigger !== 'scenario');
    if (s.liveCold) resetOverlay();
    s.promise = new Promise((r) => (s.done = r));
    s.timer = setTimeout(() => { if (state.session === s) endSession(true); }, 60000);   // 버려진 세션의 타이머가 현재 세션을 종료하지 않도록
    requestAnimationFrame((t) => frameTick(t, s));
    document.dispatchEvent(new CustomEvent('speed:start'));   // 읽기 전용 알림(실시간 카드용)
    return s;
  }
  function frameTick(t, s) {
    if (state.session !== s) return;   // 세션별 rAF 체인(버려진 세션의 체인은 종료)
    if (s.lastFrame != null) s.frames.push(t - s.lastFrame);
    s.lastFrame = t;
    $('live').textContent = 'fps ' + (s.frames.length ? (1000 / (s.frames[s.frames.length - 1] || 1)).toFixed(0) : '–') + ' (측정 중)';
    requestAnimationFrame((tt) => frameTick(tt, s));
  }
  function onCamChange() {
    if (!state.ready) return;
    requestSync();
    if (!state.session && state.scn.running) return;   // 시나리오 실행 중 단계 사이의 잔여 카메라 이벤트는 측정 세션으로 만들지 않음
    if (!state.session) {
      if (!anyOverlayOn() || state.inFindS || state.inMinZoom) return;   // 범위 맞추기(findS)·최소 줌(서울 전체) 적용으로 생긴 지도 이동은 측정 기록에서 제외   // 사용자 결정: 관로를 하나도 켜지 않으면 직접 조작 측정을 하지 않음(자동 시나리오는 시작 시 0개 차단)
      beginSession('cam');
    }
    const s = state.session; s.camSeen = true; s.tN = null; s.armed = false;
    const zz = state.nmap.getZoom(); if (zz < s.zmin) s.zmin = zz; if (zz > s.zmax) s.zmax = zz;
  }
  document.addEventListener('visibilitychange', () => { if (state.session && document.visibilityState !== 'visible') state.session.hidden = true; });
  function onNaverIdle() {
    const s = state.session; if (!s || s.tN != null) return;
    if (s.target) { // 시나리오: 목표 뷰 도달 확인
      const c = state.nmap.getCenter();
      if (Math.abs(state.nmap.getZoom() - s.target.zoom) > 0.01 || Math.abs(c.lat() - s.target.lat) > 2e-4 || Math.abs(c.lng() - s.target.lng) > 2e-4) return;
    }
    s.tN = mark();
    syncNow();
    s.armed = true;            // 이후 첫 완료 렌더를 tEnd 로
    if (!anyOverlayOn()) return endSession(false);
    state.ml.triggerRepaint();
  }
  function onMlRender() {
    const s = state.session;
    if (s && s.armed && state.ml.loaded()) endSession(false);
  }
  function pct(a, p) { if (!a.length) return null; const b = [...a].sort((x, y) => x - y); return b[Math.min(b.length - 1, Math.floor(p * b.length))]; }
  /* 네이버 zoom → 요청될 타일 z (tileSize 반영, 소스 min/maxzoom 적용). minzoom 미만이면 null(오버레이 비어 있음) */
  function tileZFor(nz) {
    const L0 = Object.values(state.layers)[0], m = (L0 && L0.meta) || {};
    const minz = m.minzoom != null ? m.minzoom : 0, maxz = m.maxzoom != null ? m.maxzoom : 18;
    const z = Math.floor(nz - 1 + Math.log2(512 / CFG.TILE_SIZE));
    return z < minz ? null : Math.min(z, maxz);
  }
  // 2026-10-28: 현재 타일은 z10~17 모두 원본 100%(full 변환). 예전 불완전 타일의 비율표(FEAT_PCT)·저줌 부분 표시 판정은 제거함.
  const fmtRange = (a, b) => (a == null && b == null) ? 'none' : (a == null ? '<min~' + b : (a === b ? '' + a : a + '-' + b));
  function endSession(timeout) {
    const s = state.session; if (!s) return;
    const tEnd = mark();
    state.session = null; clearTimeout(s.timer);
    const tiles = state.tileLog.slice(s.tileStart);
    const nTile = tiles.length, bytes = tiles.some((t) => t.bytes == null) ? null : tiles.reduce((a, t) => a + t.bytes, 0);
    const emptyReq = tiles.some((t) => t.bytes == null) ? null : tiles.filter((t) => t.bytes === 0).length;
    let gzipKb = null;   // 같은 오리진이라 Resource Timing encodedBodySize(gzip 전송 본문) 사용
    try {
      const rts = performance.getEntriesByType('resource').filter((e) => e.startTime >= s.t0 - 50 && e.name.indexOf('/tiles/') >= 0 && e.name.indexOf('meta.json') < 0), used = new Set(); let sum = 0, miss = 0;
      tiles.forEach((tl) => {
        let best = -1;
        if (tl.url) rts.forEach((e, i) => { if (!used.has(i) && e.name === tl.url && e.startTime >= tl.t - 1 && (best < 0 || e.startTime < rts[best].startTime)) best = i; });
        if (best < 0) { miss++; return; }
        used.add(best); sum += rts[best].encodedBodySize;
      });
      gzipKb = miss ? null : +(sum / 1024).toFixed(1);
    } catch (e) { gzipKb = null; }
    const ends = tiles.filter((t) => t.ms != null).map((t) => t.t + t.ms - s.t0);
    const loadDone = ends.length ? +Math.max(...ends).toFixed(1) : null;   // 조작 시작 t0 ~ 이 단계 마지막 타일 응답 완료
    const f = s.frames, drop = f.reduce((a, dt) => a + Math.max(0, Math.round(dt / 16.67) - 1), 0);
    const lt = state.longtasks.filter((l) => l.start >= s.t0 && l.start <= tEnd);
    const fpsAvg = f.length ? 1000 / (f.reduce((a, b) => a + b, 0) / f.length) : null;
    const rfl = {}; let rfTotal = 0;   // 종료 시점에 화면에 실제 렌더된 선 개수(레이어 ON 일 때만)
    s.layers.forEach((id) => { try { const k = state.ml.getLayer('lyr-' + id) ? state.ml.queryRenderedFeatures({ layers: ['lyr-' + id] }).length : null; rfl[id] = k; if (k != null) rfTotal += k; } catch (e) { rfl[id] = null; } });
    const z = state.nmap.getZoom();
    if (z < s.zmin) s.zmin = z; if (z > s.zmax) s.zmax = z;
    const tzA = tileZFor(s.zmin), tzB = tileZFor(s.zmax);
    const kind = s.target && s.target.kind ? s.target.kind : (Math.abs(z - s.from.zoom) > 0.01 ? 'zoom' : 'pan');
    // 배율 기준 S: '서울 전체 범위 맞추기'(findS) 결과 우선, 없으면 지도 크기로 계산한 S_auto. 배율% = 100*2^(zoom-S), zoom 은 기록값(소수 2자리)
    const seoulS = state.S || state.S_auto || null, zFrom = +s.from.zoom.toFixed(2), zTo = +z.toFixed(2);
    const scalePct = (v) => seoulS == null ? null : +(100 * Math.pow(2, v - seoulS)).toFixed(1);
    const rec = {
      id: ++state.seq, time: s.wall, trigger: s.trigger, step: s.target && s.target.name || '', run: s.target && s.target.run || null,
      kind, mode: s.layers.length ? 'tile' : 'baseline', layers: s.layers.join('+'),
      pan_px: s.target && s.target.pan_px != null ? s.target.pan_px : null, pan_m: s.target && s.target.pan_m != null ? s.target.pan_m : null,
      rendered_features_at_end: s.layers.length ? rfTotal : null, rendered_by_layer: s.layers.length ? Object.keys(rfl).map((k) => k + '=' + rfl[k]).join(' ') : '',
      live_cold: !!s.liveCold,
      // 이 측정 직전 관로 타일 캐시 초기화 여부: 시나리오는 target.cache_reset(그 패스의 cold 설정), 직접 조작은 live_cold
      cache_reset: s.target ? !!s.target.cache_reset : !!s.liveCold,
      viewport: $('stage').clientWidth + 'x' + $('stage').clientHeight, dpr: devicePixelRatio,
      zoom_from: zFrom, zoom_to: zTo, zoom_min: +s.zmin.toFixed(2), zoom_max: +s.zmax.toFixed(2),
      tile_z_expected: fmtRange(tzA, tzB), tile_z_final: tileZFor(z), tile_z_requested: [...new Set(tiles.map((t) => t.z).filter((x) => x != null))].sort((a, b) => a - b).join('|'),
      overlay_empty_final: tileZFor(z) == null, seoul_S: seoulS,
      seoul_S_basis: state.S ? 'findS' : (state.S_auto ? 'auto' : null), scale_pct_from: scalePct(zFrom), scale_pct_to: scalePct(zTo),
      total_ms: +(tEnd - s.t0).toFixed(1), naver_ms: s.tN != null ? +(s.tN - s.t0).toFixed(1) : null,
      overlay_ms: s.tN != null ? +(tEnd - s.tN).toFixed(1) : null, timeout: !!timeout, events: s.events,
      tile_requests: nTile, tile_data_requests: emptyReq == null ? null : nTile - emptyReq, tile_empty_requests: emptyReq, tile_gzip_kb: gzipKb, load_done_from_t0_ms: loadDone, excluded_prestart: s.trigger === 'cam' && !!state.inFindS, tile_kb: bytes == null ? null : +(bytes / 1024).toFixed(1),
      tile_ms_p50: pct(tiles.map((t) => t.ms).filter((x) => x != null), .5) != null ? +pct(tiles.map((t) => t.ms).filter((x) => x != null), .5).toFixed(1) : null,
      tile_ms_max: tiles.length && tiles[0].ms != null ? +Math.max(...tiles.map((t) => t.ms)).toFixed(1) : null,
      fps_valid: !s.hidden && f.length >= 3, visible_throughout: !s.hidden,
      frames: f.length, fps_avg: fpsAvg != null ? +fpsAvg.toFixed(1) : null, dt_max_ms: f.length ? +Math.max(...f).toFixed(1) : null,
      dt_p95_ms: f.length ? +pct(f, .95).toFixed(1) : null, dropped_frames: drop, longtasks: lt.length, longtask_ms: +lt.reduce((a, l) => a + l.dur, 0).toFixed(0)
    };
    if (!(s.target && s.target.measure === false)) {
      state.records.push(rec);
      if (state.records.length > MAX_RECORDS) state.records.splice(0, state.records.length - MAX_RECORDS);   // 사용자 결정: 최근 100건만 보관(오래된 기록부터 삭제)
      renderRecords();
    }
    $('last').textContent = '마지막: ' + rec.kind + ' ' + rec.total_ms + 'ms (네이버 ' + rec.naver_ms + ' + 오버레이 ' + rec.overlay_ms + ') 요청 ' + rec.tile_requests + (timeout ? ' [타임아웃]' : '');
    $('live').textContent = 'fps ' + (rec.fps_avg || '–') + ' (평균)';
    s.done(rec);
    document.dispatchEvent(new CustomEvent('speed:end', { detail: rec }));   // 읽기 전용 알림(실시간 카드용)
  }

  /* ---------- 실시간 카드 (읽기 전용 구독: 측정 로직 불변, 값 확정 후 DOM 만 갱신) ---------- */
  (function card() {
    const el = $('card'), sw = $('optCard'), q = (k) => el.querySelector('[data-k="' + k + '"]');
    const fmt = (v) => Number(v).toLocaleString('en-US', { maximumFractionDigits: 0 });
    document.addEventListener('speed:start', () => {
      if (!sw.checked) return;
      q('big').textContent = '측정 중…'; q('big').className = 'big busy'; q('sub').textContent = '';
    });
    document.addEventListener('speed:no-layer', () => {   // 관로 미선택 상태에서 지도 조작: 측정하지 않음
      if (!sw.checked) return;
      q('big').textContent = '선택된 관로 없음'; q('big').className = 'big empty'; q('sub').textContent = '상단에서 표시할 관로를 선택해 주세요.';
    });
    document.addEventListener('speed:end', (e) => {
      if (!sw.checked) return;
      const r = e.detail, big = q('big'), sub = q('sub');
      big.className = 'big';
      if (r.timeout) { big.textContent = '시간 초과'; sub.textContent = ''; return; }
      if (r.mode === 'baseline') { big.textContent = '관로 끔'; sub.textContent = ''; return; }
      const op = r.kind === 'zoom' ? (r.zoom_to > r.zoom_from ? '확대' : '축소') : '이동';
      big.textContent = fmt(r.overlay_ms) + ' ms';
      const lines = r.rendered_features_at_end;
      sub.textContent = op + ' · 네이버 ' + fmt(r.naver_ms) + ' ms · 요청 수 ' + r.tile_requests + ' · ' + (lines === 0 ? '표시 안 됨' : '선 ' + fmt(lines));
      big.classList.toggle('bad', lines === 0);
    });
    sw.addEventListener('change', () => { el.hidden = !sw.checked; if (sw.checked) { q('big').textContent = '–'; q('sub').textContent = ''; } });
  })();

  /* ---------- 결과 표시/내보내기 ---------- */
  function renderRecords() {
    const tb = $('tbl').tBodies[0]; tb.innerHTML = '';
    state.records.slice(-MAX_RECORDS).reverse().forEach((r) => {
      const tr = document.createElement('tr');
      // 표시 8열, 자동 비교 표와 같은 순서: 번호|동작|배율|관로|캐시 초기화|관로 표시 시간(td.pipe-time)|요청 수|전송 용량 MiB
      // (전체·지도 시간·평균 FPS는 표에서 제외, 레코드에는 total_ms·naver_ms·fps_avg 유지). 내보내기는 렌더된 DOM 표 기준
      // 요청 수 = tile_requests(빈 타일 포함), 전송 용량 MiB = tile_gzip_kb/1024(gzip 실제 전송량, 소수 1자리). 자동 비교 표와 같은 정의
      [r.id, r.kind + '/' + r.mode, r.zoom_from + '→' + r.zoom_to, r.layers, r.cache_reset ? '초기화' : '유지', r.overlay_ms, r.tile_requests, r.tile_gzip_kb != null ? (r.tile_gzip_kb / 1024).toFixed(1) : null].forEach((v, i) => { const td = document.createElement('td'); td.textContent = v == null ? '–' : v; if (i === 5) td.className = 'pipe-time'; tr.appendChild(td); });
      tb.appendChild(tr);
    });
    $('count').textContent = state.records.length + '건';   // 보관 기록 = 표시 기록(최대 MAX_RECORDS건)
    renderSummary();
  }
  /* ---------- 통계: 같은 시나리오 단계·모드별 반복 요약 ---------- */
  function stats(a) {
    const v = a.filter((x) => x != null && isFinite(x)).sort((x, y) => x - y), n = v.length;
    if (!n) return null;
    const mean = v.reduce((p, c) => p + c, 0) / n;
    const med = n % 2 ? v[(n - 1) / 2] : (v[n / 2 - 1] + v[n / 2]) / 2;
    const sd = n > 1 ? Math.sqrt(v.reduce((p, c) => p + (c - mean) * (c - mean), 0) / (n - 1)) : null; // 표본 표준편차(n-1)
    const r = (x) => x == null ? null : +x.toFixed(1);
    return { n, median: r(med), min: r(v[0]), max: r(v[n - 1]), sd: r(sd), mean: r(mean) };
  }
  const layerKey = (r) => String(r.layers || '').split('+').filter(Boolean).sort().join('+');
  const modeKey = (r) => (r.mode === 'tile' ? 'on' : r.mode);
  function summarize(recs) {
    const rows = [], groups = new Map();
    recs.filter((r) => r.trigger === 'scenario' && r.step && !r.timeout).forEach((r) => {
      // 묶음 키: 단계+모드+관로+캐시 초기화 여부(초기화/유지 기록이 한 행에 섞이지 않게)
      const k = r.step + '\u0001' + modeKey(r) + '\u0001' + layerKey(r) + '\u0001' + !!r.cache_reset;
      if (!groups.has(k)) groups.set(k, { step: r.step, mode: modeKey(r), kind: r.kind, layers: layerKey(r), cache_reset: !!r.cache_reset, recs: [] });
      groups.get(k).recs.push(r);
    });
    for (const g of groups.values()) {
      const rr = g.recs, row = {
        step: g.step, mode: g.mode, kind: g.kind, cache_reset: g.cache_reset, pan_px: rr[0].pan_px, pan_m: rr[0].pan_m, viewports: [...new Set(rr.map((r) => r.viewport))].join(' / '), layers:[...new Set(rr.map((r) => r.layers))].join(' / '),
        zoom_from: rr[0].zoom_from, zoom_to: rr[0].zoom_to, tile_z_expected: [...new Set(rr.map((r) => r.tile_z_expected))].join(' / '),
        rendered_min: (() => { const v = rr.map((r) => r.rendered_features_at_end).filter((x) => x != null); return v.length ? Math.min(...v) : null; })(), overlay_empty_final: rr.some((r) => r.overlay_empty_final),
        total_ms: stats(rr.map((r) => r.total_ms)), naver_ms: stats(rr.map((r) => r.naver_ms)), overlay_ms: stats(rr.map((r) => r.overlay_ms)),
        tile_requests: stats(rr.map((r) => r.tile_requests)), tile_data_requests: stats(rr.map((r) => r.tile_data_requests)), tile_empty_requests: stats(rr.map((r) => r.tile_empty_requests)), tile_gzip_kb: stats(rr.map((r) => r.tile_gzip_kb)), tile_kb: stats(rr.map((r) => r.tile_kb)), load_done_from_t0_ms: stats(rr.map((r) => r.load_done_from_t0_ms)), fps_avg: stats(rr.filter((r) => r.fps_valid).map((r) => r.fps_avg)),
        timeouts_excluded: recs.filter((r) => r.trigger === 'scenario' && r.step === g.step && r.timeout && modeKey(r) === g.mode && layerKey(r) === g.layers && !!r.cache_reset === g.cache_reset).length
      };
      rows.push(row);
    }
    return rows;
  }
  function renderSummary() {
    const tb = $('sumTbl').tBodies[0]; tb.innerHTML = '';
    const rows = summarize(state.records);
    rows.forEach((r) => {
      // 표시 8열: 단계·관로·캐시 초기화·반복·관로 표시 시간 평균(overlay_ms)·최소~최대·요청 수 평균(tile_requests, 빈 타일 포함)·전송 용량 평균 MiB(tile_gzip_kb/1024).
      // 평균은 stats().mean(소수 1자리). summarize() 의 다른 필드(중앙값 등)는 그대로.
      const o = r.overlay_ms, req = r.tile_requests, gz = r.tile_gzip_kb;
      const tr = document.createElement('tr');
      [r.step, r.layers, r.cache_reset ? '초기화' : '유지', o && o.n, o && o.mean != null ? o.mean.toFixed(1) : null, o && o.n >= 2 ? o.min + ' ~ ' + o.max : null,
        req && req.mean, gz && gz.mean != null ? (gz.mean / 1024).toFixed(1) : null].forEach((v, i) => {
        const td = document.createElement('td'); td.textContent = v == null ? '–' : v;
        if (i === 4) td.className = 'pipe-time';   // 관로 표시 시간 열 음영(개별 기록 표와 같은 class)
        tr.appendChild(td);
      });
      tr.title = '레이어: ' + (r.layers || '없음(네이버만)');
      tb.appendChild(tr);
    });
    $('sumNote').textContent = '';
  }
  /* ---------- 내보내기: 화면에 렌더된 표(DOM) 그대로 엑셀(.xlsx)·CSV ---------- */
  // 기준은 렌더된 DOM 표(index.html 표시 formatter 가 열 순서·문구를 바꾼 뒤의 머리글·셀 텍스트와 순서).
  // 표 밖 설명·부가 정보는 넣지 않음. 엑셀 스타일은 계산된 스타일(getComputedStyle)에서 읽음.
  function download(name, data, type) {
    const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([data], { type })); a.download = name; document.body.appendChild(a); a.click(); a.remove();
  }
  const stamp = () => (($('runLabel') && $('runLabel').value.trim()) ? $('runLabel').value.trim().replace(/[^A-Za-z0-9._-]/g, '_') + '-' : '') + new Date().toISOString().replace(/[:T]/g, '-').slice(0, 19);
  const esc = (v) => { const s = v == null ? '' : String(v); return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
  const rgba = (s) => { const m = (String(s).match(/[\d.]+/g) || []).map(Number); return m.length >= 3 ? [m[0], m[1], m[2], m.length > 3 ? m[3] : 1] : [0, 0, 0, 0]; };
  const argb = (c) => 'FF' + c.slice(0, 3).map((v) => Math.round(v).toString(16).padStart(2, '0')).join('').toUpperCase();
  // 셀 배경: 반투명 배경(예: 관로 표시 시간 열)을 불투명한 조상 배경(줄무늬 행·결과 창) 위에 합성한 화면 색
  function shownBg(el) {
    const layers = [];
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) { const c = rgba(getComputedStyle(n).backgroundColor); if (c[3] > 0) { layers.push(c); if (c[3] >= 1) break; } }
    let o = [255, 255, 255];
    for (let i = layers.length - 1; i >= 0; i--) { const [r, g, b, a] = layers[i]; o = [r * a + o[0] * (1 - a), g * a + o[1] * (1 - a), b * a + o[2] * (1 - a)]; }
    return o;
  }
  const edge = (w, s, c) => parseFloat(w) > 0 && s !== 'none' && s !== 'hidden' ? { style: parseFloat(w) >= 2 ? 'medium' : 'thin', color: { argb: argb(rgba(c)) } } : null;
  const shown = (el) => getComputedStyle(el).display !== 'none';
  // 클릭 시점의 표를 동기적으로 읽어 둠(저장 대화상자 대기 중 표가 바뀌어도 클릭 시점 내용 저장)
  function readTable(t) {
    const trs = [...t.tHead.rows, ...t.tBodies[0].rows].filter(shown);
    const box = getComputedStyle(t.closest('.tbl') || t);
    return {
      outer: edge(box.borderTopWidth, box.borderTopStyle, box.borderTopColor),
      rows: trs.map((tr) => ({
        height: tr.getBoundingClientRect().height,
        cells: [...tr.cells].filter(shown).map((c) => {
          const cs = getComputedStyle(c);
          const align = ({ left: 'left', start: 'left', right: 'right', end: 'right', center: 'center', '-webkit-center': 'center', justify: 'justify' })[cs.textAlign] || 'left';
          return {
            text: c.textContent.trim(), width: c.getBoundingClientRect().width, align,
            valign: ({ top: 'top', bottom: 'bottom' })[cs.verticalAlign] || 'middle',
            pad: parseFloat(align === 'right' ? cs.paddingRight : cs.paddingLeft) || 0,
            font: { name: cs.fontFamily.split(',')[0].replace(/["']/g, '').trim(), size: Math.round(parseFloat(cs.fontSize) * 0.75 * 2) / 2, bold: (parseInt(cs.fontWeight, 10) || 400) >= 600, italic: cs.fontStyle === 'italic', color: { argb: argb(rgba(cs.color)) } },
            fill: argb(shownBg(c)),
            border: { top: edge(cs.borderTopWidth, cs.borderTopStyle, cs.borderTopColor), bottom: edge(cs.borderBottomWidth, cs.borderBottomStyle, cs.borderBottomColor), left: edge(cs.borderLeftWidth, cs.borderLeftStyle, cs.borderLeftColor), right: edge(cs.borderRightWidth, cs.borderRightStyle, cs.borderRightColor) }
          };
        })
      }))
    };
  }
  const csvFrom = (snap) => '﻿' + snap.rows.map((r) => r.cells.map((c) => esc(c.text)).join(',')).join('\r\n');
  // 숫자로 보이는 텍스트 → 숫자 셀 + 화면과 같은 소수 자릿수·천 단위 쉼표·% 표시 형식. 그 외(–, 범위, 화살표 등)는 텍스트
  function asNumber(s) {
    const m = /^(-?)(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(%?)$/.exec(s);
    if (!m || /^0\d/.test(m[2])) return null;
    const dec = m[3] ? m[3].length : 0;
    let v = Number(m[1] + m[2].replace(/,/g, '') + (m[3] ? '.' + m[3] : ''));
    let fmt = (m[2].includes(',') ? '#,##0' : '0') + (dec ? '.' + '0'.repeat(dec) : '');
    if (m[4]) { v /= 100; fmt += '%'; }
    return { v, fmt };
  }
  async function xlsxFrom(snap, sheetName) {
    const wb = new ExcelJS.Workbook();
    const ws = wb.addWorksheet(sheetName, { views: [{ state: 'frozen', xSplit: 0, ySplit: 1, showGridLines: false }] });   // 머리글 행 고정, 화면처럼 눈금선 없음
    const last = snap.rows.length - 1;
    snap.rows.forEach((r, ri) => {
      const row = ws.getRow(ri + 1);
      if (r.height > 0) row.height = +(r.height * 0.75).toFixed(2);   // px → pt
      r.cells.forEach((c, ci) => {
        const x = row.getCell(ci + 1), n = ri > 0 ? asNumber(c.text) : null;   // 머리글은 항상 텍스트
        if (n) { x.value = n.v; x.numFmt = n.fmt; } else x.value = c.text;
        x.font = c.font;
        x.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: c.fill } };
        x.alignment = { horizontal: c.align, vertical: c.valign, indent: c.align !== 'center' && c.pad >= 8 ? 1 : 0 };
        const b = {};
        ['top', 'bottom', 'left', 'right'].forEach((k) => { if (c.border[k]) b[k] = c.border[k]; });
        if (snap.outer) { if (ri === 0) b.top = snap.outer; if (ri === last) b.bottom = snap.outer; if (ci === 0) b.left = snap.outer; if (ci === r.cells.length - 1) b.right = snap.outer; }   // 표 바깥 테두리(.tbl)
        x.border = b;
      });
    });
    // 열 너비: 화면 머리글 칸 너비(px) 기준(기본 글꼴 숫자 폭 7px = 1). 표가 숨겨져 너비를 못 읽으면 글자 수로 대체
    (snap.rows[0] ? snap.rows[0].cells : []).forEach((c, ci) => {
      const px = c.width || Math.max(...snap.rows.map((r) => r.cells[ci] ? [...r.cells[ci].text].reduce((a, ch) => a + (ch.charCodeAt(0) > 0x2e7f ? 13 : 7), 0) + 32 : 0));
      ws.getColumn(ci + 1).width = +(px / 7).toFixed(2);
    });
    return wb.xlsx.writeBuffer();
  }
  // 저장 결과 표시(saveState 요소가 있을 때만)
  const saveMsg = (msg) => { const st = document.getElementById('saveState'); if (st) st.textContent = msg; };
  // 저장 대화상자(showSaveFilePicker)로 사용자가 경로·파일명을 골라 저장. 사용자 제스처 유지를 위해 첫 await 는 picker.
  // build 는 동기 값 또는 Promise(ArrayBuffer 등) 모두 허용, picker 다음에 실행. 미지원·오류(취소 제외)면 <a download> 대체.
  async function saveFile(name, build, type, description, ext) {
    const fallback = async (note) => {
      try { download(name, await build(), type); saveMsg(note + name); } catch (e) { saveMsg('저장 실패: ' + (e && e.message || e)); }
    };
    if (typeof window.showSaveFilePicker !== 'function') return fallback('다운로드: ');
    let h;
    try {
      h = await window.showSaveFilePicker({ suggestedName: name, types: [{ description, accept: { [type]: [ext] } }] });
    } catch (e) {
      if (e && e.name === 'AbortError') { saveMsg('저장 취소'); return; }
      return fallback('저장 대화상자 사용 불가(' + (e && e.message || e) + ') — 다운로드: ');
    }
    try {
      const data = await build();
      const w = await h.createWritable();
      await w.write(new Blob([data], { type }));
      await w.close();
      saveMsg('저장됨: ' + (h.name || name));
    } catch (e) { saveMsg('저장 실패: ' + (e && e.message || e)); }
  }
  const XLSX_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
  function exportTable(tableId, sheetName, base, format) {
    const t = $(tableId);
    if (!t.tBodies[0].rows.length) { saveMsg('저장할 기록 없음'); return; }
    if (format === 'xlsx' && !window.ExcelJS) { saveMsg('엑셀 저장 불가: 엑셀 생성 라이브러리(ExcelJS)를 불러오지 못함'); return; }
    const snap = readTable(t), name = base + '_' + stamp() + '.' + format;
    return format === 'xlsx'
      ? saveFile(name, () => xlsxFrom(snap, sheetName), XLSX_TYPE, '엑셀 통합 문서', '.xlsx')
      : saveFile(name, () => csvFrom(snap), 'text/csv', 'CSV', '.csv');
  }
  $('expXlsx').onclick = () => exportTable('tbl', '개별 기록', '관로표시시간_개별기록', 'xlsx');
  $('expCsv').onclick = () => exportTable('tbl', '개별 기록', '관로표시시간_개별기록', 'csv');
  $('expSumXlsx').onclick = () => exportTable('sumTbl', '자동 비교 기록', '관로표시시간_자동비교', 'xlsx');
  $('expSumCsv').onclick = () => exportTable('sumTbl', '자동 비교 기록', '관로표시시간_자동비교', 'csv');
  $('clearRec').onclick = () => { state.records = []; renderRecords(); };

  /* ---------- 자동 시나리오 ---------- */
  // 기본 시나리오는 init() 에서 P 정의로 설정(SEOUL 상수 초기화 이후)
  const SEOUL = [126.7954714, 37.4338554, 127.2225354, 37.6939493]; // 백엔드 meta bounds (W,S,E,N)
  async function findS() { // 현재 창/스테이지에서 서울 bounds 가 모두 들어오는 가장 큰 네이버 정수 zoom
    const n = state.nmap, c = new naver.maps.LatLng((SEOUL[1] + SEOUL[3]) / 2, (SEOUL[0] + SEOUL[2]) / 2);
    n.setOptions('minZoom', 6);
    for (let z = 15; z >= 6; z--) {
      n.setCenter(c); n.setZoom(z, false); await sleep(500);
      const b = n.getBounds();
      if (b.hasLatLng(new naver.maps.LatLng(SEOUL[1], SEOUL[0])) && b.hasLatLng(new naver.maps.LatLng(SEOUL[3], SEOUL[2]))) return z;
    }
    return null;
  }
  // 지도를 움직이지 않고(카메라 불변) 현재 네이버 지도 컨테이너 크기로 서울 bounds 가 모두 들어가는 가장 큰 정수 네이버 zoom(S_auto).
  // 가정: 네이버 zoom z 의 세계 폭 = 256*2^z px (웹메르카토르, syncNow 의 MapLibre m=z-1·tileSize 512 와 같은 가정). 범위는 findS 와 같은 6~15.
  function seoulFitZoom(w, h) {
    if (!(w > 0 && h > 0)) return null;
    const mx = (lon) => (lon + 180) / 360;
    const my = (lat) => { const p = lat * Math.PI / 180; return (1 - Math.log(Math.tan(p) + 1 / Math.cos(p)) / Math.PI) / 2; };
    const fw = mx(SEOUL[2]) - mx(SEOUL[0]), fh = my(SEOUL[1]) - my(SEOUL[3]);   // 세계 폭 1 기준 서울 bounds 폭·높이
    for (let z = 15; z >= 6; z--) { const ws = 256 * Math.pow(2, z); if (fw * ws <= w && fh * ws <= h) return z; }
    return null;
  }
  function updateSAuto() {
    const el = $('naver') || $('stage'), w = el ? el.clientWidth : 0, h = el ? el.clientHeight : 0;
    state.S_auto = seoulFitZoom(w, h);
    state.S_autoView = { stage: [w, h], win: [innerWidth, innerHeight], dpr: devicePixelRatio };
    return state.S_auto;
  }
  // 사용자 요청(2026-10-28): 서울 전체보다 더 축소하지 못하게 네이버 minZoom = 실측 S(범위 맞추기) 우선, 없으면 S_auto, 둘 다 없으면 6(기존값).
  // findS 탐색 중(minZoom 6 으로 내려 지도를 움직임)에는 건드리지 않고, applyS 가 끝난 뒤 다시 호출된다.
  // 현재 줌이 새 minZoom 보다 작아 지도가 확대되면 그 줌 변화는 측정 기록에서 제외(state.inMinZoom, onCamChange·zoomstart 가드).
  function minZoomTarget() { return state.S || state.S_auto || 6; }
  function applyMinZoom() {
    const n = state.nmap; if (!n || state.inFindS) return;
    const mz = minZoomTarget();
    let cur = null; try { cur = n.getOptions('minZoom'); } catch (e) { /* 미지원 */ }
    const needZoom = n.getZoom() < mz;
    if (cur === mz && !needZoom) return;
    state.inMinZoom = true;
    // 창 크기 변경으로 이미 시작된 직접 조작 세션(cam/user:*) 안에서 확대가 일어나면 그 세션은 기록하지 않음(target.measure=false, endSession 규칙 그대로)
    const s0 = state.session;
    if (needZoom && s0 && !state.scn.running && (s0.trigger === 'cam' || String(s0.trigger).indexOf('user:') === 0)) s0.target = Object.assign({}, s0.target || {}, { measure: false });
    try { n.setOptions('minZoom', mz); if (n.getZoom() < mz) n.setZoom(mz, false); } catch (e) { /* noop */ }
    if (!needZoom) { state.inMinZoom = false; return; }
    clearTimeout(state.minZoomTimer);   // 확대가 일어난 경우: 네이버 idle 까지(최대 1.5초) 제외 유지
    const done = () => { clearTimeout(state.minZoomTimer); state.inMinZoom = false; };
    naver.maps.Event.once(n, 'idle', done); state.minZoomTimer = setTimeout(done, 1500);
  }
  // 새 정의(사용자 확정): 확대 = S→18 한 번에 +1 줌(7단계), 축소 = 18→S 한 번에 -1 줌(7단계),
  // 이동 = 줌 13/15/17 에서 스테이지 폭 50% 동쪽 1회(거리는 네이버 투영으로 계산, 이동 후 복귀는 측정 제외). 중심은 서울 bounds 중심.
  const DISTRICTS = { '강북구': [37.6397, 127.0256], '강남구': [37.5172, 127.0473], '강동구': [37.5301, 127.1238], '강서구': [37.5510, 126.8495] };
  // 사용자 확정 시나리오(P): 100%=서울 전체(줌 S) 즉시 진입(대전→서울, morph 끔), 확대 200~1600%(줌 S+1~S+4), 축소 역순,
  // 이동=400%(줌 S+2) 고정, 서울 중앙→강북구→강남구→강동구→강서구→서울 중앙. 배율은 S=100%, +1줌=x2. step 이름은 표에 그대로 쓰임.
  function buildPScenario(S) {
    const lat = (SEOUL[1] + SEOUL[3]) / 2, lng = (SEOUL[0] + SEOUL[2]) / 2, st = [];
    const pct = (z) => (100 * Math.pow(2, z - S));
    st.push({ name: '준비 대전', lat: 36.35, lng: 127.38, zoom: S, measure: false, animate: false });
    st.push({ name: '100%', lat, lng, zoom: S, animate: false });
    for (let z = S + 1; z <= S + 4; z++) st.push({ name: '확대 ' + pct(z) + '%', lat, lng, zoom: z });
    for (let z = S + 3; z >= S; z--) st.push({ name: '축소 ' + pct(z) + '%', lat, lng, zoom: z });
    st.push({ name: '준비 이동', lat, lng, zoom: S + 2, measure: false });
    ['강북구', '강남구', '강동구', '강서구'].forEach((g) => st.push({ name: '이동 ' + g, lat: DISTRICTS[g][0], lng: DISTRICTS[g][1], zoom: S + 2, reportMove: true }));
    st.push({ name: '이동 서울 중앙', lat, lng, zoom: S + 2, reportMove: true });
    return st;
  }
  // 시나리오 생성(genScn·init·자동 실행 기준 줌 맞춤 공용). state.scnGen = 마지막 생성 {kind, S, str(텍스트 상자 값)}
  function buildScenarioFor(kind, S) { return buildPScenario(S); }
  function setScenario(kind, S) {
    $('scenario').value = JSON.stringify(buildScenarioFor(kind, S));
    state.scnS = S;
    state.scnGen = { kind, S: state.scnS, str: $('scenario').value };
  }
  // 손으로 바꾼 JSON 의 기준 줌 추정: P 형식 '100%' 단계 zoom, 없으면 이름의 'S=숫자'. 둘 다 없으면 null(경고 생략)
  function scnBasis(steps) {
    if (!Array.isArray(steps)) return null;
    const p = steps.find((st) => st && st.name === '100%' && Number.isFinite(st.zoom)); if (p) return p.zoom;
    for (const st of steps) { const m = st && typeof st.name === 'string' && /S=(\d+)/.exec(st.name); if (m) return +m[1]; }
    return null;
  }
  async function applyS() { // S 탐색 + 지도 minZoom 을 S 로 고정(S 미만 축소 금지)
    // 성공·null·예외 모두 finally 에서 findS 가 내린 minZoom 6 을 기준(실측 S 우선, 없으면 S_auto)으로 복원. 예외는 호출부로 전달
    state.inFindS = true; let S;
    try {
      S = await findS(); await sleep(600);
      if (S != null) { state.S = S; state.SView = { stage: [$('stage').clientWidth, $('stage').clientHeight], win: [innerWidth, innerHeight], dpr: devicePixelRatio }; }
    } finally { state.inFindS = false; applyMinZoom(); }
    if (S == null) return null;
    $('sInfo').textContent = '서울 전체 기준 줌 S=' + S + ' (스테이지 ' + state.SView.stage.join('x') + '). 지도 minZoom=' + S + '(이보다 축소 불가). 창 크기가 바뀌면 S 가 달라질 수 있으니 다시 탐색하세요.';
    return S;
  }
  $('genScn').onclick = async () => {
    $('genScn').disabled = true; $('scnState').textContent = '서울 전체 줌(S) 탐색 중…';
    let S = null;
    try { S = await applyS(); } catch (e) { console.warn('범위 맞추기 실패', e); } finally { $('genScn').disabled = false; }   // 예외여도 버튼 복구(minZoom 은 applyS finally 에서 복원)
    if (S == null) { $('scnState').textContent = '범위 맞추기 실패'; return; }
    setScenario('new', S);
    $('scnState').textContent = 'S=' + S + ' (창 ' + innerWidth + 'x' + innerHeight + ', 스테이지 ' + $('stage').clientWidth + 'x' + $('stage').clientHeight + '). 시나리오 생성됨';
  };
  async function runStep(step, run, animate, pause, cacheReset) {
    await sleep(pause);
    if (state.scn.stop) return;
    const n = state.nmap, c = n.getCenter();
    if (step.panEastFrac) { // 현재 중심에서 스테이지 폭 x frac 픽셀만큼 동쪽(네이버 투영 fromCoordToOffset/fromOffsetToCoord)
      const pj = n.getProjection(), o = pj.fromCoordToOffset(c), px = $('naver').clientWidth * step.panEastFrac;
      const c1 = pj.fromOffsetToCoord(new naver.maps.Point(o.x + px, o.y));
      const R = 6371008.8, rad = Math.PI / 180, dLat = (c1.lat() - c.lat()) * rad, dLng = (c1.lng() - c.lng()) * rad;
      const hv = Math.sin(dLat / 2) ** 2 + Math.cos(c.lat() * rad) * Math.cos(c1.lat() * rad) * Math.sin(dLng / 2) ** 2;
      step = Object.assign({}, step, { lat: c1.lat(), lng: c1.lng(), pan_px: +px.toFixed(1), pan_m: +(2 * R * Math.asin(Math.sqrt(hv))).toFixed(1) });
    }
    if (step.reportMove) { // 현재 중심 → 도착점 실제 거리(m, 네이버 투영 픽셀)
      const pj = n.getProjection(), c1 = new naver.maps.LatLng(step.lat, step.lng), o0 = pj.fromCoordToOffset(c), o1 = pj.fromCoordToOffset(c1);
      const R = 6371008.8, rad = Math.PI / 180, dLat = (c1.lat() - c.lat()) * rad, dLng = (c1.lng() - c.lng()) * rad;
      const hv = Math.sin(dLat / 2) ** 2 + Math.cos(c.lat() * rad) * Math.cos(c1.lat() * rad) * Math.sin(dLng / 2) ** 2;
      step = Object.assign({}, step, { pan_px: +Math.hypot(o1.x - o0.x, o1.y - o0.y).toFixed(1), pan_m: +(2 * R * Math.asin(Math.sqrt(hv))).toFixed(1) });
    }
    const kind = Math.abs(n.getZoom() - step.zoom) > 0.01 ? 'zoom' : 'pan';
    const target = { name: step.name, lat: step.lat, lng: step.lng, zoom: step.zoom, kind, run, measure: step.measure, pan_px: step.pan_px, pan_m: step.pan_m, cache_reset: !!cacheReset };
    if (state.session) clearTimeout(state.session.timer);   // 직전 세션 타이머 해제
    state.session = null;
    const s = beginSession('scenario', target); s.camSeen = false;
    const ll = new naver.maps.LatLng(step.lat, step.lng);
    if (animate && step.animate !== false) n.morph(ll, step.zoom); else { n.setCenter(ll); n.setZoom(step.zoom, false); }
    setTimeout(() => { if (state.session === s && !s.camSeen && s.tN == null) onNaverIdle(); }, 600); // 이미 목표 뷰인 경우
    return s.promise;
  }
  // cold: 측정 단계 직전마다 오버레이 소스 재생성 + ?nc= 갱신(MapLibre 메모리 캐시·HTTP 캐시 무효화) 후 현재 뷰 로드 완료까지 대기. 이 구간은 측정 세션 밖.
  async function coldPrep() {
    resetOverlay(); await sleep(200);
    const t = performance.now(); let ok = 0;
    while (!state.scn.stop && performance.now() - t < 60000 && ok < 3) { if (state.ml.loaded() && state.ml.areTilesLoaded()) ok++; else ok = 0; await sleep(100); }
  }
  // rAF 가 정상(약 60Hz)일 때까지 대기: 창/패널이 숨겨져 rAF 가 1초 단위로 제한되면 측정값이 무효이므로 그 상태로 측정하지 않음(측정 구간 밖).
  async function waitHealthy() {
    while (!state.scn.stop) {
      const dts = await new Promise((res) => { const a = []; let last = null; const f = (t) => { if (last != null) a.push(t - last); last = t; if (a.length < 4) requestAnimationFrame(f); else res(a); }; requestAnimationFrame(f); setTimeout(() => res(null), 1500); });
      const sizeOk = !state.SView || ($('stage').clientWidth === state.SView.stage[0] && $('stage').clientHeight === state.SView.stage[1]);   // S 탐색 시점과 스테이지 크기가 같을 때만 측정
      if (dts && Math.max(...dts) < 60 && sizeOk) return;
      if (!sizeOk) $('scnState').textContent += ' (스테이지 크기 변경: 원래 크기로 복원 대기)';
      state.healthWaits = (state.healthWaits || 0) + 1; $('scnState').textContent += ' (화면 숨김/rAF 지연: 대기)';
      await sleep(1000);
    }
  }
  async function runPass(steps, N, label, animate, pause, cold, measure) {
    for (let r = 1; r <= N && !state.scn.stop; r++) {
      for (const st of steps) {
        if (state.scn.stop) break;
        $('scnState').textContent = label + ' · 반복 ' + r + '/' + N + ' · ' + st.name;
        await waitHealthy();
        if (state.scn.stop) break;
        const doCold = !!(cold && measure && st.measure !== false && Object.values(state.layers).some((l) => l.on));   // 이 단계 직전 캐시 초기화 여부(레코드 cache_reset)
        if (doCold) { $('scnState').textContent += ' (캐시 제거 중)'; await coldPrep(); }
        if (state.scn.stop) break;
        await runStep(measure ? st : Object.assign({}, st, { measure: false }), r, animate, pause, doCold);
        if (state.scn.stop) break;
        state.scn.completed++;
        emitScenario('progress');
      }
    }
  }
  function emitScenario(kind, extra = {}) {
    const sc = state.scn, elapsed = performance.now() - sc.started;
    const estimate = sc.completed > 0 ? elapsed / sc.completed * sc.total : null;
    document.dispatchEvent(new CustomEvent('speed:scenario-' + kind, { detail: {
      layers: sc.layers.slice(), estimated_ms: estimate,
      remaining_ms: estimate == null ? null : Math.max(0, estimate - elapsed),
      estimate_basis: estimate == null ? '산정 중' : '현재 실행의 완료 단계 경과 평균',
      completed_steps: sc.completed, total_steps: sc.total, elapsed_ms: elapsed,
      stopped: !!sc.stop,
      records_count: state.records.filter((r) => r.trigger === 'scenario' && r.id > sc.startSeq).length,
      ...extra
    } }));
  }
  $('runScn').onclick = async () => {
    if (state.scn.running) return;
    // 기준 줌 맞춤(측정 전, 측정 정의 불변): 마지막 생성 시나리오를 그대로 두었고 현재 기준 줌(state.S || state.S_auto, 레코드 배율과 같은 값)이
    // 생성 때와 다르면 같은 생성기로 다시 생성. 손으로 바꾼 JSON 은 덮어쓰지 않고 기준 불일치 경고만.
    let scnNote = '';
    { const curS = state.S || state.S_auto || null, g = state.scnGen;
      if (g && $('scenario').value === g.str) {
        if (g.S != null && curS != null && g.S !== curS) { setScenario(g.kind, curS); scnNote = ' [기준 줌 S=' + curS + '로 시나리오를 맞춤(생성 시 S=' + g.S + ')]'; }
      } else if (curS != null) {
        let js = null; try { js = JSON.parse($('scenario').value); } catch (e) { /* 아래 검사에서 오류 표시 */ }
        const b = scnBasis(js);
        if (b != null && b !== curS) scnNote = ' [경고: 시나리오 기준 줌 S=' + b + ', 현재 기준 줌 S=' + curS + ' — 손으로 바꾼 JSON이라 그대로 실행, 배율 기록은 S=' + curS + ' 기준]';
      }
      if (scnNote) $('scnState').textContent = scnNote.trim();
    }
    let steps; try {
      steps = JSON.parse($('scenario').value);
      if (!Array.isArray(steps) || !steps.length || steps.some((st) => !Number.isFinite(st.lat) || !Number.isFinite(st.lng) || !Number.isFinite(st.zoom))) throw new Error('시나리오 좌표를 확인하세요.');
    } catch (e) { return alert('시나리오 JSON 오류: ' + e.message); }
    const N = Math.max(1, +$('repeat').value), pause = +$('pause').value, animate = $('optAnim').checked, cold = $('optCold').checked;
    const onIds = Object.keys(state.layers).filter((k) => state.layers[k].on);
    if (!onIds.length) return alert('측정할 관로를 선택하세요.');
    if (!state.ready || state.session) return alert('현재 지도 조작이 끝난 후 실행하세요.');
    { let mz = null; try { mz = state.nmap.getOptions('minZoom'); } catch (e) { /* 미지원 */ }   // 지도 minZoom 보다 낮은 단계는 도달 불가(타임아웃) → 경고만
      const low = Number.isFinite(mz) ? steps.filter((st) => st.zoom < mz).length : 0;
      if (low) { scnNote += ' [경고: 지도 minZoom=' + mz + ' 미만 단계 ' + low + '개]'; $('scnState').textContent = scnNote.trim(); } }
    const controls = [...document.querySelectorAll('#layers input'), ...['genScn', 'clearRec', 'repeat', 'pause', 'optCold', 'optAnim', 'optLiveCold'].map($)].filter(Boolean);
    const disabled = controls.map((el) => el.disabled);
    controls.forEach((el) => { el.disabled = true; });
    state.scn = { running: true, stop: false, layers: onIds.slice(), startSeq: state.seq,
      started: performance.now(), completed: 0, total: steps.length * N };
    $('runScn').disabled = true; $('stopScn').disabled = false;
    emitScenario('start');
    let failure = null;
    try {
      await runPass(steps, N, '실행' + scnNote, animate, pause, cold, true);
    } catch (e) {
      failure = e.message || String(e); state.scn.stop = true;
    } finally {
      $('scnState').textContent = (failure ? '실행 오류: ' + failure : state.scn.stop ? '중단됨' : '완료') + scnNote;
      state.scn.running = false; $('runScn').disabled = false; $('stopScn').disabled = true;
      controls.forEach((el, i) => { el.disabled = disabled[i]; });
      emitScenario('end', { error: failure });
    }
    // (서버 자동 저장 삭제: 저장은 JSON/CSV 버튼의 저장 대화상자로만)
  };
  $('stopScn').onclick = () => { state.scn.stop = true; if (state.session) endSession(true); };

  /* ---------- 레이어 UI ---------- */
  async function loadMeta(id) {
    try {
      const r = await fetch(ORIGIN + '/tiles/' + id + '/meta.json', { cache: 'no-store' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const m = await r.json();
      const sl = m.source_layer || m.layer_name || (m.vector_layers && m.vector_layers[0] && m.vector_layers[0].id) || id;
      return { meta: m, srcLayer: sl, ok: true };
    } catch (e) { return { meta: { error: String(e) }, srcLayer: id, ok: false }; }
  }
  async function buildLayerUi() {
    const box = $('layers');
    for (const c of CFG.LAYERS) {
      const info = await loadMeta(c.id);
      state.layers[c.id] = { on: !!info.ok, meta: info.meta, srcLayer: info.srcLayer };   // 사용자 결정: 관로 두 개 기본 켜짐(메타 조회 실패 시만 끔)
      const row = document.createElement('label'); row.className = 'lyr';
      row.innerHTML = '<input type="checkbox"><span class="sw" style="background:' + c.color + '"></span><span>' + c.label + '</span>';
      state.layers[c.id].cb = row.firstChild; row.firstChild.checked = state.layers[c.id].on;
      row.firstChild.onchange = (e) => {
        if (state.scn.running) { e.target.checked = state.layers[c.id].on; return; }
        state.layers[c.id].on = e.target.checked;
        if (e.target.checked) addTileLayer(c.id); else removeTileLayer(c.id);
      };
      box.appendChild(row);
    }
  }

  /* ---------- 초기화 ---------- */
  function hud() {
    if (!state.ready) return;
    const c = state.nmap.getCenter();
    $('hud').textContent = 'naver zoom ' + state.nmap.getZoom().toFixed(2) + '  center ' + c.lat().toFixed(5) + ', ' + c.lng().toFixed(5) +
      '\nML zoom ' + state.ml.getZoom().toFixed(2) + '  tile req ' + state.tileLog.length;
  }
  function probeRaf() { // 1초간 rAF 빈도: 숨김/스로틀 상태면 낮게 나옴 → fps 값 유효성 판단 근거
    const ts = []; const tick = (t) => { ts.push(t); if (t - ts[0] < 1000) requestAnimationFrame(tick); else {
      const dts = ts.slice(1).map((x, i) => x - ts[i]);
      state.rafProbe = { frames: ts.length - 1, hz: +(1000 / (dts.reduce((a, b) => a + b, 0) / dts.length)).toFixed(1), dt_max_ms: +Math.max(...dts).toFixed(1), visibility: document.visibilityState };
    } }; requestAnimationFrame(tick);
  }
  async function probeHeaders() { // 실제 타일 응답 헤더(캐시 정책 등) 1회 확인. 내부 측정 로그에는 넣지 않음
    try {
      const z = 15, x = 27947, y = 12682, r = await fetch(ORIGIN + '/tiles/' + CFG.LAYERS[0].id + '/' + z + '/' + x + '/' + y + '.pbf', { cache: 'no-store' });
      state.tileHeaders = { sample: z + '/' + x + '/' + y, status: r.status, cacheControl: r.headers.get('cache-control'), contentEncoding: r.headers.get('content-encoding'), xServerMs: r.headers.get('x-server-ms') };
      await r.arrayBuffer();
    } catch (e) { state.tileHeaders = { error: String(e) }; }
  }
  async function init() {
    try { performance.setResourceTimingBufferSize(100000); } catch (e) { /* noop */ }
    try { new PerformanceObserver((l) => l.getEntries().forEach((e) => state.longtasks.push({ start: e.startTime, dur: e.duration }))).observe({ entryTypes: ['longtask'] }); } catch (e) { /* 미지원 */ }
    await loadNaver();
    if (!window.naver || !naver.maps) throw new Error('naver.maps 미정의');
    // 최소 줌 = 서울 전체가 들어오는 줌(S_auto, 지도 이동 없는 계산). 초기 줌이 그보다 작으면 minZoom 으로 올림
    const mz0 = updateSAuto() || 6;
    state.nmap = new naver.maps.Map('naver', { center: new naver.maps.LatLng(CFG.DEFAULT_CENTER[0], CFG.DEFAULT_CENTER[1]), zoom: Math.max(CFG.DEFAULT_ZOOM, mz0), minZoom: mz0, maxZoom: 21 });
    initOverlay();
    await buildLayerUi();
    if (!state.ml.isStyleLoaded()) await new Promise((r) => state.ml.once('load', r));   // 기본 켜짐 관로를 지도에 올리기 전 스타일 로드 대기
    for (const id of Object.keys(state.layers)) if (state.layers[id].on) addTileLayer(id);
    const E = naver.maps.Event, n = state.nmap;
    ['zoomstart', 'dragstart', 'pinchstart'].forEach((ev) => E.addListener(n, ev, () => {
      if (!state.ready || state.session || state.inFindS || state.inMinZoom) return;
      if (!anyOverlayOn()) { if (!state.scn.running) document.dispatchEvent(new CustomEvent('speed:no-layer')); return; }   // 관로 미선택: 측정 안 함
      beginSession('user:' + ev);
    }));
    ['center_changed', 'zoom_changed', 'bounds_changed'].forEach((ev) => E.addListener(n, ev, onCamChange));
    E.addListener(n, 'idle', onNaverIdle);
    E.addListener(n, 'tilesloaded', () => { if (state.session) state.session.tNaverTiles = mark(); });
    updateSAuto(); applyMinZoom(); new ResizeObserver(() => { updateSAuto(); applyMinZoom(); }).observe($('naver'));   // 배율 기준 S_auto·최소 줌: 로드 시 + 지도 크기 변경 시(S_auto 계산은 지도 이동 없음)
    syncNow(); setInterval(hud, 300);
    probeRaf(); probeHeaders();
    window.addEventListener('resize', () => { try { n.autoResize && n.autoResize(); } catch (e) { /* noop */ } state.ml.resize();
      if (state.SView && ($('stage').clientWidth !== state.SView.stage[0] || $('stage').clientHeight !== state.SView.stage[1])) $('sInfo').textContent += '\n⚠ 스테이지 크기가 S 탐색 시점(' + state.SView.stage.join('x') + ')과 달라짐: S 재탐색 필요.'; });
    setScenario('new', state.S || state.S_auto || 11);  // 새 정의 기본: 현재 기준 줌(S_auto, 없으면 11). '범위 맞추기'로 갱신, 자동 실행 시 기준 줌이 바뀌었으면 다시 맞춤
    { const lc = document.getElementById('optLiveCold'), lb = lc && lc.closest('label'); if (lb) lb.hidden = false; }   // 검증 후 노출(직접 조작 측정마다 캐시 초기화 토글)
    state.ready = true;
    setStatus('준비됨 · 네이버 지도 v' + (naver.maps.VERSION || '?') + ' + MapLibre ' + state.mlVersion + ' · ' + location.origin, 'ok');
    window.__app = state; // 디버그/검증용
  }
  init().catch((e) => { console.error(e); setStatus('초기화 오류: ' + e.message, 'err'); });
})();
