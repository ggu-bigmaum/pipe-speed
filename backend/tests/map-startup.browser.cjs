/* A failed local-data API must not prevent background/vector map startup. */
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const base = process.env.PIPE_TEST_URL || 'http://localhost:8012/app/';
(async () => {
  const browser = await chromium.launch({channel:'chrome',headless:true,args:['--enable-unsafe-swiftshader']});
  try {
    for (const status of [404, 503]) {
      const page = await browser.newPage({viewport:{width:1440,height:900}});
      const errors=[]; page.on('pageerror', e=>errors.push(e.message));
      await page.route('**/distance/meta', route=>route.fulfill({status,contentType:'application/json',body:JSON.stringify({error:`test data error ${status}`})}));
      await page.goto(base,{waitUntil:'domcontentloaded'});
      await page.waitForFunction(()=>window.pipeDistance?.map,null,{timeout:5000});
      await page.waitForFunction(()=>document.getElementById('baseLabel').textContent!=='배경지도 준비 중',null,{timeout:20000});
      assert.equal(await page.locator('#map .ol-viewport').count(),1);
      await page.waitForFunction(()=>document.querySelector('#base canvas') || [...document.querySelectorAll('#base img')].some(img=>img.complete && img.naturalWidth>0),null,{timeout:20000});
      assert.equal(await page.locator('#case1').isEnabled(),true);
      assert.equal(await page.locator('#run').isEnabled(),false);
      assert.ok(await page.locator('#vectorList input').count());
      await page.locator('#fit').click();
      if (status===503) {
        await page.unroute('**/distance/meta');
        await page.locator('#case1').check();
        await page.waitForFunction(()=>!document.getElementById('reload').disabled);
        assert.equal(await page.evaluate(()=>pipeDistance.getMeta().dataset_id),'kict_water_seoul');
      }
      await page.locator('#sewerCase1').check();
      await page.waitForFunction(()=>!document.getElementById('reload').disabled);
      assert.equal(await page.evaluate(()=>pipeDistance.getMeta().dataset_id),'kict_sewer_seoul');
      await page.unroute('**/distance/meta');
      await page.locator('#case1').check();
      await page.waitForFunction(()=>!document.getElementById('reload').disabled);
      assert.equal(await page.evaluate(()=>pipeDistance.getMeta().dataset_id),'kict_water_seoul');
      assert.deepEqual(errors,[]);
      console.log(`PASS initial ${status}: background, vector controls, fit, alternate dataset and retry`);
      await page.close();
    }
  } finally {await browser.close();}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
