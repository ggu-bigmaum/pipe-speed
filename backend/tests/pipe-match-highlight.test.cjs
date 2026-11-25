const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {webcrypto, createHash} = require('node:crypto');
const script = path.resolve(__dirname, '../../frontend/pipe-match-highlight.js');
class Stroke { constructor(options) { Object.assign(this, options); } getWidth() { return this.width; } }
class Style { constructor(options) { Object.assign(this, options); } getZIndex() { return this.zIndex; } getStroke() { return this.stroke; } }
const hashing = new Set();
const cryptoFacade = {subtle: {digest(...args) {
  const pending = webcrypto.subtle.digest(...args); hashing.add(pending);
  pending.then(() => hashing.delete(pending), () => hashing.delete(pending));
  return pending;
}}};
const flush = async () => { await Promise.allSettled([...hashing]); for (let i = 0; i < 4; i++) await new Promise(setImmediate); };
function fixture({manualMetadata = false, allPairs = false} = {}) {
  const requests = [], changes = [], handlers = {}, frames = [];
  let bounds = [0, 0, 100, 100];
  const view = {on: (name, fn) => { handlers[name] = fn; }, getResolution: () => 100, calculateExtent: () => bounds};
  const grid = {getZForResolution: () => 6, getTileCoordExtent: ([z, x, y]) => [x * 100, y * 100, x * 100 + 99, y * 100 + 99]};
  const makeEntry = id => ({id, generation: 0, checkbox: {checked: false}, source: {getTileGrid: () => grid}, layer: {visible: false, zIndex: 10, getVisible() { return this.visible; }, getZIndex() { return this.zIndex; }, setZIndex(value) { this.zIndex = value; }, changed() { changes.push(id); }}});
  const entries = [makeEntry('kict_national'), makeEntry('sa001')];
  if (allPairs) entries.push(...['kict_sewer_seoul', 'sb001', 'kict_sewer_national', 'kict_sewer_reference'].map(makeEntry));
  const context = {window: {}, crypto: cryptoFacade, WeakRef, Uint8Array, ArrayBuffer, AbortController, URL, location: {href: 'http://test/app/'}, atob: text => Buffer.from(text, 'base64').toString('binary'), setTimeout: () => 1, clearTimeout() {}, requestAnimationFrame: fn => { frames.push(fn); return frames.length; },
    ol: {style: {Style, Stroke}, extent: {intersects: (a, b) => a[0] <= b[2] && a[2] >= b[0] && a[1] <= b[3] && a[3] >= b[1]}},
    fetch: (url, options = {}) => {
      if (url.endsWith('/metadata') && !manualMetadata) return Promise.resolve({ok: true, json: async () => ({available: true, version: url.includes('/sewer/') ? 'sewer-v1' : 'v1'})});
      return new Promise((resolve, reject) => requests.push({url, options, resolve: (body, ok = true) => resolve({ok, json: async () => body}), reject}));
    }};
  vm.runInNewContext(fs.existsSync(script) ? fs.readFileSync(script, 'utf8') : '', context);
  assert.equal(typeof context.window.PipeMatchHighlight?.create, 'function', 'highlight module provides create');
  const service = context.window.PipeMatchHighlight.create({map: {getView: () => view, getSize: () => [800, 600]}, getEntries: () => entries, apiBase: 'http://test/', onStateChange() {}});
  const toggle = on => { for (const entry of entries) { entry.checkbox.checked = on; entry.layer.visible = on; } service.syncVisibility(); };
  const setLayers = ids => { for (const entry of entries) { entry.checkbox.checked = ids.includes(entry.id); entry.layer.visible = entry.checkbox.checked; } service.syncVisibility(); };
  const tile = (x = 0, count = 2, layer = 'sa001') => {
    const features = Array.from({length: count}, () => ({getProperties: () => ({layer}), getGeometry: () => ({getType: () => 'LineString'})}));
    const raw = new Uint8Array([x % 256, count, 3]).buffer;
    const value = {getTileCoord: () => [6, x, 0], getFeatures: () => features};
    const entry = entries.find(entry => entry.id === layer);
    service.attachTile(entry, value, features, raw);
    return {tile: value, features, raw, layer, entry, sha: createHash('sha256').update(new Uint8Array(raw)).digest('hex')};
  };
  const reply = (target, bits = 1, extra = {}) => ({available: true, version: target.layer === 'sb001' ? 'sewer-v1' : 'v1', layer: target.layer, sha256: target.sha, feature_count: target.features.length, bits_base64: Buffer.from([bits]).toString('base64'), matched_count: bits === 3 ? 2 : bits ? 1 : 0, schema_version: 1, ...extra});
  const base = new Style({stroke: new Stroke({color: '#df7c24', width: 1.8}), zIndex: 0});
  return {service, entries, requests, changes, toggle, setLayers, tile, reply, base, frames, move: next => { bounds = next; handlers['change:center'](); }};
}

test('both water layers are required and successful flags share cached styles', async () => {
  const f = fixture(), t = f.tile(); await flush();
  f.entries[1].checkbox.checked = true; f.entries[1].layer.visible = true; f.service.syncVisibility();
  assert.equal(f.requests.length, 0); assert.equal(f.service.style(t.features[0], f.entries[1], f.base, 100), f.base);
  f.toggle(true); await flush(); assert.equal(f.requests.length, 1);
  f.requests[0].resolve(f.reply(t)); await flush();
  const styles = f.service.style(t.features[0], f.entries[1], f.base, 100);
  assert.equal(styles.length, 1); assert.equal(styles, f.service.style(t.features[0], f.entries[1], f.base, 100));
  assert.equal(f.service.style(t.features[1], f.entries[1], f.base, 100), f.base);
  assert.equal(f.service.state().matchedTileCount, 1);
  f.toggle(false); assert.equal(f.service.style(t.features[0], f.entries[1], f.base, 100), f.base);
  assert.equal(f.service.state().active, false);
});

test('MVT ordinals survive a renderer in-place array reorder', async () => {
  const f = fixture(), t = f.tile(), original = t.features[0]; t.features.reverse(); f.toggle(true); await flush();
  f.requests[0].resolve(f.reply(t)); await flush();
  assert.equal(f.service.style(original, f.entries[1], f.base, 100).length, 1);
  assert.equal(f.service.style(t.features[0], f.entries[1], f.base, 100), f.base);
});

test('mismatched schema, version, tile bytes, count and bitset fail closed', async () => {
  for (const mismatch of [{schema_version: 2}, {version: 'other'}, {sha256: '0'.repeat(64)}, {feature_count: 99}, {bits_base64: ''}, {bits_base64: 'gA=='}, {matched_count: 2}, {layer: 'other'}]) {
    const f = fixture(), t = f.tile(); f.toggle(true); await flush(); f.requests[0].resolve(f.reply(t, 1, mismatch)); await flush();
    assert.equal(f.service.style(t.features[0], f.entries[1], f.base, 100), f.base);
    assert.equal(f.service.state().status, 'error');
  }
});

test('turning off aborts pending lookup and a late successful response cannot paint', async () => {
  const f = fixture(), t = f.tile(); f.toggle(true); await flush(); const old = f.requests[0]; f.toggle(false);
  assert.equal(old.options.signal.aborted, true); old.resolve(f.reply(t)); await flush();
  assert.equal(f.service.style(t.features[0], f.entries[1], f.base, 100), f.base);
  f.toggle(true); await flush(); assert.equal(f.requests.length, 2);
  f.requests[1].resolve(f.reply(t)); await flush(); assert.equal(f.service.style(t.features[0], f.entries[1], f.base, 100).length, 1);
});

test('metadata failure and stale activation metadata never enable comparison', async () => {
  const f = fixture({manualMetadata: true}), t = f.tile(); f.toggle(true); await flush(); const old = f.requests[0];
  f.toggle(false); f.toggle(true); f.requests[1].resolve({available: false, version: 'v1'}, false); await flush();
  old.resolve({available: true, version: 'v1'}); await flush();
  assert.equal(f.requests.filter(r => r.url.includes('/tiles/')).length, 0);
  assert.equal(f.service.state().status, 'error'); assert.equal(f.service.style(t.features[0], f.entries[1], f.base, 100), f.base);
});

test('a failed tile retries only after a new activation and valid cache avoids another tile request', async () => {
  const f = fixture(), t = f.tile(); f.toggle(true); await flush(); f.requests[0].resolve({available: false}, false); await flush();
  for (let i = 0; i < 10; i++) f.service.style(t.features[0], f.entries[1], f.base, 100);
  await flush(); assert.equal(f.requests.length, 1);
  f.toggle(false); f.toggle(true); await flush(); f.requests[1].resolve(f.reply(t)); await flush();
  f.toggle(false); f.toggle(true); await flush(); assert.equal(f.requests.length, 2);
  assert.equal(f.service.style(t.features[0], f.entries[1], f.base, 100).length, 1);
});

test('registry and request queue remain bounded and an evicted OL-cached tile can re-register', async () => {
  const f = fixture(); f.move([0, 0, 10000, 100]); const tiles = Array.from({length: 40}, (_, i) => f.tile(i)); await flush();
  f.toggle(true); await flush(); assert.ok(f.service.state().registeredTileCount <= 32); assert.ok(f.service.state().queuedTileCount <= 32);
  assert.ok(f.requests.length <= 2);
  f.move([0, 0, 99, 99]);
  f.service.style(tiles[0].features[0], f.entries[1], f.base, 100); await flush();
  assert.ok(f.requests.some(request => request.url.endsWith('/6/0/0')));
  assert.ok(f.service.state().registeredTileCount <= 32);
});

test('empty and offscreen tiles do not request bitsets; reload generations invalidate old proof', async () => {
  const f = fixture(), empty = f.tile(0, 0), offscreen = f.tile(3); f.toggle(true); await flush();
  assert.equal(f.requests.length, 0);
  const t = f.tile(); await flush(); f.requests[0].resolve(f.reply(t)); await flush();
  f.entries[1].generation++;
  assert.equal(f.service.style(t.features[0], f.entries[1], f.base, 100), f.base);
  assert.equal(empty.features.length, 0); assert.equal(offscreen.features.length, 2);
});

test('highlight styles retain distinct canvas batches and use only two width tiers', async () => {
  const f = fixture(), t = f.tile(); f.toggle(true); await flush(); f.requests[0].resolve(f.reply(t)); await flush();
  const otherBase = new Style({zIndex: 17});
  const far = f.service.style(t.features[0], f.entries[1], f.base, 100), near = f.service.style(t.features[0], f.entries[1], f.base, 10), batch = f.service.style(t.features[0], f.entries[1], otherBase, 100);
  assert.deepEqual(Array.from(far, style => style.getStroke().getWidth()), [1.8]);
  assert.deepEqual(Array.from(near, style => style.getStroke().getWidth()), [3.2]);
  assert.deepEqual(Array.from(near, style => style.getStroke().color), ['#17212f']);
  assert.notEqual(batch[0].getZIndex(), far[0].getZIndex());
  assert.equal(near, f.service.style(t.features[0], f.entries[1], f.base, 1));
  assert.equal(f.service.style(t.features[0], f.entries[0], f.base, 10), f.base);
});

test('an evicted descriptor can return during another style pass at the same viewport', async () => {
  const f = fixture(); f.move([0, 0, 10000, 100]); f.toggle(true); await flush();
  const tiles = Array.from({length: 40}, (_, i) => f.tile(i)); await flush();
  for (let cycle = 0; cycle < 25; cycle++) {
    for (const request of f.requests.filter(request => !request.done)) {
      request.done = true;
      request.resolve(f.reply(tiles[Number(request.url.split('/').at(-2))]));
    }
    await flush();
  }
  assert.equal(f.requests.some(request => request.url.endsWith('/6/0/0')), false);
  f.service.style(tiles[0].features[0], f.entries[1], f.base, 100); await flush();
  assert.equal(f.requests.some(request => request.url.endsWith('/6/0/0')), true);
});

test('active Seoul emphasis stays above KICT regardless of asynchronous layer insertion order', () => {
  const f = fixture(); f.entries[1].layer.setZIndex(11);
  f.toggle(true);
  assert.equal(f.entries[1].layer.getZIndex(), 30);
  assert.equal(f.entries[0].layer.getZIndex(), 10);
  assert.ok(f.entries[1].layer.getZIndex() < 200, 'clicked selection remains above auto emphasis');
  f.toggle(false); assert.equal(f.entries[1].layer.getZIndex(), 11, 'original layer priority restored');
});

test('all sixteen water/sewer combinations activate only their own complete pair', () => {
  const ids = ['kict_national', 'sa001', 'kict_sewer_seoul', 'sb001'];
  for (let mask = 0; mask < 16; mask++) {
    const f = fixture({allPairs: true});
    f.setLayers(ids.filter((id, index) => mask & (1 << index)));
    const state = f.service.state();
    assert.equal(state.pairs.water.active, (mask & 3) === 3);
    assert.equal(state.pairs.sewer.active, (mask & 12) === 12);
    f.toggle(false);
  }
});

test('national/reference sewer does not substitute for the Seoul comparison source', () => {
  const f = fixture({allPairs: true});
  f.setLayers(['sb001', 'kict_sewer_national', 'kict_sewer_reference']);
  assert.equal(f.service.state().pairs.sewer.active, false); assert.equal(f.requests.length, 0);
});

test('all four layers prove independently and switching off water preserves sewer proof and priority', async () => {
  const f = fixture({allPairs: true}), water = f.tile(), sewer = f.tile(0, 2, 'sb001');
  f.setLayers(['kict_national', 'sa001', 'kict_sewer_seoul', 'sb001']); await flush();
  assert.equal(f.requests.length, 2);
  f.requests.find(request => request.url.includes('/water/')).resolve(f.reply(water));
  f.requests.find(request => request.url.includes('/sewer/')).resolve(f.reply(sewer)); await flush();
  assert.equal(f.service.style(water.features[0], water.entry, f.base, 10).length, 1);
  assert.equal(f.service.style(sewer.features[0], sewer.entry, f.base, 10).length, 1);
  f.setLayers(['sa001', 'kict_sewer_seoul', 'sb001']);
  assert.equal(f.service.style(water.features[0], water.entry, f.base, 10), f.base);
  assert.equal(f.service.style(sewer.features[0], sewer.entry, f.base, 10).length, 1);
  assert.equal(water.entry.layer.getZIndex(), 10); assert.equal(sewer.entry.layer.getZIndex(), 30);
});

test('one pair metadata failure does not prevent the other pair from becoming ready', async () => {
  const f = fixture({allPairs: true, manualMetadata: true}), water = f.tile(), sewer = f.tile(0, 2, 'sb001');
  f.setLayers(['kict_national', 'sa001', 'kict_sewer_seoul', 'sb001']); await flush();
  f.requests.find(request => request.url.includes('/water/')).resolve({available: false, message: '상수 자료 확인 필요'}, false);
  f.requests.find(request => request.url.includes('/sewer/')).resolve({available: true, version: 'sewer-v1', highlight_label: '하수 검증 기준', description: '시험용으로 확인한 기준'}); await flush();
  f.requests.find(request => request.url.includes('/sewer/tiles/')).resolve(f.reply(sewer)); await flush();
  assert.equal(f.service.state().pairs.water.status, 'error');
  assert.equal(f.service.state().pairs.sewer.status, 'ready');
  assert.equal(f.service.state().pairs.sewer.highlightLabel, '하수 검증 기준');
  assert.equal(f.service.style(water.features[0], water.entry, f.base, 10), f.base);
  assert.equal(f.service.style(sewer.features[0], sewer.entry, f.base, 10).length, 1);
});

test('shared request limit includes both pair metadata and flags, and a released slot serves the other pair', async () => {
  const f = fixture({allPairs: true, manualMetadata: true}); f.move([0, 0, 300, 100]);
  const water = [f.tile(), f.tile(1)], sewer = [f.tile(0, 2, 'sb001'), f.tile(1, 2, 'sb001')];
  f.setLayers(['kict_national', 'sa001', 'kict_sewer_seoul', 'sb001']); await flush();
  assert.equal(f.requests.length, 2); assert.equal(f.service.state().inflightCount, 2);
  f.requests[0].resolve({available: true, version: 'v1'}); await flush();
  assert.equal(f.requests.length, 3); assert.equal(f.service.state().inflightCount, 2);
  f.requests[1].resolve({available: true, version: 'sewer-v1'}); await flush();
  assert.equal(f.requests.length, 4); assert.equal(f.service.state().inflightCount, 2);
  assert.ok(f.requests[2].url.includes('/water/')); assert.ok(f.requests[3].url.includes('/sewer/'));
  f.requests[2].resolve(f.reply(water[0])); await flush();
  assert.equal(f.service.state().inflightCount, 2);
  assert.ok(f.requests[4].url.includes('/sewer/'), 'other pair gets first opportunity after a flag response');
  f.toggle(false); assert.equal(f.service.state().inflightCount, 0);
  assert.ok(sewer.length === 2);
});

test('a late disabled sewer response cannot remove or replace an active water result', async () => {
  const f = fixture({allPairs: true}), water = f.tile(), sewer = f.tile(0, 2, 'sb001');
  f.setLayers(['kict_national', 'sa001', 'kict_sewer_seoul', 'sb001']); await flush();
  const oldSewer = f.requests.find(request => request.url.includes('/sewer/'));
  f.requests.find(request => request.url.includes('/water/')).resolve(f.reply(water)); await flush();
  f.setLayers(['kict_national', 'sa001', 'sb001']);
  assert.equal(oldSewer.options.signal.aborted, true); oldSewer.resolve(f.reply(sewer)); await flush();
  assert.equal(f.service.state().pairs.water.status, 'ready');
  assert.equal(f.service.style(water.features[0], water.entry, f.base, 10).length, 1);
  assert.equal(f.service.style(sewer.features[0], sewer.entry, f.base, 10), f.base);
});

test('unavailable sewer criteria remain pending without implying a comparison result', async () => {
  const f = fixture({allPairs: true, manualMetadata: true}), sewer = f.tile(0, 2, 'sb001');
  f.setLayers(['kict_sewer_seoul', 'sb001']); await flush();
  f.requests[0].resolve({available: false, message: '하수 비교 기준 확인이 필요합니다.'}, false); await flush();
  const state = f.service.state().pairs.sewer;
  assert.equal(state.highlightLabel, '3자리 반올림 후 형상 일치');
  assert.equal(state.message, '하수 비교 기준 확인이 필요합니다.');
  assert.equal(f.requests.length, 1, 'unavailable criteria never request or manufacture tile flags');
  assert.equal(f.service.style(sewer.features[0], sewer.entry, f.base, 10), f.base);
});

test('both pairs hide the KICT duplicate only after the same municipal tile is verified, then restore on deselect', async () => {
  for (const [left,right] of [['kict_national','sa001'],['kict_sewer_seoul','sb001']]) {
    const f=fixture({allPairs:true}), k=f.tile(0,2,left), s=f.tile(0,2,right);
    f.setLayers([left,right]); await flush();
    assert.equal(f.requests.length,2);
    const kr=f.requests.find(r=>r.url.includes('/tiles/'+left+'/'));
    const sr=f.requests.find(r=>r.url.includes('/tiles/'+right+'/'));
    kr.resolve(f.reply(k,1,{version:left.includes('sewer')?'sewer-v1':'v1',counterpart_sha256:s.sha})); await flush();
    assert.equal(f.service.style(k.features[0],k.entry,f.base,100),f.base,'keep original until counterpart is ready');
    sr.resolve(f.reply(s)); await flush();
    assert.equal(f.service.style(k.features[0],k.entry,f.base,100).length,0,'matched KICT is not drawn');
    assert.equal(f.service.style(k.features[1],k.entry,f.base,100),f.base,'unmatched KICT remains');
    const black=f.service.style(s.features[0],s.entry,f.base,100);
    assert.equal(black.length,1); assert.equal(black[0].getStroke().color,'#17212f');
    assert.equal(f.service.style(s.features[1],s.entry,f.base,100),f.base,'unmatched Seoul remains');
    f.setLayers([left]); assert.equal(f.service.style(k.features[0],k.entry,f.base,100),f.base);
    f.setLayers([right]); assert.equal(f.service.style(s.features[0],s.entry,f.base,100),f.base);
  }
});

test('a changed municipal tile never suppresses the KICT original using stale counterpart proof', async () => {
  const f=fixture({allPairs:true}), k=f.tile(0,2,'kict_national'), s=f.tile(0,2,'sa001');
  f.setLayers(['kict_national','sa001']); await flush();
  f.requests.find(r=>r.url.includes('/tiles/kict_national/')).resolve(
    f.reply(k,1,{counterpart_sha256:'stale-municipal-sha'}));
  f.requests.find(r=>r.url.includes('/tiles/sa001/')).resolve(f.reply(s));
  await flush();
  assert.equal(f.service.style(k.features[0],k.entry,f.base,100),f.base);
  assert.equal(f.service.style(s.features[0],s.entry,f.base,100)[0].getStroke().color,'#17212f');
});
