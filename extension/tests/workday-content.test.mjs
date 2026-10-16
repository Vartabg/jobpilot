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
