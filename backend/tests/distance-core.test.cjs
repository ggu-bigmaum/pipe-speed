const {test}=require('node:test'); const assert=require('node:assert/strict');
const fs=require('node:fs'); const path=require('node:path');
const target=path.resolve(__dirname,'../../frontend/distance-core.js');
const context={window:{}};
require('node:vm').runInNewContext(fs.readFileSync(target,'utf8'),context);
const core=context.window.PipeDistanceCore;
const meta={origin:[302600,4174500],pixel_size:[100,100],width:370,height:303};
test('click maps to the actual original grid cell',()=>assert.deepEqual({...core.cellAt([302650,4158850],meta)},{col:0,row:156}));
test('outer east/south and negative coordinates do not select another cell',()=>{
 for(const p of [[339600,4158850],[302650,4144200],[302599.999,4158850],[NaN,4158850]]) assert.equal(core.cellAt(p,meta),null);
});
test('four increasing bins need five colours',()=>{
 assert.equal(core.validStyle([10,50,100,500],Array(5).fill('#112233')),true);
 assert.equal(core.validStyle([0,10,50,100],Array(5).fill('#112233')),true);
 assert.equal(core.validStyle([10,10,100,500],Array(5).fill('#112233')),false);
 assert.equal(core.validStyle([10,50,100,NaN],Array(5).fill('#112233')),false);
 assert.equal(core.validStyle([0,10,50,100,500],Array(6).fill('#112233')),false);
});
