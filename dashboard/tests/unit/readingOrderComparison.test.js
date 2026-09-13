import test from 'node:test';
import assert from 'node:assert/strict';
import { parseReadingOrderComparison, readingOrderReason } from '../../src/utils/readingOrderComparison.ts';

const snapshot = () => ({ status:'available',reason:null,sha256:'a'.repeat(64),page_count:2,page_number:1,width:600,height:800,preview_png_base64:'iVBORw0KGgo=',blocks:[{index:1,text:'Second painted paragraph',source:'MCID',bbox:[20,100,200,120]},{index:2,text:'First painted paragraph',source:'MCID',bbox:null}],unpositioned_count:1 });
const envelope = () => ({scan_id:'scan',page_number:1,artifact_id:'artifact',source:snapshot(),saved:{...snapshot(),blocks:[{index:1,text:'First painted paragraph',source:'MCID',bbox:null}],unpositioned_count:1}});

test('comparison preserves independently sourced semantic sequences and nullable positions', () => {
  const data=envelope();
  assert.deepEqual(parseReadingOrderComparison(data,'scan',1),data);
  assert.equal(data.source.blocks[0].text,'Second painted paragraph');
  assert.equal(data.saved.blocks[0].text,'First painted paragraph');
});
test('comparison rejects stale scan/page and invalid or mismatched geometry', () => {
  assert.throws(()=>parseReadingOrderComparison(envelope(),'other',1));
  assert.throws(()=>parseReadingOrderComparison(envelope(),'scan',2));
  for(const bbox of [[0,0,Infinity,5],[-1,0,10,20],[1,1,601,2],[20,0,10,5]]) {
    const data=envelope();data.source.blocks[0].bbox=bbox;
    assert.throws(()=>parseReadingOrderComparison(data,'scan',1));
  }
});
test('comparison refuses invented available order, malformed images and unsafe hashes', () => {
  for(const update of [{blocks:[]},{sha256:'not-a-hash'},{preview_png_base64:'https://example.org/image.png'},{status:'complete'}]) {
    const data=envelope();Object.assign(data.saved,update);
    assert.throws(()=>parseReadingOrderComparison(data,'scan',1));
  }
});
test('unavailable versions are independent and cannot contain asserted order', () => {
  const data=envelope();
  data.saved={...snapshot(),status:'unavailable',reason:'untagged_pdf',blocks:[],unpositioned_count:0};
  assert.equal(parseReadingOrderComparison(data,'scan',1).source.status,'available');
  data.saved.blocks=snapshot().blocks;
  assert.throws(()=>parseReadingOrderComparison(data,'scan',1));
  assert.equal(readingOrderReason('/private/file.pdf'), 'Reading-order evidence is unavailable for this version.');
});
test('a version above the document limit does not hide the other readable version', () => {
  const data=envelope();
  data.saved={...snapshot(),status:'unavailable',reason:'limit_exceeded',page_count:501,blocks:[],unpositioned_count:0};
  assert.equal(parseReadingOrderComparison(data,'scan',1).source.status,'available');
});
test('text bounds count Unicode code points consistently with the PDF extractor', () => {
  const data=envelope();
  data.source.blocks[0].text='😀'.repeat(100001);
  assert.equal(parseReadingOrderComparison(data,'scan',1).source.blocks[0].text,data.source.blocks[0].text);
});
