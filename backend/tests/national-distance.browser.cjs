/* Real local national files: selection, viewport grids, source identity and MVT decoding. */
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const base = process.env.PIPE_TEST_URL || 'http://localhost:8012/app/';
const output = process.env.PIPE_TEST_OUT;
(async () => {
  const browser = await chromium.launch({channel:'chrome',headless:true,args:['--enable-unsafe-swiftshader']});
  const page = await browser.newPage({viewport:{width:1440,height:900}});
  const errors=[], grids=[], files=[], checks=[];
  page.on('pageerror', error=>errors.push(error.message));
  page.on('request', request=>{
    if (/\/distance\/national\/.*\/gridlines/.test(request.url())) grids.push(request.url());
    if (/files\/pipe_case[12]_m\.tif/.test(request.url())) files.push(request.url());
  });
  const note=text=>{checks.push(text);console.log('PASS '+text);};
  const ready=dataset=>page.waitForFunction(id=>window.pipeDistance?.getMeta()?.dataset_id===id&&!document.getElementById('reload').disabled,dataset,{timeout:60000});
  async function zoomToCell() {
    // Use the API's real valid grid edges to locate a cell, including sparse islands.
    const sample=await page.evaluate(async()=>{
      const m=pipeDistance.getMeta(), route=document.getElementById('downloadResult').href.split('/files/')[0];
      for(let row=0;row<m.height;row+=50) for(let col=0;col<m.width;col+=100) {
        const q=new URLSearchParams({min_col:col,max_col:Math.min(m.width-1,col+99),min_row:row,max_row:Math.min(m.height-1,row+49)});
        const data=await(await fetch(route+'/gridlines?'+q)).json();
        const edge=data.geometry?.coordinates?.[0];
        if(edge){const a=edge[0];return [Math.floor((a[0]-m.origin[0])/m.pixel_size[0]),Math.floor((m.origin[1]-a[1])/m.pixel_size[1])];}
      }
      throw new Error('No valid cells found');
    });
    await page.evaluate(([col,row])=>{
      const m=pipeDistance.getMeta(),view=pipeDistance.map.getView();
      view.setCenter(ol.proj.transform([m.origin[0]+(col+.5)*m.pixel_size[0],m.origin[1]-(row+.5)*m.pixel_size[1]],m.crs,'EPSG:3857'));view.setZoom(15);
    },sample);
    await page.waitForFunction(()=>pipeDistance.getGrid().getSource().getFeatures().length>0);
    return sample;
  }
  async function rasterValue(cell, caseId) {
    const result=await page.evaluate(async({cell,caseId})=>{
      const m=pipeDistance.getMeta(),route=document.getElementById('downloadResult').href.split('/files/')[0];
      const data=await(await fetch(route+'/cell?'+new URLSearchParams({col:cell[0],row:cell[1]}))).json();
      const pixel=pipeDistance.map.getPixelFromCoordinate(ol.proj.transform([m.origin[0]+(cell[0]+.5)*m.pixel_size[0],m.origin[1]-(cell[1]+.5)*m.pixel_size[1]],m.crs,'EPSG:3857'));
      const shown=pipeDistance.getLayer().getData(pixel);
      return {matched:data.matched,expected:data[caseId+'_m'],actual:shown?Array.from(shown):null};
    },{cell,caseId});
    assert.ok(result.matched&&result.actual&&result.actual[1]>0,'actual rendered national TIFF pixel is present');
    assert.ok(Math.abs(result.actual[0]-result.expected)<.05,JSON.stringify(result));
  }
  try {
    await page.goto(base+'?dataset=kict_water_national_39',{waitUntil:'domcontentloaded'});
    await page.waitForFunction(()=>window.pipeDistance&&!document.getElementById('reload').disabled,null,{timeout:60000});
    assert.equal(await page.evaluate(()=>pipeDistance.getMeta().dataset_id),'kict_water_national_39','national query ID selects the actual region');
    await ready('kict_water_national_39');
    assert.equal(await page.locator('#nationalWaterRegion').inputValue(),'39');
    assert.equal(await page.locator('#nationalWaterRegion option').count(),18);
    assert.equal(await page.locator('#nationalSewerRegion option[value="36"]').evaluate(option=>option.disabled),true);
    assert.equal(await page.locator('input[data-raster]:checked').count(),1);
    assert.equal(await page.locator('#nationalWaterGroup').evaluate(group=>group.open),true);
    assert.ok(await page.evaluate(()=>pipeDistance.map.getView().getMinZoom()<=6),'national view can zoom out to Korea');
    assert.match(await page.locator('#legendCase').innerText(),/제주.*Case 1/);
    assert.ok(files.at(-1).includes('/national/water/39/files/pipe_case1_m.tif'));
    note('national query, 17-region catalog, missing Jeonnam and original TIFF identity');
    const sample=await zoomToCell();
    const viewportRequests=grids.filter(url=>new URL(url).searchParams.has('min_col'));
    assert.ok(viewportRequests.length);
    for(const url of viewportRequests){const p=new URL(url).searchParams;assert.ok((+p.get('max_col')-+p.get('min_col')+1)*(+p.get('max_row')-+p.get('min_row')+1)<=12000);}
    const box=await page.locator('#map').boundingBox();await page.mouse.click(box.x+box.width/2,box.y+box.height/2);
    await page.waitForFunction(()=>!document.getElementById('cellDetails').hidden);
    const cell=await(await page.request.get(new URL('/distance/national/water/39/cell?col='+sample[0]+'&row='+sample[1],base).href)).json();
    assert.equal(await page.locator('#gridId').innerText(),cell.grid_id);
    assert.equal(await page.locator('#pipeId').innerText(),cell.pipe_id??'없음');
    await rasterValue(sample,'case1');
    note('viewport-bounded valid grid and regional original grid ID lookup');
    // A reply for an old viewport must never overwrite the current grid.
    let arrive,release,first=true;
    const arrived=new Promise(resolve=>arrive=resolve), delayed=new Promise(resolve=>release=resolve);
    const gridPattern='**/distance/national/water/39/gridlines?*';
    await page.route(gridPattern,async route=>{
      if(!first){await route.continue();return;} first=false;arrive();await delayed;
      await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({crs:'EPSG:32652',pixel_size:[100,100],geometry:{type:'MultiLineString',coordinates:[[[0,0],[100,0]]]}})});
    });
    const pan=()=>page.evaluate(()=>{const v=pipeDistance.map.getView(),c=v.getCenter();v.setCenter([c[0]+150,c[1]+150]);});
    await pan();await arrived;await pan();
    await page.waitForFunction(()=>pipeDistance.getGrid().getSource().getFeatures().length>0);
    const gridBefore=await page.evaluate(()=>pipeDistance.getGrid().getSource().getExtent());
    release();await page.waitForTimeout(250);
    assert.deepEqual(await page.evaluate(()=>pipeDistance.getGrid().getSource().getExtent()),gridBefore);
    await page.unroute(gridPattern);
    await page.route(gridPattern,route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({crs:'EPSG:32652',pixel_size:[100,100],omitted:true,reason:'격자선을 보려면 지도를 확대해 주세요.',geometry:{type:'MultiLineString',coordinates:[]}})}));
    await pan();await page.waitForFunction(()=>document.getElementById('gridHint').textContent.includes('확대'));
    assert.equal(await page.evaluate(()=>pipeDistance.getGrid().getSource().getFeatures().length),0);
    await page.unroute(gridPattern);
    const gridCount=grids.length;
    await page.evaluate(()=>pipeDistance.map.getView().setZoom(9));await page.waitForTimeout(500);
    assert.equal(grids.length,gridCount,'wide national view is omitted before requesting grid geometry');
    await zoomToCell();
    note('stale viewport grid ignored; backend omission and wide-view request limit honored');
    const center=await page.evaluate(()=>pipeDistance.map.getView().getCenter());
    await page.locator('#nationalWaterCase2').check();await ready('kict_water_national_39');
    assert.deepEqual(await page.evaluate(()=>pipeDistance.map.getView().getCenter()),center);
    assert.match(await page.locator('#legendCase').innerText(),/Case 2/);
    assert.ok(files.at(-1).includes('/national/water/39/files/pipe_case2_m.tif'));
    await rasterValue(sample,'case2');
    await page.locator('#nationalWaterRegion').selectOption('21');await ready('kict_water_national_21');
    assert.notDeepEqual(await page.evaluate(()=>pipeDistance.map.getView().getCenter()),center);
    assert.match(await page.locator('#measureCase').innerText(),/부산.*Case 2/);
    assert.equal(await page.locator('input[data-raster]:checked').count(),1);
    await rasterValue(await zoomToCell(),'case2');
    note('Case switch keeps view; region switch fits new data and keeps measurement Case');
    await page.locator('#nationalSewerRegion').selectOption('39');await ready('kict_sewer_national_39');
    await page.locator('#nationalSewerCase2').check();await ready('kict_sewer_national_39');
    assert.ok(files.at(-1).includes('/national/sewer/39/files/pipe_case2_m.tif'));
    await rasterValue(await zoomToCell(),'case2');await page.locator('#nationalSewerCase2').uncheck();
    assert.equal(await page.evaluate(()=>pipeDistance.getGrid().getVisible()),false);
    note('regional sewer Cases and grid visibility follow raster selection');
    await page.locator('#legacyWaterGroup > summary').click();
    await page.locator('#legacySewerGroup > summary').click();
    await page.locator('#case1').check();await ready('kict_water_seoul');
    await page.locator('#sewerCase2').check();await ready('kict_sewer_seoul');
    const records=await page.evaluate(()=>pipeDistance.getRecords().filter(r=>r.type==='원본 읽기'));
    assert.ok(records.some(r=>r.dataset_id==='kict_water_national_39'&&r.case==='case2'));
    assert.ok(records.some(r=>r.dataset_id==='kict_sewer_national_39'&&r.case==='case2'));
    note('existing Seoul selectors and measurement dataset identity retained');
    await page.waitForFunction(()=>pipeDistance.getVectors().find(e=>e.id==='kict_sewer_national')?.source);
    const decoded=await page.evaluate(async()=>{
      const entry=pipeDistance.getVectors().find(e=>e.id==='kict_sewer_national');
      const m=await(await fetch('../tiles/kict_sewer_national/meta.json')).json();
      const data=await(await fetch('../tiles/kict_sewer_national/6/54/24.pbf')).arrayBuffer();
      const features=entry.format.readFeatures(data,{extent:entry.source.getTileGrid().getTileCoordExtent([6,54,24]),featureProjection:'EPSG:3857'});
      return {metadata:m.vector_layer_ids, layers:[...new Set(features.map(f=>f.get('layer')))],count:features.length};
    });
    assert.equal(decoded.metadata.length,16);assert.ok(decoded.count>0);assert.ok(decoded.layers.length>1,'MVT must decode multiple original regional layer IDs');
    assert.ok(!decoded.layers.includes('kict_sewer_seoul'));
    note('outside-Seoul MVT decodes original regional layers and excludes Seoul');
    await page.locator('#sewerCase2').uncheck();
    for(const area of [{name:'busan',center:[129.0756,35.1796],layer:'sewer_busan'},{name:'gyeonggi',center:[127.0286,37.2636],layer:'sewer_gyeonggi_south'}]) {
      await page.evaluate(({center,layer})=>{
        const e=pipeDistance.getVectors().find(e=>e.id==='kict_sewer_national');
        window.nationalVectorFeatures=0;
        const loaded=event=>{for(const feature of event.tile.getFeatures())if(feature.get('layer')===layer)window.nationalVectorFeatures++;};
        if(window.nationalVectorListener)e.source.un('tileloadend',window.nationalVectorListener);
        window.nationalVectorListener=loaded;e.source.on('tileloadend',loaded);
        const view=pipeDistance.map.getView();view.setCenter(ol.proj.fromLonLat(center));view.setZoom(14);
      },area);
      await page.locator('#vector-kict_sewer_national').check();
      await page.waitForFunction(()=>window.nationalVectorFeatures>0&&document.getElementById('vectorState').textContent==='현재 화면 표시 완료',null,{timeout:60000});
      const colored=await page.evaluate(()=>{
        let count=0;
        for(const canvas of document.querySelectorAll('#vectorMap canvas')){
          const context=canvas.getContext('2d');if(!context)continue;
          const pixels=context.getImageData(0,0,canvas.width,canvas.height).data;
          for(let i=0;i<pixels.length;i+=4)if(pixels[i]<65&&pixels[i+1]>85&&pixels[i+1]<165&&pixels[i+2]>70&&pixels[i+2]<150&&pixels[i+3]>0)count++;
        }
        return count;
      });
      assert.ok(colored>100,'national '+area.name+' vector features actually draw');
      if(output){fs.mkdirSync(output,{recursive:true});await page.screenshot({path:output+'/national-vector-'+area.name+'.png'});}
      await page.locator('#vector-kict_sewer_national').uncheck();
    }
    note('Busan and Gyeonggi national sewer vector features actually render visible pixels');
    await page.locator('#nationalWaterRegion').selectOption('31');await ready('kict_water_national_31');
    await rasterValue(await zoomToCell(),'case1');
    if(output){
      await page.evaluate(()=>{const v=pipeDistance.map.getView();v.setCenter(ol.proj.fromLonLat([127.0286,37.2636]));v.setZoom(15);});
      await page.waitForFunction(()=>pipeDistance.getGrid().getSource().getFeatures().length>0);
      await page.waitForTimeout(600);await page.screenshot({path:output+'/national-raster-gyeonggi.png'});
    }
    note('Gyeonggi original raster value agrees with regional cell result');
    const badMeta='**/distance/national/sewer/21/meta';
    await page.route(badMeta,route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'검사용 전국 메타 오류'})}));
    await page.locator('#nationalSewerRegion').selectOption('21');
    await page.waitForFunction(()=>document.getElementById('status').textContent==='검사용 전국 메타 오류'&&!document.getElementById('nationalSewerCase1').disabled);
    assert.equal(await page.locator('input[data-raster]:checked').count(),0);
    assert.equal(await page.locator('#run').isDisabled(),true);
    assert.equal(await page.locator('#downloadResult').getAttribute('href'),null);
    assert.equal(await page.evaluate(()=>pipeDistance.getGrid().getVisible()),false);
    assert.equal(await page.locator('#detailPanel').isVisible(),false);
    await page.unroute(badMeta);await page.locator('#nationalSewerCase1').check();await ready('kict_sewer_national_21');
    const badTiff='**/distance/national/sewer/21/files/pipe_case2_m.tif?*';
    await page.route(badTiff,route=>route.fulfill({status:503,body:'test TIFF unavailable'}));
    await page.locator('#nationalSewerCase2').check();
    await page.waitForFunction(()=>document.getElementById('status').textContent.includes('원본 파일을 받지 못했습니다')&&!document.getElementById('reload').disabled);
    assert.equal(await page.evaluate(()=>Boolean(pipeDistance.getLayer())),false);
    assert.equal(await page.evaluate(()=>pipeDistance.getGrid().getVisible()),false);
    assert.equal(await page.locator('#rasterLegend').isVisible(),false);
    await page.unroute(badTiff);await page.locator('#reload').click();await ready('kict_sewer_national_21');
    note('regional metadata and TIFF failures clear old map/results and recover on retry');
    await page.route('**/tiles/kict_sewer_national/meta.json',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({minzoom:6,maxzoom:17,complete:false,count:243135,expected_count:3634657,missing_regions:['sewer_jeju'],vector_layer_ids:['sewer_gangwon']})}));
    await page.reload({waitUntil:'domcontentloaded'});await ready('kict_water_national_39');
    await page.waitForFunction(()=>document.querySelector('#vector-kict_sewer_national').closest('label').classList.contains('unavailable'));
    assert.equal(await page.locator('#vector-kict_sewer_national').isDisabled(),true);
    assert.match(await page.locator('#vector-kict_sewer_national').locator('..').innerText(),/설치 미완료/);
    note('partial national vector installation cannot be enabled or shown as complete');
    await page.setViewportSize({width:390,height:844});await page.locator('#toggleLayers').click();
    assert.equal(await page.locator('#nationalWaterRegion').isVisible(),true);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    if(output){fs.mkdirSync(output,{recursive:true});await page.screenshot({path:output+'/national-mobile.png'});fs.writeFileSync(output+'/national-browser.json',JSON.stringify({checks,errors,files,decoded},null,2));}
    assert.deepEqual(errors,[]);note('mobile catalog fits and browser has no uncaught errors');
  } finally {await browser.close();}
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
