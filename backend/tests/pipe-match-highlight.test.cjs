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
function fixture({manualMetadata = false} = {}) {
  const requests = [], changes = [], handlers = {}, frames = [];
  let bounds = [0, 0, 100, 100];
  const view = {on: (name, fn) => { handlers[name] = fn; }, getResolution: () => 100, calculateExtent: () => bounds};
  const grid = {getZForResolution: () => 6, getTileCoordExtent: ([z, x, y]) => [x * 100, y * 100, x * 100 + 99, y * 100 + 99]};
  const makeEntry = id => ({id, generation: 0, checkbox: {checked: false}, source: {getTileGrid: () => grid}, layer: {visible: false, zIndex: 10, getVisible() { return this.visible; }, getZIndex() { return this.zIndex; }, setZIndex(value) { this.zIndex = value; }, changed() { changes.push(id); }}});
  const entries = [makeEntry('kict_national'), makeEntry('sa001')];
  const context = {window: {}, crypto: cryptoFacade, WeakRef, Uint8Array, ArrayBuffer, AbortController, URL, location: {href: 'http://test/app/'}, atob: text => Buffer.from(text, 'base64').toString('binary'), setTimeout: () => 1, clearTimeout() {}, requestAnimationFrame: fn => { frames.push(fn); return frames.length; },
    ol: {style: {Style, Stroke}, extent: {intersects: (a, b) => a[0] <= b[2] && a[2] >= b[0] && a[1] <= b[3] && a[3] >= b[1]}},
    fetch: (url, options = {}) => {
      if (url.endsWith('/metadata') && !manualMetadata) return Promise.resolve({ok: true, json: async () => ({available: true, version: 'v1'})});
      return new Promise((resolve, reject) => requests.push({url, options, resolve: (body, ok = true) => resolve({ok, json: async () => body}), reject}));
    }};
  vm.runInNewContext(fs.existsSync(script) ? fs.readFileSync(script, 'utf8') : '', context);
  assert.equal(typeof context.window.PipeMatchHighlight?.create, 'function', 'highlight module provides create');
  const service = context.window.PipeMatchHighlight.create({map: {getView: () => view, getSize: () => [800, 600]}, getEntries: () => entries, apiBase: 'http://test/', onStateChange() {}});
  const toggle = on => { for (const entry of entries) { entry.checkbox.checked = on; entry.layer.visible = on; } service.syncVisibility(); };
  const tile = (x = 0, count = 2) => {
    const features = Array.from({length: count}, () => ({getProperties: () => ({layer: 'sa001'}), getGeometry: () => ({getType: () => 'LineString'})}));
    const raw = new Uint8Array([x % 256, count, 3]).buffer;
    const value = {getTileCoord: () => [6, x, 0], getFeatures: () => features};
    service.attachTile(entries[1], value, features, raw);
    return {tile: value, features, raw, sha: createHash('sha256').update(new Uint8Array(raw)).digest('hex')};
  };
  const reply = (target, bits = 1, extra = {}) => ({available: true, version: 'v1', layer: 'sa001', sha256: target.sha, feature_count: target.features.length, bits_base64: Buffer.from([bits]).toString('base64'), matched_count: bits === 3 ? 2 : bits ? 1 : 0, schema_version: 1, ...extra});
  const base = new Style({stroke: new Stroke({color: '#df7c24', width: 1.8}), zIndex: 0});
  return {service, entries, requests, changes, toggle, tile, reply, base, frames, move: next => { bounds = next; handlers['change:center'](); }};
}

test('both water layers are required and successful flags share cached styles', async () => {
  const f = fixture(), t = f.tile(); await flush();
  f.entries[1].checkbox.checked = true; f.entries[1].layer.visible = true; f.service.syncVisibility();
  assert.equal(f.requests.length, 0); assert.equal(f.service.style(t.features[0], f.entries[1], f.base, 100), f.base);
  f.toggle(true); await flush(); assert.equal(f.requests.length, 1);
  f.requests[0].resolve(f.reply(t)); await flush();
  const styles = f.service.style(t.features[0], f.entries[1], f.base, 100);
  assert.equal(styles.length, 2); assert.equal(styles, f.service.style(t.features[0], f.entries[1], f.base, 100));
  assert.equal(f.service.style(t.features[1], f.entries[1], f.base, 100), f.base);
  assert.equal(f.service.state().matchedTileCount, 1);
  f.toggle(false); assert.equal(f.service.style(t.features[0], f.entries[1], f.base, 100), f.base);
  assert.equal(f.service.state().active, false);
});

test('MVT ordinals survive a renderer in-place array reorder', async () => {
  const f = fixture(), t = f.tile(), original = t.features[0]; t.features.reverse(); f.toggle(true); await flush();
  f.requests[0].resolve(f.reply(t)); await flush();
  assert.equal(f.service.style(original, f.entries[1], f.base, 100).length, 2);
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
  f.requests[1].resolve(f.reply(t)); await flush(); assert.equal(f.service.style(t.features[0], f.entries[1], f.base, 100).length, 2);
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
  assert.equal(f.service.style(t.features[0], f.entries[1], f.base, 100).length, 2);
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
  assert.deepEqual(Array.from(far, style => style.getStroke().getWidth()), [2.6, 1.4]);
  assert.deepEqual(Array.from(near, style => style.getStroke().getWidth()), [5, 3]);
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
