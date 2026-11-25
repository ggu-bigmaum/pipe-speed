/* Loading must never trap the layer selector, even if a TIF request stalls. */
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const base = process.env.PIPE_TEST_URL || 'http://localhost:8000/app/';
(async () => {
  const browser = await chromium.launch({channel:'chrome', headless:true, args:['--enable-unsafe-swiftshader']});
  const page = await browser.newPage({viewport:{width:2550,height:1330}});
  const errors=[], files=[];
  page.on('pageerror', e=>errors.push(e.message));
  page.on('request', r=>{if(/files\/pipe_case[12]_m\.tif/.test(r.url()))files.push(r.url());});
  if(process.env.PIPE_TEST_FRONTEND) await page.route('**/app/**', async route=>{
    const name=new URL(route.request().url()).pathname.slice('/app/'.length)||'index.html';
    if(['index.html','distance.js'].includes(name)) await route.fulfill({path:path.join(process.env.PIPE_TEST_FRONTEND,name)});
    else await route.continue();
  });
  try {
    await page.goto(base,{waitUntil:'domcontentloaded'});
    await page.waitForFunction(()=>document.querySelector('#nationalWaterRegion option[value="all"]'));
    await page.waitForTimeout(1000);
    assert.equal(files.length,0,'Opening the map must not automatically fetch all regional TIFs');
    assert.equal(await page.locator('#nationalWaterRegion').inputValue(),'all');
    assert.equal(await page.locator('input[data-raster]:checked').count(),0);
    console.log('PASS nationwide default without automatic raster download');
    await page.route('**/distance/national/water/*/files/*.tif*', ()=>new Promise(()=>{}));
    await page.locator('#nationalWaterCase1').check();
    await page.waitForFunction(()=>document.getElementById('status').textContent.includes('다운로드'));
    assert.ok(await page.locator('#nationalWaterCase1').isEnabled(),'Loading checkbox must remain cancellable');
    assert.ok(await page.locator('#nationalWaterRegion').isEnabled(),'Region selector must remain usable');
    assert.ok(await page.locator('#stop').isVisible(),'Cancel must not be hidden in advanced settings');
    await page.locator('#nationalWaterCase1').uncheck();
    await page.waitForFunction(()=>!document.getElementById('nationalWaterCase1').checked&&document.getElementById('stop').disabled,{},{timeout:5000});
    assert.equal(await page.evaluate(()=>pipeDistance.getLayer()),null);
    assert.equal(await page.evaluate(()=>pipeDistance.getSources().length),0);
    console.log('PASS uncheck aborts stalled request and releases loading state');
    await page.unroute('**/distance/national/water/*/files/*.tif*');
    await page.locator('#nationalWaterRegion').selectOption('39');
    await page.waitForFunction(()=>pipeDistance.getMeta()?.dataset_id==='kict_water_national_39'&&!document.getElementById('reload').disabled&&document.getElementById('status').textContent.includes('표시 완료'),{},{timeout:60000});
    assert.equal(await page.evaluate(()=>pipeDistance.getSources().length),1);
    console.log('PASS selecting another region recovers and displays original raster');
    // A stalled decoder is not fetch: cancelling it must release the queue too.
    await page.evaluate(()=>{
      window.realGetView=ol.source.GeoTIFF.prototype.getView;
      ol.source.GeoTIFF.prototype.getView=function(){
        return new Promise(resolve=>{window.releaseOldDecode=()=>realGetView.call(this).then(resolve);});
      };
    });
    await page.locator('#reload').click();
    await page.waitForFunction(()=>window.releaseOldDecode&&document.getElementById('status').textContent.includes('파일 해석'));
    await page.evaluate(()=>{ol.source.GeoTIFF.prototype.getView=realGetView;});
    await page.locator('#nationalWaterRegion').selectOption('21');
    await page.waitForFunction(()=>pipeDistance.getMeta()?.dataset_id==='kict_water_national_21'&&!document.getElementById('reload').disabled&&document.getElementById('status').textContent.includes('표시 완료'),{},{timeout:15000});
    await page.evaluate(()=>releaseOldDecode());
    await page.waitForTimeout(100);
    assert.equal(await page.evaluate(()=>pipeDistance.getMeta().dataset_id),'kict_water_national_21');
    assert.deepEqual(await page.evaluate(()=>pipeDistance.getSources().map(s=>s.get('dataset_id'))),['kict_water_national_21']);
    console.log('PASS region switch cancels stalled decode; late completion cannot replace current layer');
    await page.route('**/distance/national/water/21/files/*.tif*',()=>new Promise(()=>{}));
    await page.locator('#reload').click();
    await page.locator('#stop').click();
    await page.waitForFunction(()=>document.getElementById('stop').disabled);
    assert.equal(await page.locator('input[data-raster]:checked').count(),0);
    console.log('PASS visible stop button clears selection and loading state');
    await page.unroute('**/distance/national/water/21/files/*.tif*');
    // Accelerate only the exact deadline, so a hanging request fails promptly in CI.
    await page.evaluate(()=>{window.realSetTimeout=window.setTimeout;window.setTimeout=(fn,ms,...args)=>realSetTimeout(fn,ms===30000?200:ms,...args);});
    await page.route('**/distance/national/water/39/files/*.tif*',()=>new Promise(()=>{}));
    await page.locator('#nationalWaterRegion').selectOption('39');
    await page.waitForFunction(()=>document.getElementById('status').classList.contains('error')&&!document.getElementById('reload').disabled,{},{timeout:5000});
    assert.match(await page.locator('#status').innerText(),/30초/);
    assert.equal(await page.evaluate(()=>pipeDistance.getLayer()),null);
    console.log('PASS hung file deadline surfaces an error and releases controls');
    assert.deepEqual(errors,[]);
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
