/* Requires the delivered sa001 z10..17 files and the local test server. */
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const base = process.env.PIPE_TEST_URL || 'http://localhost:8012/app/';
const output = process.env.PIPE_TEST_OUT;
(async () => {
  const browser = await chromium.launch({channel:'chrome', headless:true, args:['--enable-unsafe-swiftshader']});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:900}});
    const errors = [], requests = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('response', response => {
      if (/\/tiles\/sa001\/\d+\//.test(response.url())) requests.push({path:new URL(response.url()).pathname,status:response.status()});
    });
    await page.goto(base, {waitUntil:'domcontentloaded'});
    await page.waitForFunction(() => window.pipeDistance?.getVectors().find(e => e.id === 'sa001')?.source);
    if (await page.locator('#case1').isChecked()) await page.locator('#case1').uncheck();
    await page.evaluate(() => {
      window.seoulTest = {features:0,properties:null};
      pipeDistance.getVectors().find(e => e.id === 'sa001').source.on('tileloadend', event => {
        const features = event.tile.getFeatures();
        seoulTest.features += features.length;
        if (features.length) seoulTest.properties = features[0].getProperties();
      });
      const view = pipeDistance.vectorMap.getView();
      view.setCenter(ol.proj.fromLonLat([126.98,37.56])); view.setZoom(11);
    });
    await page.locator('#vector-sa001').check();
    await page.waitForFunction(() => document.getElementById('vectorState').textContent === '현재 화면 표시 완료', null, {timeout:90000});
    const coverage = await page.evaluate(() => {
      const map = pipeDistance.vectorMap, frame = map.getTargetElement().getBoundingClientRect();
      return [[126.85,37.53],[127.03,37.50],[127.02,37.65]].map(coordinate => {
        const point = map.getPixelFromCoordinate(ol.proj.fromLonLat(coordinate));
        let orange = 0;
        for (const canvas of map.getTargetElement().querySelectorAll('canvas')) {
          const rect = canvas.getBoundingClientRect();
          if (!rect.width || !rect.height) continue;
          const x = Math.round((frame.left + point[0] - rect.left) * canvas.width / rect.width);
          const y = Math.round((frame.top + point[1] - rect.top) * canvas.height / rect.height);
          const pixels = canvas.getContext('2d').getImageData(x-20,y-20,40,40).data;
          for (let i=0;i<pixels.length;i+=4) if (pixels[i]>180 && pixels[i+1]>60 && pixels[i+1]<170 && pixels[i+2]<80 && pixels[i+3]>0) orange++;
        }
        return {coordinate,orange};
      });
    });
    console.log('City visible pixels:', JSON.stringify(coverage));
    assert.ok(coverage.every(area => area.orange > 20), 'A decoded tile must actually draw: western, southeastern and northeastern Seoul');
    assert.ok(await page.evaluate(() => seoulTest.features > 1400000));
    assert.ok(await page.evaluate(() => Object.hasOwn(seoulTest.properties,'PLINE_ID')));
    if (output) { fs.mkdirSync(output,{recursive:true}); await page.screenshot({path:output+'/seoul-vector-city.png'}); }
    await page.evaluate(() => pipeDistance.vectorMap.getView().setZoom(9));
    await page.waitForFunction(() => document.getElementById('vectorState').textContent === '현재 화면 표시 완료',null,{timeout:90000});
    assert.ok(!requests.some(request => /\/sa001\/(7|8|9)\//.test(request.path)));
    await page.evaluate(() => pipeDistance.vectorMap.getView().setZoom(17));
    await page.waitForFunction(() => document.getElementById('vectorState').textContent === '현재 화면 표시 완료',null,{timeout:60000});
    if (output) await page.screenshot({path:output+'/seoul-vector-detail.png'});
    await page.locator('#vector-sa001').uncheck();
    assert.equal(await page.evaluate(() => pipeDistance.getVectors().find(e => e.id === 'sa001').layer.getVisible()),false);
    await page.locator('#vector-kict_national').check();
    await page.waitForFunction(() => document.getElementById('vectorState').textContent === '현재 화면 표시 완료',null,{timeout:60000});
    await page.locator('#vector-kict_national').uncheck();
    await page.locator('#sewerCase2').check();
    await page.waitForFunction(() => pipeDistance.getMeta()?.dataset_id === 'kict_sewer_seoul' && !document.getElementById('reload').disabled);
    assert.deepEqual(errors,[]);
    console.log('PASS: Seoul geometry/IDs, dense tile pixels, zoom, existing national vector and sewer TIFF.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error.stack); process.exitCode=1; });
