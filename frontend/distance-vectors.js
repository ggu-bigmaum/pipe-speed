/* Existing vector-tile API, displayed independently from the TIFF renderer. */
(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  // A single Canvas stroke for a million-feature MVT can silently draw nothing.
  // Split rendering instructions, retaining every feature and its coordinates.
  const FEATURES_PER_CANVAS_BATCH = 16384;
  const COMPARE_MIN_ZOOM = 14;
  const COMPARE_PAIRS = [['kict_national', 'sa001'], ['kict_sewer_seoul', 'sb001']];
  const canvasBatch = Symbol('canvasBatch');
  function create({map, view, config, tilesBase, onChange, onSelect, onRecord, highlight}) {
    const entries = [];
    let pending, sequence = 0, selectionOrder = 0;
    const selected = () => entries.filter(entry => entry.checkbox.checked && entry.layer?.getVisible());
    function releaseEntry(entry) {
      entry.layer?.setVisible(false); entry.overlay?.setVisible(false);
      if (!entry.source) return;
      // Clear both source/render tile caches and invalidate late decode results.
      entry.generation++;
      for (const controller of entry.requests.values()) controller.abort('superseded');
      entry.requests.clear(); entry.failedTiles.clear(); entry.retryTiles.clear(); entry.failed = false;
      entry.source.refresh();
    }
    function syncZoomGate() {
      const restricted = view.getZoom() < COMPARE_MIN_ZOOM;
      for (const ids of COMPARE_PAIRS) {
        const pair = ids.map(id => entries.find(e => e.id === id)).filter(Boolean);
        const chosen = pair.filter(e => e.checkbox.checked).sort((a, b) => a.selectionOrder - b.selectionOrder);
        if (restricted && chosen.length > 1) {
          for (const entry of chosen.slice(1)) { entry.checkbox.checked = false; releaseEntry(entry); }
        }
        for (const entry of pair) {
          if (!entry.ready) continue;
          const blocked = restricted && !entry.checkbox.checked && pair.some(e => e !== entry && e.checkbox.checked);
          entry.checkbox.disabled = blocked;
          entry.note.textContent = blocked ? '확대하면 비교 가능 · 줌 14 이상' : entry.failed ? '타일 읽기 오류' : entry.readyNote;
        }
      }
      highlight?.syncVisibility();
    }
    const needed = (entry, tile) => {
      const coordinate = tile.getTileCoord(), grid = entry.source.getTileGrid();
      return coordinate[0] === grid.getZForResolution(view.getResolution(), 1) && ol.extent.intersects(grid.getTileCoordExtent(coordinate), view.calculateExtent(map.getSize()));
    };
    function refresh(entry) {
      entry.generation++;
      for (const controller of entry.requests.values()) controller.abort('superseded');
      entry.failed = false; entry.failedTiles.clear(); entry.retryTiles.clear();
      entry.source.refresh(); entry.note.textContent = entry.readyNote;
      highlight?.syncVisibility();
    }
    const updateTarget = () => {
      const items = selected();
      $('vectorTarget').textContent = items.length ? items.map(e => e.label).join(' · ') : '왼쪽에서 벡터타일을 선택하세요.';
      $('reloadVector').disabled = !items.length || Boolean(pending);
    };
    function measure(type = '벡터타일 표시') {
      pending?.cancel();
      const items = selected(); updateTarget();
      if (!items.length || document.hidden) { $('vectorMs').textContent = '—'; $('vectorState').textContent = ''; return; }
      for (const entry of items) { entry.failed = [...entry.failedTiles.values()].some(tile => needed(entry, tile)); if (!entry.failed) entry.note.textContent = entry.readyNote; }
      if (items.some(e => e.failed)) { $('vectorMs').textContent = '—'; $('vectorState').textContent = '타일을 읽지 못했습니다. 다시 읽기를 눌러 주세요.'; return; }
      const token = ++sequence, started = performance.now();
      let done = false, timer;
      const cleanup = () => { clearTimeout(timer); map.un('rendercomplete', rendered); if (pending?.token === token) pending = null; updateTarget(); };
      const finish = (message, cancelled = false) => {
        if (done) return; done = true; cleanup();
        if (cancelled || document.hidden) return;
        if (message) {
          $('vectorMs').textContent = '—'; $('vectorState').textContent = message;
          onRecord({case: items.map(e => e.id).join('+'), label: items.map(e => e.label).join(' + '), type, download_ms: null, display_ms: null, total_ms: null, bytes: null, status: message});
          return;
        }
        const elapsed = performance.now() - started;
        $('vectorMs').textContent = elapsed.toFixed(1) + ' ms'; $('vectorState').textContent = '현재 화면 표시 완료';
        // This timer includes tile receipt as well as rendering; it cannot be
        // reported as a separately measured download or decode interval.
        onRecord({case: items.map(e => e.id).join('+'), label: items.map(e => e.label).join(' + '), type, download_ms: null, display_ms: null, total_ms: elapsed, bytes: null, status: '완료'});
      };
      const rendered = () => requestAnimationFrame(() => requestAnimationFrame(() => finish()));
      pending = {token, cancel: () => finish(null, true), fail: message => finish(message)};
      $('vectorMs').textContent = '—'; $('vectorState').textContent = '현재 화면을 표시하는 중…'; updateTarget();
      map.on('rendercomplete', rendered);
      timer = setTimeout(() => finish('30초 안에 표시가 끝나지 않았습니다.'), 30000);
      map.render();
    }
    function cancelObsolete() {
      for (const entry of entries) {
        for (const [tile, controller] of entry.requests) {
          if (!entry.checkbox.checked || !needed(entry, tile)) {
            entry.retryTiles.set(tile.getTileCoord().join('/'), tile);
            controller.abort('offscreen');
          }
        }
        if (entry.checkbox.checked && [...entry.retryTiles.values()].some(tile => needed(entry, tile))) {
          // A source tile's parent render tile also caches ERROR. Refresh both
          // caches when returning to a cancelled area; resetting source state
          // alone would leave that area blank.
          refresh(entry);
        }
      }
    }
    function redraw() { syncZoomGate(); cancelObsolete(); onChange(); measure(); }
    view.on('change:center', redraw); view.on('change:resolution', redraw);
    document.addEventListener('visibilitychange', () => {
      if (document.hidden && pending) { pending.cancel(); $('vectorMs').textContent = '—'; $('vectorState').textContent = '탭이 숨겨져 측정을 중단했습니다.'; }
    });
    $('reloadVector').onclick = () => {
      for (const entry of selected()) refresh(entry);
      measure('벡터타일 다시 읽기');
    };
    const layers = config.LAYERS || [];
    const mainCount = layers.filter(layer => !layer.reference).length;
    $('vectorCount').textContent = mainCount; $('layerCount').textContent = mainCount + '종';
    for (const layer of layers) {
      const row = document.createElement('label'), checkbox = document.createElement('input'), copy = document.createElement('span'), title = document.createElement('strong'), note = document.createElement('small'), line = document.createElement('span');
      row.className = 'layer-row'; checkbox.type = 'checkbox'; checkbox.disabled = true; checkbox.id = 'vector-' + layer.id;
      copy.className = 'layer-copy'; title.textContent = layer.listLabel || layer.label; note.textContent = '자료 확인 중…';
      row.title = [layer.listLabel || layer.label, layer.badge, layer.note].filter(Boolean).join(' · ');
      checkbox.setAttribute('aria-label', row.title);
      if (layer.badge) { const badge = document.createElement('span'); badge.className = 'scope-badge'; badge.textContent = layer.badge; title.append(badge); }
      line.className = 'layer-line'; line.style.background = layer.color || '#4dabf7'; copy.append(title, note); row.append(checkbox, copy, line); $(layer.container || 'vectorList').append(row);
      const entry = {...layer, readyNote: '', color: layer.color || '#4dabf7', row, checkbox, note, source: null, layer: null, failed: false, generation: 0, tileGenerations: new WeakMap(), failedTiles: new Map(), retryTiles: new Map(), requests: new Map(), cancelledTiles: new WeakSet()}; entries.push(entry);
      checkbox.onchange = () => {
        if (!entry.ready) return;
        if (checkbox.checked) entry.selectionOrder = ++selectionOrder;
        syncZoomGate();
        if (entry.failed && checkbox.checked) refresh(entry);
        if (checkbox.checked) { entry.layer.setVisible(true); entry.overlay?.setVisible(true); }
        else if (entry.layer.getVisible()) releaseEntry(entry);
        cancelObsolete(); syncZoomGate(); onChange(); onSelect(); measure();
      };
      (async () => {
        try {
          const tileId = entry.tileId || entry.id;
          const response = await fetch(tilesBase + encodeURIComponent(tileId) + '/meta.json', {cache: 'no-store', signal: AbortSignal.timeout(10000)});
          if (!response.ok) throw new Error(response.status === 404 ? '자료 없음 · 서버 데이터 연결 필요' : '자료 확인 실패');
          const meta = await response.json();
          if (meta.complete === false) throw new Error(`설치 미완료 · ${meta.missing_regions?.length || '일부'}개 지역 자료 확인 필요`);
          if (!Number.isInteger(meta.minzoom) || !Number.isInteger(meta.maxzoom) || meta.maxzoom < meta.minzoom) throw new Error('타일 정보 확인 필요');
          const layerIds = meta.vector_layer_ids || [meta.vector_layer_id || entry.id];
          if (!Array.isArray(layerIds) || !layerIds.length || layerIds.some(id => typeof id !== 'string' || !id)) throw new Error('타일 레이어 정보 확인 필요');
          const format = new ol.format.MVT({layers: layerIds});
          entry.format = format;
          const styles = [];
          const stroke = new ol.style.Stroke({color: entry.color, width: 1.8});
          const featureStyle = (feature, resolution) => {
            const batch = feature[canvasBatch] || 0;
            const baseStyle = styles[batch] || (styles[batch] = new ol.style.Style({stroke, zIndex: batch}));
            return highlight ? highlight.style(feature, entry, baseStyle, resolution) : baseStyle;
          };
          const source = new ol.source.VectorTile({
            format, minZoom: meta.minzoom, maxZoom: meta.maxzoom, tileSize: config.TILE_SIZE || 512, wrapX: false, transition: 0,
            url: tilesBase + encodeURIComponent(tileId) + '/{z}/{x}/{y}.pbf',
            tileLoadFunction(tile, url) {
              tile.setLoader(async (extent, resolution, projection) => {
                // A queued OL loader may run after a zoom gate or checkbox hide.
                if (!entry.checkbox.checked || !entry.layer?.getVisible()) { tile.setFeatures([]); return; }
                const controller = new AbortController(), timer = setTimeout(() => controller.abort('timeout'), 30000);
                const generation = entry.generation; entry.tileGenerations.set(tile, generation);
                entry.requests.set(tile, controller);
                try {
                  const response = await fetch(url, {signal: controller.signal});
                  if (!response.ok) throw new Error('타일 응답 오류');
                  if (response.status === 204) { tile.setFeatures([]); return; }
                  const data = await response.arrayBuffer();
                  if (controller.signal.aborted || generation !== entry.generation) throw new Error('타일 요청이 바뀌었습니다.');
                  const features = data.byteLength ? format.readFeatures(data, {extent, featureProjection: projection}) : [];
                  for (let i = FEATURES_PER_CANVAS_BATCH; i < features.length; i++) {
                    // Private render metadata, not an attribute in the tile or source data.
                    features[i][canvasBatch] = Math.floor(i / FEATURES_PER_CANVAS_BATCH);
                  }
                  // Hash/proof work is asynchronous and never delays the base tile.
                  // Attach original ordinals before OL can sort its feature array.
                  highlight?.attachTile(entry, tile, features, data);
                  tile.setFeatures(features);
                } catch (_) {
                  if (controller.signal.reason === 'offscreen' || controller.signal.reason === 'superseded' || generation !== entry.generation) {
                    // Release OL's load queue. cancelObsolete refreshes the
                    // source/render caches when this area is needed again.
                    entry.cancelledTiles.add(tile); tile.setState(3);
                  } else tile.setState(3);
                } finally { clearTimeout(timer); if (entry.requests.get(tile) === controller) entry.requests.delete(tile); }
              });
            }
          });
          source.on('tileloaderror', event => {
            if (entry.cancelledTiles.delete(event.tile)) return;
            if (entry.tileGenerations.get(event.tile) !== entry.generation) return;
            entry.failedTiles.set(event.tile.getKey(), event.tile);
            if (entry.checkbox.checked && needed(entry, event.tile)) {
              entry.failed = true; note.textContent = '타일 읽기 오류';
              pending?.fail('타일을 읽지 못했습니다. 다시 읽기를 눌러 주세요.');
            }
          });
          entry.source = source;
          entry.layer = new ol.layer.VectorTile({source, visible: false, zIndex: 10, style: featureStyle});
          if (entry.overlayPath) {
            const base = config.API_BASE ? config.API_BASE.replace(/\/$/, '') + '/' : new URL('../', location.href).href;
            const response = await fetch(base + entry.overlayPath, {signal: AbortSignal.timeout(30000)});
            if (!response.ok) throw new Error('참고용 보정 자료 확인 필요');
            const data = await response.json();
            if (data.type !== 'FeatureCollection' || !Array.isArray(data.features)) throw new Error('보정 연결 자료 형식 확인 필요');
            const features = new ol.format.GeoJSON().readFeatures(data, {dataProjection:'EPSG:4326', featureProjection:view.getProjection()});
            entry.overlay = new ol.layer.Vector({source:new ol.source.Vector({features}),visible:false,zIndex:11,
              style:new ol.style.Style({stroke:new ol.style.Stroke({color:'#df7c24',width:3})})});
            map.addLayer(entry.overlay);
            entry.readyNote = '회색: 원본 · 주황: 추가 연결';
          }
          map.addLayer(entry.layer); entry.ready = true; checkbox.disabled = false; note.textContent = entry.readyNote; syncZoomGate();
        } catch (error) { row.classList.add('unavailable'); note.textContent = error.message; row.title = error.message; }
      })();
    }
    return {entries, selected};
  }
  window.PipeVectorCatalog = {create};
})();
