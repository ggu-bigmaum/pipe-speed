/* Read-only picking of visible pipe features. No new data fetch or source edits. */
(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const labels = {
    FTR_IDN:'시설물 번호', ORG_IDN:'원본 관로 ID', org_idn:'원본 관로 ID',
    PLINE_ID:'관로 ID', source_pipe_id:'원본 관로 ID', original_pipe_id:'원본 관로 번호',
    STD_DIP:'관경', std_dip:'관경', PLINE_CALB:'관경',
    MOP_CDE:'재질 코드', PLINE_MTRQ:'재질 코드', FTR_CDE:'시설물 코드',
    SAA_CDE:'관종 코드', TY_JL_CDE:'관로 유형 코드',
    HOL_STD:'가로 규격', VEL_STD:'세로 규격', std_hol:'가로 규격', std_vel:'세로 규격',
    SAT_MHE:'시작 맨홀', END_MHE:'끝 맨홀', feature_role:'관로 구분',
    correction_stage:'보정 단계', roadtypes_stage:'도로 유형 단계', treatment_code:'처리구역 코드'
  };
  const display = value => value == null ? '값 없음' : typeof value === 'object' ? JSON.stringify(value) : String(value);
  function copyGeometry(feature) {
    const geometry=feature.getGeometry();
    if(typeof geometry.getCoordinates==='function') return geometry.clone();
    // The bundled global OL build does not export module-level toGeometry().
    // Copy only this picked RenderFeature, preserving its coordinates and parts.
    const flat=feature.getFlatCoordinates(),stride=feature.getStride(),layout={2:'XY',3:'XYZ',4:'XYZM'}[stride];
    if(!layout) return null;
    const coordinates=(start,end)=>{const result=[];for(let i=start;i<end;i+=stride) result.push(flat.slice(i,i+stride));return result;};
    if(geometry.getType()==='LineString') return new ol.geom.LineString(coordinates(0,flat.length),layout);
    if(geometry.getType()==='MultiLineString') {
      let start=0;
      const lines=feature.getEnds().map(end=>{const line=coordinates(start,end);start=end;return line;});
      return new ol.geom.MultiLineString(lines,layout);
    }
    return null;
  }
  function create({map, getEntries, onSelect}) {
    let candidates = [], selected = null;
    const source = new ol.source.Vector();
    const highlight = new ol.layer.Vector({source, zIndex:200, style:[
      new ol.style.Style({stroke:new ol.style.Stroke({color:'#fff',width:8})}),
      new ol.style.Style({stroke:new ol.style.Stroke({color:'#e22b57',width:4})})
    ]});
    map.addLayer(highlight);
    function clear() {
      candidates=[]; selected=null; source.clear();
      $('pipeDetails').hidden=true; $('pipeCandidates').replaceChildren(); $('pipeAttributes').replaceChildren();
      if ($('detailPanel').dataset.mode === 'pipe') {
        $('detailPanel').hidden=true; delete $('detailPanel').dataset.mode;
      }
    }
    function available(item) {
      return item.entry.checkbox.checked && item.layer.getVisible() && map.getLayers().getArray().includes(item.layer);
    }
    function properties(feature) {
      const geometryName=feature.getGeometryName?.() || 'geometry';
      return Object.entries(feature.getProperties()).filter(([key,value])=>key!==geometryName && key!=='layer' && !(value && typeof value.getType==='function'));
    }
    function identifier(feature) {
      const p=feature.getProperties();
      for(const key of ['ORG_IDN','org_idn','PLINE_ID','source_pipe_id','original_pipe_id','FTR_IDN','IDN','link_id']) {
        if(p[key] != null && p[key] !== '') return `${key}: ${display(p[key])}`;
      }
      return feature.getId()!=null ? `타일 객체: ${feature.getId()}` : '번호 미제공';
    }
    function choose(index) {
      const item=candidates[index];
      if(!item || !available(item)) { clear(); return; }
      const geometry=copyGeometry(item.feature);
      if(!geometry) { clear(); return; }
      selected=item; source.clear(); source.addFeature(new ol.Feature({geometry}));
      $('detailPanel').dataset.mode='pipe'; $('detailPanel').hidden=false;
      $('detailTitle').textContent='선택한 관로'; $('closeDetails').setAttribute('aria-label','관로 정보 닫기');
      $('cellDetails').hidden=true; $('gridDetailsExtra').hidden=true; $('pipeDetails').hidden=false;
      $('pickMessage').textContent=item.entry.label + (item.layer===item.entry.overlay ? ' · 추가 연결' : '');
      $('pipeCandidates').value=String(index); $('pipeCandidatePicker').hidden=candidates.length<2;
      $('pipeAttributes').replaceChildren();
      const rows=properties(item.feature);
      if(item.feature.getId()!=null) rows.unshift(['타일 객체 번호',item.feature.getId()]);
      for(const [key,value] of rows) {
        const dt=document.createElement('dt'),dd=document.createElement('dd');
        dt.textContent=labels[key] ? `${labels[key]} (${key})` : key;
        dd.textContent=display(value); $('pipeAttributes').append(dt,dd);
      }
      if(!rows.length) {
        const text=document.createElement('p'); text.textContent='제공된 속성이 없습니다.'; $('pipeAttributes').append(text);
      }
    }
    function pick(coordinate) {
      const entries=getEntries().filter(e=>e.checkbox.checked && e.layer);
      const layers=new Map();
      for(const entry of entries) for(const layer of [entry.layer,entry.overlay]) if(layer?.getVisible()) layers.set(layer,entry);
      const hits=[], seen=new Set();
      if(layers.size) map.forEachFeatureAtPixel(map.getPixelFromCoordinate(coordinate),(feature,layer)=>{
        const type=feature.getGeometry()?.getType();
        if(!layers.has(layer) || !['LineString','MultiLineString'].includes(type) || seen.has(feature)) return;
        seen.add(feature); hits.push({feature,layer,entry:layers.get(layer)});
      },{layerFilter:layer=>layers.has(layer),hitTolerance:6,checkWrapped:false});
      clear();
      if(!hits.length) return false;
      onSelect(); candidates=hits;
      hits.forEach((item,index)=>{
        const option=document.createElement('option'); option.value=String(index);
        option.textContent=`${index+1}. ${item.entry.label} · ${identifier(item.feature)}`;
        $('pipeCandidates').append(option);
      });
      choose(0); return true;
    }
    function syncVisibility() {
      // Clear the old candidate list when any contributing layer is switched off.
      if(candidates.some(item=>!available(item))) clear();
    }
    $('pipeCandidates').onchange=()=>choose(Number($('pipeCandidates').value));
    map.getView().on('change:resolution',clear);
    return {pick,clear,syncVisibility,getSelection:()=>selected ? {
      dataset:selected.entry.id,featureId:selected.feature.getId()??null,highlightCount:source.getFeatures().length
    } : null};
  }
  window.PipeVectorSelection={create};
})();
