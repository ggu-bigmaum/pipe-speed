/* Startup and availability logic with DOM/map adapters; real rendering is covered by browser QA. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const frontend = path.resolve(__dirname, '../../frontend');

class Element {
  constructor() {
    this.checked = false; this.disabled = true; this.hidden = false;
    this.value = ''; this.textContent = ''; this.style = {}; this.children = [];
    const classes = new Set();
    this.classList = {toggle: (name, enabled) => enabled ? classes.add(name) : classes.delete(name), contains: name => classes.has(name)};
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this[name] = value; }
  removeAttribute(name) { delete this[name]; }
}
class MapAdapter {
  constructor(options = {}) { Object.assign(this, options); }
  on() {} un() {} addLayer() {} removeLayer() {} render() {} fit() {} dispose() {} clear() {}
  getZoom() { return 11; }
  getSource() { return this.source; }
  setVisible(visible) { this.visible = visible; }
}

async function startup(cityStates, search = '') {
  const elements = {};
  let highlightState = {active: false, status: 'inactive'}, highlightChange;
  const html = fs.readFileSync(path.join(frontend, 'index.html'), 'utf8');
  for (const [, id] of html.matchAll(/id="([^"]+)"/g)) elements[id] = new Element();
  const rasters = [...html.matchAll(/<input id="([^"]+)" data-raster/g)].map(([, id]) => {
    elements[id].id = id; return elements[id];
  });
  elements.opacity.value = '90';
  const requests = [];
  const document = {
    getElementById: id => { assert.ok(elements[id], `Missing DOM element: ${id}`); return elements[id]; },
    querySelectorAll: selector => { assert.equal(selector, 'input[data-raster]'); return rasters; },
    createElement: () => new Element(), createTextNode: text => text,
    body: new Element(), documentElement: new Element(), hidden: false, addEventListener() {}
  };
  const ol = {
    View: MapAdapter, Map: MapAdapter,
    source: {Vector: MapAdapter, OSM: MapAdapter}, layer: {Vector: MapAdapter, Tile: MapAdapter},
    style: {Style: MapAdapter, Stroke: MapAdapter, Circle: MapAdapter, Fill: MapAdapter},
    control: {Zoom: MapAdapter, ScaleLine: MapAdapter, Attribution: MapAdapter},
    interaction: {defaults: {defaults: () => []}, DragPan: MapAdapter, MouseWheelZoom: MapAdapter},
    proj: {fromLonLat: xy => xy, transformExtent: xy => xy}
  };
  const context = {
    document, ol, URL, URLSearchParams, AbortController, AbortSignal, DOMException, structuredClone,
    setTimeout, clearTimeout, performance, location: {href: 'http://test/app/', search},
    localStorage: {getItem: () => null}, matchMedia: () => ({matches: false, addEventListener() {}}),
    getComputedStyle: () => ({getPropertyValue: () => '300'}),
    fetch: async url => {
      requests.push(url);
      let body;
      if (url.endsWith('/national/catalog')) body = {groups: [
        {id: 'water', regions: [{code: '39', label: '제주', dataset_id: 'kict_water_national_39', route: 'national/water/39/', available: true}]},
        {id: 'sewer', regions: []}
      ]};
      const city = /\/(seoul_water|seoul_sewer)\/availability$/.exec(url)?.[1];
      if (city) {
        if (cityStates[city] instanceof Error) throw cityStates[city];
        body = cityStates[city];
      }
      return {ok: Boolean(body), status: body ? 200 : 503, json: async () => body || {error: 'metadata sentinel'}};
    }
  };
  context.window = {ol, GeoTIFF: {}, APP_CONFIG: {}, PipeDistanceCore: require(path.join(frontend, 'distance-core.js')),
    PipeVectorCatalog: {create: () => ({selected: () => [], entries: []})},
    PipeMatchHighlight: {create: ({onStateChange}) => { highlightChange = onStateChange; return {syncVisibility() {}, state: () => highlightState}; }},
    PipeComparison: {create: () => ({show() {}, clear() {}})},
    PipeVectorSelection: {create: () => ({pick: () => false, clear() {}, syncVisibility() {}, getSelection: () => null})}};
  await vm.runInNewContext(fs.readFileSync(path.join(frontend, 'distance.js'), 'utf8'), context);
  for (let i = 0; i < 5; i++) await new Promise(setImmediate);
  return {elements, requests, context, rasters, setHighlightState: value => { highlightState = value; highlightChange(); }};
}
const state = (dataset_id, available = false, extra = {}) => ({
  dataset_id, label: dataset_id === 'seoul_water' ? '서울시 상수' : '서울시 하수',
  available, status: available ? 'ready' : 'waiting', reason: available ? '' : '자료 대기', ...extra
});

test('missing city data leaves legacy and national controls available without auto-selection', async () => {
  const {elements, requests, rasters} = await startup({seoul_water: state('seoul_water'), seoul_sewer: state('seoul_sewer')});
  assert.equal(elements.case1.disabled, false);
  assert.equal(elements.sewerCase1.disabled, false);
  assert.equal(elements.nationalWaterCase1.disabled, false);
  for (const stem of ['seoulWater', 'seoulSewer']) {
    assert.equal(elements[stem + 'Case1'].disabled, true);
    assert.equal(elements[stem + 'Case2'].disabled, true);
    assert.equal(elements[stem + 'State'].textContent, '자료 대기');
  }
  assert.equal(rasters.some(input => input.checked), false);
  assert.equal(requests.some(url => url.includes('/meta') || url.includes('/files/')), false);
});

test('each ready municipal group selects its own route; one invalid dataset does not disable the other', async () => {
  for (const [id, stem, other] of [['seoul_water', 'seoulWater', 'seoul_sewer'], ['seoul_sewer', 'seoulSewer', 'seoul_water']]) {
    const {elements, requests} = await startup({[id]: state(id, true), [other]: state(other, false, {status: 'invalid', reason: '격자 불일치'})});
    assert.equal(elements[stem + 'Case1'].disabled, false);
    assert.equal(elements[stem + 'Case2'].disabled, false);
    assert.equal(elements[other === 'seoul_water' ? 'seoulWaterNote' : 'seoulSewerNote'].textContent, '격자 불일치');
    elements[stem + 'Case2'].checked = true;
    elements[stem + 'Case2'].onchange();
    for (let i = 0; i < 5; i++) await new Promise(setImmediate);
    assert.deepEqual(requests.filter(url => url.endsWith('/meta')), ['http://test/distance/' + id + '/meta']);
    assert.equal(elements.case1.disabled, false);
  }
});

test('availability network failure and wrong source identity are scoped to their city groups', async () => {
  const {elements, requests} = await startup({seoul_water: new Error('offline'), seoul_sewer: state('kict_sewer_seoul', true)});
  assert.equal(elements.seoulWaterCase1.disabled, true);
  assert.equal(elements.seoulSewerCase1.disabled, true);
  assert.match(elements.seoulWaterNote.textContent, /확인|연결/);
  assert.match(elements.seoulSewerNote.textContent, /구분|출처/);
  assert.equal(elements.nationalWaterCase1.disabled, false);
  assert.equal(elements.status.classList.contains('error'), false);
  assert.equal(requests.some(url => url.endsWith('/meta')), false);
});

test('direct municipal link waits for availability and does not select missing files', async () => {
  const {elements, requests, rasters} = await startup({seoul_water: state('seoul_water'), seoul_sewer: state('seoul_sewer')}, '?dataset=seoul_water');
  assert.equal(rasters.some(input => input.checked), false);
  assert.equal(elements.case1.disabled, false);
  assert.equal(elements.seoulWaterCase1.disabled, true);
  assert.equal(requests.some(url => url.endsWith('/meta')), false);
  assert.match(elements.status.textContent, /서울시 상수.*자료 대기/);
});

test('strict comparison legend follows its own loading/error state without selecting a raster', async () => {
  const {elements, rasters, setHighlightState} = await startup({seoul_water: state('seoul_water'), seoul_sewer: state('seoul_sewer')});
  const text = node => typeof node === 'string' ? node : (node.textContent || '') + node.children.map(text).join('');
  setHighlightState({active: true, status: 'loading'});
  assert.match(text(elements.vectorLegend), /ID·전체 선형 완전일치/);
  assert.match(text(elements.vectorLegend), /확인하는 중/);
  setHighlightState({active: true, status: 'error'});
  assert.match(text(elements.vectorLegend), /확인하지 못/);
  assert.equal(rasters.some(input => input.checked), false);
  assert.equal(elements.rasterLegend.hidden, true);
  setHighlightState({active: false, status: 'inactive'});
  assert.doesNotMatch(text(elements.vectorLegend), /완전일치/);
});
