import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { before, after, test } from "node:test";
import { chromium } from "@playwright/test";
import { captureCurrentJob, runCurrentAction } from "../actions.js";
import { sanitizeRun } from "../diagnostics.js";

const script = await readFile(new URL("../content.js", import.meta.url), "utf8");
let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

const profile = {
  first_name: "Alex", last_name: "Example", email: "alex@example.org",
  phone: "555-010-1200", city: "Austin", state: "TX", country: "United States",
  work_authorized: true, requires_sponsorship: false,
  linkedin_url: "https://www.linkedin.com/in/example", github_url: "https://github.com/example",
  portfolio_url: "https://example.org",
};
const resume = {
  metadata: { id: "test-resume", name: "Alex-Resume.pdf", type: "application/pdf", size: 26 },
  dataUrl: "data:application/pdf;base64," + Buffer.from("%PDF-1.4\nfixture resume\n%%EOF").toString("base64"),
};

async function fixture(html, run) {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.route("**/*", route => {
    if (route.request().isNavigationRequest()) return route.fulfill({ contentType: "text/html", body: html });
    return route.abort();
  });
  await page.goto("https://boards.greenhouse.io/example/jobs/123");
  await page.addScriptTag({ content: script });
  try { return await run(page); } finally { await context.close(); }
}
const execute = (page, action, extra = {}) => page.evaluate(
  options => globalThis.__jobpilotRun(options), { action, profile, ...extra },
);

test("details fills verified contact fields and preserves manual and unsafe answers", async () => {
  await fixture(`<form>
    <label>First name <input name="first_name" required></label>
    <label>Email <input type="email" name="email" value="manual@example.org" required></label>
    <label>Desired salary <input name="desired_salary" required></label>
    <label>Gender <select name="gender" required><option value="">Choose</option><option>Female</option></select></label>
    <label>Agree to terms <input name="consent" type="checkbox" required></label>
    <label>Authorized to work in the United States <select name="work_auth" required><option value="">Choose</option><option>Yes</option><option>No</option></select></label>
  </form>`, async page => {
    const result = await execute(page, "details");
    assert.equal(await page.locator('[name="first_name"]').inputValue(), "Alex");
    assert.equal(await page.locator('[name="email"]').inputValue(), "manual@example.org");
    assert.equal(await page.locator('[name="desired_salary"]').inputValue(), "");
    assert.equal(await page.locator('[name="gender"]').inputValue(), "");
    assert.equal(await page.locator('[name="consent"]').isChecked(), false);
    assert.equal(await page.locator('[name="work_auth"]').inputValue(), "Yes");
    assert.equal(result.filled.length, 2);
    assert.equal(result.resume.status, "not-requested");
    assert.ok(result.required.some(item => /salary/i.test(item.label)));
    assert.ok(result.required.some(item => /gender/i.test(item.label)));
    assert.ok(result.required.some(item => /terms/i.test(item.label)));
  });
});

test("inspect reports missing radio groups, checkboxes, native and React controls without claiming fills", async () => {
  await fixture(`<form>
    <label>Salary expectations <input name="salary" required></label>
    <label>Gender <select name="gender" required><option value="">Choose</option><option>Female</option></select></label>
    <fieldset><legend>Citizenship *</legend><label><input type="radio" name="citizenship" value="yes" required>Yes</label><label><input type="radio" name="citizenship" value="no" required>No</label></fieldset>
    <label>Privacy consent <input type="checkbox" name="privacy" required></label>
    <div class="field"><label for="veteran">Veteran status *</label><div class="react-select__control"><input id="veteran" role="combobox" aria-required="true" aria-expanded="false"></div></div>
  </form>`, async page => {
    const result = await execute(page, "inspect");
    assert.equal(result.filled.length, 0);
    assert.ok(result.required.some(item => /salary/i.test(item.label)));
    assert.ok(result.required.some(item => /gender/i.test(item.label)));
    assert.equal(result.required.filter(item => item.type === "radio").length, 1);
    assert.ok(result.required.some(item => /privacy/i.test(item.label)));
    assert.ok(result.required.some(item => /veteran/i.test(item.label)));
    assert.equal(await page.locator('[name="privacy"]').isChecked(), false);
  });
});

test("unknown authorization remains unanswered instead of defaulting to yes", async () => {
  await fixture(`<label>Authorized to work in the United States <select name="auth" required><option value="">Choose</option><option>Yes</option><option>No</option></select></label>`, async page => {
    const result = await execute(page, "details", { profile: { ...profile, work_authorized: null } });
    assert.equal(await page.locator("select").inputValue(), "");
    assert.equal(result.filled.length, 0);
    assert.equal(result.required.length, 1);
  });
});

test("US work authorization does not answer a different country's question", async () => {
  await fixture(`<label>Authorized to work in Canada <select name="auth" required><option value="">Choose</option><option>Yes</option><option>No</option></select></label>`, async page => {
    const result = await execute(page, "details");
    assert.equal(await page.locator("select").inputValue(), "");
    assert.equal(result.filled.length, 0);
    assert.equal(result.required.length, 1);
  });
});

test("country-mismatched and country-unknown authorization stays manual", async () => {
  for (const [country, question] of [["Canada", "Authorized to work in the United States"], ["", "Authorized to work"]]) {
    await fixture(`<label>${question}<select name="auth" required><option value="">Choose</option><option>Yes</option><option>No</option></select></label>`, async page => {
      const result = await execute(page, "details", { profile: { ...profile, country } });
      assert.equal(await page.locator("select").inputValue(), "");
      assert.equal(result.filled.length, 0);
    });
  }
});

test("resume assignment is prepared, not employer acceptance, and leaves contact fields alone", async () => {
  await fixture(`<label>First name <input name="first_name"></label><div class="field"><label for="resume">Resume</label><input type="file" id="resume" name="resume"></div>`, async page => {
    const result = await execute(page, "resume", { resume });
    assert.equal(result.resume.status, "prepared");
    assert.equal(await page.locator('[name="first_name"]').inputValue(), "");
    assert.equal(await page.locator('[type="file"]').evaluate(input => input.files[0]?.name), "Alex-Resume.pdf");
  });
});

test("resume acceptance requires rendered attachment evidence", async () => {
  await fixture(`<div class="field"><label for="resume">Resume</label><input type="file" id="resume" name="resume"><div id="attachment"></div></div>
    <script>document.querySelector('#resume').addEventListener('change', event => {
      document.querySelector('#attachment').textContent = event.target.files[0].name;
      const button = document.createElement('button'); button.type = 'button'; button.textContent = 'Remove resume'; document.querySelector('#attachment').append(button);
    });</script>`, async page => {
    const result = await execute(page, "resume", { resume });
    assert.equal(result.resume.status, "accepted");
  });
});

test("an upload error defeats attachment appearance", async () => {
  await fixture(`<div class="field"><label for="resume">Resume</label><input type="file" id="resume" name="resume"><div id="attachment"></div></div>
    <script>document.querySelector('#resume').addEventListener('change', event => {
      document.querySelector('#attachment').innerHTML = '<span>Alex-Resume.pdf</span><button type="button">Remove resume</button><div role="alert" class="error">Upload failed: invalid file</div>';
    });</script>`, async page => {
    const result = await execute(page, "resume", { resume });
    assert.equal(result.resume.status, "failed");
  });
});

test("a lone unrelated upload is never used as the resume target", async () => {
  await fixture(`<label>Cover letter <input type="file" name="cover_letter"></label>`, async page => {
    const result = await execute(page, "resume", { resume });
    assert.equal(result.resume.status, "no-target");
    assert.equal(await page.locator('[type="file"]').evaluate(input => input.files.length), 0);
  });
});

test("a preexisting resume file is preserved", async () => {
  await fixture(`<label>Resume <input type="file" name="resume"></label>`, async page => {
    await page.locator('[type="file"]').setInputFiles({ name: "Manual-Resume.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-fixture") });
    const result = await execute(page, "resume", { resume });
    assert.equal(result.resume.status, "already-attached");
    assert.equal(await page.locator('[type="file"]').evaluate(input => input.files[0].name), "Manual-Resume.pdf");
  });
});

test("numbered history sections use verified row numbers, preserve manual titles, and skip ambiguous fields", async () => {
  const saved = { ...profile, work_history: [
    { company: "Current Co", title: "Current title", start_date: "2025-01", end_date: "2026-09", current: true },
    { company: "Previous Co", title: "Previous title", start_date: "2022-03", end_date: "2024-12", current: false },
  ], education: [{ school: "Example School", degree: "Certificate", field: "Computer Science", start_date: "2020", end_date: "2021" }] };
  await fixture(`<label>Company <input id="ambiguous-company"></label><label>Start date <input id="ambiguous-date"></label>
    <fieldset><legend>Work experience 2</legend><label>Company <input id="company-2"></label><label>Job title <input id="title-2" value="Manual title"></label><label>Start date <input type="month" id="start-2"></label><label>End date <input type="month" id="end-2"></label></fieldset>
    <fieldset><legend>Work experience 1</legend><label>Company <input id="company-1"></label><label>Job title <input id="title-1"></label><label>Start date <input type="month" id="start-1"></label><label>End date <input type="month" id="end-1"></label><label>Employer email <input id="employer-email"></label><label>City <input id="employer-city"></label></fieldset>
    <fieldset><legend>Education 1</legend><label>School <input id="school-1"></label><label>Degree <input id="degree-1"></label><label>Field of study <input id="field-1"></label><label>Email <input id="school-email"></label></fieldset>`, async page => {
    await execute(page, "details", { profile: saved });
    for (const [id, value] of [
      ["company-2", "Previous Co"], ["start-2", "2022-03"], ["end-2", "2024-12"], ["title-2", "Manual title"],
      ["company-1", "Current Co"], ["title-1", "Current title"], ["start-1", "2025-01"], ["end-1", ""],
      ["school-1", "Example School"], ["degree-1", "Certificate"], ["field-1", "Computer Science"],
      ["employer-email", ""], ["employer-city", ""], ["school-email", ""],
      ["ambiguous-company", ""], ["ambiguous-date", ""],
    ]) assert.equal(await page.locator("#" + id).inputValue(), value, id);
  });
});

test("job capture reads JSON-LD as data and does not execute description HTML", async () => {
  await fixture(`<h1>Fallback heading</h1>
    <script type="application/ld+json">not valid json</script>
    <script type="application/ld+json">{"@graph":[{"@type":["JobPosting"],"title":"Software Engineer","hiringOrganization":{"name":"Example Co"},"description":"<p>Build useful software.</p><img src=x onerror='globalThis.executed=true'>","identifier":{"value":"REQ-123"}}]}</script>`, async page => {
    const previous = globalThis.chrome;
    globalThis.chrome = {
      tabs: { query: async () => [{ id: 1, url: page.url(), title: "Job" }] },
      scripting: { executeScript: async request => [{ frameId: 0, result: await page.evaluate(
        source => (0, eval)("(" + source + ")")(), request.func.toString(),
      ) }] },
    };
    try {
      const result = await captureCurrentJob();
      assert.equal(result.title, "Software Engineer");
      assert.equal(result.company, "Example Co");
      assert.equal(result.request_id, "REQ-123");
      assert.ok(result.description.includes("Build useful software."));
      assert.equal(await page.evaluate(() => globalThis.executed), undefined);
    } finally { globalThis.chrome = previous; }
  });
});

test('Ashby capture gets the company from its page title when application metadata is absent', async () => {
  await fixture('<title>Platform Engineer @ Example Company</title><h1>Platform Engineer</h1>', async page => {
    // Same DOM as an Ashby application; no JSON-LD JobPosting is present.
    await page.route('https://jobs.ashbyhq.com/**', route=>route.fulfill({contentType:'text/html',body:'<title>Platform Engineer @ Example Company</title><h1>Platform Engineer</h1>'}));
    await page.goto('https://jobs.ashbyhq.com/example/req-123/application');
    const previous=globalThis.chrome;
    globalThis.chrome={tabs:{query:async()=>[{id:1,url:page.url()}]},scripting:{executeScript:async request=>[
      {frameId:0,result:await page.evaluate(source=>(0,eval)('('+source+')')(),request.func.toString())}
    ]}};
    try {
      const captured=await captureCurrentJob();
      assert.equal(captured.title,'Platform Engineer');
      assert.equal(captured.company,'Example Company');
    } finally {globalThis.chrome=previous;}
  });
});

test("the real action callbacks fill a modern Greenhouse form and independently confirm its nested resume", async () => {
  await fixture(`<form id="application-form">
    <label>First Name *<input id="first_name" value="Applicant correction" required></label>
    <label>Last Name *<input id="last_name" required></label>
    <label>Email *<input id="email" type="email" required></label>
    <label>Country *<select id="country" required><option value="">Select...</option><option>USA</option></select></label>
    <div class="field-wrapper"><label id="candidate-location-label" for="candidate-location">Location (City)*</label>
      <div class="select__control"><input id="candidate-location" role="combobox" aria-labelledby="candidate-location-label" aria-controls="places" aria-required="true"><span class="select__single-value"></span></div>
      <div id="places" role="listbox"></div></div>
    <label>Are you currently located in the United States? *<select id="located" required><option value="">Select...</option><option>Yes</option><option>No</option></select></label>
    <label>What is your current location: City, State *<input id="current-location" required></label>
    <label>Will you now or in the future require sponsorship (i.e. H1b, OPT, H4, F1, etc.) to legally work in the United States *<select id="sponsorship" required><option value="">Select...</option><option>Yes</option><option>No</option></select></label>
    <div class="file-upload"><div id="upload-label-resume">Resume/CV *</div><div class="file-upload__wrapper"><div class="button-container"><div class="secondary-button"><label for="resume">Attach</label><div><input id="resume" type="file"></div></div></div><p>Accepted file types: pdf, doc, docx, txt, rtf</p><div id="attachment"></div></div></div>
    <script>
      document.querySelector('#country').addEventListener('change', () => { const input=document.querySelector('#last_name'); input.replaceWith(input.cloneNode(true)); });
      document.querySelector('#candidate-location').addEventListener('input', () => setTimeout(() => { document.querySelector('#places').innerHTML='<div role="option">Austin Gardens Colonia, Texas, United States</div><div role="option">Austin, Texas, United States</div>'; },200));
      document.querySelector('#places').addEventListener('click', event => { document.querySelector('.select__single-value').textContent=event.target.textContent; document.querySelector('#candidate-location').value=''; });
      document.querySelector('#resume').addEventListener('change', event => { document.querySelector('#attachment').textContent=event.target.files[0].name; });
    </script></form>`, async page => {
    const previous=globalThis.chrome;
    const payloads=[];
    globalThis.chrome={tabs:{query:async()=>[{id:1,url:page.url()}]},scripting:{executeScript:async request=>{
      if(request.files) { await page.addScriptTag({content:script}); return [{frameId:0,documentId:'fixture-document'}]; }
      payloads.push(request.args?.[0]);
      return [{frameId:0,documentId:'fixture-document',result:await page.evaluate(({source,args})=>(0,eval)('('+source+')')(...(args||[])),{source:request.func.toString(),args:request.args})}];
    }}};
    try {
      const details=await runCurrentAction('details',profile,null,{expectedUrl:page.url()});
      assert.equal(details.status,'complete');
      assert.equal(await page.locator('#first_name').inputValue(),'Applicant correction');
      assert.equal(await page.locator('#last_name').inputValue(),'Example');
      assert.equal(await page.locator('.select__single-value').textContent(),'Austin, Texas, United States');
      assert.equal(await page.locator('#current-location').inputValue(),'Austin, TX');
      assert.equal(await page.locator('#located').inputValue(),'Yes');
      assert.equal(await page.locator('#sponsorship').inputValue(),'No');
      assert.ok(details.frames[0].required.some(item=>/resume/i.test(item.label)));
      const upload=await runCurrentAction('resume',profile,resume,{expectedUrl:page.url()});
      assert.equal(upload.frames[0].resume.status,'accepted');
      assert.equal(upload.frames[0].required.length,0);
      assert.ok(payloads.filter(p=>p?.action==='resume').every(p=>Object.keys(p.profile).length===0));
    } finally {globalThis.chrome=previous;}
  });
});

test('disclosure opt-in supports native select and bounded native radios on ordinary providers without clicking agreements',async()=>{
  await fixture(`<form id="application-form"><label>First name<input id="first_name"></label>
    <label for="gender">Gender *</label><select id="gender"><option value="">Choose</option><option value="female">Female</option><option value="private-decline-value">Wish to not disclose</option></select>
    <fieldset><legend>Are you a veteran? *</legend><label><input id="veteran-yes" type="radio" name="military">Yes</label><label><input id="veteran-no" type="radio" name="military">No</label></fieldset>
    <label>Signature<input id="signature"></label><label><input id="agreement" type="checkbox">I agree</label></form>`,async page=>{
    const result=await execute(page,'details',{profile:{...profile,disclosure_preferences:{veteran_status:'veteran',other_demographics:'decline'}}});
    assert.equal(await page.locator('#first_name').inputValue(),profile.first_name);
    assert.equal(await page.locator('#gender').inputValue(),'private-decline-value');assert.equal(await page.locator('#veteran-yes').isChecked(),true);
    assert.equal(await page.locator('#signature').inputValue(),'');assert.equal(await page.locator('#agreement').isChecked(),false);
    assert.equal(result.filled.length,3);assert.equal(JSON.stringify(result).includes('private-decline-value'),false);
  });
});

test('disclosure malformed preferences disable the whole object and opt-out leaves ordinary-provider questions unchanged',async()=>{
  await fixture(`<form id="application-form"><label for="gender">Gender</label><select id="gender"><option value="">Choose</option><option value="decline">Wish to not disclose</option></select>
    <label for="veteran">Veteran status</label><select id="veteran"><option value="">Choose</option><option value="yes">Yes</option></select></form>
    <script>window.disclosureEvents=0;document.querySelectorAll('select').forEach(el=>el.oninput=el.onchange=()=>disclosureEvents++);</script>`,async page=>{
    for(const preferences of [undefined,null,[],false,'decline',{}, {veteran_status:'',other_demographics:''},
      {veteran_status:'veteran',other_demographics:'unexpected'}, {veteran_status:'unexpected',other_demographics:'decline'},
      {veteran_status:null,other_demographics:'decline'},{veteran_status:'veteran',other_demographics:null}]) {
      const result=await execute(page,'details',{profile:{disclosure_preferences:preferences}});
      assert.equal(result.filled.length,0,JSON.stringify(preferences));
      assert.equal(await page.locator('#gender').inputValue(),'');assert.equal(await page.locator('#veteran').inputValue(),'');
    }
    assert.equal(await page.evaluate(()=>disclosureEvents),0);
  });
});

test('disclosure associated comboboxes select only their own menu and preserve existing dropdown searches',async()=>{
  const combo=(id,question,value='')=>`<div class="field"><label for="${id}">${question}</label><div class="select__control" id="${id}-control"><input id="${id}" role="combobox" aria-expanded="false" value="${value}"><span class="select__single-value"></span></div></div>`;
  await fixture('<form id="application-form">'+combo('gender','Gender')+combo('search','Race','Applicant search')+
    combo('unassociated','Disability status')+'</form><div id="unrelated-list" role="listbox"><div role="option">Prefer not to answer</div></div>'+`<script>
    window.searchClicks=0;document.querySelector('#search-control').onmousedown=()=>searchClicks++;
    document.querySelector('#gender-control').onmousedown=()=>{
      const input=document.querySelector('#gender');input.setAttribute('aria-expanded','true');input.setAttribute('aria-controls','gender-list');
      if(document.querySelector('#gender-list'))return;const list=document.createElement('div');list.id='gender-list';list.setAttribute('role','listbox');list.innerHTML='<div role="option">Wish to not disclose</div>';
      list.onclick=()=>{document.querySelector('#gender-control .select__single-value').textContent='Wish to not disclose';input.value='';input.setAttribute('aria-expanded','false');list.remove();};document.body.append(list);
    };</script>`,async page=>{
    const result=await execute(page,'details',{profile:{disclosure_preferences:{other_demographics:'decline'}}});
    assert.equal(result.filled.length,1);assert.equal(await page.locator('#gender-control .select__single-value').textContent(),'Wish to not disclose');
    assert.equal(await page.locator('#search').inputValue(),'Applicant search');assert.equal(await page.evaluate(()=>searchClicks),0);
    assert.equal(await page.locator('#unassociated').inputValue(),'');assert.equal(await page.locator('#unrelated-list').isVisible(),true);
    assert.ok(result.skipped.some(row=>row.label==='Disability status'&&/No unique offered choice/.test(row.reason)));
  });
});

test('disclosure classification ignores technical names, answer-bearing aria labels, unrelated questions and role-only radios',async()=>{
  await fixture(`<form id="application-form"><select name="gender" aria-label="Gender"><option value="">Choose</option><option>Prefer not to answer</option></select>
    <label for="other">Are you the spouse of a veteran?</label><select id="other"><option value="">Choose</option><option>Yes</option></select>
    <label><input id="text" name="veteran_status">Veteran status</label>
    <div role="radiogroup" aria-label="Gender"><div role="radio" aria-checked="false">Prefer not to answer</div></div>
    <label><input id="agreement" type="checkbox">Prefer not to answer demographic questions</label></form>`,async page=>{
    const result=await execute(page,'details',{profile:{disclosure_preferences:{veteran_status:'veteran',other_demographics:'decline'}}});
    assert.equal(result.filled.length,0);assert.equal(await page.locator('select').first().inputValue(),'');
    assert.equal(await page.locator('#other').inputValue(),'');assert.equal(await page.locator('#text').inputValue(),'');
    assert.equal(await page.locator('[role=radio]').getAttribute('aria-checked'),'false');assert.equal(await page.locator('#agreement').isChecked(),false);
  });
});

test('disclosure diagnostic reasons stay manual and never retain saved preferences or offered choices',()=>{
  const report={status:'complete',pageUrl:'https://boards.greenhouse.io/example/jobs/123',frames:[{status:'complete',
    skipped:[{label:'Gender',reason:'No saved disclosure choice applies to this question. Answer it yourself.'},
      {label:'Veteran status',reason:'No unique offered choice matches your saved disclosure preference. Review this question yourself.'}],
    filled:[{label:'Gender',key:'disclosure_preferences.other_demographics',value:'PRIVATE_DECLINE_CHOICE'}]}]};
  const saved=sanitizeRun({action:'details',profile:{disclosure_preferences:{veteran_status:'protected_veteran',other_demographics:'decline'}},report});
  assert.equal(saved.health,'manual');assert.deepEqual(saved.frames[0].skipped.map(row=>row.reason),['disclosure_manual','disclosure_choice']);
  for(const secret of ['disclosure_preferences','protected_veteran','other_demographics','PRIVATE_DECLINE_CHOICE']) assert.equal(JSON.stringify(saved).includes(secret),false);
});
