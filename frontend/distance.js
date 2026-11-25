/* Original GeoTIFF benchmarks. Result lookup is keyed by the original grid ID. */
(async function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const cfg = window.APP_CONFIG || {};
  const core = window.PipeDistanceCore;
  const prefix = cfg.API_BASE ? cfg.API_BASE.replace(/\/$/, '') + '/distance/' : new URL('../distance/', location.href).href;
  const datasets = [
    {id: 'kict_water_seoul', label: '건기연 상수(기존 서울)', route: '', inputs: {case1: 'case1', case2: 'case2'}},
    {id: 'kict_sewer_seoul', label: '건기연 하수(서울·보정본)', route: 'kict_sewer_seoul/', inputs: {case1: 'sewerCase1', case2: 'sewerCase2'}},
    {id: 'seoul_water', label: '서울시 상수', route: 'seoul_water/', statusStem: 'seoulWater', available: false, inputs: {case1: 'seoulWaterCase1', case2: 'seoulWaterCase2'}},
    {id: 'seoul_sewer', label: '서울시 하수', route: 'seoul_sewer/', statusStem: 'seoulSewer', available: false, inputs: {case1: 'seoulSewerCase1', case2: 'seoulSewerCase2'}}
  ];
  const municipalDatasets = datasets.filter(dataset => dataset.statusStem);
  const requestedDatasetId = new URLSearchParams(location.search).get('dataset');
  const requestedDataset = requestedDatasetId === 'kict_water_seoul' ? 'kict_water_national_11' : requestedDatasetId === 'kict_sewer_national_11' ? 'kict_sewer_seoul' : requestedDatasetId;
  const nationalGroups = ['water', 'sewer'].map(utility => {
    const stem = utility === 'water' ? 'nationalWater' : 'nationalSewer';
    return {utility, stem, regions: [], inputs: {case1: stem + 'Case1', case2: stem + 'Case2'}};
  });
  let selectedDataset = datasets.find(d => d.id === requestedDataset) || datasets[0];
  let datasetRevision = 0;
  let rasterRequest = 0, rasterTask = Promise.resolve();
  let rasterEntries = [], currentSources = [];
  const datasetApi = (dataset, path) => prefix + dataset.route + path;
  const metadataExtent = m => [m.origin[0], m.origin[1] - m.height * m.pixel_size[1], m.origin[0] + m.width * m.pixel_size[0], m.origin[1]];
  async function mapLimited(items, callback, signal) {
    const results = new Array(items.length); let cursor = 0, failed = false;
    await Promise.all(Array.from({length: Math.min(4, items.length)}, async () => {
      while (cursor < items.length && !failed && !signal.aborted) {
        const index = cursor++;
        try { results[index] = await callback(items[index], index); } catch (error) { failed = true; throw error; }
      }
    }));
    if (signal.aborted) throw new DOMException('중단', 'AbortError');
    return results;
  }
  // Abort also releases non-fetch work such as GeoTIFF.getView(). Its late
  // result must never attach a cancelled source to the next selected layer.
  function loadingStep(operation, controller, label) {
    return new Promise((resolve, reject) => {
      const signal = controller.signal;
      let done = false;
      const finish = (error, value) => {
        if (done) return; done = true;
        clearTimeout(timer); signal.removeEventListener('abort', aborted);
        error ? reject(error) : resolve(value);
      };
      const aborted = () => finish(signal.reason || new DOMException('중단', 'AbortError'));
      const timer = setTimeout(() => controller.abort(new Error(`${label} · 30초 동안 완료되지 않았습니다. 다시 선택해 주세요.`)), 30000);
      signal.addEventListener('abort', aborted, {once: true});
      if (signal.aborted) aborted();
      else Promise.resolve().then(operation).then(value => finish(null, value), error => finish(error));
    });
  }
  const rasterInputs = () => [...document.querySelectorAll('input[data-raster]')].map(input => input.id);
  const rasterChecked = () => rasterInputs().some(id => $(id).checked);
  function checkRaster(id) {
    rasterInputs().forEach(input => { $(input).checked = input === selectedDataset.inputs[id]; });
  }
  const defaults = {breaks: [10, 50, 100, 500], colors: ['#08306b', '#2171b5', '#4292c6', '#9ecae1', '#f7fbff']};
  let style = structuredClone(defaults), meta, map, view, selection, currentLayer, currentSource, vectorMap, vectorCatalog, gridLayer, pipePicker;
  let selectedCase = 'case1', busy = false, running = false, stopped = false, active, clickController, clickSeq = 0;
  let records = [], sourceRevision = 0, suppressMove = false, ready = false;
  let moveController, gridController, gridTimer, gridRevision = 0, gridDatasetId;
  const status = (message, error = false) => { $('status').textContent = message; $('status').classList.toggle('error', error); };
  const ms = value => value == null ? '—' : value.toFixed(1) + ' ms';
  const number = value => Number(value).toLocaleString('ko-KR', {maximumFractionDigits: 3});
  const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
  const visibleDocument = () => { if (document.hidden) throw new Error('탭이 숨겨져 측정을 중단했습니다.'); };
  const caseName = id => `${selectedDataset.label} · Case ${id === 'case1' ? 1 : 2}`;
  function measureTab(kind) {
    for (const name of ['raster', 'vector']) {
      const selected = kind === name;
      $(name + 'Tab').setAttribute('aria-selected', String(selected));
      $(name + 'Tab').tabIndex = selected ? 0 : -1;
      $(name + 'SpeedPane').hidden = !selected;
    }
  }
  for (const name of ['raster', 'vector']) {
    $(name + 'Tab').onclick = () => measureTab(name);
    $(name + 'Tab').onkeydown = event => {
      if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
        event.preventDefault(); const next = event.key === 'Home' ? 'raster' : event.key === 'End' ? 'vector' : name === 'raster' ? 'vector' : 'raster'; measureTab(next); $(next + 'Tab').focus();
      }
    };
  }
  function closeDetails() { ++clickSeq; clickController?.abort(); selection?.clear(); pipePicker?.clear(); $('cellDetails').hidden = true; $('detailPanel').hidden = true; }
  $('closeDetails').onclick = closeDetails;
  $('editStyle').onclick = () => $('styleDialog').showModal();
  $('closeStyle').onclick = () => $('styleDialog').close();
  const narrow = matchMedia('(max-width:700px)');
  function sidebar(open) { document.body.classList.toggle('sidebar-open', open); $('toggleLayers').setAttribute('aria-expanded', String(open)); $('layerSidebar').inert = narrow.matches && !open; }
  $('toggleLayers').onclick = () => sidebar(!document.body.classList.contains('sidebar-open'));
  $('closeLayers').onclick = () => sidebar(false);
  narrow.addEventListener('change', () => sidebar(false)); sidebar(false);
  if (narrow.matches) { $('speedPanel').open = false; $('legendPanel').open = false; }
  function syncLegend() {
    const shown = Boolean(currentLayer?.getVisible());
    gridLayer?.setVisible(shown && gridDatasetId === selectedDataset.id);
    $('rasterLegend').hidden = !shown;
    $('legendCase').textContent = caseName(selectedCase);
    $('measureCase').textContent = rasterChecked() ? caseName(selectedCase) : '선택 없음';
    $('vectorLegend').replaceChildren();
    const vectors = vectorCatalog?.selected() || [];
    for (const entry of vectors) { const row = document.createElement('div'), line = document.createElement('span'); row.className = 'vector-legend-row'; line.className = 'layer-line'; line.style.background = entry.color; row.append(line, document.createTextNode(entry.label)); $('vectorLegend').append(row); }
    $('emptyLegend').hidden = shown || vectors.length > 0;
  }
  async function json(url, options) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `서버 응답 오류 (${response.status})`);
    return data;
  }
  function controls() {
    for (const id of rasterInputs()) $(id).disabled = !ready || running;
    for (const dataset of municipalDatasets) for (const input of Object.values(dataset.inputs)) $(input).disabled ||= !dataset.available;
    for (const id of ['reload', 'run', 'fit']) $(id).disabled = !ready || busy || running;
    for (const group of nationalGroups) {
      const available = group.regions.some(region => region.available);
      $(group.stem + 'Region').disabled = !ready || running || !available;
      for (const input of Object.values(group.inputs)) $(input).disabled ||= !available;
    }
    $('stop').disabled = !busy && !running;
    $('stop').hidden = !busy && !running;
    $('repeat').disabled = busy || running;
    $('applyStyle').disabled = busy || running;
    $('resetStyle').disabled = busy || running;
    $('opacity').disabled = busy || running;
    $('run').disabled = !ready || !meta || busy || running;
    $('reload').disabled = !ready || !meta || busy || running || !rasterChecked();
  }
  function rasterStyle() {
    const expression = ['case', ['==', ['band', 2], 0], 'rgba(0,0,0,0)'];
    style.breaks.forEach((limit, i) => expression.push(['<=', ['band', 1], limit], style.colors[i]));
    expression.push(style.colors[style.breaks.length]);
    return {color: expression};
  }
  function legend() {
    $('legend').replaceChildren();
    const last = style.colors.length - 1;
    style.colors.forEach((color, i) => {
      const row = document.createElement('span'), swatch = document.createElement('i');
      swatch.className = 'swatch'; swatch.style.backgroundColor = color;
      row.append(swatch, document.createTextNode(i === 0 ? `≤${style.breaks[0]}` : i === last ? `>${style.breaks[last - 1]}` : `${style.breaks[i - 1]}–${style.breaks[i]}`));
      $('legend').append(row);
    });
  }
  function editBins() {
    $('bins').replaceChildren();
    const last = style.colors.length - 1;
    style.colors.forEach((color, i) => {
      const label = document.createElement('label'), title = document.createElement('span'), input = document.createElement('input');
      title.textContent = i < last ? `구간 ${i + 1} 상한` : '마지막 구간'; input.type = 'color'; input.value = color; input.id = 'color' + i; input.setAttribute('aria-label', `구간 ${i + 1} 색상`);
      label.append(title);
      if (i < last) { const limit = document.createElement('input'); limit.type = 'number'; limit.min = '0'; limit.step = 'any'; limit.value = style.breaks[i]; limit.id = 'break' + i; limit.setAttribute('aria-label', `구간 ${i + 1} 상한 미터`); label.append(limit); }
      label.append(input); $('bins').append(label);
    });
  }
  try { const saved = JSON.parse(localStorage.getItem('pipe-distance-style-v2')); if (saved && core.validStyle(saved.breaks, saved.colors)) style = saved; } catch (_) { /* Optional saved preference. */ }
  editBins(); legend();
  $('applyStyle').onclick = () => {
    const nBreaks = style.breaks.length, nColors = style.colors.length;
    const next = {breaks: Array.from({length: nBreaks}, (_, i) => $('break' + i).value === '' ? NaN : Number($('break' + i).value)), colors: Array.from({length: nColors}, (_, i) => $('color' + i).value)};
    if (!core.validStyle(next.breaks, next.colors)) { $('styleError').textContent = `0 이상의 경계값 ${nBreaks}개를 작은 순서로 입력하세요.`; return; }
    style = next; $('styleError').textContent = ''; legend();
    if (currentLayer) currentLayer.setStyle(rasterStyle());
    try { localStorage.setItem('pipe-distance-style-v2', JSON.stringify(style)); } catch (_) {}
  };
  $('resetStyle').onclick = () => { style = structuredClone(defaults); editBins(); $('applyStyle').click(); };
  $('opacity').oninput = () => { $('opacityValue').textContent = $('opacity').value + '%'; if (currentLayer) currentLayer.setOpacity(Number($('opacity').value) / 100); gridLayer?.setOpacity(Number($('opacity').value) / 100); };
  function metrics(download, display, total) { $('downloadMs').textContent = ms(download); $('renderMs').textContent = ms(display); $('totalMs').textContent = ms(total); }
  function addRecord(record) {
    const vector = record.type?.includes('벡터');
    records.push({time: new Date().toISOString(), dataset_id: vector ? '' : selectedDataset.id, dataset_label: vector ? '' : selectedDataset.label, case: selectedCase, zoom: view.getZoom().toFixed(2), viewport: map.getSize().join('x'), ...record});
    $('recordCount').textContent = records.length; $('csv').disabled = !records.length; renderRecords();
  }
  function renderRecords() {
    $('recordsBody').replaceChildren();
    records.slice(-200).reverse().forEach(record => {
      const tr = document.createElement('tr');
      [record.type, record.label || `${record.dataset_label} · ${record.case}`, record.download_ms?.toFixed(1) ?? '—', record.display_ms?.toFixed(1) ?? '—', record.total_ms?.toFixed(1) ?? '—', record.bytes == null ? '—' : (record.bytes / 1000).toFixed(1), record.zoom, record.status].forEach(value => { const td = document.createElement('td'); td.textContent = value; tr.append(td); });
      $('recordsBody').append(tr);
    });
    $('summary').textContent = datasets.flatMap(dataset => ['case1', 'case2'].map(id => {
      const rs = records.filter(r => r.dataset_id === dataset.id && r.case === id && r.type === '원본 읽기' && r.status === '완료');
      return rs.length ? `${dataset.label} ${id}: ${rs.length}회 · 평균 ${ms(rs.reduce((sum, r) => sum + r.total_ms, 0) / rs.length)}` : '';
    })).filter(Boolean).join(' / ');
  }
  $('showRecords').onclick = () => $('recordsDialog').showModal();
  $('closeRecords').onclick = () => $('recordsDialog').close();
  $('clearRecords').onclick = () => { records = []; $('recordCount').textContent = 0; $('csv').disabled = true; renderRecords(); };
  $('csv').onclick = () => {
    const keys = ['time', 'type', 'dataset_id', 'dataset_label', 'case', 'download_ms', 'display_ms', 'total_ms', 'bytes', 'zoom', 'viewport', 'status'];
    const quote = value => '"' + String(value ?? '').replace(/"/g, '""') + '"';
    const blob = new Blob(['\uFEFF' + [keys, ...records.map(r => keys.map(k => r[k]))].map(row => row.map(quote).join(',')).join('\r\n')], {type: 'text/csv;charset=utf-8'});
    const url = URL.createObjectURL(blob), a = document.createElement('a'); a.href = url; a.download = 'distance-speed-' + new Date().toISOString().slice(0, 10) + '.csv'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  function removeRaster() {
    moveController?.abort(); $('moveMs').textContent = '—';
    cancelGrid();
    gridLayer?.setVisible(false);
    sourceRevision++;
    if (currentLayer) { map.removeLayer(currentLayer); currentLayer.dispose(); }
    for (const source of currentSources) source.dispose();
    currentSources = [];
    currentLayer = currentSource = null;
  }
  function afterRender(signal, sources = currentSources.slice()) {
    return new Promise((resolve, reject) => {
      let done = false;
      const finish = error => {
        if (done) return; done = true; clearTimeout(timer); map.un('rendercomplete', rendered); sources.forEach(source => source.un('tileloaderror', failed)); signal?.removeEventListener('abort', aborted);
        error ? reject(error) : resolve();
      };
      const rendered = async () => { await frame(); await frame(); finish(signal?.aborted ? new DOMException('중단', 'AbortError') : document.hidden ? new Error('탭이 숨겨져 측정을 중단했습니다.') : null); };
      const failed = () => finish(new Error('래스터 해석·그리기에 실패했습니다.'));
      const aborted = () => finish(new DOMException('중단', 'AbortError'));
      const timer = setTimeout(() => finish(new Error('30초 안에 래스터 표시가 끝나지 않았습니다.')), 30000);
      map.on('rendercomplete', rendered); sources.forEach(source => source.on('tileloaderror', failed)); signal?.addEventListener('abort', aborted, {once: true});
      if (signal?.aborted) aborted(); else map.render();
    });
  }
  async function loadCase(id) {
    if (busy) return false;
    visibleDocument(); busy = true; selectedCase = id; active = new AbortController(); controls();
    if (!running) $('runState').textContent = '';
    checkRaster(id);
    measureTab('raster'); $('measureCase').textContent = caseName(id);
    metrics(null, null, null); $('measurementInfo').textContent = '다운로드 및 표출 중…'; status(`${caseName(id)} 원본 읽는 중…`);
    const controller = active;
    let started, downloadDone, byteCount, success = false;
    try {
      removeRaster(); await frame(); started = performance.now();
      let downloaded = 0, decoded = 0;
      const progress = (phase, count) => { if (!controller.signal.aborted) status(`${caseName(id)} · ${phase} ${count}/${rasterEntries.length}`); };
      progress('다운로드', 0);
      const downloads = await mapLimited(rasterEntries, async entry => {
        const file = entry.meta.cases.find(c => c.id === id);
        const data = await loadingStep(async () => {
          const response = await fetch(datasetApi(entry.dataset, 'files/' + file.filename + '?fresh=' + Date.now()), {cache: 'no-store', signal: controller.signal});
          if (!response.ok) throw new Error(`${entry.dataset.label} 원본 파일을 받지 못했습니다 (${response.status}).`);
          return response.arrayBuffer();
        }, controller, `${entry.dataset.label} 다운로드`);
        progress('다운로드', ++downloaded);
        return {entry, data};
      }, controller.signal);
      downloadDone = performance.now(); byteCount = downloads.reduce((sum, item) => sum + item.data.byteLength, 0);
      metrics(downloadDone - started, null, null); progress('파일 해석', 0);
      const sources = await mapLimited(downloads, async ({entry, data}) => {
        const source = new ol.source.GeoTIFF({sources: [{blob: new Blob([data]), nodata: entry.meta.nodata}], normalize: false, interpolate: false, transition: 0, wrapX: false});
        source.set('dataset_id', entry.dataset.id);
        currentSources.push(source);
        const sourceView = await loadingStep(() => source.getView(), controller, `${entry.dataset.label} 파일 해석`);
        controller.signal.throwIfAborted();
        if (sourceView.projection.getCode() !== entry.meta.crs) throw new Error('TIF와 result의 좌표계가 다릅니다.');
        source.setTileGridForProjection(view.getProjection(), ol.tilegrid.createXYZ({maxZoom: view.getMaxZoom()}));
        progress('파일 해석', ++decoded);
        return {source, extent: ol.proj.transformExtent(metadataExtent(entry.meta), entry.meta.crs, view.getProjection(), 24)};
      }, controller.signal);
      currentSources = sources.map(item => item.source); currentSource = currentSources[0];
      // Reproject at the display zoom. The default grid stops near the 100 m
      // source resolution; enlarging those coarse Mercator pixels moves the
      // apparent cell boundaries away from the original UTM grid.
      // All regional sources share this texture cache. At wide viewports the
      // default 512 entries cannot hold even one frame (17 * 60 tiles), so tiles
      // are evicted before rendercomplete can fire. Reserve two view levels.
      const size = map.getSize(), width = Math.max(size[0], screen.width), height = Math.max(size[1], screen.height);
      const cacheSize = Math.max(512, 2 * sources.length * (Math.ceil(width / 256) + 2) * (Math.ceil(height / 256) + 2));
      currentLayer = new ol.layer.WebGLTile({source: currentSource, cacheSize,
        ...(sources.length > 1 ? {sources: extent => sources.filter(item => ol.extent.intersects(extent, item.extent)).map(item => item.source)} : {}),
        style: rasterStyle(), opacity: Number($('opacity').value) / 100});
      map.getLayers().insertAt(0, currentLayer);
      status(`${caseName(id)} · 지도에 그리는 중…`);
      await afterRender(controller.signal); visibleDocument();
      const ended = performance.now(), download = downloadDone - started, display = ended - downloadDone;
      metrics(download, display, ended - started);
      $('measurementInfo').textContent = `${downloads.length > 1 ? downloads.length + '개 지역 TIF' : meta.cases.find(c => c.id === id).filename} · ${(byteCount / 1000).toFixed(1)} KB · HTTP 캐시 우회`;
      addRecord({type: '원본 읽기', download_ms: download, display_ms: display, total_ms: ended - started, bytes: byteCount, status: '완료'});
      status(`${caseName(id)} 표시 완료 · 격자를 클릭해 보세요.`); success = true;
    } catch (error) {
      const cancelled = controller.signal.aborted && controller.signal.reason?.name === 'AbortError';
      controller.abort();
      removeRaster(); metrics(null, null, null);
      const message = cancelled ? '불러오기를 중단했습니다.' : error.message;
      if (cancelled) rasterInputs().forEach(input => { $(input).checked = false; });
      status(message, !cancelled); $('measurementInfo').textContent = message;
      addRecord({type: '원본 읽기', download_ms: downloadDone && started ? downloadDone - started : null, display_ms: null, total_ms: null, bytes: byteCount ?? null, status: message});
    } finally { busy = false; active = null; syncLegend(); controls(); if (success) scheduleGrid(); }
    return success;
  }
  function stop() { ++rasterRequest; stopped = true; active?.abort(); $('runState').textContent = '중단 요청'; }
  $('stop').onclick = stop;
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { moveController?.abort(); if (busy || running) stop(); }
  });
  function gridHint(message = '') { $('gridHint').textContent = message; $('gridHint').hidden = !message; }
  function cancelGrid(clear = false) {
    ++gridRevision; clearTimeout(gridTimer); gridController?.abort(); gridController = null;
    if (clear) { gridLayer?.getSource().clear(); gridDatasetId = null; gridLayer?.setVisible(false); }
  }
  // Densifying the transformed extent covers curved UTM/Mercator edges too.
  function viewportGridBounds(metadata) {
    const extent = ol.proj.transformExtent(view.calculateExtent(map.getSize()), view.getProjection(), metadata.crs, 24);
    const [x, y] = metadata.origin, [sx, sy] = metadata.pixel_size;
    let min_col = Math.floor((extent[0] - x) / sx), max_col = Math.floor((extent[2] - x) / sx);
    let min_row = Math.floor((y - extent[3]) / sy), max_row = Math.floor((y - extent[1]) / sy);
    if (![min_col, max_col, min_row, max_row].every(Number.isFinite) || max_col < 0 || max_row < 0 || min_col >= metadata.width || min_row >= metadata.height) return null;
    min_col = Math.max(0, min_col); max_col = Math.min(metadata.width - 1, max_col);
    min_row = Math.max(0, min_row); max_row = Math.min(metadata.height - 1, max_row);
    return {min_col, max_col, min_row, max_row};
  }
  function scheduleGrid() {
    clearTimeout(gridTimer);
    if (!meta || !currentLayer?.getVisible()) return;
    if (meta.grid_mode !== 'viewport' && gridDatasetId === selectedDataset.id) { syncLegend(); return; }
    gridTimer = setTimeout(loadGrid, meta.grid_mode === 'viewport' ? 180 : 0);
  }
  async function loadGrid() {
    if (!meta || !currentLayer?.getVisible()) return;
    cancelGrid();
    const revision = gridRevision, dataRevision = datasetRevision, dataset = selectedDataset;
    const controller = gridController = new AbortController();
    const requests = rasterEntries.map(entry => {
      if (entry.meta.grid_mode !== 'viewport') return {...entry, path: 'gridlines', count: 0};
      const bounds = viewportGridBounds(entry.meta);
      return bounds ? {...entry, path: 'gridlines?' + new URLSearchParams(bounds), count: (bounds.max_col - bounds.min_col + 1) * (bounds.max_row - bounds.min_row + 1)} : null;
    }).filter(Boolean);
    if (meta.grid_mode === 'viewport') {
      const count = requests.reduce((sum, request) => sum + request.count, 0);
      if (!requests.length || view.getZoom() < 12 || count > (meta.grid_cell_limit || 12000)) {
        gridLayer.getSource().clear(); gridDatasetId = null; syncLegend();
        gridHint(requests.length ? '격자선을 보려면 지도를 확대해 주세요.' : '선택 지역의 격자 범위 밖입니다.');
        return;
      }
    }
    try {
      const results = await mapLimited(requests, request => json(datasetApi(request.dataset, request.path), {cache: 'no-store', signal: controller.signal}), controller.signal);
      if (controller.signal.aborted || revision !== gridRevision || dataRevision !== datasetRevision || dataset !== selectedDataset || !currentLayer?.getVisible()) return;
      const omitted = results.find(data => data.omitted);
      if (omitted) {
        gridLayer.getSource().clear(); gridDatasetId = null; syncLegend();
        gridHint(omitted.reason || '격자선을 보려면 지도를 확대해 주세요.'); return;
      }
      const features = results.map(data => {
        const step = Math.min(...data.pixel_size);
        const lines = data.geometry.coordinates.map(([a, b]) => {
          const count = Math.max(1, Math.round(Math.hypot(b[0] - a[0], b[1] - a[1]) / step));
          return Array.from({length: count + 1}, (_, i) => [a[0] + (b[0] - a[0]) * i / count, a[1] + (b[1] - a[1]) * i / count]);
        });
        return new ol.Feature(new ol.geom.MultiLineString(lines).transform(data.crs, view.getProjection()));
      });
      gridLayer.getSource().clear();
      gridLayer.getSource().addFeatures(features);
      gridDatasetId = dataset.id; gridHint();
      syncLegend();
    } catch (error) {
      if (error.name !== 'AbortError' && revision === gridRevision && dataRevision === datasetRevision) gridHint('격자선을 불러오지 못했습니다. 지도를 이동하거나 자료를 다시 선택해 주세요.');
    }
  }
  async function switchDataset(dataset, id) {
    if (busy || running) return;
    if (dataset === selectedDataset && meta) {
      await loadCase(id);
      return;
    }
    busy = true; active = new AbortController(); const controller = active;
    ++datasetRevision; closeDetails(); removeRaster(); cancelGrid(true); gridHint();
    selectedDataset = dataset; selectedCase = id; meta = null; rasterEntries = [];
    $('downloadResult').removeAttribute('href');
    rasterInputs().forEach(input => { $(input).checked = false; });
    $(dataset.inputs[id]).checked = true;
    controls(); syncLegend(); metrics(null, null, null); status(`${dataset.label} 자료 확인 중…`);
    $('measureCase').textContent = `${dataset.label} · 자료 확인 중`;
    let loaded = false;
    try {
      const entries = await mapLimited(dataset.members || [dataset], async member => {
        const m = await loadingStep(() => json(datasetApi(member, 'meta'), {cache: 'no-store', signal: controller.signal}), controller, `${member.label} 자료 확인`);
        controller.signal.throwIfAborted();
        if (m.dataset_id !== member.id) throw new Error('선택한 자료와 서버의 자료 구분이 다릅니다.');
        return {dataset: member, meta: m};
      }, controller.signal);
      if (!entries.length) throw new Error('표시할 지역 자료가 없습니다.');
      rasterEntries = entries; meta = entries[0].meta;
      if (dataset.members) {
        if (entries.some(entry => entry.meta.crs !== meta.crs || entry.meta.pixel_size.some((size, i) => size !== meta.pixel_size[i]))) throw new Error('지역 자료의 좌표계·격자 크기가 서로 다릅니다.');
        const extent = ol.extent.createEmpty(); entries.forEach(entry => ol.extent.extend(extent, metadataExtent(entry.meta)));
        meta = {...meta, dataset_id: dataset.id, label: dataset.label, origin: [extent[0], extent[3]],
          width: (extent[2] - extent[0]) / meta.pixel_size[0], height: (extent[3] - extent[1]) / meta.pixel_size[1],
          result_filename: null, result_count: entries.reduce((sum, entry) => sum + entry.meta.result_count, 0), region_count: entries.length};
      } else $('downloadResult').href = datasetApi(dataset, 'files/' + meta.result_filename);
      if (dataset.utility) $(nationalGroups.find(group => group.utility === dataset.utility).stem + 'Region').value = dataset.code;
      fit();
      loaded = true;
    } catch (error) {
      const cancelled = controller.signal.aborted && controller.signal.reason?.name === 'AbortError';
      controller.abort();
      meta = null; rasterEntries = [];
      $(dataset.inputs[id]).checked = false; syncLegend();
      if (dataset.statusStem && !cancelled) municipalState(dataset, false, 'invalid', error.message);
      status(cancelled ? '자료 전환을 중단했습니다.' : error.message, !cancelled);
    }
    finally { busy = false; active = null; controls(); }
    if (loaded) await loadCase(id);
  }
  function requestRaster(dataset, id) {
    const request = ++rasterRequest;
    active?.abort();
    // Wait for cancelled work to dispose its sources before starting the latest
    // request. Rapid clicks replace pending selections rather than racing them.
    rasterTask = rasterTask.catch(() => {}).then(async () => {
      if (request !== rasterRequest) return;
      if (id) await switchDataset(dataset, id);
      else {
        removeRaster(); cancelGrid(true); closeDetails();
        rasterInputs().forEach(input => { $(input).checked = false; });
        metrics(null, null, null); $('moveMs').textContent = '—';
        status('TIF 표시를 껐습니다.'); syncLegend(); controls();
      }
    }).catch(error => status(error.message, true));
    return rasterTask;
  }
  function rasterChange(dataset, id, input) {
    if ($(input).checked) { if (dataset && dataset.available !== false) requestRaster(dataset, id); }
    else requestRaster(null, null);
  }
  for (const dataset of datasets) for (const [id, input] of Object.entries(dataset.inputs)) $(input).onchange = () => rasterChange(dataset, id, input);
  for (const group of nationalGroups) {
    const region = () => group.regions.find(item => item.code === $(group.stem + 'Region').value);
    for (const [id, input] of Object.entries(group.inputs)) $(input).onchange = () => rasterChange(region(), id, input);
    $(group.stem + 'Region').onchange = () => {
      const dataset = region();
      if (dataset?.available) requestRaster(dataset, selectedDataset.utility === group.utility ? selectedCase : 'case1');
    };
  }
  function municipalState(dataset, available, state, reason = '') {
    dataset.available = available; dataset.reason = reason;
    $(dataset.statusStem + 'State').textContent = available ? '' : state === 'waiting' ? '자료 대기' : '확인 필요';
    $(dataset.statusStem + 'Note').textContent = available || state === 'waiting' ? '' : reason;
  }
  async function loadMunicipalAvailability() {
    await Promise.all(municipalDatasets.map(async dataset => {
      try {
        const data = await json(datasetApi(dataset, 'availability'), {cache: 'no-store', signal: AbortSignal.timeout(15000)});
        if (data.dataset_id !== dataset.id) throw new Error('선택한 자료와 서버의 자료 구분이 다릅니다.');
        municipalState(dataset, data.available === true && data.status === 'ready', data.status, data.reason || '자료 확인이 필요합니다.');
      } catch (error) {
        municipalState(dataset, false, 'invalid', '자료 확인 필요 · ' + error.message);
      } finally { controls(); }
    }));
  }
  async function loadNationalCatalog() {
    try {
      const catalog = await json(prefix + 'national/catalog', {cache: 'no-store', signal: AbortSignal.timeout(15000)});
      for (const group of nationalGroups) {
        const data = catalog.groups.find(item => item.id === group.utility);
        if (!data?.regions) throw new Error('전국 지역 목록을 확인해 주세요.');
        const select = $(group.stem + 'Region'); select.replaceChildren();
        group.regions = data.regions.filter(region => group.utility !== 'sewer' || region.code !== '11').map(region => ({...region, regionLabel: region.label, id: region.dataset_id, utility: group.utility, inputs: group.inputs,
          label: `건기연 ${group.utility === 'water' ? '상수' : '하수'} · ${region.label}${group.utility === 'sewer' ? (region.code === '11' ? '·보정' : '·원본') : ''}`}));
        const members = group.regions.filter(region => region.available);
        group.regions.unshift({code: 'all', id: `kict_${group.utility}_national_all`, regionLabel: group.utility === 'water' ? '전국' : '전체(서울 외)',
          label: `건기연 ${group.utility === 'water' ? '상수(전국)' : '하수(서울 외·원본)'}`,
          utility: group.utility, inputs: group.inputs, available: members.length > 0, members});
        for (const region of group.regions) {
          const option = document.createElement('option'); option.value = region.code; option.disabled = !region.available;
          option.textContent = region.available ? region.regionLabel : `${region.regionLabel} · ${region.reason || '자료 없음'}`;
          select.append(option); datasets.push(region);
        }
        const first = group.regions.find(region => region.available); if (first) select.value = first.code;
        const missing = group.regions.filter(region => !region.available);
        $(group.stem + 'Note').textContent = group.utility === 'water' ? '' : '원본 · 전남 제외';
        if (missing.some(region => !(group.utility === 'sewer' && region.code === '36'))) $(group.stem + 'Note').textContent = [$(group.stem + 'Note').textContent, '일부 지역 자료 확인 필요'].filter(Boolean).join(' · ');
      }
      return true;
    } catch (_) {
      for (const group of nationalGroups) $(group.stem + 'Note').textContent = '전국 지역 목록을 불러오지 못했습니다. 새로고침해 주세요.';
      return false;
    } finally { controls(); }
  }
  $('reload').onclick = () => requestRaster(selectedDataset, selectedCase);
  $('run').onclick = async () => {
    const count = Number($('repeat').value);
    if (!Number.isInteger(count) || count < 1 || count > 20) { $('runState').textContent = '반복 횟수는 1~20회입니다.'; return; }
    running = true; stopped = false; controls();
    let completed = 0;
    try {
      for (let i = 0; i < count && !stopped; i++) {
        for (const id of ['case1', 'case2']) {
          if (stopped) break; $('runState').textContent = `${i + 1}/${count}회 · ${id.toUpperCase()}`;
          if (!await loadCase(id)) { stopped = true; break; } completed++; await frame();
        }
      }
    } catch (error) { stopped = true; status(error.message, true); }
    finally { running = false; controls(); $('runState').textContent = `${stopped ? '중단' : '완료'} · ${completed}/${count * 2}건 측정`; }
  };
  function fit() {
    moveController?.abort();
    suppressMove = true;
    const extent = meta ? ol.proj.transformExtent(metadataExtent(meta), meta.crs, 'EPSG:3857', 16)
      : ol.proj.transformExtent([124.5, 32.8, 132, 38.8], 'EPSG:4326', 'EPSG:3857');
    const right = narrow.matches ? 24 : parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--dock')) + 50;
    view.fit(extent, {padding: [70, right, 60, 35], duration: 0});
    setTimeout(() => { suppressMove = false; }, 200);
  }
  $('fit').onclick = fit;
  async function pick(coordinate) {
    if (!currentLayer?.getVisible()) return;
    $('detailPanel').dataset.mode = 'grid'; $('gridDetailsExtra').hidden = false; $('pipeDetails').hidden = true;
    $('closeDetails').setAttribute('aria-label', '격자 정보 닫기');
    $('detailTitle').textContent = `${selectedDataset.label} · 선택 격자`;
    $('detailPanel').hidden = false;
    const sequence = ++clickSeq; clickController?.abort(); clickController = new AbortController();
    const controller = clickController;
    selection.clear(); $('cellDetails').hidden = true;
    if (selectedDataset.members) $('downloadResult').removeAttribute('href');
    const candidates = rasterEntries.map(entry => ({...entry, cell: core.cellAt(ol.proj.transform(coordinate, 'EPSG:3857', entry.meta.crs), entry.meta)})).filter(entry => entry.cell);
    if (!candidates.length) { $('pickMessage').textContent = '자료 범위 밖입니다.'; return; }
    $('pickMessage').textContent = 'result 조회 중…';
    try {
      const results = await mapLimited(candidates, async entry => ({...entry,
        data: await json(datasetApi(entry.dataset, `cell?col=${entry.cell.col}&row=${entry.cell.row}`), {signal: controller.signal, cache: 'no-store'})}), controller.signal);
      if (sequence !== clickSeq) return;
      // Regional bounding boxes overlap; only a valid result cell identifies the region.
      const match = results.reverse().find(entry => entry.data.matched);
      if (!match) { $('pickMessage').textContent = '값이 없는 격자입니다 (NoData).'; return; }
      const {data: d, cell, meta: cellMeta, dataset: cellDataset} = match;
      if (d.dataset_id !== cellDataset.id) throw new Error('선택한 자료와 조회 결과의 구분이 다릅니다.');
      $('detailTitle').textContent = `${cellDataset.label} · 선택 격자`;
      $('downloadResult').href = datasetApi(cellDataset, 'files/' + cellMeta.result_filename);
      $('pickMessage').textContent = 'result의 원래 격자 ID로 연결했습니다.'; $('cellDetails').hidden = false;
      $('gridId').textContent = d.grid_id; $('value1').textContent = number(d.case1_m) + ' m'; $('value2').textContent = number(d.case2_m) + ' m';
      $('passFlag').textContent = d.pass_flag ? '통과' : '미통과'; $('pipeId').textContent = d.pipe_id ?? '없음';
      $('nearXY').textContent = `${number(d.near_x)} / ${number(d.near_y)}`; $('nearLonLat').textContent = `${d.near_lon.toFixed(7)} / ${d.near_lat.toFixed(7)}`;
      const tx = xy => ol.proj.transform(xy, cellMeta.crs, 'EPSG:3857');
      const center = tx(d.center), near = tx([d.near_x, d.near_y]), [sx, sy] = cellMeta.pixel_size;
      const x = cellMeta.origin[0] + cell.col * sx, y = cellMeta.origin[1] - cell.row * sy;
      const polygon = [[x, y], [x + sx, y], [x + sx, y - sy], [x, y - sy], [x, y]].map(tx);
      selection.addFeatures([
        new ol.Feature({kind: 'cell', geometry: new ol.geom.Polygon([polygon])}),
        new ol.Feature({kind: 'line', geometry: new ol.geom.LineString([center, near])}),
        new ol.Feature({kind: 'center', geometry: new ol.geom.Point(center)}),
        new ol.Feature({kind: 'near', geometry: new ol.geom.Point(near)})]);
    } catch (error) { if (error.name !== 'AbortError' && sequence === clickSeq) $('pickMessage').textContent = error.message; }
  }
  async function background() {
    let nmap, sync, observer, fallbackMap;
    const fallback = reason => {
      if (fallbackMap) return;
      if (sync) map.un('precompose', sync);
      observer?.disconnect(); nmap?.destroy(); nmap = null;
      $('base').replaceChildren();
      fallbackMap = new ol.Map({target: 'base', view, layers: [new ol.layer.Tile({source: new ol.source.OSM()})], interactions: [], controls: [new ol.control.Attribution({collapsible: false})]});
      $('baseLabel').textContent = `OpenStreetMap 배경 · ${reason}`;
    };
    if (!cfg.NAVER_CLIENT_ID || cfg.NAVER_CLIENT_ID === 'YOUR_NAVER_CLIENT_ID') { fallback('네이버 키 미설정'); return; }
    try {
      await new Promise((resolve, reject) => {
        const script = document.createElement('script'), timer = setTimeout(() => reject(new Error('네이버 로드 지연')), 12000);
        script.src = 'https://openapi.map.naver.com/openapi/v3/maps.js?ncpKeyId=' + encodeURIComponent(cfg.NAVER_CLIENT_ID);
        script.onload = () => { clearTimeout(timer); resolve(); }; script.onerror = () => { clearTimeout(timer); reject(new Error('네이버 로드 오류')); };
        window.navermap_authFailure = () => { clearTimeout(timer); fallback('네이버 인증 오류'); reject(new Error('네이버 인증 오류')); };
        document.head.append(script);
      });
      if (fallbackMap) return;
      const center = ol.proj.toLonLat(view.getCenter());
      nmap = new naver.maps.Map('base', {center: new naver.maps.LatLng(center[1], center[0]), zoom: Math.round(view.getZoom()), draggable: false, scrollWheel: false, pinchZoom: false, keyboardShortcuts: false, disableDoubleClickZoom: true, disableKineticPan: true, tileTransition: false});
      // updateBy animates internally and lags OL by a frame; setCenter/setZoom
      // without transition land Naver in the same frame OL draws the raster.
      sync = event => {
        const camera = event.frameState.viewState, c = ol.proj.toLonLat(camera.center);
        const z = Math.round(camera.zoom);
        if (nmap.getZoom() !== z) nmap.setZoom(z, false);
        nmap.setCenter(new naver.maps.LatLng(c[1], c[0]));
      };
      map.on('precompose', sync);
      map.on('postrender', sync);
      observer = new ResizeObserver(() => { nmap.setSize(new naver.maps.Size($('base').clientWidth, $('base').clientHeight)); map.render(); }); observer.observe($('base'));
      $('baseLabel').textContent = '네이버 배경 · 배경 로딩은 측정 제외';
    } catch (_) { fallback('네이버 연결 실패'); }
  }
  try {
    if (!window.ol || !window.GeoTIFF) throw new Error('지도 라이브러리를 불러오지 못했습니다.');
    view = new ol.View({projection: 'EPSG:3857', center: ol.proj.fromLonLat([126.98, 37.56]), zoom: 11, minZoom: 6, maxZoom: 20, constrainResolution: true, enableRotation: false});
    selection = new ol.source.Vector();
    const selectionStyles = {
      cell: [
        new ol.style.Style({stroke: new ol.style.Stroke({color: '#fff', width: 4})}),
        new ol.style.Style({stroke: new ol.style.Stroke({color: '#1a1a1a', width: 2})})
      ],
      line: [
        new ol.style.Style({stroke: new ol.style.Stroke({color: '#fff', width: 4})}),
        new ol.style.Style({stroke: new ol.style.Stroke({color: '#1a1a1a', width: 2, lineDash: [5, 6]})})
      ],
      center: new ol.style.Style({image: new ol.style.Circle({radius: 5, fill: new ol.style.Fill({color: '#2468b4'}), stroke: new ol.style.Stroke({color: '#fff', width: 2})})}),
      near: new ol.style.Style({image: new ol.style.Circle({radius: 6, fill: new ol.style.Fill({color: '#b52b35'}), stroke: new ol.style.Stroke({color: '#fff', width: 2})})})
    };
    // Naver's background uses integer zoom levels. Wheel/trackpad, double-click
    // and keyboard zoom must not animate through fractional overlay scales.
    // Kinetic drag inertia animates OL after the user releases the mouse; the
    // Naver DOM overlay cannot keep up smoothly, so disable it for alignment.
    const interactions = ol.interaction.defaults.defaults({zoomDuration: 0, mouseWheelZoom: false, altShiftDragRotate: false, pinchRotate: false, dragPan: false});
    interactions.push(new ol.interaction.DragPan({kinetic: null}));
    interactions.push(new ol.interaction.MouseWheelZoom({duration: 0, constrainResolution: true}));
    map = new ol.Map({target: 'map', view, interactions, layers: [], controls: [new ol.control.Zoom({duration: 0}), new ol.control.ScaleLine()]});
    // The background must start even when local raster metadata is unavailable.
    background();
    vectorMap = new ol.Map({target: 'vectorMap', view, layers: [new ol.layer.Vector({source: selection, zIndex: 100, style: feature => selectionStyles[feature.get('kind')]})], interactions: [], controls: []});
    gridLayer = new ol.layer.Vector({source: new ol.source.Vector(), zIndex: 5, minZoom: 12, visible: false,
      opacity: Number($('opacity').value) / 100,
      style: new ol.style.Style({stroke: new ol.style.Stroke({color: 'rgba(35,63,84,.42)', width: .6})})});
    vectorMap.addLayer(gridLayer);
    const tilesBase = cfg.API_BASE ? cfg.API_BASE.replace(/\/$/, '') + '/tiles/' : new URL('../tiles/', location.href).href;
    vectorCatalog = window.PipeVectorCatalog.create({map: vectorMap, view, config: cfg, tilesBase, onChange: () => { syncLegend(); pipePicker?.syncVisibility(); }, onSelect: () => measureTab('vector'), onRecord: addRecord});
    pipePicker = window.PipeVectorSelection.create({map: vectorMap, getEntries: () => vectorCatalog.entries, onSelect: closeDetails});
    map.on('singleclick', event => {
      if (pipePicker.pick(event.coordinate)) return;
      closeDetails(); pick(event.coordinate);
    });
    const measureMove = async () => {
      moveController?.abort();
      if (!ready || busy || running || suppressMove || !currentLayer?.getVisible() || document.hidden) return;
      const controller = moveController = new AbortController();
      // View changes happen before the moved frame is rendered. Each later
      // change invalidates the previous interval; hidden tabs never contribute.
      const revision = sourceRevision, id = selectedCase, t = performance.now();
      try { await afterRender(controller.signal); } catch (_) { return; }
      if (controller.signal.aborted || revision !== sourceRevision || busy || running || document.hidden || !currentLayer?.getVisible()) return;
      const elapsed = performance.now() - t;
      $('moveMs').textContent = ms(elapsed);
      addRecord({case: id, type: '메모리 재표시', download_ms: 0, display_ms: elapsed, total_ms: elapsed, bytes: 0, status: '완료'});
    };
    view.on('change:center', measureMove); view.on('change:resolution', measureMove);
    const invalidateViewportGrid = () => { if (meta?.grid_mode === 'viewport') cancelGrid(true); };
    view.on('change:center', invalidateViewportGrid); view.on('change:resolution', invalidateViewportGrid);
    map.on('moveend', scheduleGrid);
    // Read-only diagnostics for browser regression tests.
    window.pipeDistance = {map, getMeta: () => meta, getRecords: () => records.slice(), getLayer: () => currentLayer, getSources: () => currentSources.slice(), getGrid: () => gridLayer, vectorMap, getVectors: () => vectorCatalog.entries.slice(), getPipeSelection: () => pipePicker.getSelection()};
    const catalogReady = loadNationalCatalog();
    const municipalReady = loadMunicipalAvailability();
    if (requestedDataset?.startsWith('kict_water_national_') || requestedDataset?.startsWith('kict_sewer_national_')) {
      await catalogReady;
      const dataset = datasets.find(item => item.id === requestedDataset);
      if (!dataset?.available) {
        rasterInputs().forEach(input => { $(input).checked = false; }); syncLegend();
        throw new Error(dataset ? `${dataset.label} · ${dataset.reason || '자료 없음'}` : '요청한 전국 지역 자료를 확인하지 못했습니다.');
      }
      ready = true; controls(); await requestRaster(dataset, 'case1');
    } else {
      if (!requestedDataset) {
        await catalogReady;
        selectedDataset = datasets.find(item => item.id === 'kict_water_national_all' && item.available) || selectedDataset;
        rasterInputs().forEach(input => { $(input).checked = false; });
        fit(); ready = true; syncLegend(); controls(); status('왼쪽에서 표시할 레이어를 선택하세요.');
      } else {
        if (selectedDataset.statusStem) await municipalReady;
        ready = true; controls();
        if (selectedDataset.available === false) {
          syncLegend(); status(`${selectedDataset.label} · ${selectedDataset.reason}`);
        } else await requestRaster(selectedDataset, 'case1');
      }
    }
  } catch (error) { status(error.message, true); $('pickMessage').textContent = '자료 연결이 완료되지 않았습니다.'; }
})();
