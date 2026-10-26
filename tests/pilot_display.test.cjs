const {test} = require('node:test');
const assert = require('node:assert/strict');
const {plan, pointRecipe, legendPattern, requireRelease} = require('../explorer/pilot-display.js');

const records = Array.from({length:50}, (_, i) => ({id:String(i), width:1600, height:1600, bounds:[i,0,i+1,1]}));
const budget = {max_images:8, max_decoded_pixels:16*1024*1024};
const overviewPixels = 1024*512;
const view = bounds => ({bounds,width:1000,height:800});

test('wide view uses only a bounded overview', () => {
  assert.deepEqual(plan(records, view([0,0,50,1]), budget, overviewPixels),
    {mode:'overview',records:[],pixels:overviewPixels});
});
test('detail follows the viewport and discards old cores after a pan', () => {
  const a = plan(records, view([.1,.1,.9,.9]), budget, overviewPixels);
  const b = plan(records, view([30.1,.1,30.9,.9]), budget, overviewPixels);
  assert.equal(a.mode,'detail'); assert.deepEqual(a.records.map(r=>r.id),['0']);
  assert.deepEqual(b.records.map(r=>r.id),['30']);
  assert.ok(a.pixels <= budget.max_decoded_pixels);
});
test('pixel and image caps fall back to a complete overview, never partial detail', () => {
  assert.equal(plan(records,view([0,0,7,1]),budget,overviewPixels).mode,'overview');
  assert.equal(plan(records,view([0,0,3,1]),{...budget,max_images:2},overviewPixels).mode,'overview');
});
test('nearby margin cores cannot exceed the pixel cap', () => {
  const result = plan(records,view([10.01,.1,15.99,.9]),budget,overviewPixels);
  assert.equal(result.mode,'detail');
  assert.deepEqual(result.records.map(r=>r.id),['10','11','12','13','14','15']);
  assert.ok(result.pixels <= budget.max_decoded_pixels);
});
test('no imagery is loaded outside the selected region', () => {
  assert.deepEqual(plan(records,view([60,2,61,3]),budget,overviewPixels),{mode:'outside',records:[],pixels:0});
});
test('small distant cores use an overview despite fitting in the memory budget', () => {
  assert.equal(plan(records.slice(0,1),view([-5,-5,5,5]),budget,overviewPixels).mode,'overview');
});
test('shared coordinates survive an exact numerical round trip at the reproduced boundary', () => {
  const point = [-.4520146668103435,51.46671542367228];
  assert.deepEqual(pointRecipe(point).split(',').map(Number),point);
  assert.notDeepEqual(point.map(x=>Number(x.toFixed(6))),point);
});
test('unknown encodings have distinct hatch colors and directions', () => {
  assert.match(legendPattern({encoding:'zero_code',colour:'#c0b8a5'}),/135deg, #c0b8a5/);
  assert.match(legendPattern({encoding:'tiff_nodata',colour:'#aab5c4'}),/45deg, #aab5c4/);
});
test('point, coverage and display records must match the page release', () => {
  const record = {release_id:'current'};
  assert.equal(requireRelease(record,'current'),record);
  for (const data of [{release_id:'old'},{},null]) assert.throws(()=>requireRelease(data,'current'),/release_mismatch/);
});
