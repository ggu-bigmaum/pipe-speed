/* Run against a local server with the real water and sewer TIFFs; no synthetic raster. */
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({channel: 'chrome', headless: true, args: ['--enable-unsafe-swiftshader']});
  const page = await browser.newPage({viewport: {width: 1440, height: 900}});
  const errors = [], results = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(process.env.PIPE_TEST_URL || 'http://localhost:8012/app/');
    await page.waitForFunction(() => window.pipeDistance && !document.getElementById('reload').disabled, null, {timeout: 45000});
    const cases = [
      {id:'case1', input:'case1', api:'/distance/'}, {id:'case2', input:'case2', api:'/distance/'},
      {id:'case1', input:'sewerCase1', api:'/distance/kict_sewer_seoul/'}, {id:'case2', input:'sewerCase2', api:'/distance/kict_sewer_seoul/'}
    ];
    for (const {id, input, api} of cases) {
      if (!await page.locator('#' + input).isChecked()) await page.locator('#' + input).check();
      await page.waitForFunction(() => !document.getElementById('reload').disabled, null, {timeout: 45000});
      await page.evaluate(async ({id, api}) => {
        const m = pipeDistance.getMeta(), f = m.cases.find(c => c.id === id);
        const bytes = await fetch(api + 'files/' + f.filename).then(r => r.arrayBuffer());
        const tiff = await GeoTIFF.fromArrayBuffer(bytes), im = await tiff.getImage();
        window.alignmentReference = {values: (await im.readRasters())[0], width: im.getWidth()};
      }, {id, api});
      for (const zoom of [13, 14, 16, 18, 20]) {
        for (const center of [[126.984, 37.568], [126.87, 37.53], [127.10, 37.60]]) {
          await page.evaluate(({zoom, center}) => new Promise(resolve => {
            const d = pipeDistance, v = d.map.getView();
            d.map.once('rendercomplete', () => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            v.setCenter(ol.proj.fromLonLat(center)); v.setZoom(zoom); d.map.render();
          }), {zoom, center});
          const result = await page.evaluate(() => {
            const d = pipeDistance, m = d.getMeta(), source = alignmentReference, size = d.map.getSize();
            const c = ol.proj.transform(d.map.getView().getCenter(), 'EPSG:3857', m.crs);
            const col = Math.floor((c[0] - m.origin[0]) / 100), row = Math.floor((m.origin[1] - c[1]) / 100);
            let tested = 0; const failures = [];
            for (let r = row - 2; r <= row + 2; r++) for (let c = col - 2; c <= col + 2; c++) {
              for (const fx of [.02, .1, .5, .9, .98]) for (const fy of [.02, .1, .5, .9, .98]) {
                const xy = [m.origin[0] + (c + fx) * 100, m.origin[1] - (r + fy) * 100];
                const p = d.map.getPixelFromCoordinate(ol.proj.transform(xy, m.crs, 'EPSG:3857'));
                if (p[0] < 1 || p[1] < 1 || p[0] >= size[0] - 1 || p[1] >= size[1] - 1) continue;
                // Ignore subpixel boundaries: compare only samples at least
                // two screen pixels inside a cell. This still catches drift
                // of entire bands visible when coarse reprojection is enlarged.
                const groundPerPixel = ol.proj.getPointResolution(d.map.getView().getProjection(), d.map.getView().getResolution(), d.map.getView().getCenter());
                if (Math.min(fx, 1 - fx, fy, 1 - fy) * 100 < 2 * groundPerPixel) continue;
                const expected = source.values[r * source.width + c], actual = d.getLayer().getData(p);
                const matches = expected === m.nodata ? !actual || !actual[1] : actual && actual[1] > 0 && Math.abs(actual[0] - expected) <= .001;
                tested++;
                if (!matches) failures.push({col: c, row: r, fx, fy, expected, actual: actual ? Array.from(actual) : null});
              }
            }
            return {tested, failures};
          });
          results.push({input, id, zoom, center, tested: result.tested, failures: result.failures.length});
          assert.ok(result.tested > 0, 'screen samples were exercised');
          assert.equal(result.failures.length, 0, JSON.stringify({id, zoom, center, failures: result.failures.slice(0, 8)}));
        }
      }
    }
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({checks: results, totalSamples: results.reduce((n, r) => n + r.tested, 0), errors}, null, 2));
  } finally { await browser.close(); }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
