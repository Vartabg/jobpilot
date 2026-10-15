import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {before, after, test} from 'node:test';
import {chromium} from '@playwright/test';

// Synthetic private-step DOMs, using structures verified in Workday's public
// apply-flow bundle on 2026-10-15. No accounts, real applications or submissions.
// https://www.myworkdaycdn.com/wday/asset/candidate-experience-apply-flow/candidate-experience-apply-flow.min.js
// Source-verified: applyFlow*Page, progressBarActiveStep, formField-${name},
// button aria-haspopup=listbox, resumeUpload/file-upload-*, numbered role=group
// panels with aria-labelledby. Employer documentation describes the step flow:
// https://hr.psu.edu/sites/hr/files/Application_Process_for_External_Applications_Non-Penn_State_Employees_.pdf
const script = await readFile(new URL('../content.js', import.meta.url), 'utf8');
const standard = 'https://fixture.wd1.myworkdayjobs.com/en-US/External/job/City/Engineer_R123/apply/applyManually';
const site = 'https://wd5.myworkdaysite.com/recruiting/fixture/External/job/City/Engineer_R123/apply/applyManually';
const profile = {first_name:'Alex',last_name:'Example',email:'alex@example.test',phone:'+1 512 555 0100',
  address_line1:'123 Example Street',city:'Austin',state:'TX',postal_code:'78701',country:'United States',
  work_history:[{company:'First Company',title:'First Role',start_date:'2020-01',end_date:'2021-02'},
    {company:'Second Company',title:'Second Role'}, {company:'Third Company',title:'Third Role',current:true,end_date:'2026-10'}],
  education:[{school:'First University',degree:'BS',field:'Computer Science'}, {school:'Second School',degree:'MS',field:'Engineering'}]};
const resume = {metadata:{name:'Exact.pdf'},dataUrl:'data:application/pdf;base64,JVBERi0xLjQK'};
const input = (id,label,extra='',auto=id) => `<div data-automation-id="formField-${auto}">${label ? `<label>${label}</label>` : ''}<input id="${id}" ${extra}></div>`;
const button = (id,label,selected='Select One') => `<div data-automation-id="formField-${id}"><label id="${id}-label">${label}</label>
  <button type="button" id="${id}" aria-haspopup="listbox" aria-labelledby="${id}-label">${selected}</button></div>`;
const flow = (html,step='applyFlowMyInfoPage',extra='') => `<div data-automation-id="applyFlowPage"><div data-automation-id="progressBarActiveStep">${step}</div>
  <div data-automation-id="${step}">${html}</div><button type="button" data-automation-id="pageFooterNextButton">Save and Continue</button>
  <button type="submit" id="submit">Submit</button>${extra}</div><script>window.navigationClicks=0;document.querySelectorAll('[data-automation-id="pageFooterNextButton"],#submit').forEach(el=>el.onclick=e=>{e.preventDefault();navigationClicks++});</script>`;
const uploader = `<div data-automation-id="resumeUpload"><h3>Resume/CV</h3><div data-automation-id="file-upload-drop-zone">
  <button type="button">Select file</button><input id="resume" data-automation-id="file-upload-input-ref" type="file" style="display:none"></div><div id="uploaded"></div></div>`;
let browser;
before(async()=>{browser=await chromium.launch();});
after(async()=>{await browser?.close();});
async function fixture(html,run,url=standard) {
  const context=await browser.newContext(),page=await context.newPage();
  await context.route('**/*',route=>route.request().isNavigationRequest()?route.fulfill({contentType:'text/html',body:html}):route.abort());
  await page.goto(url);await page.addScriptTag({content:script});
  try{return await run(page);}finally{await context.close();}
}
const execute=(page,action='details',saved=profile)=>page.evaluate(async payload=>__jobpilotRun({...payload,expectedWorkdayStep:__jobpilotWorkdayScope().stepKey}),{action,profile:saved,resume,requireVisible:true});
function choices(id,values,delay=0) {
  return `<script>document.querySelector('#${id}').addEventListener('click',()=>{
    const control=document.querySelector('#${id}');control.setAttribute('aria-expanded','true');
    setTimeout(()=>{const menu=document.createElement('div');menu.id='${id}-list';menu.setAttribute('role','listbox');
      menu.innerHTML=${JSON.stringify(values.map(value=>`<div role="option">${value}</div>`).join(''))};document.body.append(menu);control.setAttribute('aria-controls',menu.id);
      menu.addEventListener('click',event=>{control.textContent=event.target.textContent;control.setAttribute('aria-expanded','false');menu.remove();});},${delay});});</script>`;
}

test('Workday contact fields use field labels/wrappers on both supported host families',async t=>{
  for(const url of [standard,site,standard.replace('wd1','wd504'),site.replace('/recruiting/','/en-GB/recruiting/')]) await t.test(new URL(url).host+new URL(url).pathname.split('/')[1],async()=>{
    await fixture(flow(input('first','Given Name(s) *','required','firstName')+input('last','Family Name','', 'lastName')+
      input('email','Email Address *','type="email" required')+input('address','Address Line 1','', 'addressLine1')+
      input('city','City')+input('postal','Postal Code','', 'postalCode')+input('custom','Team administrator','', 'firstName')),
    async page=>{
      const scope=await page.evaluate(()=>__jobpilotWorkdayScope());assert.equal(scope.canFillDetails,true);assert.equal(scope.canAttachResume,false);
      const report=await execute(page);assert.equal(report.provider,'Workday');assert.equal(report.status,'complete');
      for(const [id,value] of [['first','Alex'],['last','Example'],['email',profile.email],['address',profile.address_line1],['city','Austin'],['postal','78701']]) assert.equal(await page.locator('#'+id).inputValue(),value,id);
      assert.equal(await page.locator('#custom').inputValue(),'','technical firstName IDs cannot override a custom question label');
      assert.equal(await page.evaluate(()=>navigationClicks),0);
    },url);
  });
});

test('Workday button dropdowns open only their associated delayed listbox and preserve selected answers',async()=>{
  await fixture(flow(button('country','Country *')+button('state','State/Province *')+button('existing','Country','Canada'))+
    '<div role="listbox" id="unrelated"><div role="option">United States</div></div>'+choices('country',['Canada','United States'],200)+choices('state',['Texas','California'],100),
  async page=>{
    await page.locator('#country').evaluate(el=>el.setAttribute('aria-required','true'));
    const before=await execute(page,'inspect');assert.ok(before.required.some(field=>field.label==='Country *'));
    const report=await execute(page);assert.equal(report.status,'complete');
    assert.equal(await page.locator('#country').textContent(),'United States');assert.equal(await page.locator('#state').textContent(),'Texas');
    assert.equal(await page.locator('#existing').textContent(),'Canada');assert.equal(await page.locator('#unrelated').isVisible(),true);
    assert.equal(report.required.length,0);assert.equal(await page.evaluate(()=>navigationClicks),0);
  });
});

test('duplicate Workday options remain manual instead of choosing the first match',async()=>{
  await fixture(flow(button('country','Country *'))+choices('country',['United States','United States']),async page=>{
    const report=await execute(page);assert.equal(report.filled.length,0);assert.equal(report.failed.length,1);
    assert.equal(await page.locator('#country').textContent(),'Select One');
  });
});

test('Workday visible repeated history rows use explicit row numbers without shifting across a hidden gap',async()=>{
  const row=(n,hidden=false)=>`<div role="group" aria-labelledby="work-${n}" ${hidden?'style="display:none"':''}><h3 id="work-${n}">Work Experience ${n}</h3>
    ${input(`company${n}`,'Company','', 'companyName')}${input(`title${n}`,'Job Title','', 'jobTitle')}${input(`end${n}`,'End Date','type="month"','endDate')}</div>`;
  const education=`<div role="group" aria-labelledby="education-2"><h3 id="education-2">Education 2</h3>${input('school','School or University','', 'schoolName')}${input('degree','Degree')}</div>`;
  await fixture(flow(row(1)+row(2,true)+row(3)+row(5)+education,'applyFlowMyExpPage'),async page=>{
    const report=await execute(page);assert.equal(report.status,'complete');
    assert.equal(await page.locator('#company1').inputValue(),'First Company');assert.equal(await page.locator('#company2').inputValue(),'');
    assert.equal(await page.locator('#company3').inputValue(),'Third Company');assert.equal(await page.locator('#company5').inputValue(),'');
    assert.equal(await page.locator('#title3').inputValue(),'Third Role');assert.equal(await page.locator('#end3').inputValue(),'','current jobs keep end dates manual');
    assert.equal(await page.locator('#school').inputValue(),'Second School');assert.equal(await page.locator('#degree').inputValue(),'MS');
    assert.equal(await page.evaluate(()=>navigationClicks),0);
  });
});

test('Workday preserves existing contact answers and leaves split phone, attestations, demographics and unknown questions manual',async()=>{
  await fixture(flow(input('first','First Name','value="Applicant edit"','firstName')+input('email','Email','value="existing@example.test"')+
    button('countryPhoneCode','Country Phone Code','United States (+1)').replace('formField-countryPhoneCode','formField-phoneNumber--countryPhoneCode')+input('phone','Phone Number','', 'phoneNumber')+
    input('salary','Salary expectations')+input('gender','Gender')+'<label><input id="attest" type="checkbox" required>I certify this application is accurate</label>'),async page=>{
    const report=await execute(page);assert.equal(await page.locator('#first').inputValue(),'Applicant edit');
    assert.equal(await page.locator('#email').inputValue(),'existing@example.test');
    for(const id of ['phone','salary','gender']) assert.equal(await page.locator('#'+id).inputValue(),'');
    assert.equal(await page.locator('#attest').isChecked(),false);assert.equal(await page.locator('#countryPhoneCode').textContent(),'United States (+1)');
    assert.ok(report.skipped.some(field=>/separate country code/.test(field.reason)));assert.equal(await page.evaluate(()=>navigationClicks),0);
  });
});

test('Workday exact resume upload uses its local evidence and preserves an existing upload',async()=>{
  await fixture(flow(uploader,'applyFlowAutoFillPage')+`<script>document.querySelector('#resume').onchange=e=>{
    const name=e.target.files[0].name;e.target.value='';document.querySelector('#uploaded').innerHTML='<span data-automation-id="file-upload-item-name">'+name+'</span><span data-automation-id="file-upload-successful">Successfully uploaded</span>';};</script>`,async page=>{
    const scope=await page.evaluate(()=>__jobpilotWorkdayScope());assert.equal(scope.canFillDetails,false);assert.equal(scope.canAttachResume,true);
    const report=await execute(page,'resume');assert.equal(report.resume.status,'accepted');
    assert.equal((await execute(page,'resume')).resume.status,'already-attached');
    assert.equal(await page.locator('[data-automation-id="file-upload-item-name"]').textContent(),'Exact.pdf');assert.equal(await page.evaluate(()=>navigationClicks),0);
  });
});

test('Workday account dialogs and unsupported routes never receive contact data or files',async t=>{
  for(const gate of ['<div data-automation-id="signInContent"><input id="account-email" data-automation-id="email"><button data-automation-id="SignInWithEmailButton">Continue with Email</button></div>',
    '<div role="dialog" aria-label="Create Account"><input id="account-email"><input type="password"></div>']) await t.test(gate.slice(0,40),async()=>{
    await fixture(flow(input('first','First Name')+uploader,'applyFlowMyExpPage')+gate,async page=>{
      const scope=await page.evaluate(()=>__jobpilotWorkdayScope());assert.equal(scope.hasForm,false);
      for(const action of ['details','resume']) assert.equal((await execute(page,action)).status,'interrupted');
      assert.equal(await page.locator('#first').inputValue(),'');assert.equal(await page.locator('#account-email').inputValue(),'');
      assert.equal(await page.locator('#resume').evaluate(el=>el.files.length),0);
    });
  });
  for(const url of ['http://fixture.wd1.myworkdayjobs.com/External/job/City/Engineer_R123','https://fixture.wd1.myworkdayjobs.com/External','https://fixture.wd1.myworkdayjobs.com/External/signIn',
    'https://fixture.wd1.myworkdayjobs.com.evil.test/External/job/x','https://employee.myworkday.com/External/job/x','https://wd1.myworkdaysite.com/recruiting/tenant/site/search']) await t.test(url,async()=>{
      await fixture(flow(input('first','First Name')),async page=>{
        assert.equal((await page.evaluate(()=>__jobpilotWorkdayScope())).hasForm,false);
        await execute(page);assert.equal(await page.locator('#first').inputValue(),'');
      },url);
    });
});

test('Workday manual questionnaire/disclosure/review steps can be inspected but are not filled or uploaded',async t=>{
  for(const step of ['applyFlowPrimaryQuestionsPage','applyFlowVoluntaryDisclosuresPage','applyFlowSelfIdentifyPage','applyFlowReviewPage']) await t.test(step,async()=>{
    await fixture(flow(input('first','First Name','required')+uploader,step),async page=>{
      const scope=await page.evaluate(()=>__jobpilotWorkdayScope());assert.equal(scope.hasForm,true);assert.equal(scope.canFillDetails,false);assert.equal(scope.canAttachResume,false);
      assert.equal((await execute(page,'inspect')).required.length,1);
      for(const action of ['details','resume']) assert.equal((await execute(page,action)).status,'interrupted');
      assert.equal(await page.locator('#first').inputValue(),'');assert.equal(await page.locator('#resume').evaluate(el=>el.files.length),0);
    });
  });
});

test('Workday stops on same-URL step replacement and never fills the next visible step',async()=>{
  await fixture(flow(input('first','First Name')+input('last','Last Name'))+`<script>document.querySelector('#first').oninput=()=>{
    document.querySelector('[data-automation-id="applyFlowMyInfoPage"]').outerHTML='<div data-automation-id="applyFlowMyExpPage"><label>Last Name<input id="next-last"></label></div>';};</script>`,async page=>{
    const report=await execute(page);assert.equal(report.status,'interrupted');assert.equal(await page.locator('#next-last').inputValue(),'');
    assert.equal(await page.evaluate(()=>navigationClicks),0);
  });
});

test('Workday delayed button options cannot be selected after a sticky step transition',async()=>{
  await fixture(flow(button('country','Country *'))+choices('country',['United States'],300),async page=>{
    const running=execute(page);await page.waitForSelector('#country[aria-expanded=true]');
    await page.evaluate(()=>document.querySelector('[data-automation-id="progressBarActiveStep"]').textContent='My Experience');
    const report=await running;assert.equal(report.status,'interrupted');assert.equal(await page.locator('#country').textContent(),'Select One');
  });
});

test('Workday file help text and pending filenames cannot claim upload acceptance; structured failures remain visible',async t=>{
  for(const fail of [false,true]) await t.test(fail?'structured upload failure':'pending filename stays prepared',async()=>{
    await fixture(flow(uploader.replace('<h3>Resume/CV</h3>','<h3>Resume/CV</h3><p>Please upload Exact.pdf</p>'),'applyFlowAutoFillPage')+
      `<script>document.querySelector('#resume').onchange=e=>{document.querySelector('#uploaded').innerHTML='<span data-automation-id="file-upload-item-name">'+e.target.files[0].name+'</span><span data-automation-id="${fail?'file-upload-failed':'file-loading-dots'}" style="display:inline-block;width:8px;height:8px">${fail?'Invalid upload':''}</span>${fail?'':'<span data-automation-id="file-upload-successful">✓</span>'}';};</script>`,async page=>{
        const report=await execute(page,'resume');assert.equal(report.resume.status,fail?'failed':'prepared');
        assert.equal(report.status,'partial');assert.equal(await page.locator('#resume').evaluate(el=>el.files.length),1,'help text is not an existing attachment');
      });
  });
});
