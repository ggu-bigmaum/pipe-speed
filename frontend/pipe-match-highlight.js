/* Verified tile-ordinal flags; original geometries and source attributes stay intact. */
(function () {
  'use strict';
  const tileDescriptor = Symbol('pipeComparisonTile'), originalOrdinal = Symbol('pipeComparisonOrdinal');
  const MAX_REGISTERED = 32, MAX_QUEUED = 32, MAX_INFLIGHT = 2;
  const PAIRS = [
    {id: 'water', label: '상수', layers: ['kict_national', 'sa001'], target: 'sa001', pendingLabel: 'ID·전체 선형 완전일치'},
    {id: 'sewer', label: '하수', layers: ['kict_sewer_seoul', 'sb001'], target: 'sb001', pendingLabel: '3자리 반올림 후 형상 일치'}
  ];
  function requestBudget() {
    let used = 0;
    const pumps = new Map();
    return {
      watch: (owner, pump) => pumps.set(owner, pump), count: () => used,
      take(owner, kind) {
        if (used >= MAX_INFLIGHT) return null;
        used++; let released = false;
        return () => {
          if (released) return; released = true; used--;
          // Newly loaded metadata should get one opportunity. Completed flag
          // work gives the other pair first opportunity, avoiding starvation.
          const first = [...pumps].filter(([id]) => kind === 'metadata' ? id === owner : id !== owner);
          const rest = [...pumps].filter(([id]) => kind === 'metadata' ? id !== owner : id === owner);
          for (const [, pump] of [...first, ...rest]) pump();
        };
      }
    };
  }
  function createPair({map, getEntries, apiBase = '', onStateChange, pair, budget}) {
    const base = apiBase ? apiBase.replace(/\/$/, '') + '/' : new URL('../', location.href).href;
    const view = map.getView(), registry = new Map(), queue = new Map(), inflight = new Map(), styles = [new Map(), new Map()];
    let members = [], active = false, activation = 0, viewportRevision = 0, descriptorId = 0;
    let version = null, metadataRequest = null, metadataError = false, redrawPending = false, emitted = '';
    let highlightLabel = 'ID·전체 선형 완전일치', description = '원본 ID와 전체 선형이 모두 같은 관로입니다.', message = '';
    let raisedLayer = null, originalLayerZ;
    const enabled = () => members.length === 2 && members[0].checkbox.checked && members[0].layer?.getVisible() && members[1].checkbox.checked && members[1].layer?.getVisible();
    const currentGeneration = descriptor => descriptor.generation === (descriptor.entry.generation || 0);
    const proved = descriptor => version != null && descriptor.version === version && descriptor.bits != null;
    function needed(descriptor) {
      if (!currentGeneration(descriptor) || !descriptor.tile.deref()) return false;
      const grid = descriptor.entry.source?.getTileGrid();
      return Boolean(grid && descriptor.coordinate[0] === grid.getZForResolution(view.getResolution(), 1) &&
        ol.extent.intersects(grid.getTileCoordExtent(descriptor.coordinate), view.calculateExtent(map.getSize())));
    }
    function state() {
      const visible = [...registry.values()].filter(needed);
      const verifiedTileCount = active ? visible.filter(proved).length : 0;
      const matchedTileCount = active ? visible.filter(descriptor => proved(descriptor) && descriptor.matchedCount > 0).length : 0;
      const errorTileCount = active ? visible.filter(descriptor => descriptor.hashError || descriptor.failedActivation === activation).length : 0;
      const pendingTileCount = active ? visible.filter(descriptor => !proved(descriptor) && !descriptor.hashError && descriptor.failedActivation !== activation).length : 0;
      return {id: pair.id, label: pair.label, highlightLabel: version ? highlightLabel : pair.pendingLabel, description, message,
        active, status: !active ? 'inactive' : metadataError || errorTileCount ? 'error' : metadataRequest || pendingTileCount ? 'loading' : 'ready',
        version, verifiedTileCount, matchedTileCount, pendingTileCount, errorTileCount,
        registeredTileCount: registry.size, queuedTileCount: queue.size, inflightCount: inflight.size + (metadataRequest?.started ? 1 : 0)};
    }
    function emit() {
      const value = state(), signature = JSON.stringify(value);
      if (signature !== emitted) { emitted = signature; onStateChange?.(value); }
    }
    function redraw() {
      if (redrawPending) return;
      redrawPending = true;
      requestAnimationFrame(() => { redrawPending = false; members.find(entry => entry.id === pair.target)?.layer?.changed(); });
    }
    function cancelJobs() {
      const oldMetadata = metadataRequest; metadataRequest = null; queue.clear();
      oldMetadata?.controller.abort(); oldMetadata?.release?.();
      for (const [id, job] of inflight) { inflight.delete(id); job.controller.abort(); job.release(); }
    }
    function register(descriptor) {
      if (!currentGeneration(descriptor) || !descriptor.tile.deref()) return;
      descriptor.seenRevision = viewportRevision;
      if (!registry.has(descriptor.id)) {
        while (registry.size >= MAX_REGISTERED) {
          const oldest = registry.keys().next().value;
          registry.get(oldest).seenRevision = -1;
          registry.delete(oldest); queue.delete(oldest);
        }
        registry.set(descriptor.id, descriptor);
      }
      if (active) pump();
    }
    function validate(data, descriptor, expectedVersion) {
      if (data.available !== true || data.schema_version !== 1 || data.layer !== pair.target || data.version !== expectedVersion ||
          data.sha256 !== descriptor.sha || data.feature_count !== descriptor.count ||
          !Number.isInteger(data.matched_count) || data.matched_count < 0 || data.matched_count > descriptor.count ||
          typeof data.bits_base64 !== 'string' || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(data.bits_base64)) throw new Error('Unverified tile flags');
      const bytes = Math.ceil(descriptor.count / 8);
      if (data.bits_base64.length !== 4 * Math.ceil(bytes / 3)) throw new Error('Unexpected bitset length');
      const binary = atob(data.bits_base64);
      if (binary.length !== bytes) throw new Error('Unexpected decoded length');
      const bits = new Uint8Array(bytes); let matched = 0;
      for (let i = 0; i < bytes; i++) {
        bits[i] = binary.charCodeAt(i);
        let value = bits[i]; while (value) { value &= value - 1; matched++; }
      }
      const tail = descriptor.count % 8;
      if ((tail && (bits[bytes - 1] >> tail)) || matched !== data.matched_count) throw new Error('Invalid trailing or match bits');
      return bits;
    }
    const validJob = job => active && enabled() && job.activation === activation && job.version === version &&
      inflight.get(job.descriptor.id) === job && !job.controller.signal.aborted && needed(job.descriptor);
    async function requestFlags(descriptor, release) {
      const job = {descriptor, activation, version, controller: new AbortController(), release};
      inflight.set(descriptor.id, job);
      const timer = setTimeout(() => job.controller.abort('timeout'), 90000);
      try {
        const response = await fetch(base + 'comparison/' + pair.id + '/tiles/' + pair.target + '/' + descriptor.coordinate.join('/'), {signal: job.controller.signal, cache: 'no-store'});
        const data = await response.json();
        if (!validJob(job)) return;
        if (!response.ok) throw new Error('Comparison tile unavailable');
        const bits = validate(data, descriptor, job.version);
        if (!validJob(job)) return;
        descriptor.bits = bits; descriptor.version = job.version; descriptor.matchedCount = data.matched_count; descriptor.failedActivation = null;
        redraw();
      } catch (_) {
        // An intentionally aborted/offscreen job must not turn the next view into an error.
        if (active && job.activation === activation && inflight.get(descriptor.id) === job && needed(descriptor)) descriptor.failedActivation = activation;
      } finally {
        clearTimeout(timer);
        if (inflight.get(descriptor.id) === job) inflight.delete(descriptor.id);
        release(); pump();
      }
    }
    function pump() {
      if (!active || !enabled() || metadataError) { emit(); return; }
      if (metadataRequest) {
        if (!metadataRequest.started) {
          const release = budget.take(pair.id, 'metadata');
          if (release) { metadataRequest.started = true; metadataRequest.release = release; requestMetadata(metadataRequest); }
        }
        emit(); return;
      }
      if (version == null) { emit(); return; }
      for (const descriptor of registry.values()) {
        if (!needed(descriptor) || !descriptor.sha || descriptor.hashError || proved(descriptor) || descriptor.failedActivation === activation || inflight.has(descriptor.id) || queue.has(descriptor.id)) continue;
        if (queue.size < MAX_QUEUED) queue.set(descriptor.id, descriptor);
      }
      while (inflight.size < MAX_INFLIGHT && queue.size) {
        const [id, descriptor] = queue.entries().next().value;
        if (!needed(descriptor) || proved(descriptor) || descriptor.hashError || descriptor.failedActivation === activation) { queue.delete(id); continue; }
        const release = budget.take(pair.id, 'flags');
        if (!release) break;
        queue.delete(id); requestFlags(descriptor, release);
      }
      emit();
    }
    async function requestMetadata(job) {
      const timer = setTimeout(() => job.controller.abort('timeout'), 15000);
      emit();
      try {
        const response = await fetch(base + 'comparison/' + pair.id + '/metadata', {signal: job.controller.signal, cache: 'no-store'}), data = await response.json();
        if (!active || metadataRequest !== job || activation !== job.activation || job.controller.signal.aborted) return;
        if (!response.ok || data.available !== true || typeof data.version !== 'string' || !data.version) throw new Error(data.message || data.reason || pair.label + ' 비교 정보를 확인할 수 없습니다.');
        version = data.version; metadataError = false; message = '';
        highlightLabel = typeof data.highlight_label === 'string' && data.highlight_label ? data.highlight_label : 'ID·전체 선형 완전일치';
        description = typeof data.description === 'string' && data.description ? data.description : '원본 ID와 전체 선형이 모두 같은 관로입니다.';
      } catch (error) {
        if (active && metadataRequest === job && activation === job.activation) { metadataError = true; message = error.message || pair.label + ' 비교 정보를 확인할 수 없습니다.'; }
      } finally {
        clearTimeout(timer);
        if (metadataRequest === job) { metadataRequest = null; redraw(); }
        job.release(); pump();
      }
    }
    function sweep() {
      ++viewportRevision;
      for (const [id, descriptor] of registry) if (!needed(descriptor)) { registry.delete(id); queue.delete(id); }
      for (const [id, job] of inflight) if (!needed(job.descriptor)) { inflight.delete(id); job.controller.abort(); job.release(); }
      pump();
    }
    function syncVisibility() {
      const entries = getEntries(); members = pair.layers.map(id => entries.find(entry => entry.id === id)).filter(Boolean);
      const next = enabled();
      const seoulLayer = members.find(entry => entry.id === pair.target)?.layer;
      if (raisedLayer && (!next || raisedLayer !== seoulLayer)) { raisedLayer.setZIndex(originalLayerZ); raisedLayer = null; }
      if (next && raisedLayer !== seoulLayer) {
        raisedLayer = seoulLayer; originalLayerZ = seoulLayer.getZIndex();
        // Style z-index orders only within one layer. Keep strict Seoul strokes
        // above KICT regardless of asynchronous layer-add order, below selection.
        seoulLayer.setZIndex(30);
      }
      if (next === active) { sweep(); return; }
      active = next; ++activation; ++viewportRevision; version = null; metadataError = false; message = ''; cancelJobs();
      // Cached vector tiles need a new style pass; refreshing their source would redownload PBFs.
      members.find(entry => entry.id === pair.target)?.layer?.changed();
      if (active) { metadataRequest = {activation, controller: new AbortController(), started: false, release: null}; pump(); } else emit();
    }
    function attachTile(entry, tile, features, rawArrayBuffer) {
      if (entry.id !== pair.target || !features.length) return;
      const descriptor = {id: ++descriptorId, entry, tile: new WeakRef(tile), coordinate: tile.getTileCoord().slice(), generation: entry.generation || 0,
        count: features.length, sha: null, bits: null, version: null, matchedCount: 0, hashError: false, failedActivation: null, seenRevision: -1};
      for (let i = 0; i < features.length; i++) {
        const feature = features[i];
        if (feature.getProperties().layer !== pair.target || !['LineString', 'MultiLineString'].includes(feature.getGeometry()?.getType())) descriptor.hashError = true;
        Object.defineProperty(feature, originalOrdinal, {value: i});
        Object.defineProperty(feature, tileDescriptor, {value: descriptor});
      }
      register(descriptor);
      if (descriptor.hashError) { emit(); return; }
      try {
        // Native digest owns the bytes only until completion. Keep only the resulting digest.
        crypto.subtle.digest('SHA-256', rawArrayBuffer).then(hash => {
          descriptor.sha = Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, '0')).join('');
          if (registry.has(descriptor.id)) pump();
        }).catch(() => { descriptor.hashError = true; emit(); });
      } catch (_) { descriptor.hashError = true; emit(); }
    }
    function style(feature, entry, baseStyle, resolution) {
      if (!active || !enabled() || entry.id !== pair.target) return baseStyle;
      const descriptor = feature[tileDescriptor];
      if (!descriptor || descriptor.entry !== entry || !currentGeneration(descriptor)) return baseStyle;
      if (descriptor.seenRevision !== viewportRevision) register(descriptor);
      if (!proved(descriptor)) return baseStyle;
      const ordinal = feature[originalOrdinal];
      if (!(descriptor.bits[ordinal >> 3] & (1 << (ordinal & 7)))) return baseStyle;
      const near = Number.isFinite(resolution) && resolution <= 20, batch = baseStyle.getZIndex?.() || 0, cache = styles[near ? 1 : 0];
      let cached = cache.get(batch);
      if (!cached) {
        cached = [new ol.style.Style({stroke: new ol.style.Stroke({color: '#ffffff', width: near ? 6 : 3.6}), zIndex: 10000 + batch * 2}),
          new ol.style.Style({stroke: new ol.style.Stroke({color: '#17212f', width: near ? 3.2 : 1.8}), zIndex: 10001 + batch * 2})];
        if (cache.size >= 256) cache.delete(cache.keys().next().value);
        cache.set(batch, cached);
      }
      return cached;
    }
    budget.watch(pair.id, pump);
    return {attachTile, style, syncVisibility, state, sweep};
  }
  function create({map, getEntries, apiBase = '', onStateChange}) {
    const budget = requestBudget();
    let workers = [], emitted = '';
    function state() {
      const pairs = Object.fromEntries(workers.map((worker, index) => [PAIRS[index].id, worker.state()]));
      const values = Object.values(pairs), enabled = values.filter(pair => pair.active);
      const result = {active: enabled.length > 0, status: !enabled.length ? 'inactive' : enabled.some(pair => pair.status === 'error') ? 'error' : enabled.some(pair => pair.status === 'loading') ? 'loading' : 'ready',
        version: enabled.length === 1 ? enabled[0].version : null, pairs, inflightCount: budget.count()};
      for (const key of ['verifiedTileCount', 'matchedTileCount', 'pendingTileCount', 'errorTileCount', 'registeredTileCount', 'queuedTileCount']) result[key] = values.reduce((sum, pair) => sum + pair[key], 0);
      return result;
    }
    function emit() { const value = state(), signature = JSON.stringify(value); if (signature !== emitted) { emitted = signature; onStateChange?.(value); } }
    workers = PAIRS.map(pair => createPair({map, getEntries, apiBase, pair, budget, onStateChange: emit}));
    const byTarget = new Map(workers.map((worker, index) => [PAIRS[index].target, worker]));
    const sweep = () => { for (const worker of workers) worker.sweep(); };
    map.getView().on('change:center', sweep); map.getView().on('change:resolution', sweep);
    return {
      attachTile: (entry, ...args) => byTarget.get(entry.id)?.attachTile(entry, ...args),
      style: (feature, entry, baseStyle, resolution) => byTarget.get(entry.id)?.style(feature, entry, baseStyle, resolution) || baseStyle,
      syncVisibility: () => { for (const worker of workers) worker.syncVisibility(); }, state
    };
  }
  window.PipeMatchHighlight = {create};
})();
