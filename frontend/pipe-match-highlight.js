/* Verified tile-ordinal flags; original geometries and source attributes stay intact. */
(function () {
  'use strict';
  const tileDescriptor = Symbol('pipeComparisonTile'), originalOrdinal = Symbol('pipeComparisonOrdinal');
  const MAX_REGISTERED = 32, MAX_QUEUED = 32, MAX_INFLIGHT = 2;
  function create({map, getEntries, apiBase = '', onStateChange}) {
    const base = apiBase ? apiBase.replace(/\/$/, '') + '/' : new URL('../', location.href).href;
    const view = map.getView(), registry = new Map(), queue = new Map(), inflight = new Map(), styles = [new Map(), new Map()];
    let water = [], active = false, activation = 0, viewportRevision = 0, descriptorId = 0;
    let version = null, metadataRequest = null, metadataError = false, redrawPending = false, emitted = '';
    let raisedLayer = null, originalLayerZ;
    const enabled = () => water.length === 2 && water[0].checkbox.checked && water[0].layer?.getVisible() && water[1].checkbox.checked && water[1].layer?.getVisible();
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
      return {active, status: !active ? 'inactive' : metadataError || errorTileCount ? 'error' : metadataRequest || pendingTileCount ? 'loading' : 'ready',
        version, verifiedTileCount, matchedTileCount, pendingTileCount, errorTileCount,
        registeredTileCount: registry.size, queuedTileCount: queue.size, inflightCount: inflight.size};
    }
    function emit() {
      const value = state(), signature = JSON.stringify(value);
      if (signature !== emitted) { emitted = signature; onStateChange?.(value); }
    }
    function redraw() {
      if (redrawPending) return;
      redrawPending = true;
      requestAnimationFrame(() => { redrawPending = false; water.find(entry => entry.id === 'sa001')?.layer?.changed(); });
    }
    function cancelJobs() {
      metadataRequest?.controller.abort(); metadataRequest = null;
      for (const job of inflight.values()) job.controller.abort();
      inflight.clear(); queue.clear();
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
      if (data.available !== true || data.schema_version !== 1 || data.layer !== 'sa001' || data.version !== expectedVersion ||
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
    async function requestFlags(descriptor) {
      const job = {descriptor, activation, version, controller: new AbortController()};
      inflight.set(descriptor.id, job);
      const timer = setTimeout(() => job.controller.abort('timeout'), 90000);
      try {
        const response = await fetch(base + 'comparison/water/tiles/sa001/' + descriptor.coordinate.join('/'), {signal: job.controller.signal, cache: 'no-store'});
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
        if (inflight.get(descriptor.id) === job) { inflight.delete(descriptor.id); pump(); }
      }
    }
    function pump() {
      if (!active || !enabled() || version == null || metadataError || metadataRequest) { emit(); return; }
      for (const descriptor of registry.values()) {
        if (!needed(descriptor) || !descriptor.sha || descriptor.hashError || proved(descriptor) || descriptor.failedActivation === activation || inflight.has(descriptor.id) || queue.has(descriptor.id)) continue;
        if (queue.size < MAX_QUEUED) queue.set(descriptor.id, descriptor);
      }
      while (inflight.size < MAX_INFLIGHT && queue.size) {
        const [id, descriptor] = queue.entries().next().value; queue.delete(id);
        if (needed(descriptor) && !proved(descriptor) && !descriptor.hashError && descriptor.failedActivation !== activation) requestFlags(descriptor);
      }
      emit();
    }
    async function metadata() {
      const job = {activation, controller: new AbortController()}; metadataRequest = job;
      const timer = setTimeout(() => job.controller.abort('timeout'), 15000);
      emit();
      try {
        const response = await fetch(base + 'comparison/water/metadata', {signal: job.controller.signal, cache: 'no-store'}), data = await response.json();
        if (!active || metadataRequest !== job || activation !== job.activation || job.controller.signal.aborted) return;
        if (!response.ok || data.available !== true || typeof data.version !== 'string' || !data.version) throw new Error('Comparison version unavailable');
        version = data.version; metadataError = false;
      } catch (_) {
        if (active && metadataRequest === job && activation === job.activation) metadataError = true;
      } finally {
        clearTimeout(timer);
        if (metadataRequest === job) { metadataRequest = null; pump(); redraw(); }
      }
    }
    function sweep() {
      ++viewportRevision;
      for (const [id, descriptor] of registry) if (!needed(descriptor)) { registry.delete(id); queue.delete(id); }
      for (const [id, job] of inflight) if (!needed(job.descriptor)) { job.controller.abort(); inflight.delete(id); }
      pump();
    }
    view.on('change:center', sweep); view.on('change:resolution', sweep);
    function syncVisibility() {
      const entries = getEntries(); water = ['kict_national', 'sa001'].map(id => entries.find(entry => entry.id === id)).filter(Boolean);
      const next = enabled();
      const seoulLayer = water.find(entry => entry.id === 'sa001')?.layer;
      if (raisedLayer && (!next || raisedLayer !== seoulLayer)) { raisedLayer.setZIndex(originalLayerZ); raisedLayer = null; }
      if (next && raisedLayer !== seoulLayer) {
        raisedLayer = seoulLayer; originalLayerZ = seoulLayer.getZIndex();
        // Style z-index orders only within one layer. Keep strict Seoul strokes
        // above KICT regardless of asynchronous layer-add order, below selection.
        seoulLayer.setZIndex(30);
      }
      if (next === active) { sweep(); return; }
      active = next; ++activation; ++viewportRevision; cancelJobs(); version = null; metadataError = false;
      // Cached vector tiles need a new style pass; refreshing their source would redownload PBFs.
      water.find(entry => entry.id === 'sa001')?.layer?.changed();
      if (active) metadata(); else emit();
    }
    function attachTile(entry, tile, features, rawArrayBuffer) {
      if (entry.id !== 'sa001' || !features.length) return;
      const descriptor = {id: ++descriptorId, entry, tile: new WeakRef(tile), coordinate: tile.getTileCoord().slice(), generation: entry.generation || 0,
        count: features.length, sha: null, bits: null, version: null, matchedCount: 0, hashError: false, failedActivation: null, seenRevision: -1};
      for (let i = 0; i < features.length; i++) {
        const feature = features[i];
        if (feature.getProperties().layer !== 'sa001' || !['LineString', 'MultiLineString'].includes(feature.getGeometry()?.getType())) descriptor.hashError = true;
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
      if (!active || !enabled() || entry.id !== 'sa001') return baseStyle;
      const descriptor = feature[tileDescriptor];
      if (!descriptor || descriptor.entry !== entry || !currentGeneration(descriptor)) return baseStyle;
      if (descriptor.seenRevision !== viewportRevision) register(descriptor);
      if (!proved(descriptor)) return baseStyle;
      const ordinal = feature[originalOrdinal];
      if (!(descriptor.bits[ordinal >> 3] & (1 << (ordinal & 7)))) return baseStyle;
      const near = Number.isFinite(resolution) && resolution <= 20, batch = baseStyle.getZIndex?.() || 0, cache = styles[near ? 1 : 0];
      let cached = cache.get(batch);
      if (!cached) {
        cached = [new ol.style.Style({stroke: new ol.style.Stroke({color: '#25364a', width: near ? 5 : 2.6}), zIndex: 10000 + batch * 2}),
          new ol.style.Style({stroke: new ol.style.Stroke({color: '#ffd43b', width: near ? 3 : 1.4}), zIndex: 10001 + batch * 2})];
        if (cache.size >= 256) cache.delete(cache.keys().next().value);
        cache.set(batch, cached);
      }
      return cached;
    }
    return {attachTile, style, syncVisibility, state};
  }
  window.PipeMatchHighlight = {create};
})();
