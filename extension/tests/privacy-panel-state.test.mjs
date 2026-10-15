import assert from 'node:assert/strict';
import {test} from 'node:test';
import {restorePrivacyReport} from '../privacy-panel.js';

test('saved scans preserve separate frame provenance without restoring answers or token URLs', () => {
  const restored = restorePrivacyReport({source_url:'https://company.test/apply?token=private', checked_at:'2026-10-15T22:30:00Z', status:'partial',
    fields:[{label:'Legal Address',required:true,frame_id:0,frame_url:'https://company.test/apply?token=private',value:'PRIVATE ANSWER'},
      {label:'Legal Address',required:false,frame_id:4,frame_url:'https://ats.test/form?token=private'}],
    frames:[{frame_id:0,source_url:'https://company.test/apply',status:'inspected',field_count:1},
      {frame_id:4,source_url:'https://ats.test/form',status:'inspected',field_count:1}], notices:[]});
  assert.equal(restored.complete,false);
  assert.equal(restored.status,'partial');
  assert.deepEqual(restored.findings.map(f=>[f.frame_id,f.frame_url,f.optionality]),[
    [0,'https://company.test/apply','required'],[4,'https://ats.test/form','optional']]);
  assert.equal(restored.frames.length,2);
  assert.doesNotMatch(JSON.stringify(restored),/PRIVATE ANSWER|token=private/);
});

test('malformed saved data cannot become a successful scan',()=>{
  assert.equal(restorePrivacyReport({fields:Array(501).fill({label:'Gender'})}),null);
  assert.equal(restorePrivacyReport(null),null);
});
