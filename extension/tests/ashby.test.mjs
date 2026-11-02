import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {before, after, test} from 'node:test';
import {chromium} from '@playwright/test';

const script = await readFile(new URL('../content.js', import.meta.url), 'utf8');
let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });
const profile = {first_name:'Alex', last_name:'Example', preferred_name:'Lex',
  email:'alex@example.org', phone:'+1 555 010 1200', city:'Austin', state:'TX',
  country:'United States', postal_code:'78701', address_line1:'123 Example Street',
  address_line2:'Unit 2', work_authorized:true, requires_sponsorship:false};
const title = (text, required=false) => `<label class="ashby-application-form-question-title ${required ? '_required_fixture_1' : ''}">${text}</label>`;
const field = (id, label, required=false, value='') => `<div class="ashby-application-form-field-entry">${title(label,required)}<input id="${id}" value="${value}" placeholder="Type here..."></div>`;
const radio = (name, question, checked=false) => `<fieldset class="ashby-application-form-input-radio-group">${title(question,true)}
  <div class="ashby-application-form-input-radio-group-option"><span><input type="radio" name="${name}" ${checked ? 'checked' : ''}></span><label>First option</label></div>
  <div class="ashby-application-form-input-radio-group-option"><span><input type="radio" name="${name}"></span><label>Second option</label></div></fieldset>`;
const yesno = (id, question, required=true, answered=false) => `<div class="ashby-application-form-field-entry">${title(question,required)}
  <div id="${id}" class="ashby-application-form-input-yesno"><button aria-pressed="false">Yes</button><button aria-pressed="${answered}">No</button></div></div>`;
async function fixture(html, run) {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.route('**/*', route => route.request().isNavigationRequest()
    ? route.fulfill({contentType:'text/html',body:`<style>._required_fixture_1::after{content:'*'} .ashby-application-form-input-radio-group-option input,.ashby-application-form-input-checkbox-group input{opacity:0}</style>${html}`})
    : route.abort());
  await page.goto('https://jobs.ashbyhq.com/example/req-123/application');
  await page.addScriptTag({content:script});
  try { await run(page); } finally { await context.close(); }
}
const execute = (page, action='details', saved=profile) => page.evaluate(
  options => globalThis.__jobpilotRun(options), {action,profile:saved});

test('Ashby question labels map verified names, phone and address without filling employee names', async () => {
  await fixture(field('preferred','What is your preferred name?',true)+
    field('legal','What is your Legal first and last name?',true)+
    field('phone','What is your phone number')+
    field('address1','What is the first line of your address?',true)+
    field('address2','What is the second line of your address (if relevant)?')+
    field('state','State/Region')+field('post','Post Code',true)+
    field('employee','If you have discussed this role with a current Camunda employee, please enter their full name here')+
    field('referral','Referral name')+field('salary','What are your salary expectations?',true), async page => {
      const report=await execute(page);
      for(const [id,value] of [['preferred','Lex'],['legal','Alex Example'],['phone',profile.phone],
        ['address1',profile.address_line1],['address2',profile.address_line2],['state','TX'],['post','78701']])
        assert.equal(await page.locator('#'+id).inputValue(),value,id);
      for(const id of ['employee','referral','salary']) assert.equal(await page.locator('#'+id).inputValue(),'');
      assert.equal(report.filled.length,7);
      assert.deepEqual(report.required,[{label:'What are your salary expectations?',type:'text'}]);
  });
});

test('preferred name and street address require explicit saved values and preserve applicant edits', async () => {
  await fixture(field('preferred','What is your preferred name?',true)+field('address','What is the first line of your address?',true)+
    field('legal','What is your Legal first and last name?',true,'Applicant correction'), async page => {
      const report=await execute(page,'details',{...profile,preferred_name:'',address_line1:''});
      assert.equal(await page.locator('#preferred').inputValue(),'');
      assert.equal(await page.locator('#address').inputValue(),'');
      assert.equal(await page.locator('#legal').inputValue(),'Applicant correction');
      assert.equal(report.skipped.filter(row=>row.reason==='No saved profile value').length,2);
      assert.equal(report.required.length,2);
  });
});

test('inspection reports CSS-required radio and Yes/No questions once and never presses them', async () => {
  await fixture(`<form>${radio('production','Production experience')}${radio('agents','AI agent experience')}
    ${radio('bpmn','BPMN experience')}${radio('status','Work authorization status')}
    ${radio('answered','Already answered',true)}<div style="display:none">${radio('hidden','Hidden question')}</div>
    ${yesno('auth','Are you legally eligible to work in the country where you’re planning to work from?')}
    ${yesno('remote','Are you able and willing to work remotely?')}
    ${yesno('fraud','Do you consent to interview fraud analysis?')}
    ${yesno('related','Are you related to any current employees?',false)}
    ${yesno('answered-toggle','Answered toggle',true,true)}
    <fieldset class="ashby-application-form-input-checkbox-group">${title('Privacy consent',true)}<div class="ashby-application-form-input-checkbox-group-option"><span><input type="checkbox" id="consent"></span><label for="consent">I agree</label></div></fieldset>
    <button id="submit">Submit Application</button></form>
    <script>document.querySelector('form').addEventListener('submit',event=>{event.preventDefault();document.body.dataset.submitted='yes';});
    document.querySelectorAll('button').forEach(button=>button.addEventListener('click',()=>{document.body.dataset.clicked='yes';}));</script>`, async page => {
      for(const action of ['inspect','details']) {
        const report=await execute(page,action);
        assert.equal(report.required.filter(row=>row.type==='radio').length,4);
        assert.equal(report.required.filter(row=>row.type==='choice').length,3);
        assert.equal(report.required.filter(row=>row.type==='checkbox').length,1);
        assert.ok(report.required.some(row=>row.label==='Production experience'));
        assert.ok(!report.required.some(row=>/Hidden|Already answered|Answered toggle|related/.test(row.label)));
        assert.equal(report.filled.length,0);
        assert.equal(await page.locator('body').getAttribute('data-submitted'),null);
        assert.equal(await page.locator('body').getAttribute('data-clicked'),null);
        assert.equal(await page.locator('#consent').isChecked(),false);
      }
  });
});

test('Ashby unassociated autocomplete gets its question label and selects an associated delayed result', async () => {
  await fixture(`<div class="ashby-application-form-field-entry">${title('Location',true)}
    <div><input id="place" class="ashby-application-form-input-autocomplete" role="combobox" aria-expanded="false" placeholder="Start typing..."></div></div>
    <div id="unrelated" role="listbox"><div role="option">Austin, Texas, United States</div></div>
    <script>const input=document.querySelector('#place');
    input.addEventListener('input',()=>{if(!input.value)return;input.setAttribute('aria-expanded','true');setTimeout(()=>{
      const menu=document.createElement('div');menu.id='associated';menu.setAttribute('role','listbox');
      menu.innerHTML='<div role="option">Austin, Minnesota, United States</div><div role="option">Austin, Texas, United States</div>';
      document.body.append(menu);input.setAttribute('aria-controls',menu.id);
      menu.addEventListener('click',event=>{input.value=event.target.textContent;input.setAttribute('aria-expanded','false');menu.remove();});
    },250);});</script>`, async page => {
      const before=await execute(page,'inspect');
      assert.deepEqual(before.required,[{label:'Location',type:'combobox'}]);
      const report=await execute(page);
      assert.equal(await page.locator('#place').inputValue(),'Austin, Texas, United States');
      assert.equal(report.filled[0]?.key,'location');
      assert.equal(report.required.length,0);
  });
});

test('Ashby autocomplete retains a failed or ambiguous result in required-field review', async () => {
  await fixture(`<div class="ashby-application-form-field-entry">${title('Location',true)}
    <div><input id="place" class="ashby-application-form-input-autocomplete" role="combobox" aria-expanded="false" aria-controls="places"></div>
    <div id="places" role="listbox"><div role="option">Austin, Minnesota, United States</div></div></div>`, async page => {
      const report=await execute(page);
      assert.equal(await page.locator('#place').inputValue(),'');
      assert.equal(report.filled.length,0);
      assert.equal(report.failed.length,1);
      assert.deepEqual(report.required,[{label:'Location',type:'combobox'}]);
  });
});
