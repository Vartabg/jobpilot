import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {before, after, test} from 'node:test';
import {chromium} from '@playwright/test';
import {captureCurrentJob, runCurrentAction, isSupportedPage, providerFor} from '../actions.js';
import {normalizeJobUrl, workdayJob} from '../model.js';

// Public anatomy observed anonymously on 2026-10-15 at UPenn's candidate site:
// jobPostingHeader/Description, JobPosting JSON-LD, adventureButton, and the
// wd-popup-frame application chooser (applyManually/autofillWithResume).
// Fixtures below are synthetic; no account, private application, or real data.
const standard = 'https://fixture.wd5.myworkdayjobs.com/en-US/External/job/Austin/Customer-Engineer_R12345';
const recruiting = 'https://wd1.myworkdaysite.com/en-US/recruiting/fixture/External/job/Austin/Customer-Engineer_R12345';
const profile = {first_name: 'Alex', last_name: 'Example', email: 'alex@example.test', phone: '+1 512 555 0100'};
const resume = {metadata: {name: 'Synthetic.pdf'}, dataUrl: 'data:application/pdf;base64,JVBERi0xLjQK'};
const content = await readFile(new URL('../content.js', import.meta.url), 'utf8');
const posting = `<header><h1 data-automation-id="headerTitle">Fixture Careers</h1><button data-automation-id="utilityButtonSignIn">Sign In</button></header>
  <main data-automation-id="jobPostingPage"><h2 data-automation-id="jobPostingHeader">Customer engineer</h2>
  <div data-automation-id="requisitionId">R12345</div><div data-automation-id="jobPostingDescription">Solve customer problems with real systems.</div>
  <a data-automation-id="adventureButton" href="#apply">Apply</a></main>`;
// Root/step automation IDs are present in Workday's public apply-flow bundle;
// the synthetic personal fields do not claim to reproduce an authenticated account.
function step(body, marker = 'applyFlowMyInfoPage', active = 'My Information') {
  return `<main data-automation-id="applyFlowPage"><h1 data-automation-id="jobTitleHeading">Customer engineer</h1>
    <ol data-automation-id="progressBar"><li data-automation-id="progressBarActiveStep" aria-current="step">${active}</li></ol>
    <section data-automation-id="${marker}"><h2>${active}</h2>${body}</section>
    <button type="button" data-automation-id="bottom-navigation-next-button" onclick="document.body.dataset.advanced='true'">Save and Continue</button>
    <button type="button" data-automation-id="bottom-navigation-submit-button" onclick="document.body.dataset.submitted='true'">Submit</button></main>`;
}
const information = `<div data-automation-id="formField-legalNameSection_firstName"><label for="first-name">Given Name</label><input id="first-name" data-automation-id="legalNameSection_firstName" aria-required="true"></div>
  <div data-automation-id="formField-legalNameSection_lastName"><label for="last-name">Family Name</label><input id="last-name" data-automation-id="legalNameSection_lastName" aria-required="true"></div>
  <div data-automation-id="formField-email"><label for="email">Email Address</label><input id="email" type="email" data-automation-id="email" aria-required="true"></div>
  <label>Preferred Name<input id="preferred-name" value="Already chosen"></label>
  <label>Gender<input id="gender"></label><label><input id="consent" type="checkbox">I consent</label>`;
const upload = `<div data-automation-id="resumeUpload"><label id="resume-label" for="resume">Resume/CV</label>
  <div data-automation-id="file-upload-drop-zone"><input id="resume" type="file" data-automation-id="file-upload-input-ref" aria-labelledby="resume-label"></div>
  <div id="attachment" data-automation-id="file-upload-item-name"></div><div id="upload-success" hidden data-automation-id="file-upload-successful">Uploaded</div></div>
  <script>document.querySelector('#resume').addEventListener('change', event => {document.querySelector('#attachment').textContent=event.target.files[0]?.name || '';document.querySelector('#upload-success').hidden=false;});</script>`;
let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser?.close(); });

async function fixture({url = standard, html = posting, bodyForUrl, beforeScript, afterScript} = {}, run) {
  const context = await browser.newContext(), page = await context.newPage(), calls = [];
  let documentNumber = 0, active = page;
  page.on('framenavigated', frame => { if (frame === page.mainFrame()) documentNumber++; });
  await page.route('**/*', route => route.request().isNavigationRequest()
    ? route.fulfill({contentType: 'text/html', body: bodyForUrl?.(new URL(route.request().url())) ?? html}) : route.abort());
  await page.goto(url);
  globalThis.chrome = {tabs: {query: async () => active ? [{id: active === page ? 7 : 8, url: active.url(), title: await active.title(), status: 'complete'}] : []},
    scripting: {executeScript: async request => {
      calls.push(request); await beforeScript?.(request, {page, context, setActive: value => { active = value; }});
      const id = `document-${documentNumber}`;
      if (request.target.documentIds && !request.target.documentIds.includes(id)) throw new Error('Document changed');
      if (request.files) { await page.addScriptTag({content}); return [{frameId: 0, documentId: id}]; }
      const result = await page.evaluate(({source, args}) => (0, eval)(`(${source})`)(...args), {source: request.func.toString(), args: request.args || []});
      await afterScript?.(request, {page, context, setActive: value => { active = value; }});
      return [{frameId: 0, documentId: id, result}];
    }}};
  try { return await run({page, context, calls}); } finally { await context.close(); }
}
function privateCalls(calls) { return calls.filter(request => Object.keys(request.args?.[0]?.profile || {}).length > 0 || request.args?.[0]?.resume?.dataUrl); }

test('Workday identity strips locale/query/apply steps while preserving host, tenant, site and job', () => {
  for (const base of [standard, recruiting]) {
    const canonical = normalizeJobUrl(base);
    assert.equal(canonical.provider, 'workday'); assert.ok(workdayJob(base));
    const localized = base.replace('/en-US/', '/fr-CA/');
    for (const variant of [base.replace('/en-US/', '/'), localized, `${localized}/apply`, `${base}/apply/autofillWithResume?source=campaign#step`, `${base}/apply/myInformation`]) {
      assert.equal(normalizeJobUrl(variant).identity, canonical.identity, variant);
      assert.equal(normalizeJobUrl(variant).url, canonical.url);
      assert.equal(isSupportedPage(variant), true); assert.equal(providerFor(variant), 'Workday');
    }
    for (const other of [base.replace('R12345', 'R54321'), base.replace('/External/', '/Internal/'), base.replace('fixture', 'other-tenant')]) {
      assert.notEqual(normalizeJobUrl(other).identity, canonical.identity, other);
    }
  }
  assert.notEqual(normalizeJobUrl(standard).identity, normalizeJobUrl(recruiting).identity, 'different public hosts are not silently merged');
});

test('only strict HTTPS public candidate job routes qualify as Workday application pages', () => {
  for (const url of [standard.replace('https:', 'http:'), standard.replace('myworkdayjobs.com', 'myworkdayjobs.com.evil.test'),
    standard.replace('fixture.wd5.', ''), standard.replace('fixture.wd5.', 'fixture.'), standard.replace('.com/', '.com:8443/'),
    'https://wd5.myworkday.com/fixture/d/home.htmld', 'https://www.myworkday.com/fixture',
    'https://fixture.wd5.myworkdayjobs.com/en-US/External', 'https://fixture.wd5.myworkdayjobs.com/en-US/External/login',
    'https://wd1.myworkdaysite.com/fixture/External/job/Austin/Role_R12345',
    'https://wd1.myworkdaysite.com/recruiting/fixture/External', 'https://user:password@fixture.wd5.myworkdayjobs.com/External/job/Role_R12345']) {
    assert.equal(workdayJob(url), null, url); assert.equal(isSupportedPage(url), false, url);
  }
});

test('public Workday canonical casing changes do not split one observed UPenn job identity', () => {
  const original = 'https://wd1.myworkdaysite.com/recruiting/upenn/careers-at-penn/job/ryan-veterinary-hospital/icu-veterinary-nurse-assistant_jr00120131-1';
  const apply = 'https://wd1.myworkdaysite.com/en-US/recruiting/upenn/careers-at-penn/job/Ryan-Veterinary-Hospital/ICU-Veterinary-Nurse-Assistant_JR00120131-1/apply/applyManually';
  assert.equal(normalizeJobUrl(original).identity, normalizeJobUrl(apply).identity);
});

test('public Workday capture uses posting metadata and the specific job heading rather than the career-site heading', async t => {
  for (const url of [standard, recruiting]) await t.test(new URL(url).host, async () => {
    await fixture({url}, async ({page}) => {
      const captured = await captureCurrentJob();
      assert.equal(captured.title, 'Customer engineer'); assert.match(captured.description, /Solve customer problems/);
      assert.equal(captured.provider, 'Workday'); assert.equal(captured.supported, true); assert.equal(captured.url, page.url());
    });
    const metadata = {'@type': 'JobPosting', title: 'Verified role title', hiringOrganization: {name: 'Fixture organization'},
      identifier: {value: 'R12345'}, description: '<p>Exact public posting.</p>'};
    await fixture({url, html: `${posting}<script type="application/ld+json">${JSON.stringify(metadata)}</script>`}, async () => {
      const captured = await captureCurrentJob(); assert.equal(captured.title, metadata.title);
      assert.equal(captured.company, 'Fixture organization'); assert.equal(captured.request_id, 'R12345');
    });
  });
});

test('capture on an opened Workday application uses jobTitleHeading ahead of the branded site heading', async () => {
  await fixture({url: `${standard}/apply/applyManually`, html: `<header><h1 data-automation-id="headerTitle">Fixture Careers</h1></header>${step(information)}`}, async () => {
    assert.equal((await captureCurrentJob()).title, 'Customer engineer');
  });
});

test('Workday review capture never reads application answers as a job description', async t => {
  for (const url of [standard, recruiting]) await t.test(new URL(url).host, async () => {
    await fixture({url: `${url}/apply/applyManually`, html: step('<p>PRIVATE_APPLICATION_ANSWER_CANARY</p>', 'applyFlowReviewPage')}, async ({page}) => {
      await page.locator('main').evaluate(el => Object.defineProperty(el, 'innerText', {get() {throw new Error('Private application text was read');}}));
      const captured = await captureCurrentJob();
      assert.equal(captured.title, 'Customer engineer');
      assert.equal(captured.description, '');
      assert.doesNotMatch(JSON.stringify(captured), /PRIVATE_APPLICATION_ANSWER_CANARY/);
    });
  });
});

test('account, registration, chooser and job-search DOM never receive profile values or résumé bytes', async t => {
  const gates = [
    ['sign in', `<div role="dialog" data-automation-id="wd-popup-frame" aria-label="Sign In"><form><label>Email Address<input data-automation-id="email" type="email" id="email"></label><label>Password<input data-automation-id="password" type="password"></label><button data-automation-id="signInSubmitButton">Sign In</button></form></div>`],
    ['create account', `<div data-automation-id="createAccountPage"><h2>Create Account</h2><form><label>Email Address<input id="email" type="email" data-automation-id="email"></label><label>Password<input type="password" data-automation-id="password"></label><label>Verify New Password<input type="password" data-automation-id="verifyPassword"></label><button>Create Account</button></form></div>`],
    ['application chooser', `${posting}<div role="dialog" aria-label="Start Your Application" data-automation-id="wd-popup-frame"><a data-automation-id="autofillWithResume">Autofill with Resume</a><a data-automation-id="applyManually">Apply Manually</a><a data-automation-id="useMyLastApplication">Use My Last Application</a></div>`],
    ['observed social sign-in gate', `<main data-automation-id="applyFlowPage"><h1 data-automation-id="jobTitleHeading">Customer engineer</h1><ol data-automation-id="progressBar"><li data-automation-id="progressBarActiveStep">Sign In</li></ol><div data-automation-id="signInContent"><button data-automation-id="GoogleSignInButton">Google</button><button data-automation-id="SignInWithEmailButton">Sign In with Email</button></div></main>`],
    ['authentication overlay over an application step', `${step(information)}<div role="dialog" data-automation-id="signInContent"><label>Email<input id="login-email" type="email"></label><label>Password<input type="password"></label></div>`],
    ['search', `<main data-automation-id="jobSearch"><h1>Search for Jobs</h1><form><label>Search<input data-automation-id="searchBox" id="search" type="text"></label><label>Email job alerts<input id="email" type="email"></label></form></main>`],
  ];
  for (const [name, html] of gates) await t.test(name, async () => {
    await fixture({url: `${standard}/apply`, html}, async ({page, calls}) => {
      const snapshot = () => page.locator('input').evaluateAll(inputs => inputs.map(input => ({value: input.value, checked: input.checked, files: input.files?.length || 0})));
      const before = await snapshot();
      for (const action of ['inspect', 'details', 'resume']) {
        const report = await runCurrentAction(action, profile, resume, {expectedUrl: standard});
        assert.equal(report.status, 'interrupted', `${name}: ${action}`);
        assert.equal(privateCalls(calls).length, 0, `${name}: no personal payload crossed the bridge`);
      }
      assert.deepEqual(await snapshot(), before, `${name}: all existing values stay unchanged`);
    });
  });
});

test('mismatched Workday job, tenant, site and host stop all page actions before injection', async t => {
  for (const url of [standard.replace('R12345', 'R54321'), standard.replace('fixture.', 'other.'), standard.replace('/External/', '/OtherSite/'), recruiting]) await t.test(url, async () => {
    await fixture({url}, async ({calls}) => {
      for (const action of ['inspect', 'details', 'resume']) await assert.rejects(runCurrentAction(action, profile, resume, {expectedUrl: standard}), /different job|changed/i);
      assert.equal(calls.length, 0);
    });
  });
});

test('Workday inspect and details affect only the visible My Information step without advancing or submitting', async t => {
  for (const base of [standard, recruiting]) await t.test(new URL(base).host, async () => {
    await fixture({url: `${base}/apply/applyManually`, html: step(information)}, async ({page, calls}) => {
      const inspected = await runCurrentAction('inspect', profile, resume, {expectedUrl: base});
      assert.equal(inspected.status, 'complete'); assert.equal(privateCalls(calls).length, 0);
      assert.equal(await page.locator('#first-name').inputValue(), '');
      const report = await runCurrentAction('details', profile, null, {expectedUrl: base});
      assert.equal(report.status, 'complete', JSON.stringify(report));
      assert.equal(await page.locator('#first-name').inputValue(), profile.first_name);
      assert.equal(await page.locator('#last-name').inputValue(), profile.last_name);
      assert.equal(await page.locator('#email').inputValue(), profile.email);
      assert.equal(await page.locator('#preferred-name').inputValue(), 'Already chosen');
      assert.equal(await page.locator('#gender').inputValue(), ''); assert.equal(await page.locator('#consent').isChecked(), false);
      assert.equal(await page.locator('body').getAttribute('data-advanced'), null); assert.equal(await page.locator('body').getAttribute('data-submitted'), null);
    });
  });
});

test('a captured lowercase public job can fill the same case-canonicalized apply URL after manual navigation', async () => {
  const original = 'https://wd1.myworkdaysite.com/recruiting/upenn/careers-at-penn/job/ryan-veterinary-hospital/icu-veterinary-nurse-assistant_jr00120131-1';
  const apply = 'https://wd1.myworkdaysite.com/en-US/recruiting/upenn/careers-at-penn/job/Ryan-Veterinary-Hospital/ICU-Veterinary-Nurse-Assistant_JR00120131-1/apply/applyManually';
  await fixture({url: original, bodyForUrl: url => url.pathname.includes('/apply/') ? step(information) : posting}, async ({page}) => {
    const captured = await captureCurrentJob(); await page.goto(apply);
    const report = await runCurrentAction('details', profile, null, {expectedUrl: captured.url});
    assert.equal(report.status, 'complete', JSON.stringify(report)); assert.equal(await page.locator('#first-name').inputValue(), profile.first_name);
  });
});

test('a verified Workday upload step attaches only the requested résumé and leaves cover letters and navigation alone', async () => {
  await fixture({url: `${standard}/apply/autofillWithResume`, html: step(`${upload}<label>Cover Letter<input type="file" id="cover"></label>`, 'applyFlowAutoFillPage', 'Autofill with Resume')}, async ({page}) => {
    const report = await runCurrentAction('resume', profile, resume, {expectedUrl: standard});
    assert.equal(report.status, 'complete', JSON.stringify(report)); assert.equal(report.frames[0].resume.status, 'accepted');
    assert.equal(await page.locator('#resume').evaluate(input => input.files[0]?.name), 'Synthetic.pdf');
    assert.equal(await page.locator('#cover').evaluate(input => input.files.length), 0);
    assert.equal(await page.locator('body').getAttribute('data-advanced'), null);
    const again = await runCurrentAction('resume', profile, {...resume, metadata: {name: 'Other.pdf'}}, {expectedUrl: standard});
    assert.equal(again.frames[0].resume.status, 'already-attached');
    assert.equal(await page.locator('#resume').evaluate(input => input.files[0]?.name), 'Synthetic.pdf');
  });
});

test('voluntary disclosures, self identification and review steps remain inspectable but receive no personal payload', async t => {
  for (const marker of ['applyFlowVoluntaryDisclosuresPage', 'applyFlowSelfIdentifyPage', 'applyFlowReviewPage']) await t.test(marker, async () => {
    await fixture({url: `${standard}/apply/applyManually`, html: step('<label>Email<input id="email" type="email"></label><label>Gender<input id="gender" required></label>', marker, marker)}, async ({page, calls}) => {
      const inspected = await runCurrentAction('inspect', profile, resume, {expectedUrl: standard});
      assert.equal(inspected.status, 'complete', JSON.stringify(inspected));
      for (const action of ['details', 'resume']) {
        const report = await runCurrentAction(action, profile, resume, {expectedUrl: standard}); assert.equal(report.status, 'interrupted');
      }
      assert.equal(privateCalls(calls).length, 0); assert.equal(await page.locator('#email').inputValue(), ''); assert.equal(await page.locator('#gender').inputValue(), '');
    });
  });
});

test('a Workday step URL transition during preflight stops before any private payload is injected', async () => {
  let changed = false;
  await fixture({url: `${standard}/apply/applyManually`, html: step(information), afterScript: async (request, {page}) => {
    if (!changed && request.args?.[0]?.probe) { changed = true; await page.evaluate(() => history.pushState({}, '', location.pathname + '/nextStep')); }
  }}, async ({page, calls}) => {
    const report = await runCurrentAction('details', profile, null, {expectedUrl: standard});
    assert.equal(report.status, 'interrupted'); assert.equal(privateCalls(calls).length, 0);
    assert.equal(await page.locator('#first-name').inputValue(), '');
  });
});

test('same-URL Workday step replacement during a run stops before writing into the next step', async () => {
  const transition = `<script>document.querySelector('#first-name').addEventListener('input', () => {
    document.querySelector('[data-automation-id="progressBarActiveStep"]').textContent='My Experience';
    const scope=document.querySelector('[data-automation-id="applyFlowMyInfoPage"]'); scope.setAttribute('data-automation-id','applyFlowMyExpPage');
    scope.innerHTML='<h2>My Experience</h2><label>Family Name<input id="next-step-name" data-automation-id="legalNameSection_lastName"></label>';
  }, {once:true});</script>`;
  await fixture({url: `${standard}/apply/applyManually`, html: step(information) + transition}, async ({page}) => {
    const report = await runCurrentAction('details', profile, null, {expectedUrl: standard});
    assert.equal(report.status, 'interrupted', JSON.stringify(report));
    assert.equal(await page.locator('#next-step-name').inputValue(), '');
    assert.equal(await page.locator('body').getAttribute('data-advanced'), null);
  });
});

const disclosurePreferences = {veteran_status: 'protected_veteran', other_demographics: 'decline'};
const disclosureProfile = {...profile, disclosure_preferences: disclosurePreferences};
const disclosureFields = `<label for="veteran">Please confirm your veteran status *</label><select id="veteran" required>
  <option value="">Select One</option><option>I IDENTIFY AS ONE OR MORE OF THE CLASSIFICATIONS OF PROTECTED VETERANS LISTED ABOVE</option>
  <option>I AM NOT A VETERAN</option><option>I DO NOT WISH TO SELF-IDENTIFY</option></select>
  <label for="race">What race or ethnicity do you most closely identify with? *</label><select id="race" required>
  <option value="">Select One</option><option>I do not wish to answer. (United States of America)</option><option>Another choice</option></select>
  <label for="first-name">First name</label><input id="first-name"><label for="signature">Signature</label><input id="signature">
  <label for="date">Date</label><input id="date" type="date"><label><input id="agreement" type="checkbox" required>I agree to privacy processing</label>`;

test('saved disclosure preferences alone reach eligible Workday later steps without contact data or agreement changes', async t => {
  for (const marker of ['applyFlowPrimaryQuestionsPage', 'applyFlowSecondaryQuestionsPage', 'applyFlowSupplementaryQuestionsPage',
    'applyFlowVoluntaryDisclosuresPage', 'applyFlowSelfIdentifyPage']) await t.test(marker, async () => {
    await fixture({url: `${standard}/apply/applyManually`, html: step(disclosureFields, marker, marker)}, async ({page, calls}) => {
      const report = await runCurrentAction('details', disclosureProfile, resume, {expectedUrl: standard});
      assert.equal(report.status, 'complete', JSON.stringify(report));
      assert.match(await page.locator('#veteran').inputValue(), /CLASSIFICATIONS OF PROTECTED VETERANS/);
      assert.match(await page.locator('#race').inputValue(), /do not wish to answer/);
      const sent = privateCalls(calls);
      assert.equal(sent.length, 1);
      assert.deepEqual(sent[0].args[0].profile, {disclosure_preferences: disclosurePreferences});
      assert.equal(sent[0].args[0].disclosureOnly, true);
      assert.equal(sent[0].args[0].resume, null);
      for (const id of ['first-name', 'signature', 'date']) assert.equal(await page.locator('#' + id).inputValue(), '');
      assert.equal(await page.locator('#agreement').isChecked(), false);
      assert.equal(await page.locator('body').getAttribute('data-advanced'), null);
      assert.equal(await page.locator('body').getAttribute('data-submitted'), null);
      assert.doesNotMatch(JSON.stringify(report), /protected_veteran|CLASSIFICATIONS OF PROTECTED VETERANS|do not wish to answer/);
      calls.length = 0;
      const again = await runCurrentAction('details', disclosureProfile, null, {expectedUrl: standard});
      assert.equal(again.frames[0].filled.length, 0);
      assert.equal(await page.locator('#agreement').isChecked(), false);
    });
  });
});

test('missing or invalid preferences never enable Workday disclosure writes or send profile values', async t => {
  for (const invalid of [undefined, null, {}, [], {veteran_status: ''}, {veteran_status: 'yes'},
    {veteran_status: 'protected_veteran', other_demographics: null}, {veteran_status: true, other_demographics: 'decline'}]) {
    await t.test(JSON.stringify(invalid) || 'missing', async () => {
      await fixture({url: `${standard}/apply/applyManually`, html: step(disclosureFields, 'applyFlowVoluntaryDisclosuresPage')}, async ({page, calls}) => {
        const report = await runCurrentAction('details', {...profile, disclosure_preferences: invalid}, null, {expectedUrl: standard});
        assert.equal(report.status, 'interrupted');
        assert.equal(privateCalls(calls).length, 0);
        assert.equal(await page.locator('#veteran').inputValue(), '');
        assert.equal(await page.locator('#race').inputValue(), '');
      });
    });
  }
});

test('saved preferences do not enable Workday review, availability or authentication steps', async t => {
  for (const marker of ['applyFlowReviewPage', 'applyFlowMyAvailabilityPage']) await t.test(marker, async () => {
    await fixture({url: `${standard}/apply/applyManually`, html: step(disclosureFields, marker)}, async ({page, calls}) => {
      for (const action of ['details', 'resume']) {
        assert.equal((await runCurrentAction(action, disclosureProfile, resume, {expectedUrl: standard})).status, 'interrupted');
      }
      assert.equal(privateCalls(calls).length, 0);
      assert.equal(await page.locator('#veteran').inputValue(), '');
    });
  });
  await fixture({url: `${standard}/apply/applyManually`, html: step(disclosureFields, 'applyFlowVoluntaryDisclosuresPage') + '<input type="password">'}, async ({page, calls}) => {
    assert.equal((await runCurrentAction('details', disclosureProfile, null)).status, 'interrupted');
    assert.equal(privateCalls(calls).length, 0);
    assert.equal(await page.locator('#veteran').inputValue(), '');
  });
});

test('same-URL replacement before a disclosure run is rechecked before any answer is written', async () => {
  let changed = false;
  await fixture({url: `${standard}/apply/applyManually`, html: step(disclosureFields, 'applyFlowVoluntaryDisclosuresPage'),
    afterScript: async (request, {page}) => {
      if (!changed && request.args?.[0]?.probe) {
        changed = true;
        await page.locator('[data-automation-id="applyFlowVoluntaryDisclosuresPage"]').evaluate(el => el.setAttribute('data-automation-id', 'applyFlowReviewPage'));
      }
    }}, async ({page, calls}) => {
    const report = await runCurrentAction('details', disclosureProfile, null, {expectedUrl: standard});
    assert.equal(report.status, 'interrupted', JSON.stringify(report));
    for (const id of ['veteran', 'race', 'first-name', 'signature', 'date']) assert.equal(await page.locator('#' + id).inputValue(), '');
    assert.equal(await page.locator('#agreement').isChecked(), false);
    for (const request of privateCalls(calls)) assert.deepEqual(request.args[0].profile, {disclosure_preferences: disclosurePreferences});
  });
});
