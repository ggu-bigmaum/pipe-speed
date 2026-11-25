/* Comparison UI behavior with a small DOM and deferred HTTP responses. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const script = path.resolve(__dirname, '../../frontend/pipe-comparison.js');
class Element {
  constructor(tag = 'div') { this.tagName = tag; this.children = []; this.hidden = false; this.disabled = false; this.value = ''; this.dataset = {}; this._text = ''; this.attributes = {}; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  setAttribute(key, value) { this.attributes[key] = value; }
  removeAttribute(key) { delete this.attributes[key]; }
}
const ids = ['pipeComparison', 'pipeComparisonMessage', 'pipeSourcePicker', 'pipeSources', 'pipeComparisonResult', 'pipeComparisonStatus', 'pipeComparisonMeaning', 'pipeComparisonFacts', 'pipeComparisonReason', 'pipeComparisonEvidenceValues', 'pipeCounterpartsHeading', 'pipeCounterparts', 'pipeCounterpartsMore', 'pipeComparisonRetry'];
const flush = async () => { for (let i = 0; i < 4; i++) await new Promise(setImmediate); };
test('sewer uses retained id and shows rounded evidence without assigning MATCH', async () => {
  const {ui, elements:e, requests}=fixture();
  ui.show(item('sb001',{id:12,IDN:'S1',geometry:{large:true}}));
  assert.match(requests[0].url,/comparison\/sewer\/lookup$/);
  assert.deepEqual(requests[0].body.properties,{id:12});
  const body=result(); body.pipe.comparison_status=null;body.pipe.rounded_geometry_match=true;body.pipe.precision_review=false;
  body.counterparts={items:[{source_fid:5,original_id:'K5',relationship:'rounded_geometry'}],total:1,next_offset:null};
  requests[0].resolve(body);await flush();
  assert.equal(e.pipeComparisonStatus.textContent,'형상 일치');
  assert.match(e.pipeComparisonFacts.textContent,/3자리 반올림/);
  assert.match(e.pipeCounterparts.textContent,/건기연.*K5/);
  assert.doesNotMatch(e.pipeComparisonStatus.textContent,/MATCH/);
  ui.show(item('kict_sewer_seoul',{id:3})); body.pipe.rounded_geometry_match=null;body.pipe.precision_review=true;
  requests[1].resolve(body);await flush();
  assert.match(e.pipeComparisonStatus.textContent,/확인 필요/);
});
function fixture() {
  const elements = Object.fromEntries(ids.map(id => [id, new Element()]));
  const requests = [];
  const document = {getElementById: id => { assert.ok(elements[id], id); return elements[id]; }, createElement: tag => new Element(tag)};
  const context = {window: {}, document, URL, AbortController, setTimeout, clearTimeout, location: {href: 'http://test/app/'}, fetch: (url, options) => new Promise((resolve, reject) => requests.push({url, options, body: JSON.parse(options.body), resolve: (body, ok = true) => resolve({ok, json: async () => body}), reject}))};
  vm.runInNewContext(fs.existsSync(script) ? fs.readFileSync(script, 'utf8') : '', context);
  assert.equal(typeof context.window.PipeComparison?.create, 'function', 'comparison module provides create');
  return {ui: context.window.PipeComparison.create({apiBase: 'http://test/'}), elements, requests};
}
function item(layer = 'sa001', props = {PLINE_ID: 'P0', PLINE_CALB: 0, PLINE_MTRQ: null}) {
  return {entry: {id: layer, label: layer === 'sa001' ? '서울시 상수관망' : '건기연 상수관망'}, feature: {getProperties: () => props}, isCurrent: () => true};
}
function result(overrides = {}) {
  return {available: true, mapping: {member_count: 1, members: [{source_fid: 0, original_id: 'P0', region: '서울'}], ambiguous: false}, pipe: {dataset: 'seoul', source_fid: 0, original_id: 'P0', comparison_status: 'MATCH', reason_code: 'exact', reason: '전체 선형 일치', coverage_ratio: 0, length_m: 0, length_scope: 'original_full', exact_same_id_geometry: false, exact_geometry_match: true, regression_review_required: false, attribute_flags: {diameter: false, count: 0}}, counterparts: {total: 0, accepted: 0, items: [], offset: 0, next_offset: null}, ...overrides};
}

test('one water layer queries only audited scalar properties and renders false/zero without HTML', async () => {
  const {ui, elements: e, requests} = fixture();
  ui.show(item('sa001', {PLINE_ID: 'P0', PLINE_CALB: 0, PLINE_MTRQ: null, geometry: {large: true}, unsafe: '<img>'}));
  assert.deepEqual(requests[0].body, {layer: 'sa001', properties: {PLINE_ID: 'P0', PLINE_CALB: 0, PLINE_MTRQ: null}});
  const body = result(); body.pipe.reason = '<img src=x onerror=alert(1)>'; requests[0].resolve(body); await flush();
  assert.match(e.pipeComparisonStatus.textContent, /일치.*MATCH/);
  assert.match(e.pipeComparisonFacts.textContent, /0%/);
  assert.match(e.pipeComparisonFacts.textContent, /아니요/);
  assert.equal(e.pipeComparisonReason.textContent, body.pipe.reason);
  assert.equal(e.pipeComparisonReason.children.length, 0);
  assert.match(e.pipeComparisonEvidenceValues.textContent, /false/);
  assert.match(e.pipeComparisonEvidenceValues.textContent, /0/);
  assert.equal(e.pipeSourcePicker.hidden, true);
  assert.equal(e.pipeComparisonResult.hidden, false);
});

test('new selection and clear ignore delayed success even when fetch ignores abort', async () => {
  const {ui, elements: e, requests} = fixture();
  ui.show(item()); ui.show(item('kict_national', {FTR_IDN: 'K2', STD_DIP: 50, MOP_CDE: 'x'}));
  assert.equal(requests[0].options.signal.aborted, true);
  const second = result(); second.pipe.reason = '두 번째'; requests[1].resolve(second); await flush();
  requests[0].resolve(result()); await flush();
  assert.equal(e.pipeComparisonReason.textContent, '두 번째');
  ui.show(item()); const late = requests[2]; ui.clear(); late.resolve(result()); await flush();
  assert.equal(e.pipeComparison.hidden, true);
  assert.equal(e.pipeComparisonReason.textContent, '');
});

test('availability guard ignores a late response after zoom, layer hiding, or grid mode switch', async () => {
  const {ui, elements: e, requests} = fixture(); let current = true;
  ui.show({...item(), isCurrent: () => current}); current = false;
  requests[0].resolve(result()); await flush();
  assert.equal(e.pipeComparisonResult.hidden, true);
});

test('ambiguous originals require an explicit choice and preserve source_fid zero', async () => {
  const {ui, elements: e, requests} = fixture(); ui.show(item());
  const mapping = {member_count: 2, ambiguous: true, members: [{source_fid: 0, original_id: 'DUP', region: '서울'}, {source_fid: 7, original_id: 'DUP', region: '서울'}]};
  requests[0].resolve(result({mapping, pipe: null})); await flush();
  assert.equal(e.pipeSources.value, '');
  assert.equal(e.pipeSources.children.length, 3);
  assert.equal(e.pipeComparisonResult.hidden, true);
  assert.equal(requests.length, 1, 'no automatic first original selection');
  assert.match(e.pipeSources.children[1].textContent, /원본 객체 0/);
  e.pipeSources.value = '0'; e.pipeSources.onchange();
  assert.equal(requests[1].body.source_fid, 0);
  requests[1].resolve(result({mapping})); await flush();
  assert.equal(e.pipeSources.value, '0');
  assert.equal(e.pipeComparisonResult.hidden, false);
});

test('counterpart paging appends all relations and uses the explicitly chosen original', async () => {
  const {ui, elements: e, requests} = fixture(); ui.show(item());
  requests[0].resolve(result({counterparts: {total: 2, accepted: 1, items: [{source_fid: 11, original_id: 'C1', relationship: 'sustained', reason: '연속', comparison_status: 'MATCH', coverage_ratio: 1}], offset: 0, next_offset: 1}})); await flush();
  assert.equal(e.pipeCounterparts.children.length, 1); assert.equal(e.pipeCounterpartsMore.hidden, false);
  e.pipeCounterpartsMore.onclick(); assert.equal(requests[1].body.offset, 1); assert.equal(requests[1].body.source_fid, 0);
  requests[1].resolve(result({counterparts: {total: 2, accepted: 1, items: [{source_fid: 12, original_id: 'C2', relationship: 'candidate_rejected', reason: '<b>보류</b>', comparison_status: 'CONFLICT', coverage_ratio: 0}], offset: 1, next_offset: null}})); await flush();
  assert.equal(e.pipeCounterparts.children.length, 2);
  assert.match(e.pipeCounterparts.textContent, /기준 미충족 후보/);
  assert.match(e.pipeCounterparts.textContent, /<b>보류<\/b>/);
  assert.equal(e.pipeCounterpartsMore.hidden, true);
  assert.match(e.pipeCounterpartsHeading.textContent, /2.*2/);
});

test('unsupported layers, unavailability and request failure never become NOT_COMPARED', async () => {
  const {ui, elements: e, requests} = fixture();
  ui.show(item('kict_sewer_national')); assert.equal(requests.length, 0); assert.match(e.pipeComparisonMessage.textContent, /연결되지/);
  ui.show(item()); requests[0].resolve({available: false, reason: '자료 연결 대기'}); await flush();
  assert.match(e.pipeComparisonMessage.textContent, /자료 연결 대기/); assert.equal(e.pipeComparisonResult.hidden, true);
  ui.show(item()); requests[1].reject(new Error('offline')); await flush();
  assert.match(e.pipeComparisonMessage.textContent, /불러오지 못/); assert.equal(e.pipeComparisonRetry.hidden, false);
  assert.doesNotMatch(e.pipeComparisonStatus.textContent, /NOT_COMPARED/);
});

test('all six business states have Korean meaning and review flags remain explicit', async () => {
  const {ui, elements: e, requests} = fixture();
  const names = {MATCH: '일치', PARTIAL_MATCH: '부분 일치', KICT_ONLY: '건기연에만 있음', LOCAL_ONLY: '지자체에만 있음', CONFLICT: '확인 필요', NOT_COMPARED: '미비교'};
  for (const [status, label] of Object.entries(names)) {
    ui.show(item()); const body = result(); body.pipe.comparison_status = status; body.pipe.regression_review_required = true;
    requests.at(-1).resolve(body); await flush();
    assert.match(e.pipeComparisonStatus.textContent, new RegExp(label));
    assert.ok(e.pipeComparisonMeaning.textContent.length > 5);
    assert.match(e.pipeComparisonEvidenceValues.textContent, /검토 필요/);
  }
  assert.match(e.pipeComparisonMeaning.textContent, /상대.*자료.*확보/);
});

test('clipped coverage is labelled as evaluation scope rather than full original length', async () => {
  const {ui, elements: e, requests} = fixture(); ui.show(item()); const body = result();
  body.pipe.length_scope = 'seoul_boundary_clip'; requests[0].resolve(body); await flush();
  assert.match(e.pipeComparisonFacts.textContent, /평가구간 대응률/);
  assert.match(e.pipeComparisonEvidenceValues.textContent, /서울 경계/);
  assert.doesNotMatch(e.pipeComparisonEvidenceValues.textContent, /seoul_boundary_clip/);
});

test('a returned original outside the audited mapping is rejected', async () => {
  const {ui, elements: e, requests} = fixture(); ui.show(item()); const body = result(); body.pipe.source_fid = 999;
  requests[0].resolve(body); await flush();
  assert.equal(e.pipeComparisonResult.hidden, true);
  assert.match(e.pipeComparisonMessage.textContent, /불러오지 못/);
});

test('HTTP failure displays the server explanation and delayed rejection cannot overwrite another selection', async () => {
  const {ui, elements: e, requests} = fixture(); ui.show(item());
  requests[0].resolve({available: false, error: 'comparison_unavailable', message: '비교 자료를 확인할 수 없습니다.'}, false); await flush();
  assert.equal(e.pipeComparisonMessage.textContent, '비교 자료를 확인할 수 없습니다.');
  ui.show(item()); ui.show(item()); requests[2].resolve(result()); await flush();
  requests[1].reject(new Error('late failure')); await flush();
  assert.equal(e.pipeComparisonMessage.textContent, ''); assert.equal(e.pipeComparisonRetry.hidden, true);
});

test('paging failure retains earlier relations and retry loads only the failed page', async () => {
  const {ui, elements: e, requests} = fixture(); ui.show(item());
  requests[0].resolve(result({counterparts: {total: 2, accepted: 2, items: [{source_fid: 1, original_id: 'A', relationship: 'exact_same_id'}], offset: 0, next_offset: 1}})); await flush();
  e.pipeCounterpartsMore.onclick(); requests[1].reject(new Error('temporary')); await flush();
  assert.equal(e.pipeCounterparts.children.length, 1);
  e.pipeComparisonRetry.onclick(); assert.equal(requests[2].body.offset, 1);
  requests[2].resolve(result({counterparts: {total: 2, accepted: 2, items: [{source_fid: 2, original_id: 'B', relationship: 'exact_other_id'}], offset: 1, next_offset: null}})); await flush();
  assert.equal(e.pipeCounterparts.children.length, 2); assert.equal(e.pipeComparisonRetry.hidden, true);
});

test('another pipe overall MATCH and coverage never turn a rejected pair into a match', async () => {
  const {ui, elements: e, requests} = fixture(); ui.show(item());
  requests[0].resolve(result({counterparts: {total: 1, accepted: 0, items: [{source_fid: 9, original_id: 'C9', relationship: 'candidate_rejected', reason: '교차 후보', accepted: false, comparison_status: 'MATCH', coverage_ratio: 1}], offset: 0, next_offset: null}})); await flush();
  assert.match(e.pipeCounterparts.textContent, /기준 미충족 후보/);
  assert.match(e.pipeCounterparts.textContent, /채택되지 않음/);
  assert.doesNotMatch(e.pipeCounterparts.textContent, /일치|MATCH|100%/);
});

test('NOT_COMPARED is comparison pending and omits unavailable length metrics', async () => {
  const {ui, elements: e, requests} = fixture(); ui.show(item()); const body = result();
  Object.assign(body.pipe, {comparison_status: 'NOT_COMPARED', coverage_ratio: null, length_m: null, length_scope: null, exact_same_id_geometry: false, exact_geometry_match: false});
  requests[0].resolve(body); await flush();
  assert.match(e.pipeComparisonFacts.textContent, /비교 전/);
  assert.doesNotMatch(e.pipeComparisonFacts.textContent, /아니요|값 없음|대응률/);
  assert.doesNotMatch(e.pipeComparisonEvidenceValues.textContent, /기준 길이|길이 기준|아니요/);
});

test('counterparts sharing one original ID retain distinct original object numbers', async () => {
  const {ui, elements: e, requests} = fixture(); ui.show(item('kict_national', {FTR_IDN: 904145, STD_DIP: 100, MOP_CDE: 'x'}));
  requests[0].resolve(result({counterparts: {total: 2, accepted: 2, items: [1035262, 1431480].map(source_fid => ({source_fid, original_id: '53703000029', relationship: 'sustained', accepted: true})), offset: 0, next_offset: null}})); await flush();
  assert.match(e.pipeCounterparts.children[0].textContent, /원본 객체 1035262/);
  assert.match(e.pipeCounterparts.children[1].textContent, /원본 객체 1431480/);
});
