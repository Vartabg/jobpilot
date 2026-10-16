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
// The selected country-code token and removal of a successful résumé's file
// input also reproduce read-only Workiva DOM observations from 2026-10-16.
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

test('Workday listbox buttons expose their fixed question labels without selected answers',async()=>{
  const choice=(id,associated=true)=>`<div data-automation-id="formField-degree"><label id="${id}-label" ${associated?`for="${id}"`:''}>Degree *</label>
    <button id="${id}" type="button" aria-haspopup="listbox" aria-labelledby="${id}-label" aria-label="Degree PRIVATE_SELECTED_ANSWER Required">PRIVATE_SELECTED_ANSWER</button></div>`;
  await fixture(flow(educationRow(1,choice('degree1'))+educationRow(2,choice('degree2',false))+
    '<div data-automation-id="formField-degree"><button id="unlabeled-degree" type="button" aria-haspopup="listbox" aria-label="Degree PRIVATE_UNVERIFIED_ANSWER Required">PRIVATE_UNVERIFIED_ANSWER</button></div>','applyFlowMyExpPage'),async page=>{
    const details=await execute(page);assert.equal(JSON.stringify(details).includes('PRIVATE_'),false);
    assert.equal(details.skipped.filter(field=>field.label==='Degree *'&&/preserved/.test(field.reason)).length,2);
    await page.locator('#degree1, #degree2').evaluateAll(buttons=>buttons.forEach(el=>el.textContent='Select One Required'));
    const inspect=await execute(page,'inspect');assert.equal(JSON.stringify(inspect).includes('PRIVATE_'),false);
    assert.deepEqual(inspect.required,[{label:'Degree *',type:'combobox'},{label:'Degree *',type:'combobox'}]);
    assert.equal(await page.evaluate(()=>navigationClicks),0);
  });
});

test('Workday Bachelor of Arts aliases match only a unique education degree option and preserve existing answers',async()=>{
  const degrees=['BA','B.A.','Bachelor of Arts (B.A.)','BA','BA','BA'];
  const saved={...profile,education:degrees.map(degree=>({...profile.education[0],degree})),work_history:[{company:'Example',title:'BA'}]};
  const html=degrees.map((_,index)=>educationRow(index+1,button('degree-'+index,'Degree *',index===5?'Associates of Science (A.S)':'Select One Required'))).join('')+
    '<div role="group" aria-labelledby="work-title"><h3 id="work-title">Work Experience 1</h3>'+button('job-title','Job Title *')+'</div>';
  await fixture(flow(html,'applyFlowMyExpPage')+choices('degree-0',['Bachelor of Arts (B.A)'])+choices('degree-1',['Bachelor of Arts'])+choices('degree-2',['BA'])+
    choices('degree-3',['BA','Bachelor of Arts (B.A)'])+choices('degree-4',['Bachelor of Fine Arts (B.F.A)','Bachelor of Science (B.S)','Bachelor of Business Administration (B.B.A)','Bachelor of Arts / Science'])+
    choices('job-title',['Bachelor of Arts (B.A)'])+'<script>window.existingDegreeClicks=0;document.querySelector("#degree-5").onclick=()=>existingDegreeClicks++;</script>',async page=>{
    const report=await execute(page,'details',saved);assert.equal(report.filled.length,3);assert.equal(report.failed.length,3);
    for(const [id,value] of [['degree-0','Bachelor of Arts (B.A)'],['degree-1','Bachelor of Arts'],['degree-2','BA']]) assert.equal(await page.locator('#'+id).textContent(),value);
    for(const id of ['degree-3','degree-4','job-title']) assert.match(await page.locator('#'+id).textContent(),/^Select One/,'Ambiguous, unrelated and non-degree fields remain unselected');
    assert.equal(await page.locator('#degree-5').textContent(),'Associates of Science (A.S)');assert.equal(await page.evaluate(()=>existingDegreeClicks),0);
    assert.equal(await page.evaluate(()=>navigationClicks),0);
  });
});

test('Workday exact LinkedIn question fills the verified URL and preserves existing or unrelated answers',async()=>{
  const saved={...profile,linkedin_url:'https://www.linkedin.com/in/synthetic-profile'};
  await fixture(flow(input('linkedin-empty','Please provide your Linkedin profile:')+input('linkedin-existing','Please provide your Linkedin profile:','value="https://www.linkedin.com/in/applicant-edit"')+
    input('headline','Please provide your LinkedIn headline:')+input('other-profile','Please provide your professional profile:'),'applyFlowMyExpPage'),async page=>{
    const report=await execute(page,'details',saved);assert.equal(report.filled.length,1);assert.equal(report.filled[0].key,'linkedin_url');
    assert.equal(await page.locator('#linkedin-empty').inputValue(),saved.linkedin_url);assert.equal(await page.locator('#linkedin-existing').inputValue(),'https://www.linkedin.com/in/applicant-edit');
    assert.equal(await page.locator('#headline').inputValue(),'');assert.equal(await page.locator('#other-profile').inputValue(),'');
    assert.equal(await page.evaluate(()=>navigationClicks),0);
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

const countryCodeToken = `<div data-automation-id="selectedItemList" role="listbox"><div data-automation-id="menuItem" role="presentation">
  <div data-automation-id="selectedItem" role="option" aria-label="USA (+1), press delete to remove"><span data-automation-id="promptOption">USA (+1)</span>
  <button type="button" aria-label="Remove USA (+1)">Remove</button></div></div></div>`;
const countryCodeField = (evidence,field='countryPhoneCode') => `<div data-automation-id="formField-${field}"><label id="country-code-label">Country Phone Code *</label>
  <div data-automation-id="multiSelectContainer"><input id="phoneNumber--countryPhoneCode" aria-labelledby="country-code-label" aria-expanded="false" required>${evidence}</div></div>`;

test('Workday selected country-code tokens count as answered without filling their empty search input or national phone field',async t=>{
  for(const field of ['countryPhoneCode','phoneNumber--countryPhoneCode']) await t.test(field,async()=>{
    await fixture(flow(countryCodeField(countryCodeToken,field)+input('phone','Phone Number','','phoneNumber'))+
      '<script>window.countryClicks=0;document.querySelector("[data-automation-id=multiSelectContainer]").onclick=()=>countryClicks++;</script>',async page=>{
      assert.equal(await page.locator('[id="phoneNumber--countryPhoneCode"]').getAttribute('role'),null,'The live Workiva search input has no explicit combobox role');
      const before=await page.locator('[data-automation-id="multiSelectContainer"]').innerHTML();
      const inspect=await execute(page,'inspect');assert.equal(inspect.required.some(field=>/Country Phone Code/.test(field.label)),false);
      const details=await execute(page);assert.ok(details.skipped.some(field=>/Country Phone Code/.test(field.label)&&/existing answer was preserved/.test(field.reason)));
      assert.equal(await page.locator('[data-automation-id="multiSelectContainer"]').innerHTML(),before);
      assert.equal(await page.locator('[id="phoneNumber--countryPhoneCode"]').inputValue(),'');assert.equal(await page.locator('#phone').inputValue(),'');
      assert.equal(await page.evaluate(()=>countryClicks),0);assert.equal(await page.evaluate(()=>navigationClicks),0);
    });
  });
});

test('Workday country-code answered evidence excludes hidden tokens, announcements, open options and neighboring fields',async t=>{
  for(const [label,evidence,extra] of [
    ['announcement only','<div data-automation-id="promptAriaInstruction">1 item selected</div>',''],
    ['hidden selected item',countryCodeToken.replace('data-automation-id="selectedItem"','style="display:none" data-automation-id="selectedItem"'),''],
    ['empty prompt option',countryCodeToken.replace('>USA (+1)</span>','> </span>'),''],
    ['unselected open dropdown option','<div role="listbox"><div role="option" data-automation-id="promptOption">USA (+1)</div></div>',''],
    ['token in a neighboring field','','<div data-automation-id="formField-otherCountry">'+countryCodeToken+'</div>'],
    ['hidden selected item list',countryCodeToken.replace('data-automation-id="selectedItemList"','style="display:none" data-automation-id="selectedItemList"'),''],
  ]) await t.test(label,async()=>{
    await fixture(flow(countryCodeField(evidence)+extra),async page=>{
      const inspect=await execute(page,'inspect');assert.ok(inspect.required.some(field=>/Country Phone Code/.test(field.label)));
      assert.equal(await page.locator('[id="phoneNumber--countryPhoneCode"]').inputValue(),'');
    });
  });
  await t.test('the same tokens on another ATS do not establish a Workday answer',async()=>{
    await fixture('<form id="application-form">'+countryCodeField(countryCodeToken)+'</form>',async page=>{
      const inspect=await execute(page,'inspect');assert.ok(inspect.required.some(field=>/Country Phone Code/.test(field.label)));
    },'https://boards.greenhouse.io/fixture/jobs/123');
  });
});

const studyToken = value => `<ul data-automation-id="selectedItemList" role="listbox">${(Array.isArray(value)?value:[value]).map(text=>`<li data-automation-id="menuItem" role="presentation">
  <div data-automation-id="selectedItem" role="option"><p data-automation-id="promptOption">${text}</p><button type="button">Remove</button></div></li>`).join('')}</ul>`;
const studyPrompt = (id,evidence='',search='') => `<div data-automation-id="formField-fieldOfStudy"><label for="${id}">Field of Study *</label>
  <div data-automation-id="multiSelectContainer"><div data-automation-id="multiselectInputContainer"><input id="${id}" type="text" value="${search}" required></div>${evidence}</div></div>`;
const educationRow = (n,html) => `<div role="group" aria-labelledby="study-row-${n}"><h3 id="study-row-${n}">Education ${n}</h3>${html}</div>`;
const trackStudyEvents = '<script>window.studyEvents=0;document.querySelectorAll("[data-automation-id=multiSelectContainer]").forEach(el=>["click","input","change"].forEach(type=>el.addEventListener(type,()=>studyEvents++)));</script>';
const manualMultiSelect = 'This Workday multi-select needs a manual choice. Review it on the application page.';

test('Workday existing study tokens preserve different saved choices and both empty and nonempty native searches',async()=>{
  await fixture(flow(educationRow(1,studyPrompt('study1',studyToken('Business Administration')))+
    educationRow(2,studyPrompt('study2',studyToken(['Economics','Finance']),'Applicant draft search')),'applyFlowMyExpPage')+trackStudyEvents,async page=>{
    assert.equal(await page.locator('#study1').getAttribute('role'),null,'The live Field of Study input is a plain native text input');
    const before=await page.locator('[data-automation-id="applyFlowMyExpPage"]').innerHTML();
    const inspect=await execute(page,'inspect');
    const details=await execute(page);assert.equal(details.filled.length,0);assert.equal(details.failed.length,0);
    assert.equal(inspect.required.length,0);
    assert.equal(details.skipped.filter(field=>/Field of Study/.test(field.label)&&/existing answer was preserved/.test(field.reason)).length,2);
    assert.equal(await page.locator('[data-automation-id="applyFlowMyExpPage"]').innerHTML(),before);
    assert.equal(await page.locator('#study1').inputValue(),'');assert.equal(await page.locator('#study2').inputValue(),'Applicant draft search');
    assert.equal(await page.evaluate(()=>studyEvents),0);assert.equal(await page.evaluate(()=>navigationClicks),0);
  });
});

test('Workday empty multi-select prompts and draft searches stay manual instead of becoming typed selections',async()=>{
  await fixture(flow(educationRow(1,studyPrompt('empty-no-list'))+educationRow(2,studyPrompt('empty-list','<ul data-automation-id="selectedItemList" role="listbox"></ul>'))+
    educationRow(3,studyPrompt('draft-search','','Unselected search text')),'applyFlowMyExpPage')+trackStudyEvents,async page=>{
    const before=await page.locator('[data-automation-id="applyFlowMyExpPage"]').innerHTML();
    const inspect=await execute(page,'inspect');assert.equal(inspect.required.filter(field=>/Field of Study/.test(field.label)).length,3);
    const details=await execute(page);assert.equal(details.filled.length,0);assert.equal(details.failed.length,0);
    assert.equal(details.skipped.filter(field=>field.reason===manualMultiSelect).length,3);
    assert.equal(await page.locator('[data-automation-id="applyFlowMyExpPage"]').innerHTML(),before);
    assert.equal(await page.locator('#empty-no-list').inputValue(),'');assert.equal(await page.locator('#empty-list').inputValue(),'');
    assert.equal(await page.locator('#draft-search').inputValue(),'Unselected search text');assert.equal(await page.evaluate(()=>studyEvents),0);
  });
});

test('Workday study selections reject hidden, neighboring, ambiguous and ordinary dropdown-option evidence',async()=>{
  const token=studyToken('Computer Science');
  const cases=[
    token.replace('data-automation-id="selectedItem"','style="display:none" data-automation-id="selectedItem"'),
    `<div data-automation-id="formField-neighbor">${token}</div>`,
    '<ul role="listbox"><li role="option" data-automation-id="promptOption">Computer Science</li></ul>',
    studyToken(' '),
    token.replace('data-automation-id="selectedItemList"','style="display:none" data-automation-id="selectedItemList"'),
    token+token,
  ];
  await fixture(flow(cases.map((evidence,index)=>educationRow(index+1,studyPrompt('negative-study-'+index,evidence))).join(''),'applyFlowMyExpPage')+trackStudyEvents,async page=>{
    const before=await page.locator('[data-automation-id="applyFlowMyExpPage"]').innerHTML();
    const inspect=await execute(page,'inspect');assert.equal(inspect.required.filter(field=>/Field of Study/.test(field.label)).length,cases.length);
    const details=await execute(page);assert.equal(details.filled.length,0);assert.equal(details.failed.length,0);
    assert.equal(details.skipped.filter(field=>field.reason===manualMultiSelect).length,cases.length);
    assert.equal(await page.locator('[data-automation-id="applyFlowMyExpPage"]').innerHTML(),before);assert.equal(await page.evaluate(()=>studyEvents),0);
  });
});

const radioQuestion = (id,name,question,isRequired=true,checked='') => `<div data-automation-id="formField-${id}"><label id="${id}-label">${question}</label>
  <div id="${id}-group" aria-required="${isRequired}" aria-labelledby="${id}-label"><label><input id="${id}-yes" type="radio" style="opacity:0" name="${name}" value="yes" ${checked==='yes'?'checked':''}>Yes</label>
  <label><input id="${id}-no" type="radio" style="opacity:0" name="${name}" value="no" ${checked==='no'?'checked':''}>No</label></div></div>`;

test('Workday required Yes/No radios report their labeled question once without answering it',async()=>{
  const question='Have you worked at Workiva before? *';
  await fixture(flow(radioQuestion('candidateIsPreviousWorker','candidateIsPreviousWorker',question))+
    '<script>window.answerEvents=0;document.querySelectorAll("input[type=radio]").forEach(el=>["click","input","change"].forEach(type=>el.addEventListener(type,()=>answerEvents++)));</script>',async page=>{
    assert.equal(await page.locator('#candidateIsPreviousWorker-group').getAttribute('role'),null,'The live question container has no radiogroup role');
    assert.equal(await page.locator('#candidateIsPreviousWorker-yes').evaluate(el=>el.required),false,'Requiredness belongs to the question container');
    assert.equal(await page.locator('#candidateIsPreviousWorker-yes').evaluate(el=>getComputedStyle(el).opacity),'0','The live native input is transparent beside its visible Yes/No label');
    const inspect=await execute(page,'inspect');assert.deepEqual(inspect.required,[{label:question,type:'radio'}]);
    const details=await execute(page);assert.deepEqual(details.required,[{label:question,type:'radio'}]);
    assert.ok(details.skipped.some(field=>field.label===question&&/yourself/.test(field.reason)));
    assert.equal(await page.locator('input[type="radio"]:checked').count(),0);assert.equal(await page.evaluate(()=>answerEvents),0);
    assert.equal(await page.evaluate(()=>navigationClicks),0);
  });
});

test('Workday transparent radios require a visible question and local associated option labels',async t=>{
  const question=radioQuestion('visibility','visibility','Required question? *');
  for(const [label,html] of [
    ['hidden question text',question.replace('id="visibility-label"','style="display:none" id="visibility-label"')],
    ['hidden question container',question.replace('id="visibility-group"','style="display:none" id="visibility-group"')],
    ['hidden option labels',question.replaceAll('<label><input','<label style="opacity:0"><input')],
    ['no associated option labels',question.replaceAll('<label><input','<span><input').replaceAll('>Yes</label>','>Yes</span>').replaceAll('>No</label>','>No</span>')],
    ['inputs without layout',question.replaceAll('style="opacity:0"','style="opacity:0;display:none"')],
  ]) await t.test(label,async()=>{
    await fixture(flow(html),async page=>{
      const inspect=await execute(page,'inspect');assert.equal(inspect.required.some(field=>field.type==='radio'),false);
      assert.equal(await page.locator('input[type="radio"]:checked').count(),0);
    });
  });
});

test('Workday radio question requiredness and selected answers stay within the labeled field and native group',async t=>{
  for(const [label,html,missing] of [
    ['optional unanswered question',radioQuestion('optional','optional','Optional preference?',false),[]],
    ['required question with a manual answer',radioQuestion('answered','answered','Have you worked here? *',true,'no'),[]],
    ['same-name neighbor cannot answer another question',radioQuestion('required','shared','Required employment question? *')+radioQuestion('neighbor','shared','Optional neighboring question?',false,'yes'),['Required employment question? *']],
    ['required parent outside the field cannot make radios required','<div aria-required="true" aria-labelledby="external-label"><div id="external-label">Unrelated required question *</div><div data-automation-id="formField-optional"><label><input type="radio" name="optional" value="yes">Yes</label><label><input type="radio" name="optional" value="no">No</label></div></div>',[]],
    ['a container spanning different native names is not one required group','<div data-automation-id="formField-mixed"><div id="mixed-label">Ambiguous required question *</div><div aria-required="true" aria-labelledby="mixed-label"><label><input type="radio" name="first" value="yes">Yes</label><label><input type="radio" name="second" value="no">No</label></div></div>',[]],
  ]) await t.test(label,async()=>{
    await fixture(flow('<form>'+html+'</form>'),async page=>{
      const before=await page.locator('input[type="radio"]').evaluateAll(inputs=>inputs.map(el=>el.checked));
      const inspect=await execute(page,'inspect');assert.deepEqual(inspect.required.filter(field=>field.type==='radio').map(field=>field.label),missing);
      await execute(page);assert.deepEqual(await page.locator('input[type="radio"]').evaluateAll(inputs=>inputs.map(el=>el.checked)),before);
      assert.equal(await page.evaluate(()=>navigationClicks),0);
    });
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

test('Workday repeat attachment preserves the successful résumé after the uploader removes its input',async()=>{
  await fixture(flow(uploader,'applyFlowAutoFillPage')+`<script>window.uploadChanges=0;window.deleteClicks=0;
    document.querySelector('#resume').onchange=e=>{
      uploadChanges++;const name=e.target.files[0].name;
      document.querySelector('[data-automation-id="file-upload-drop-zone"]').remove();
      document.querySelector('#uploaded').innerHTML='<div data-automation-id="file-upload-item"><div data-automation-id="file-upload-item-name">'+name+'</div><div data-automation-id="file-upload-successful">Successfully uploaded</div><button type="button" data-automation-id="delete-file">Delete</button></div>';
      document.querySelector('[data-automation-id="delete-file"]').onclick=()=>deleteClicks++;
    };</script>`,async page=>{
    assert.equal((await execute(page,'resume')).resume.status,'accepted');
    assert.equal(await page.locator('input[type="file"]').count(),0,'Workday removes the upload input after success');
    const before=await page.locator('[data-automation-id="resumeUpload"]').innerHTML();
    const report=await execute(page,'resume');
    assert.equal(report.resume.status,'already-attached');assert.equal(report.status,'complete');
    assert.equal(await page.locator('[data-automation-id="resumeUpload"]').innerHTML(),before);
    assert.equal(await page.evaluate(()=>uploadChanges),1);assert.equal(await page.evaluate(()=>deleteClicks),0);
    assert.equal(await page.evaluate(()=>navigationClicks),0);
  });
});

test('Workday upload evidence without an input must be successful and local to the explicit résumé item',async t=>{
  const name='<div data-automation-id="file-upload-item-name">Existing.pdf</div>';
  const success='<div data-automation-id="file-upload-successful">Successfully uploaded</div>';
  const item=html=>`<div data-automation-id="file-upload-item">${html}<button type="button" data-automation-id="delete-file">Delete</button></div>`;
  for(const [label,html] of [
    ['existing different résumé is preserved',`<div data-automation-id="resumeUpload">${item(name+success)}</div>`],
    ['filename alone is insufficient',`<div data-automation-id="resumeUpload">${item(name)}</div>`],
    ['failed upload cannot reuse a stale success marker',`<div data-automation-id="resumeUpload">${item(name+success+'<div data-automation-id="file-upload-failed">Upload failed</div>')}</div>`],
    ['pending upload cannot reuse a stale success marker',`<div data-automation-id="resumeUpload">${item(name+success+'<div data-automation-id="file-loading-dots" style="width:8px;height:8px"></div>')}</div>`],
    ['another upload section is not the résumé',`<div data-automation-id="resumeUpload"><h3>Resume/CV</h3></div><div data-automation-id="additionalAttachments">${item(name+success)}</div>`],
    ['success outside the named item is insufficient',`<div data-automation-id="resumeUpload">${item(name)}${success}</div>`],
  ]) await t.test(label,async()=>{
    await fixture(flow(html,'applyFlowMyExpPage'),async page=>{
      const before=await page.locator('[data-automation-id="applyFlowMyExpPage"]').innerHTML();
      const report=await execute(page,'resume');
      assert.equal(report.resume.status,label==='existing different résumé is preserved'?'already-attached':'no-target');
      assert.equal(await page.locator('[data-automation-id="applyFlowMyExpPage"]').innerHTML(),before);
      assert.equal(await page.evaluate(()=>navigationClicks),0);
    });
  });
});

const experienceUpload = (id,title='Resume/CV',evidence='',withInput=true) => `<div role="group" aria-labelledby="${id}-section"><h4 id="${id}-section">${title}</h4>
  <div><div data-automation-id="formField-"><label id="${id}-file-label">Upload a file (5MB max)*</label><div><div>
  <div data-automation-id="attachments-FileUpload" aria-labelledby="${id}-file-label"><div><button type="button">Select files</button>${withInput?'<input type="file" data-automation-id="file-upload-input-ref" style="display:none">':''}</div>${evidence}</div>
  </div></div></div></div></div>`;
const acceptedExperienceFile = `<div data-automation-id="file-upload-item"><div data-automation-id="file-upload-item-name">SoftwareEngineer.pdf</div><span>5.5KB</span>
  <div data-automation-id="file-upload-successful">Successfully Uploaded!</div><button type="button" data-automation-id="delete-file">Delete</button></div>`;

test('Workday My Experience preserves an existing PDF inside the named Resume/CV section and generic upload field',async()=>{
  await fixture(flow(experienceUpload('resume','Resume/CV',acceptedExperienceFile),'applyFlowMyExpPage')+
    '<script>window.uploadEvents=0;document.querySelector("[data-automation-id=attachments-FileUpload]").addEventListener("change",()=>uploadEvents++);document.querySelector("[data-automation-id=delete-file]").onclick=()=>uploadEvents++;</script>',async page=>{
    const section=page.locator('[role="group"][aria-labelledby="resume-section"]');
    const before=await section.innerHTML();const report=await execute(page,'resume');assert.equal(report.resume.status,'already-attached');
    assert.equal(await section.innerHTML(),before);assert.equal(await section.locator('input[type=file]').evaluate(el=>el.files.length),0);
    await section.locator('input[type=file]').evaluate(el=>el.remove());
    assert.equal((await execute(page,'resume')).resume.status,'already-attached');
    assert.equal(await page.evaluate(()=>uploadEvents),0);assert.equal(await page.evaluate(()=>navigationClicks),0);
  });
});

test('Workday My Experience targets only the unique Resume/CV local uploader when another section already has a PDF',async()=>{
  await fixture(flow(experienceUpload('resume')+experienceUpload('supporting','Supporting Documents',acceptedExperienceFile),'applyFlowMyExpPage')+
    `<script>document.querySelector('[aria-labelledby="resume-section"] input[type=file]').onchange=e=>{
      const scope=e.target.closest('[data-automation-id="attachments-FileUpload"]');
      scope.insertAdjacentHTML('beforeend','<div data-automation-id="file-upload-item"><div data-automation-id="file-upload-item-name">'+e.target.files[0].name+'</div><div data-automation-id="file-upload-successful">Successfully Uploaded!</div></div>');};</script>`,async page=>{
    const report=await execute(page,'resume');assert.equal(report.resume.status,'accepted');
    assert.equal(await page.locator('[aria-labelledby="resume-section"] input[type=file]').evaluate(el=>el.files[0].name),'Exact.pdf');
    assert.equal(await page.locator('[aria-labelledby="supporting-section"] input[type=file]').evaluate(el=>el.files.length),0);
    assert.equal(await page.locator('[aria-labelledby="supporting-section"] [data-automation-id="file-upload-item-name"]').textContent(),'SoftwareEngineer.pdf');
  });
});

test('Workday My Experience résumé association refuses unrelated, multiple, and nonlocal upload evidence',async t=>{
  for(const [label,html] of [
    ['unrelated upload section',experienceUpload('other','Supporting Documents',acceptedExperienceFile)],
    ['two Resume/CV sections',experienceUpload('first','Resume/CV',acceptedExperienceFile)+experienceUpload('second')],
    ['competing uploaders in one section',experienceUpload('resume','Resume/CV',acceptedExperienceFile).replace('</h4>','</h4><div data-automation-id="attachments-FileUpload"><input type="file"></div>')],
    ['success elsewhere in the section',experienceUpload('resume','Resume/CV','',false).replace('</h4>','</h4>'+acceptedExperienceFile)],
  ]) await t.test(label,async()=>{
    await fixture(flow(html,'applyFlowMyExpPage'),async page=>{
      const before=await page.locator('[data-automation-id="applyFlowMyExpPage"]').innerHTML();
      const report=await execute(page,'resume');assert.equal(report.resume.status,'no-target');
      assert.equal(await page.locator('[data-automation-id="applyFlowMyExpPage"]').innerHTML(),before);
      assert.ok((await page.locator('input[type=file]').evaluateAll(inputs=>inputs.map(el=>el.files.length))).every(count=>count===0));
      assert.equal(await page.evaluate(()=>navigationClicks),0);
    });
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

const questionnaireChoice = (id,question) => `<div data-automation-id="formField-${id}"><fieldset><legend><p>${question} <span>*</span></p></legend>
  <div><div><div><button id="primaryQuestionnaire--${id}" type="button" aria-haspopup="listbox" aria-label=" Select One Required">Select One</button></div></div></div></fieldset></div>`;

test('Workday questionnaire choices use their visible direct legends while Fill and Attach remain manual',async()=>{
  const questions=['Can you meet the published role schedule?','Are you authorized to work in the listed country?','Will you require employment sponsorship?'];
  await fixture(flow(questions.map((question,index)=>questionnaireChoice('question-'+index,question)).join('')+input('manual-first','First Name')+uploader,'applyFlowPrimaryQuestionsPage')+
    '<script>window.questionClicks=0;document.querySelectorAll("button[aria-haspopup=listbox]").forEach(el=>el.onclick=()=>questionClicks++);</script>',async page=>{
    const first=page.locator('#primaryQuestionnaire--question-0');
    assert.equal(await first.getAttribute('aria-labelledby'),null);assert.equal(await first.getAttribute('aria-required'),null);
    const before=await page.locator('[data-automation-id="applyFlowPrimaryQuestionsPage"]').innerHTML();
    const report=await execute(page,'inspect');assert.deepEqual(report.required,questions.map(question=>({label:question+' *',type:'combobox'})));
    assert.equal(JSON.stringify(report).includes('Select One Required'),false);
    for(const action of ['details','resume']) assert.equal((await execute(page,action)).status,'interrupted');
    assert.equal(await page.locator('[data-automation-id="applyFlowPrimaryQuestionsPage"]').innerHTML(),before);
    assert.equal(await page.locator('#manual-first').inputValue(),'');assert.equal(await page.locator('#resume').evaluate(el=>el.files.length),0);
    assert.equal(await page.evaluate(()=>questionClicks),0);assert.equal(await page.evaluate(()=>navigationClicks),0);
  });
});

test('Workday questionnaire legend fallback refuses hidden, neighboring, nested and ambiguous questions',async()=>{
  const choice=id=>`<button id="${id}" type="button" aria-haspopup="listbox" aria-label=" Select One Required">Select One</button>`;
  const cases=[
    questionnaireChoice('hidden','Hidden question?').replace('<legend>','<legend style="display:none">'),
    '<div data-automation-id="formField-neighbor"><fieldset><legend>Neighbor question? *</legend></fieldset></div><div data-automation-id="formField-target"><fieldset>'+choice('neighbor-target')+'</fieldset></div>',
    '<div data-automation-id="formField-nested"><fieldset><legend>Outer question? *</legend><fieldset>'+choice('nested-target')+'</fieldset></fieldset></div>',
    '<div data-automation-id="formField-shared"><fieldset><legend>Ambiguous shared question? *</legend>'+choice('shared-first')+choice('shared-second')+'</fieldset></div>',
    '<div data-automation-id="formField-legends"><fieldset><legend>First question? *</legend><legend>Second question? *</legend>'+choice('two-legends')+'</fieldset></div>',
    '<fieldset><legend>Outside-field question? *</legend><div data-automation-id="formField-inner">'+choice('outside-target')+'</div></fieldset>',
  ];
  await fixture(flow(cases.join(''),'applyFlowPrimaryQuestionsPage'),async page=>{
    const report=await execute(page,'inspect');assert.deepEqual(report.required,[]);
    assert.equal(await page.evaluate(()=>navigationClicks),0);
  });
});

const transparentAgreement = (id='termsAndConditions--acceptTermsAndAgreements') => `<div data-automation-id="formField-acceptTermsAndAgreements"><div><div><div><div>
  <input id="${id}" type="checkbox" aria-required="true" style="opacity:0;width:24px;height:24px"><label for="${id}">I agree.*</label></div></div></div></div></div>`;

test('Workday transparent agreement checkbox appears as required without accepting it or changing the manual step',async()=>{
  const demographics=['Gender','Race/Ethnicity','Veteran Status'];
  await fixture(flow(demographics.map((question,index)=>questionnaireChoice('demographic-'+index,question)).join('')+transparentAgreement()+input('manual-first','First Name')+uploader,'applyFlowVoluntaryDisclosuresPage')+
    '<script>window.agreementEvents=0;document.querySelectorAll("input[type=checkbox]").forEach(el=>["click","input","change"].forEach(type=>el.addEventListener(type,()=>agreementEvents++)));</script>',async page=>{
    const agreement=page.locator('[id="termsAndConditions--acceptTermsAndAgreements"]');
    assert.equal(await agreement.evaluate(el=>el.required),false);assert.equal(await agreement.evaluate(el=>getComputedStyle(el).opacity),'0');
    const before=await page.locator('[data-automation-id="applyFlowVoluntaryDisclosuresPage"]').innerHTML();
    const report=await execute(page,'inspect');assert.deepEqual(report.required,[...demographics.map(question=>({label:question+' *',type:'combobox'})),{label:'I agree.*',type:'checkbox'}]);
    for(const action of ['details','resume']) assert.equal((await execute(page,action)).status,'interrupted');
    assert.equal(await agreement.isChecked(),false);assert.equal(await page.locator('#manual-first').inputValue(),'');assert.equal(await page.locator('#resume').evaluate(el=>el.files.length),0);
    assert.equal(await page.locator('[data-automation-id="applyFlowVoluntaryDisclosuresPage"]').innerHTML(),before);
    assert.equal(await page.evaluate(()=>agreementEvents),0);assert.equal(await page.evaluate(()=>navigationClicks),0);
  });
});

test('Workday transparent checkbox inspection rejects hidden, off-field, absent and ambiguous labels or inputs without layout',async()=>{
  const cases=[
    transparentAgreement('hidden-label').replace('<label ','<label style="display:none" '),
    '<div data-automation-id="formField-checkbox"><input id="off-field" type="checkbox" aria-required="true" style="opacity:0;width:24px;height:24px"></div><div data-automation-id="formField-other"><label for="off-field">Other field agreement *</label></div>',
    transparentAgreement('no-label').replace('<label for="no-label">I agree.*</label>',''),
    transparentAgreement('ambiguous').replace('<label for="ambiguous">','<label for="ambiguous">Second agreement *</label><label for="ambiguous">'),
    '<div data-automation-id="formField-hidden"><div style="opacity:0"><input id="hidden-parent" type="checkbox" aria-required="true" style="opacity:0;width:24px;height:24px"></div><label for="hidden-parent">Hidden parent agreement *</label></div>',
    transparentAgreement('no-layout').replace('opacity:0;','display:none;opacity:0;'),
    transparentAgreement('zero-layout').replace('width:24px;height:24px','width:0;height:0;padding:0;border:0'),
    transparentAgreement('hidden-input').replace('opacity:0;','visibility:hidden;opacity:0;'),
  ];
  await fixture(flow(cases.join(''),'applyFlowVoluntaryDisclosuresPage'),async page=>{
    assert.deepEqual((await execute(page,'inspect')).required,[]);assert.equal(await page.locator('input[type=checkbox]:checked').count(),0);
  });
  await fixture('<form id="application-form">'+transparentAgreement('generic-checkbox')+'</form>',async page=>{
    assert.deepEqual((await execute(page,'inspect')).required,[]);
  },'https://boards.greenhouse.io/fixture/jobs/123');
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

// Disclosure controls reproduce the fixed public questions and associated
// portal listbox observed read-only on Workiva, 2026-10-16. Values below are
// synthetic; tests never load or submit a real application.
const protectedChoice='I IDENTIFY AS ONE OR MORE OF THE CLASSIFICATIONS OF PROTECTED VETERANS LISTED ABOVE';
const veteranDecline='I DO NOT WISH TO SELF-IDENTIFY';
const savedDisclosures=(veteran_status='',other_demographics='')=>({...profile,disclosure_preferences:{veteran_status,other_demographics}});
const disclosureSelect=(id,question,options,extra='')=>`<div data-automation-id="formField-${id}"><label for="${id}">${question}</label><select id="${id}" ${extra}><option value="">Select One</option>${options.map((text,index)=>`<option value="choice-${index}">${text}</option>`).join('')}</select></div>`;
const disclosureRadios=(id,question,options,extra='')=>`<div data-automation-id="formField-${id}"><label id="${id}-label">${question}</label><div aria-required="true" aria-labelledby="${id}-label">${options.map((text,index)=>`<label><input id="${id}-${index}" name="${id}" type="radio" style="opacity:0" value="choice-${index}" ${extra}>${text}</label>`).join('')}</div></div>`;

test('disclosure veteran enums map exact general and protected choices without inferring protected status',async t=>{
  for(const [pref,general,protectedIndex] of [
    ['protected_veteran',1,0],['veteran',0,-1],['not_veteran',2,1],['decline',3,2],
  ]) await t.test(pref,async()=>{
    await fixture(flow(disclosureSelect('general','Please confirm your veteran status *',['Yes',protectedChoice,'I am not a veteran',veteranDecline])+
      disclosureSelect('protected','Are you a protected veteran? *',[protectedChoice,'I am not a protected veteran',veteranDecline])+
      disclosureSelect('gender','Gender *',['Female','Wish to not disclose'])+input('manual-name','First Name')+transparentAgreement(),'applyFlowVoluntaryDisclosuresPage'),async page=>{
      const result=await execute(page,'details',savedDisclosures(pref));
      assert.equal(await page.locator('#general').inputValue(),'choice-'+general);
      assert.equal(await page.locator('#protected').inputValue(),protectedIndex<0?'':'choice-'+protectedIndex);
      assert.equal(await page.locator('#gender').inputValue(),'');assert.equal(await page.locator('#manual-name').inputValue(),'');
      assert.equal(await page.locator('input[type=checkbox]').isChecked(),false);assert.equal(result.failed.length,0);
      assert.equal(await page.evaluate(()=>navigationClicks),0);
      for(const secret of ['protected_veteran',protectedChoice,veteranDecline,'choice-']) assert.equal(JSON.stringify(result).includes(secret),false,secret);
    });
  });
});

test('disclosure nondisclosure covers only named demographics and never answers unset veteran preferences',async()=>{
  const fields=[['gender','Gender','Wish to not disclose'],['pronouns','What are your pronouns?','Prefer not to say'],
    ['race','What race or ethnicity do you most closely identify with?','I do not wish to answer. (United States of America)'],
    ['ethnicity','Ethnicity','Decline to disclose'],['orientation','Sexual orientation','Prefer not to answer'],
    ['disability','Do you have a disability?','I do not want to answer'],['disability-form','Disability status','Decline to self-identify']];
  await fixture(flow(fields.map(([id,question,decline])=>disclosureSelect(id,question,['No',decline])).join('')+
    disclosureSelect('veteran','Veteran status',['Yes',veteranDecline])+input('signature','Full Name')+input('date','Date','type=date')+
    transparentAgreement(),'applyFlowSelfIdentifyPage'),async page=>{
    const result=await execute(page,'details',savedDisclosures('','decline'));
    for(const [id] of fields) assert.equal(await page.locator('#'+id).inputValue(),'choice-1',id);
    for(const id of ['veteran','signature','date']) assert.equal(await page.locator('#'+id).inputValue(),'');
    assert.equal(await page.locator('input[type=checkbox]').isChecked(),false);assert.equal(result.filled.length,fields.length);
    assert.equal(await page.evaluate(()=>navigationClicks),0);
  });
});

test('disclosure options reject subtype, spouse, negation, duplicate, disabled and value-collision bait before any events',async()=>{
  const cases=[
    ['subtype','Please confirm your veteran status',['Disabled veteran','Recently separated veteran','Armed Forces Service Medal veteran','Active duty wartime veteran']],
    ['negated','Please confirm your veteran status',['I IDENTIFY AS A VETERAN, JUST NOT A PROTECTED VETERAN','I am not a protected veteran']],
    ['qualified','Please confirm your veteran status',[protectedChoice+' (disabled veteran)']],
    ['duplicate','Are you a protected veteran?',[protectedChoice,protectedChoice]],
    ['spouse','Are you the spouse of a protected veteran?',['Yes',protectedChoice]],
    ['branch','What branch of the military did you serve in?',['Yes',protectedChoice]],
    ['disabled-question','Are you a disabled veteran?',['Yes',protectedChoice]],
  ];
  const collision=disclosureSelect('collision','Gender',['Female','Prefer not to answer']).replaceAll('value="choice-1"','value="choice-0"');
  const disabled=disclosureSelect('disabled','Gender',['Prefer not to answer']).replace('<option value="choice-0">','<option disabled value="choice-0">');
  await fixture(flow(cases.map(([id,q,opts])=>disclosureSelect(id,q,opts)).join('')+collision+disabled+
    disclosureSelect('protected-only-no','Are you a veteran?',['Not a protected veteran'])+
    disclosureSelect('disability-no','Do you have a disability?',['No','No disability']), 'applyFlowVoluntaryDisclosuresPage')+
    '<script>window.choiceEvents=0;document.querySelectorAll("select").forEach(el=>{el.oninput=el.onchange=()=>choiceEvents++});</script>',async page=>{
    const result=await execute(page,'details',savedDisclosures('protected_veteran','decline'));
    assert.equal(result.filled.length,0);assert.equal(await page.evaluate(()=>choiceEvents),0);
    assert.deepEqual(await page.locator('select').evaluateAll(els=>els.map(el=>el.value)),Array(cases.length+4).fill(''));
    assert.equal(await page.evaluate(()=>navigationClicks),0);
  });
  await fixture(flow(disclosureSelect('general','Are you a veteran?',['Not a protected veteran']),'applyFlowPrimaryQuestionsPage'),async page=>{
    assert.equal((await execute(page,'details',savedDisclosures('not_veteran'))).filled.length,0);
    assert.equal(await page.locator('#general').inputValue(),'');
  });
});

test('disclosure existing selections and native multiple selections are preserved without events',async()=>{
  const existing=disclosureSelect('existing','Gender',['Male','Wish to not disclose']).replace('value="choice-0"','selected value="choice-0"');
  const multiple=disclosureSelect('multiple','Race',['Existing A','Existing B','Prefer not to answer'],'multiple').replace('value="choice-0"','selected value="choice-0"').replace('value="choice-1"','selected value="choice-1"');
  await fixture(flow(existing+multiple+button('existing-button','Please confirm your veteran status', 'PRIVATE_EXISTING_CHOICE')+
    disclosureRadios('existing-radio','Are you a veteran?',['Yes','No']).replace('id="existing-radio-1"','checked id="existing-radio-1"'),'applyFlowVoluntaryDisclosuresPage')+
    '<script>window.choiceEvents=0;document.querySelectorAll("select,input,button[aria-haspopup]").forEach(el=>{el.oninput=el.onchange=el.onclick=()=>choiceEvents++});</script>',async page=>{
    const before=await page.locator('[data-automation-id="applyFlowVoluntaryDisclosuresPage"]').innerHTML();
    const result=await execute(page,'details',savedDisclosures('protected_veteran','decline'));
    assert.equal(result.filled.length,0);assert.equal(await page.evaluate(()=>choiceEvents),0);
    assert.equal(await page.locator('[data-automation-id="applyFlowVoluntaryDisclosuresPage"]').innerHTML(),before);
    assert.equal(JSON.stringify(result).includes('PRIVATE_EXISTING_CHOICE'),false);
    assert.deepEqual(await page.locator('#multiple').evaluate(el=>[...el.selectedOptions].map(o=>o.value)),['choice-0','choice-1']);
  });
});

test('disclosure Workiva button portal options become associated only after opening and match fixed labels',async()=>{
  await fixture(flow(questionnaireChoice('veteran','Please confirm your veteran status')+questionnaireChoice('race','What race or ethnicity do you most closely identify with?')+
    questionnaireChoice('gender','Gender'),'applyFlowVoluntaryDisclosuresPage')+choices('primaryQuestionnaire--veteran',['Yes',protectedChoice],150)+
    choices('primaryQuestionnaire--race',['Another choice','I do not wish to answer. (United States of America)'],150)+choices('primaryQuestionnaire--gender',['Female','Wish to not disclose'],150),async page=>{
    assert.equal(await page.locator('#primaryQuestionnaire--veteran').getAttribute('aria-controls'),null);
    const result=await execute(page,'details',savedDisclosures('protected_veteran','decline'));
    assert.equal(result.filled.length,3);assert.equal(result.failed.length,0);
    assert.equal(await page.locator('#primaryQuestionnaire--veteran').textContent(),protectedChoice);assert.equal(await page.locator('#primaryQuestionnaire--gender').textContent(),'Wish to not disclose');
    assert.equal(JSON.stringify(result).includes(protectedChoice),false);
    assert.equal((await execute(page,'details',savedDisclosures('decline','decline'))).filled.length,0,'repeat preserves the first answers');
    assert.equal(await page.evaluate(()=>navigationClicks),0);
  });
});

test('disclosure native radio groups use only their fixed question and unique visible associated choices',async t=>{
  for(const [pref,options,index] of [['protected_veteran',['Yes',protectedChoice],1],['veteran',['Yes','No'],0],['not_veteran',['Yes','No'],1],['decline',['Yes',veteranDecline],1]]) await t.test(pref,async()=>{
    await fixture(flow(disclosureRadios('veteran','Please confirm your veteran status *',options)+
      disclosureRadios('gender','Gender *',['Female','Wish to not disclose'])+transparentAgreement(),'applyFlowVoluntaryDisclosuresPage'),async page=>{
      const result=await execute(page,'details',savedDisclosures(pref,'decline'));
      assert.equal(await page.locator('#veteran-'+index).isChecked(),true);assert.equal(await page.locator('#gender-1').isChecked(),true);
      assert.equal(await page.locator('input[type=checkbox]').isChecked(),false);assert.equal(result.filled.length,2);
      assert.deepEqual(result.filled.map(item=>item.label),['Please confirm your veteran status *','Gender *']);
      assert.equal(await page.evaluate(()=>navigationClicks),0);
    });
  });
});

test('disclosure radios reject hidden, off-field, ambiguous and unverified option-only questions',async()=>{
  const html=disclosureRadios('duplicate','Gender',['Prefer not to answer','Prefer not to answer'])+
    disclosureRadios('hidden','Gender',['Female','Prefer not to answer']).replace('<label><input id="hidden-1"','<label style="display:none"><input id="hidden-1"')+
    disclosureRadios('hidden-question','Gender',['Prefer not to answer']).replace('<label id="hidden-question-label"','<label style="display:none" id="hidden-question-label"')+
    '<div data-automation-id="formField-unverified"><label><input type="radio" id="unverified" name="unverified">Veteran status</label><label><input type="radio" name="unverified">Prefer not to answer</label></div>'+
    disclosureRadios('collision-radio','Gender',['Prefer not to answer'])+'<div data-automation-id="formField-other-radio"><label><input id="neighbor-answer" type="radio" name="collision-radio" checked>Applicant answer</label></div>'+
    '<div data-automation-id="formField-off"><label id="off-label">Gender</label><div aria-required="true" aria-labelledby="off-label"><input id="off" type="radio" name="off"></div></div><div data-automation-id="formField-neighbor"><label for="off">Prefer not to answer</label></div>';
  await fixture(flow(html,'applyFlowVoluntaryDisclosuresPage'),async page=>{
    const result=await execute(page,'details',savedDisclosures('decline','decline'));
    assert.equal(result.filled.length,0);assert.equal(await page.locator('input:checked').count(),1);
    assert.equal(await page.locator('#neighbor-answer').isChecked(),true,'same-name native questions cannot uncheck a neighboring answer');
  });
});

test('disclosure delayed menu stops if the fixed question or same-URL step changes',async t=>{
  for(const change of ['question','step']) await t.test(change,async()=>{
    await fixture(flow(button('gender','Gender *'),'applyFlowVoluntaryDisclosuresPage')+choices('gender',['Wish to not disclose'],350),async page=>{
      const running=execute(page,'details',savedDisclosures('','decline'));await page.waitForSelector('#gender[aria-expanded=true]');
      await page.evaluate(change=>{
        if(change==='question') document.querySelector('#gender-label').textContent='Are you authorized to work?';
        else document.querySelector('[data-automation-id="applyFlowVoluntaryDisclosuresPage"]').outerHTML='<div data-automation-id="applyFlowSelfIdentifyPage"><label>Full name<input id="next-name"></label></div>';
      },change);
      const result=await running;assert.equal(result.status,'interrupted');assert.equal(result.filled.length,0);
      if(change==='question') assert.equal(await page.locator('#gender').textContent(),'Select One');
      else assert.equal(await page.locator('#next-name').inputValue(),'');
      assert.equal(await page.evaluate(()=>navigationClicks),0);
    });
  });
});
