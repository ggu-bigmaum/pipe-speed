const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {webcrypto, createHash} = require('node:crypto');
const frontend = path.resolve(__dirname, '../../frontend');
class Element {
  constructor() { this.children = []; this.style = {}; this.checked = false; this.disabled = false; this.classList = {add() {}}; }
  append(...children) { this.children.push(...children); }
  setAttribute(name, value) { this[name] = value; }
}
class Style { constructor(options) { Object.assign(this, options); } getZIndex() { return this.zIndex; } }
class Stroke { constructor(options) { Object.assign(this, options); } }
class Source {
  constructor(options) { Object.assign(this, options); }
  on() {} refresh() { this.refreshed = true; }
  getTileGrid() { return {getZForResolution: () => 6, getTileCoordExtent: () => [0, 0, 100, 100]}; }
}
class Layer { constructor(options) { Object.assign(this, options); } setVisible(visible) { this.visible = visible; } getVisible() { return this.visible; } getZIndex() { return this.zIndex; } setZIndex(value) { this.zIndex = value; } changed() {} }
async function fixture({count = 2, sewer = false, allLayers = false} = {}) {
  const elements = Object.fromEntries(['vectorTarget', 'reloadVector', 'vectorMs', 'vectorState', 'vectorCount', 'layerCount', 'vectorList'].map(id => [id, new Element()]));
  const requests = [], timers = [], hashPromises = [], layers = [], coords = [0, 0, 1, 1];
  const raw = new Uint8Array([1, 2, 3]).buffer;
  const features = Array.from({length: count}, () => ({getProperties: () => ({layer: 'sa001'}), getGeometry: () => ({getType: () => 'LineString'}), coordinates: coords}));
  let zoom = 12;
  const handlers = {};
  const view = {on(name, fn) { (handlers[name] ||= []).push(fn); }, getZoom: () => zoom, getResolution: () => 100, calculateExtent: () => [0, 0, 100, 100]};
  const map = {getView: () => view, getSize: () => [800, 600], on() {}, un() {}, render() {}, addLayer: layer => layers.push(layer)};
  const context = {window: {}, document: {getElementById: id => elements[id], createElement: () => new Element(), addEventListener() {}, hidden: false}, URL, location: {href: 'http://test/app/'}, WeakRef, Uint8Array, AbortController, AbortSignal, performance,
    crypto: {subtle: {digest: (...args) => { const promise = webcrypto.subtle.digest(...args); hashPromises.push(promise); return promise; }}},
    atob: text => Buffer.from(text, 'base64').toString('binary'), setTimeout: (fn, ms) => { timers.push(ms); return timers.length; }, clearTimeout() {}, requestAnimationFrame() {},
    ol: {style: {Style, Stroke}, source: {VectorTile: Source}, layer: {VectorTile: Layer}, format: {MVT: class {readFeatures() { return features; }}}, extent: {intersects: () => true}},
    fetch: async (url, options) => {
      if (url.endsWith('/meta.json')) return {ok: true, json: async () => ({minzoom: 6, maxzoom: 16, vector_layer_id: url.includes('sa001') ? 'sa001' : 'kict_national'})};
      if (url.endsWith('.pbf')) return {ok: true, status: 200, arrayBuffer: async () => raw};
      if (url.endsWith('/metadata')) return {ok: true, json: async () => ({available: true, version: 'v1'})};
      return new Promise(resolve => requests.push({url, options, resolve: (body, ok = true) => resolve({ok, json: async () => body})}));
    }};
  for (const file of ['pipe-match-highlight.js', 'distance-vectors.js']) vm.runInNewContext(fs.readFileSync(path.join(frontend, file), 'utf8'), context);
  let catalog;
  const highlight = context.window.PipeMatchHighlight.create({map, getEntries: () => catalog?.entries || [], onStateChange() {}});
  const configuredLayers = allLayers
    ? ['kict_national', 'sa001', 'kict_sewer_seoul', 'sb001', 'kict_sewer_national', 'reference_sewer'].map(id => ({id, label: id, reference: id === 'reference_sewer'}))
    : [{id: sewer ? 'kict_sewer_seoul' : 'kict_national', label: '건기연', color: '#4dabf7'}, {id: sewer ? 'sb001' : 'sa001', label: '서울시', color: '#df7c24'}];
  catalog = context.window.PipeVectorCatalog.create({map, view, tilesBase: 'http://test/tiles/', config: {LAYERS: configuredLayers}, highlight, onChange() {}, onSelect() {}, onRecord() {}});
  const flush = async () => { await Promise.all(hashPromises); for (let i = 0; i < 4; i++) await new Promise(setImmediate); };
  await flush();
  const set = (entry, checked) => { entry.checkbox.checked = checked; entry.checkbox.onchange(); };
  const entry = catalog.entries[1];
  const tile = {getTileCoord: () => [6, 0, 0], setLoader(loader) { this.loader = loader; }, getFeatures() { return this.features; }, setFeatures(value) { this.symbolsBeforeInstall = Object.getOwnPropertySymbols(value[0] || {}).length; this.features = value; }, setState(state) { this.state = state; }};
  const load = async () => { entry.source.tileLoadFunction(tile, 'http://test/tiles/sa001/6/0/0.pbf'); await tile.loader([0, 0, 100, 100], 100, 'EPSG:3857'); await flush(); };
  const body = () => {
    const bits = Buffer.alloc(Math.ceil(features.length / 8)); const ordinal = features.length - 1; bits[ordinal >> 3] |= 1 << (ordinal & 7);
    return {available: true, version: 'v1', layer: 'sa001', sha256: createHash('sha256').update(new Uint8Array(raw)).digest('hex'), feature_count: features.length, bits_base64: bits.toString('base64'), matched_count: 1, schema_version: 1};
  };
  const setZoom = value => { zoom = value; for (const fn of handlers['change:resolution'] || []) fn(); };
  return {catalog, highlight, entry, features, coords, tile, load, body, requests, set, setZoom, flush, timers};
}

for (const sewer of [false, true]) test(`${sewer ? 'sewer' : 'water'} pair requires zoom 12 and releases second selection on zoom out`, async () => {
  const f = await fixture({sewer}), first = f.catalog.entries[0], second = f.entry;
  f.setZoom(11); f.set(first, true);
  assert.equal(second.checkbox.disabled, true);
  assert.match(second.note.textContent, /12/);
  f.set(second, true); // Even a queued change event cannot bypass the gate.
  assert.equal(second.checkbox.checked, false);
  assert.equal(second.layer.getVisible(), false);
  f.setZoom(12); assert.equal(second.checkbox.disabled, false);
  f.set(second, true); assert.equal(second.layer.getVisible(), true);
  const controller = new AbortController(); second.requests.set(f.tile, controller);
  const generation = second.generation;
  f.setZoom(11.99);
  assert.equal(first.layer.getVisible(), true);
  assert.equal(second.checkbox.checked, false);
  assert.equal(second.layer.getVisible(), false);
  assert.equal(controller.signal.aborted, true);
  assert.ok(second.generation > generation);
  assert.equal(second.source.refreshed, true);
  assert.equal(f.highlight.state().active, false);
  f.setZoom(12); assert.equal(second.checkbox.disabled, false);
  assert.equal(second.checkbox.checked, false, 'zooming in never silently reloads a second layer');
});

test('first selected source is retained even when municipal source was selected first', async () => {
  const f = await fixture(), kict = f.catalog.entries[0];
  f.set(f.entry, true); f.set(kict, true); f.setZoom(11);
  assert.equal(f.entry.layer.getVisible(), true);
  assert.equal(kict.checkbox.checked, false);
  f.set(f.entry, false); assert.equal(kict.checkbox.disabled, false);
});

test('an already queued loader does not decode or install a hidden comparison layer', async () => {
  const f = await fixture();
  f.set(f.catalog.entries[0], true); f.set(f.entry, true); f.setZoom(11);
  await f.load();
  assert.equal(f.tile.features.length, 0);
  assert.equal(f.tile.symbolsBeforeInstall, 0);
  assert.equal(f.requests.length, 0);
});

test('every first vector blocks every other vector below 12, including mixed and reference sources', async () => {
  for (let firstIndex = 0; firstIndex < 6; firstIndex++) {
    const f = await fixture({allLayers: true});
    f.setZoom(11.99);
    const first = f.catalog.entries[firstIndex];
    f.set(first, true);
    for (const entry of f.catalog.entries) {
      assert.equal(entry.checkbox.disabled, entry !== first, `${first.id} -> ${entry.id}`);
    }
    f.setZoom(12);
    for (const entry of f.catalog.entries) {
      assert.equal(entry.checkbox.disabled, false);
      if (entry !== first) f.set(entry, true);
    }
    assert.equal(f.catalog.selected().length, 6);
    f.setZoom(11.99);
    assert.equal(f.catalog.selected().length, 1);
    assert.equal(f.catalog.selected()[0], first);
    for (const entry of f.catalog.entries.filter(e => e !== first)) {
      assert.equal(entry.source.refreshed, true);
      assert.equal(entry.layer.getVisible(), false);
    }
    f.set(first, false);
    assert.ok(f.catalog.entries.every(entry => !entry.checkbox.disabled));
  }
});

test('catalog installs ordinal metadata before base features and adds navy emphasis only after pair proof', async () => {
  const f = await fixture({count: 16385});
  f.set(f.entry, true); await f.load();
  assert.equal(f.tile.features, f.features, 'base tile is installed immediately with original feature objects');
  assert.ok(f.tile.symbolsBeforeInstall >= 2, 'original ordinal and tile descriptor precede tile.setFeatures');
  assert.equal(f.requests.length, 0, 'one water layer makes no bitset request');
  assert.equal(f.entry.layer.style(f.features.at(-1), 100).getZIndex(), 1, 'canvas batch remains distinct');
  f.set(f.catalog.entries[0], true); await f.flush(); assert.equal(f.requests.length, 1);
  assert.ok(f.timers.includes(90000), 'server queue and large sidecar build have a separate bounded deadline');
  f.requests[0].resolve(f.body()); await f.flush();
  const styles = f.entry.layer.style(f.features.at(-1), 100);
  assert.equal(styles.length, 2); assert.equal(styles[0].stroke.color, '#ffffff'); assert.equal(styles[1].stroke.color, '#17212f'); assert.equal(styles[0].getZIndex(), 10002);
  assert.equal(f.entry.layer.style(f.features[0], 100).stroke.color, '#df7c24');
  assert.equal(f.features.at(-1).coordinates, f.coords, 'original geometry is not copied or replaced');
  f.set(f.catalog.entries[0], false);
  assert.equal(f.entry.layer.style(f.features.at(-1), 100).stroke.color, '#df7c24');
});

test('comparison sidecar failure leaves the original catalog tile healthy', async () => {
  const f = await fixture(); f.set(f.entry, true); f.set(f.catalog.entries[0], true); await f.load();
  assert.equal(f.requests.length, 1); f.requests[0].resolve({available: false}, false); await f.flush();
  assert.equal(f.entry.failed, false); assert.equal(f.tile.state, undefined); assert.equal(f.tile.features, f.features);
  assert.equal(f.entry.layer.style(f.features[1], 100).stroke.color, '#df7c24');
  assert.equal(f.highlight.state().status, 'error');
});
