/* Exercise the real regional files through the all-region UI. */
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = process.env.PIPE_TEST_URL || 'http://localhost:8000/app/';
(async () => {
  const browser = await chromium.launch({channel:'chrome',headless:true,args:['--enable-unsafe-swiftshader']});
  const page = await browser.newPage({viewport:{width:2550,height:1330}});
  const errors=[], files=[];
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{if(/files\/pipe_case[12]_m\.tif/.test(request.url())) files.push(request.url());});
  if(process.env.PIPE_TEST_FRONTEND) await page.route('**/app/**',async route=>{
    const name=new URL(route.request().url()).pathname.slice('/app/'.length)||'index.html';
    if(['index.html','distance.js'].includes(name)) await route.fulfill({path:path.join(process.env.PIPE_TEST_FRONTEND,name)});
    else await route.continue();
  });
  const ready=id=>page.waitForFunction(id=>window.pipeDistance?.getMeta()?.dataset_id===id&&!document.getElementById('reload').disabled&&document.getElementById('status').textContent.includes('표시 완료'),id,{timeout:120000});
  async function checkCell(utility,code,caseId) {
    const route=new URL(`/distance/national/${utility}/${code}/`,base).href;
    const m=await(await page.request.get(route+'meta')).json();
    // Locate a real valid cell without relying on a guessed grid ID.
    let sample;
    for(let row=0;row<m.height&&!sample;row+=60) for(let col=0;col<m.width&&!sample;col+=100){
      const q=new URLSearchParams({min_col:col,max_col:Math.min(m.width-1,col+99),min_row:row,max_row:Math.min(m.height-1,row+59)});
      const g=await(await page.request.get(route+'gridlines?'+q)).json(),edge=g.geometry?.coordinates?.[0];
      if(edge) sample={col:Math.floor((edge[0][0]-m.origin[0])/100),row:Math.floor((m.origin[1]-edge[0][1])/100)};
    }
    assert.ok(sample,'valid regional cell found');
    const d=await(await page.request.get(route+'cell?'+new URLSearchParams(sample))).json();
    assert.ok(d.matched);
    await page.evaluate(({m,d})=>{const v=pipeDistance.map.getView();v.setCenter(ol.proj.transform(d.center,m.crs,'EPSG:3857'));v.setZoom(15);},{m,d});
    await page.waitForFunction(()=>pipeDistance.getGrid().getSource().getFeatures().length>0);
    await page.waitForFunction(({m,d,caseId})=>{
      const pixel=pipeDistance.map.getPixelFromCoordinate(ol.proj.transform(d.center,m.crs,'EPSG:3857'));
      // OL getData picks the last bounding-box source even if its pixel is NoData.
      // Inspect this region's already-rendered texture; do not alter the rendered mosaic.
      const layer=pipeDistance.getLayer(),getSources=layer.getSources;
      layer.getSources=()=>pipeDistance.getSources().filter(s=>s.get('dataset_id')===m.dataset_id);
      try {const value=layer.getData(pixel);return value&&value[1]>0&&Math.abs(value[0]-d[caseId+'_m'])<.05;}
      finally {layer.getSources=getSources;}
    },{m,d,caseId},{timeout:30000});
    const rgba=await page.evaluate(({m,d})=>{
      const map=pipeDistance.map,pixel=map.getPixelFromCoordinate(ol.proj.transform(d.center,m.crs,'EPSG:3857'));
      const canvas=document.querySelector('#map canvas'),gl=canvas.getContext('webgl')||canvas.getContext('webgl2');
      map.renderSync();const p=new Uint8Array(4);gl.readPixels(Math.floor(pixel[0]),canvas.height-1-Math.floor(pixel[1]),1,1,gl.RGBA,gl.UNSIGNED_BYTE,p);return [...p];
    },{m,d});
    const colors=[[8,48,107],[33,113,181],[66,146,198],[158,202,225],[247,251,255]],v=d[caseId+'_m'];
    const bin=[10,50,100,500].findIndex(limit=>v<=limit),color=colors[bin<0?4:bin];
    assert.ok(rgba[3]>220,'mosaic has a visible non-NoData pixel');
    color.forEach((channel,i)=>assert.ok(Math.abs(rgba[i]/rgba[3]*255-channel)<4,JSON.stringify({rgba,color})));
    await page.evaluate(({m,d})=>pipeDistance.map.dispatchEvent({type:'singleclick',coordinate:ol.proj.transform(d.center,m.crs,'EPSG:3857')}),{m,d});
    await page.waitForFunction(()=>!document.getElementById('cellDetails').hidden);
    assert.equal(await page.locator('#gridId').innerText(),d.grid_id);
    assert.equal(await page.locator('#downloadResult').getAttribute('href'),route+'files/'+m.result_filename);
    console.log(`PASS ${utility}/${code} ${caseId}: rendered value, original grid ID and regional result download`);
  }
  try {
    await page.goto(base,{waitUntil:'domcontentloaded'});
    assert.equal(await page.locator('#case1').evaluate(e=>e.closest('details').open),false,'legacy water collapsed by default');
    assert.equal(await page.locator('#sewerCase1').evaluate(e=>e.closest('details').open),false,'legacy sewer collapsed by default');
    await page.waitForFunction(()=>!document.getElementById('nationalWaterCase1').disabled);
    assert.equal(files.length,0,'idle page does not download rasters');
    await page.locator('#nationalWaterCase1').check();
    await ready('kict_water_national_all');
    assert.equal(await page.locator('#nationalWaterRegion option').first().innerText(),'전국');
    assert.equal(await page.locator('#nationalSewerRegion option').first().innerText(),'전체(서울 외)');
    assert.equal(await page.locator('#nationalSewerRegion option[value="11"]').count(),0);
    assert.equal(await page.locator('#nationalWaterRegion').inputValue(),'all');
    assert.equal(await page.evaluate(()=>pipeDistance.getSources().length),17);
    assert.equal(new Set(files.filter(u=>u.includes('/national/water/')&&u.includes('case1')).map(u=>new URL(u).pathname)).size,17);
    if(process.env.PIPE_TEST_OUT){fs.mkdirSync(process.env.PIPE_TEST_OUT,{recursive:true});await page.screenshot({path:path.join(process.env.PIPE_TEST_OUT,'national-overview.png')});}
    console.log('PASS initial nationwide view: 17 water rasters, first option and collapsed Seoul groups');
    await checkCell('water','21','case1'); await checkCell('water','39','case1'); await checkCell('water','31','case1');
    await page.locator('#nationalWaterCase2').check();await ready('kict_water_national_all');
    await checkCell('water','21','case2');
    await page.locator('#nationalSewerRegion').selectOption('all');await ready('kict_sewer_national_all');
    assert.equal(await page.evaluate(()=>pipeDistance.getSources().length),15);
    assert.equal(await page.locator('#nationalSewerRegion option[value="36"]').evaluate(e=>e.disabled),true);
    await checkCell('sewer','39','case1');
    await page.locator('#nationalSewerCase2').check();await ready('kict_sewer_national_all');await checkCell('sewer','31','case2');
    await page.locator('#nationalWaterRegion').selectOption('39');await ready('kict_water_national_39');
    assert.equal(await page.evaluate(()=>pipeDistance.getSources().length),1);
    await checkCell('water','39','case1');
    console.log('PASS all four nationwide Cases, missing-region exclusion and return to one region');
    await page.route('**/distance/national/water/21/files/*.tif*',route=>route.fulfill({status:503,body:'test unavailable'}));
    await page.locator('#nationalWaterRegion').selectOption('all');
    await page.waitForFunction(()=>document.getElementById('status').classList.contains('error')&&!document.getElementById('reload').disabled);
    assert.equal(await page.evaluate(()=>pipeDistance.getLayer()),null,'partial download must not count as completed national view');
    assert.equal(await page.evaluate(()=>pipeDistance.getSources().length),0);
    await page.unroute('**/distance/national/water/21/files/*.tif*');
    await page.locator('#nationalWaterRegion').selectOption('39');await ready('kict_water_national_39');
    assert.deepEqual(errors,[]);
    console.log('PASS failed national download clears partial sources and recovers');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
