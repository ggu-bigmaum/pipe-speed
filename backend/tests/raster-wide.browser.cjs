const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict'),path=require('node:path');
(async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true,args:['--enable-unsafe-swiftshader']});
  const page=await browser.newPage({viewport:{width:2550,height:1330}});
  if(process.env.PIPE_TEST_FRONTEND)await page.route('**/app/distance.js',r=>r.fulfill({path:path.join(process.env.PIPE_TEST_FRONTEND,'distance.js')}));
  try{
    await page.goto((process.env.PIPE_TEST_URL||'http://localhost:8000/app/')+'?dataset=kict_water_national_all',{waitUntil:'domcontentloaded'});
    await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('표시 완료'),{},{timeout:15000});
    assert.equal(await page.evaluate(()=>pipeDistance.getSources().length),17);
    const result=await page.evaluate(()=>pipeDistance.getRecords().find(r=>r.type==='원본 읽기'));
    assert.equal(result.status,'완료');
    console.log('PASS 2550×1330 nationwide raster completes:',JSON.stringify(result));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
