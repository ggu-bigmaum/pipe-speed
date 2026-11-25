/* Object comparison is separate from raw tile attributes and map highlighting. */
(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const fields = {kict_national: ['FTR_IDN', 'STD_DIP', 'MOP_CDE'], sa001: ['PLINE_ID', 'PLINE_CALB', 'PLINE_MTRQ'], kict_sewer_seoul: ['id'], sb001: ['id']};
  const sewer = item => ['kict_sewer_seoul', 'sb001'].includes(item.entry.id);
  const municipal = item => ['sa001', 'sb001'].includes(item.entry.id);
  const states = {
    MATCH: ['일치', '동일하거나 비교 기준에서 상당히 유사한 관로입니다.'],
    PARTIAL_MATCH: ['부분 일치', '비교 기준을 충족하는 일부 구간이 대응합니다.'],
    KICT_ONLY: ['건기연에만 있음', '확보한 비교 자료에서 대응 관로가 확인되지 않은 건기연 관로입니다.'],
    LOCAL_ONLY: ['지자체에만 있음', '건기연 비교 자료에서 대응 관로가 확인되지 않은 지자체 관로입니다.'],
    CONFLICT: ['확인 필요', '자료 간 차이 또는 대응 판단을 확인해야 하는 관로입니다.'],
    NOT_COMPARED: ['미비교', '상대 지자체 자료가 확보되지 않아 비교하지 않은 관로입니다.']
  };
  const relations = {exact_same_id: '동일 ID·전체 선형 일치', exact_other_id: '다른 ID·전체 선형 일치', sustained: '연속 구간 대응', candidate_rejected: '기준 미충족 후보', id_only: 'ID만 같은 후보', legacy_exact_only: '기존 비교 범위에서 일치'};
  const scopes = {original_full: '원본 전체 선형', whole_original: '원본 전체 선형', full_original: '원본 전체 선형', seoul_boundary_clip: '서울 경계로 자른 구간', legacy_clip: '기존 서울 경계 비교 구간'};
  const display = value => value == null ? '값 없음' : typeof value === 'object' ? JSON.stringify(value) : String(value);
  const yesno = value => value === true ? '예' : value === false ? '아니요' : '확인되지 않음';
  const percent = value => typeof value === 'number' && Number.isFinite(value) ? `${(value * 100).toLocaleString('ko-KR', {maximumFractionDigits: 2})}%` : '값 없음';
  function row(target, name, value) {
    const dt = document.createElement('dt'), dd = document.createElement('dd');
    dt.textContent = name; dd.textContent = display(value); target.append(dt, dd);
  }
  function create({apiBase = ''} = {}) {
    const base = apiBase ? apiBase.replace(/\/$/, '') + '/' : new URL('../', location.href).href;
    let selected = null, controller = null, revision = 0, sourceFid = null, members = [], nextOffset = null, shown = 0, retryOffset = 0;
    const valid = (token, item) => token === revision && selected === item && (item.isCurrent?.() ?? true);
    function resetResult() {
      $('pipeComparisonResult').hidden = true;
      for (const id of ['pipeComparisonStatus', 'pipeComparisonMeaning', 'pipeComparisonReason', 'pipeComparisonFacts', 'pipeComparisonEvidenceValues', 'pipeCounterpartsHeading', 'pipeCounterparts']) $(id).replaceChildren();
      $('pipeCounterpartsMore').hidden = true; nextOffset = null; shown = 0;
    }
    function clear() {
      ++revision; controller?.abort(); controller = null; selected = null; sourceFid = null; members = [];
      resetResult(); $('pipeComparison').hidden = true; $('pipeComparisonMessage').textContent = '';
      $('pipeSourcePicker').hidden = true; $('pipeSources').replaceChildren(); $('pipeSources').value = '';
      $('pipeComparisonRetry').hidden = true;
    }
    function mappingView(mapping) {
      members = Array.isArray(mapping?.members) ? mapping.members : [];
      const multiple = mapping?.ambiguous === true || mapping?.member_count > 1;
      $('pipeSourcePicker').hidden = !multiple;
      $('pipeSources').replaceChildren();
      if (multiple) {
        const placeholder = document.createElement('option'); placeholder.value = ''; placeholder.textContent = '원본 관로를 선택하세요'; $('pipeSources').append(placeholder);
        for (const member of members) {
          const option = document.createElement('option'); option.value = String(member.source_fid);
          option.textContent = [member.region, `원본 객체 ${display(member.source_fid)}`, `관로 ID ${display(member.original_id)}`].filter(value => value != null && value !== '').join(' · ');
          $('pipeSources').append(option);
        }
        $('pipeSources').value = sourceFid == null ? '' : String(sourceFid);
      }
      return multiple;
    }
    function renderPipe(pipe, item) {
      if (sewer(item)) {
        $('pipeComparisonStatus').textContent = pipe.rounded_geometry_match === true ? '형상 일치' : pipe.precision_review ? '정밀도 처리 확인 필요' : '전체 형상 일치 없음';
        $('pipeComparisonStatus').dataset.state = 'SEWER_GEOMETRY';
        $('pipeComparisonMeaning').textContent = '소수점 3자리 반올림 후 전체 형상 비교 결과입니다. 6개 비교상태와 별도입니다.';
        $('pipeComparisonReason').textContent = pipe.reason;
        const facts = $('pipeComparisonFacts'); facts.replaceChildren();
        row(facts, '비교 자료', '건기연 서울 하수 보정본 ↔ 서울시 하수');
        row(facts, '원본 관로 ID', pipe.original_id);
        row(facts, '3자리 반올림 후 형상 일치', yesno(pipe.rounded_geometry_match));
        const evidence = $('pipeComparisonEvidenceValues'); evidence.replaceChildren();
        row(evidence, '판정 기준', 'ID 조건 제외 · 소수점 3자리 반올림 · 전체 형상 비교');
        row(evidence, '원본 객체', pipe.source_fid);
        row(evidence, '원본 형상', '저장 좌표 유지 · 비교용 사본만 반올림');
        if (pipe.precision_review) row(evidence, '확인 사유', '반올림 후 선이 축약되거나 유효하지 않아 판정을 보류했습니다.');
        $('pipeComparisonResult').hidden = false;
        return;
      }
      const state = states[pipe.comparison_status];
      if (!state) throw new Error('Unknown comparison status');
      $('pipeComparisonStatus').textContent = `${state[0]} · ${pipe.comparison_status}`;
      $('pipeComparisonStatus').dataset.state = pipe.comparison_status;
      $('pipeComparisonMeaning').textContent = state[1];
      $('pipeComparisonReason').textContent = pipe.reason || '저장된 판정 근거가 없습니다.';
      const notCompared = pipe.comparison_status === 'NOT_COMPARED';
      const facts = $('pipeComparisonFacts'); facts.replaceChildren();
      row(facts, '비교 자료', item.entry.id === 'sa001' ? '서울시 상수 ↔ 건기연 상수' : '건기연 상수 ↔ 서울시 상수');
      row(facts, '원본 관로 ID', pipe.original_id);
      if (pipe.coverage_ratio != null) row(facts, '평가구간 대응률', percent(pipe.coverage_ratio));
      row(facts, 'ID·전체 선형 일치', notCompared ? '비교 전' : yesno(pipe.exact_same_id_geometry));
      const evidence = $('pipeComparisonEvidenceValues'); evidence.replaceChildren();
      row(evidence, '판정 기준', pipe.reason_code);
      if (pipe.length_scope != null) row(evidence, '길이 기준', scopes[pipe.length_scope] || pipe.length_scope);
      if (pipe.length_scope === 'seoul_boundary_clip') row(evidence, '평가 범위', '길이와 대응률은 서울 경계로 자른 비교 구간 기준입니다. 전체 선형 일치는 원본 전체 관로를 별도로 비교한 값입니다.');
      if (typeof pipe.length_m === 'number') row(evidence, '기준 길이', `${pipe.length_m.toLocaleString('ko-KR', {maximumFractionDigits: 2})} m`);
      row(evidence, '전체 선형 일치', notCompared ? '비교 전' : yesno(pipe.exact_geometry_match));
      row(evidence, '기존 결과 확인', pipe.regression_review_required === true ? '검토 필요' : pipe.regression_review_required === false ? '추가 검토 표시 없음' : '확인되지 않음');
      if (pipe.attribute_flags != null) row(evidence, '속성 비교 기록', pipe.attribute_flags);
      $('pipeComparisonResult').hidden = false;
    }
    function renderCounterparts(data, append, item) {
      const items = Array.isArray(data?.items) ? data.items : [];
      if (!append) { $('pipeCounterparts').replaceChildren(); shown = 0; }
      for (const other of items) {
        const li = document.createElement('li'), title = document.createElement('strong'), detail = document.createElement('span');
        title.textContent = `${municipal(item) ? '건기연' : '서울시'} · ${display(other.original_id)} · 원본 객체 ${display(other.source_fid)}`;
        const parts = [other.relationship === 'rounded_geometry' ? '3자리 반올림 후 전체 형상 일치' : relations[other.relationship] || display(other.relationship)];
        // A counterpart's overall status/coverage may describe its relationship
        // with a different pipe. Only this pair's evidence belongs in this list.
        if (other.accepted === false) parts.push('채택되지 않음');
        detail.textContent = parts.join(' · '); li.append(title, detail);
        if (other.reason) { const reason = document.createElement('p'); reason.textContent = other.reason; li.append(reason); }
        $('pipeCounterparts').append(li);
      }
      shown += items.length;
      const total = Number.isInteger(data?.total) ? data.total : shown;
      $('pipeCounterpartsHeading').textContent = total ? `대응·검토 관계 ${total}건 중 ${shown}건 표시${Number.isInteger(data.accepted) ? ` · 채택 ${data.accepted}건` : ''}` : '저장된 대응·검토 관계가 없습니다.';
      nextOffset = Number.isInteger(data?.next_offset) && data.next_offset > (data.offset ?? 0) ? data.next_offset : null;
      $('pipeCounterpartsMore').hidden = nextOffset == null;
    }
    async function lookup(offset = 0) {
      const item = selected;
      if (!item || !(item.isCurrent?.() ?? true)) return;
      const token = ++revision; controller?.abort(); controller = new AbortController();
      const currentController = controller, timer = setTimeout(() => currentController.abort('timeout'), 15000);
      const append = offset > 0; retryOffset = offset;
      if (!append) resetResult();
      $('pipeComparisonMessage').textContent = append ? '대응 관계를 더 불러오는 중…' : '비교 정보를 확인하는 중…';
      $('pipeComparisonRetry').hidden = true; $('pipeCounterpartsMore').disabled = true;
      const body = {layer: item.entry.id, properties: item.properties};
      if (sourceFid != null) body.source_fid = sourceFid;
      if (offset > 0) body.offset = offset;
      try {
        const response = await fetch(base + `comparison/${sewer(item) ? 'sewer' : 'water'}/lookup`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body), signal: currentController.signal});
        const data = await response.json();
        if (!valid(token, item)) return;
        if (!response.ok || data.available !== true) {
          const mappingMissing = [data.code, data.error_code, data.error].includes('mapping_not_found');
          $('pipeComparisonMessage').textContent = mappingMissing ? '현재 타일에 대응하는 원본 관로를 찾지 못했습니다.' : data.message || data.reason || '이 자료의 비교 결과가 연결되지 않았습니다.';
          $('pipeComparisonRetry').hidden = false; return;
        }
        const multiple = mappingView(data.mapping);
        if (!data.pipe) {
          $('pipeComparisonMessage').textContent = multiple ? `같은 타일 속성에 원본 관로 ${data.mapping.member_count}개가 연결됩니다. 원본을 선택해 비교 결과를 확인하세요.${members.length < data.mapping.member_count ? ` 현재 후보 ${members.length}개를 표시합니다.` : ''}` : '원본 관로의 비교 결과를 확인할 수 없습니다.';
          return;
        }
        // An ambiguous response must never silently choose its first original.
        if (multiple && sourceFid == null) { $('pipeComparisonMessage').textContent = '원본 관로를 선택해 비교 결과를 확인하세요.'; return; }
        if (!members.some(member => String(member.source_fid) === String(data.pipe.source_fid))) throw new Error('Original is outside mapping');
        if (sourceFid != null && String(data.pipe.source_fid) !== String(sourceFid)) throw new Error('Original identity changed');
        sourceFid = data.pipe.source_fid;
        renderPipe(data.pipe, item); renderCounterparts(data.counterparts, append, item);
        $('pipeComparisonMessage').textContent = data.pipe.regression_review_required === true ? '기존 비교 결과와의 차이는 추가 검토가 필요합니다.' : '';
      } catch (_) {
        if (!valid(token, item)) return;
        $('pipeComparisonMessage').textContent = '비교 정보를 불러오지 못했습니다. 다시 확인해 주세요.';
        $('pipeComparisonRetry').hidden = false;
      } finally {
        clearTimeout(timer);
        if (valid(token, item)) $('pipeCounterpartsMore').disabled = false;
      }
    }
    function show(item) {
      clear(); $('pipeComparison').hidden = false;
      if (!fields[item.entry.id]) { $('pipeComparisonMessage').textContent = '이 자료의 비교 결과는 아직 연결되지 않았습니다.'; return; }
      const properties = item.feature.getProperties();
      selected = {...item, properties: Object.fromEntries(fields[item.entry.id].map(key => [key, properties[key] ?? null]))};
      lookup();
    }
    $('pipeSources').onchange = () => {
      const member = members.find(value => String(value.source_fid) === $('pipeSources').value);
      if (!member) { ++revision; controller?.abort(); sourceFid = null; resetResult(); $('pipeComparisonMessage').textContent = '원본 관로를 선택해 비교 결과를 확인하세요.'; return; }
      sourceFid = member.source_fid; lookup();
    };
    $('pipeCounterpartsMore').onclick = () => { if (nextOffset != null && !$('pipeCounterpartsMore').disabled) lookup(nextOffset); };
    $('pipeComparisonRetry').onclick = () => lookup(retryOffset);
    clear();
    return {show, clear};
  }
  window.PipeComparison = {create};
})();
