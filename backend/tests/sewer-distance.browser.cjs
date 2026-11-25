/* Real local files: dataset switching, lookup, delayed response isolation and speed identity. */
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const base = process.env.PIPE_TEST_URL || 'http://localhost:8012/app/';
const out = process.env.PIPE_TEST_OUT;
(async () => {
  const browser = await chromium.launch({channel:'chrome',headless:true,args:['--enable-unsafe-swiftshader']});
  const page = await browser.newPage({viewport:{width:1440,height:900},acceptDownloads:true});
  const errors=[],files=[],checks=[];
  page.on('pageerror', e => errors.push(e.message));
  page.on('request', r => {if (/files\/pipe_case[12]_m\.tif/.test(r.url())) files.push(r.url());});
  const ready=()=>page.waitForFunction(()=>window.pipeDistance && !document.getElementById('reload').disabled,null,{timeout:45000});
  const note=s=>{checks.push(s);console.log('PASS '+s);};
  async function choose(id,dataset){
    await page.locator('#'+id).check();await ready();
    assert.equal(await page.evaluate(()=>pipeDistance.getMeta().dataset_id),dataset);
    assert.equal(await page.locator('input[data-raster]:checked').count(),1);
  }
  async function clickKnown(){
    await page.evaluate(()=>{const d=pipeDistance,m=d.getMeta(),view=d.map.getView();view.setZoom(14);view.setCenter(ol.proj.transform([302650,4158850],m.crs,'EPSG:3857'));});
    await page.waitForTimeout(400);
    const box=await page.locator('#map').boundingBox();await page.mouse.click(box.x+box.width/2,box.y+box.height/2);
    await page.waitForFunction(()=>!document.getElementById('cellDetails').hidden&&document.getElementById('gridId').textContent==='14914652');
  }
  try {
    await page.goto(base+'?dataset=kict_sewer_seoul',{waitUntil:'domcontentloaded'});await ready();
    await page.locator('#legacyWaterGroup > summary').click();
    await page.locator('#legacySewerGroup > summary').click();
    assert.equal(new URL(page.url()).pathname, new URL(base).pathname, 'main page stays at its entry URL');
    assert.equal(await page.locator('#sewerCase1').count(),1,'sewer Case1 selector exists');
    assert.equal(await page.evaluate(()=>pipeDistance.getMeta().dataset_id),'kict_sewer_seoul');
    assert.match(await page.locator('#legendCase').innerText(),/건기연 하수.*Case 1/);
    assert.ok(files.at(-1).includes('/distance/kict_sewer_seoul/files/'));note('sewer initial layer and named legend');
    await clickKnown();
    const expected=await(await page.request.get(new URL('/distance/kict_sewer_seoul/cell?col=0&row=156',base).href)).json();
    assert.equal(await page.locator('#pipeId').innerText(),expected.pipe_id);
    assert.match(await page.locator('#detailTitle').innerText(),/하수/);
    assert.ok((await page.locator('#downloadResult').getAttribute('href')).endsWith('/distance/kict_sewer_seoul/files/pipe_dist_result.csv'));note('sewer click and CSV belong to selected data');
    await choose('sewerCase2','kict_sewer_seoul');assert.match(await page.locator('#legendCase').innerText(),/Case 2/);note('sewer Case2 renders');
    await choose('case1','kict_water_seoul');await clickKnown();assert.equal(await page.locator('#pipeId').innerText(),'5396300000');
    assert.ok((await page.locator('#downloadResult').getAttribute('href')).endsWith('/distance/files/pipe_dist_result.xlsx'));note('water selection restores water lookup and XLSX');
    await choose('case2','kict_water_seoul');note('water Case2 regression');
    await page.route('**/distance/kict_sewer_seoul/meta', route => route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'검사용 자료 연결 오류'})}));
    await page.locator('#sewerCase1').click();
    await page.waitForFunction(()=>document.getElementById('status').textContent.includes('검사용 자료 연결 오류')&&!document.getElementById('sewerCase1').disabled);
    assert.equal(await page.locator('input[data-raster]:checked').count(),0);
    assert.equal(await page.locator('#detailPanel').isVisible(),false);
    await page.unroute('**/distance/kict_sewer_seoul/meta');
    await choose('sewerCase1','kict_sewer_seoul');
    note('dataset error clears old results and selection can recover');
    // A slow water grid request must not replace the newly selected sewer grid.
    let release, pending; const arrived=new Promise(resolve=>pending=resolve); const delayed=new Promise(resolve=>release=resolve);
    await page.route('**/distance/gridlines',async route=>{pending();await delayed;await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({crs:'EPSG:32652',pixel_size:[100,100],geometry:{type:'MultiLineString',coordinates:[[[0,0],[100,0]]]}})});});
    await choose('case1','kict_water_seoul');await arrived;await choose('sewerCase1','kict_sewer_seoul');
    await page.waitForFunction(()=>pipeDistance.getGrid().getSource().getFeatures().length>0);
    const before=await page.evaluate(()=>pipeDistance.getGrid().getSource().getExtent());release();await page.waitForTimeout(300);
    assert.deepEqual(await page.evaluate(()=>pipeDistance.getGrid().getSource().getExtent()),before);await page.unroute('**/distance/gridlines');note('late previous grid response ignored');
    await page.locator('.advanced summary').click();await page.locator('#repeat').fill('1');await page.locator('#run').click();
    await page.waitForFunction(()=>document.getElementById('runState').textContent==='완료 · 2/2건 측정',null,{timeout:60000});
    let records=await page.evaluate(()=>pipeDistance.getRecords());let last=records.filter(r=>r.type==='원본 읽기').slice(-2);
    assert.deepEqual(last.map(r=>r.dataset_id),['kict_sewer_seoul','kict_sewer_seoul']);assert.deepEqual(last.map(r=>r.case),['case1','case2']);
    assert.ok(records.some(r=>r.dataset_id==='kict_water_seoul'));note('measurements retain dataset and Case identity');
    await choose('sewerCase1','kict_sewer_seoul');await clickKnown();
    if(out){await page.screenshot({path:out+'/sewer-local-desktop.png'});}
    const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#csv').click()]);
    if(out){const csvPath=out+'/measurements.csv';await download.saveAs(csvPath);const csv=fs.readFileSync(csvPath,'utf8');assert.ok(csv.includes('dataset_id')&&csv.includes('kict_sewer_seoul')&&csv.includes('kict_water_seoul'));}note('CSV separates water and sewer measurements');
    await page.setViewportSize({width:390,height:844});await page.locator('#closeDetails').click();await page.locator('#toggleLayers').click();
    await page.waitForFunction(()=>Math.abs(document.getElementById('layerSidebar').getBoundingClientRect().left)<1);
    assert.equal(await page.locator('#sewerCase1').isVisible(),true);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    if(out)await page.screenshot({path:out+'/sewer-local-mobile.png'});note('mobile dataset selection fits');
    assert.deepEqual(errors,[]);
    if(out)fs.writeFileSync(out+'/browser_checks.json',JSON.stringify({passed:true,checks,errors,files},null,2));
  } finally {await browser.close();}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
