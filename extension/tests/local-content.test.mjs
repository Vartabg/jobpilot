import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {before,after,test} from 'node:test';
import {chromium} from '@playwright/test';
const scripts=await Promise.all(['content.js','local-content.js'].map(file=>readFile(new URL(`../${file}`,import.meta.url),'utf8')));
let browser;
before(async()=>{browser=await chromium.launch();});
after(async()=>{await browser?.close();});
const url='https://jobs.ashbyhq.com/example/req-123/application';
async function fixture(body,fn) {
  const context=await browser.newContext(), page=await context.newPage();
  await page.route('**/*',route=>route.request().isNavigationRequest()?route.fulfill({contentType:'text/html',body}):route.abort());
  await page.goto(url);
  for (const content of scripts) await page.addScriptTag({content});
  try {await fn(page);} finally {await context.close();}
}
const run=(page,op,extra={})=>page.evaluate(request=>globalThis.__jobpilotLocalRun(request),{op,expectedUrl:url,...extra});
const inspect=page=>run(page,'inspect');
const base='<form id="application"><label>First name<input name="first_name" required></label><button type="submit">Submit application</button></form>';

test('read-only diagnostics expose validation state without opening menus or exposing answers',async()=>{
  await fixture('<form id="application"><label for="country">Country*</label><input id="country" role="combobox" required aria-controls="countries" aria-expanded="false" value="PRIVATE_COUNTRY"><label for="gender">Gender</label><input id="gender" role="combobox" aria-invalid="true" value="PRIVATE_GENDER"><div id="countries" role="listbox" hidden><div role="option">PRIVATE_CHOICE</div></div></form>',async page=>{
    await page.evaluate(()=>{window.changes=0;for(const el of document.querySelectorAll('input'))for(const event of ['click','input','change','focus','invalid'])el.addEventListener(event,()=>window.changes++);document.querySelector('#gender').setCustomValidity('PRIVATE_VALIDATION_MESSAGE');});
    const result=await inspect(page), raw=JSON.stringify(result);
    assert.equal(raw.includes('PRIVATE_'),false);
    assert.equal(typeof result.diagnostics.visible,'boolean');
    assert.equal(result.diagnostics.controls[0].associatedListCount,1);
    assert.equal(result.diagnostics.controls[0].visibleOptionCount,0);
    assert.equal(result.diagnostics.controls[1].validation.customError,true);
    assert.equal(result.diagnostics.controls[1].validation.ariaInvalid,true);
    assert.equal(await page.evaluate(()=>window.changes),0);
    assert.equal(await page.locator('#countries').isHidden(),true);
    assert.equal(await page.locator('#gender').inputValue(),'PRIVATE_GENDER');
  });
});

test('inventory exports exact fixed labels, choices and constraints, never entered values',async()=>{
  await fixture(`<form id="application"><label for="name">Full name</label><input id="name" value="PRIVATE_NAME" required maxlength="80">
    <label for="location">Preferred office</label><input id="location" role="combobox" aria-label="Preferred office PRIVATE_CITY" value="PRIVATE_CITY">
    <input role="combobox" aria-label="Unverified PRIVATE_SELECTION" value="PRIVATE_SELECTION">
    <fieldset><legend>Citizenship *</legend><label><input type="radio" name="citizen" value="us" required>United States</label><label><input type="radio" name="citizen" value="other">Other</label></fieldset></form>`,async page=>{
    const result=await inspect(page), text=JSON.stringify(result);
    assert.equal(text.includes('PRIVATE_'),false);
    assert.equal(result.questions[0].label,'Full name');
    assert.equal(result.questions[0].constraints.maxlength,'80');
    assert.equal(result.questions[1].label,'Preferred office');
    assert.equal(result.questions[2].labelVerified,false);
    assert.deepEqual(result.questions[3].choices.map(q=>q.label),['United States','Other']);
    assert.equal(result.questions[3].personalDecision,true);
    assert.equal(result.source.evidenceType,'live_extension_control_inventory');
  });
});

test('changed answers, form replacement, expired and consumed snapshots cannot mutate',async()=>{
  await fixture(base,async page=>{
    let info=await inspect(page);
    await page.locator('input').fill('Manual');
    await assert.rejects(run(page,'answer',{snapshot:info.snapshot,questionId:info.questions[0].id,value:'Other'}),/changed/);
    info=await inspect(page);
    await page.evaluate(()=>document.querySelector('form').outerHTML=document.querySelector('form').outerHTML);
    await assert.rejects(run(page,'details',{snapshot:info.snapshot,profile:{first_name:'Other'}}),/changed/);
    info=await inspect(page);
    await page.evaluate(()=>globalThis.__jobpilotLocalState.snapshot.expiresAt=0);
    await assert.rejects(run(page,'details',{snapshot:info.snapshot,profile:{}}),/expired/);
    await page.locator('input').fill(''); info=await inspect(page);
    await run(page,'answer',{snapshot:info.snapshot,questionId:info.questions[0].id,value:'Alex'});
    await assert.rejects(run(page,'answer',{snapshot:info.snapshot,questionId:info.questions[0].id,value:'Other'}),/consumed/);
  });
});

test('existing answers survive unless replacement is explicit, and legal questions need a separate decision',async()=>{
  await fixture('<form id="application"><label>Name<input value="Manual"></label><label>Agree to privacy terms<input type="checkbox" required></label></form>',async page=>{
    let info=await inspect(page);
    await assert.rejects(run(page,'answer',{snapshot:info.snapshot,questionId:info.questions[0].id,value:'Other'}),/preserved/);
    info=await inspect(page);
    await assert.rejects(run(page,'answer',{snapshot:info.snapshot,questionId:info.questions[1].id,value:true}),/personal or legal/);
    assert.equal(await page.locator('[type=checkbox]').isChecked(),false);
    info=await inspect(page);
    await run(page,'answer',{snapshot:info.snapshot,questionId:info.questions[1].id,value:true,explicitPersonalDecision:true});
    assert.equal(await page.locator('[type=checkbox]').isChecked(),true);
    assert.equal(await page.locator('input').first().inputValue(),'Manual');
  });
});

test('details strips saved demographic choices and stops on synchronous form replacement',async()=>{
  await fixture(`<form id="application"><label>First name<input name="first_name"></label><label>Last name<input name="last_name"></label>
    <label>Gender<select><option value="">Choose</option><option>Prefer not to say</option></select></label></form>`,async page=>{
    const info=await inspect(page);
    await page.evaluate(()=>document.querySelector('input').addEventListener('input',()=>{
      const old=document.querySelector('form'), replacement=old.cloneNode(true);old.replaceWith(replacement);
    }));
    const result=await run(page,'details',{snapshot:info.snapshot,profile:{first_name:'Alex',last_name:'Example',disclosure_preferences:{other_demographics:'decline'}}});
    assert.equal(result.report.status,'interrupted');
    assert.equal(await page.locator('[name=last_name]').inputValue(),'');
    assert.equal(await page.locator('select').inputValue(),'');
  });
});

test('ordinary details fills multiple fields with a bound guard',async()=>{
  await fixture('<form id="application"><label>First name<input name="first_name"></label><label>Last name<input name="last_name"></label></form>',async page=>{
    const info=await inspect(page);
    const result=await run(page,'details',{snapshot:info.snapshot,profile:{first_name:'Alex',last_name:'Example'}});
    assert.equal(result.report.status,'complete');
    assert.equal(await page.locator('[name=last_name]').inputValue(),'Example');
  });
});

test('exact native choices work and changed choices invalidate snapshots',async()=>{
  await fixture('<form id="application"><label>Office<select required><option value="">Choose</option><option value="nyc">New York</option><option value="sf">San Francisco</option></select></label></form>',async page=>{
    let info=await inspect(page);
    await run(page,'answer',{snapshot:info.snapshot,questionId:info.questions[0].id,value:'New York'});
    assert.equal(await page.locator('select').inputValue(),'nyc');
    info=await inspect(page);
    await page.locator('option').last().evaluate(el=>el.textContent='Changed');
    await assert.rejects(run(page,'answer',{snapshot:info.snapshot,questionId:info.questions[0].id,value:'Changed',replace:true}),/changed/);
  });
});

test('own labels keep personal decisions gated inside a generic fieldset and duplicate option values choose exact text',async()=>{
  await fixture('<form id="application"><fieldset><legend>Personal information</legend><label>Agree to privacy terms<input type="checkbox" required></label><label>Office<select><option value="">Choose</option><option value="same">Seattle</option><option value="same">New York</option></select></label></fieldset></form>',async page=>{
    let info=await inspect(page);
    assert.match(info.questions[0].label,/privacy terms/);
    await assert.rejects(run(page,'answer',{snapshot:info.snapshot,questionId:info.questions[0].id,value:true}),/personal or legal/);
    info=await inspect(page);
    await run(page,'answer',{snapshot:info.snapshot,questionId:info.questions[1].id,value:'New York'});
    assert.equal(await page.locator('select').evaluate(el=>el.selectedOptions[0].textContent),'New York');
  });
});

test('prevented checkbox and radio changes cannot claim the requested answer was accepted',async()=>{
  await fixture('<form id="application"><label>Optional feature<input type="checkbox" checked onclick="event.preventDefault()"></label><fieldset><legend>Office</legend><label><input type="radio" name="office" checked>Boston</label><label><input type="radio" name="office" onclick="event.preventDefault()">New York</label></fieldset></form>',async page=>{
    let info=await inspect(page);
    await assert.rejects(run(page,'answer',{snapshot:info.snapshot,questionId:info.questions[0].id,value:false,replace:true}),/checkbox state/);
    assert.equal(await page.locator('[type=checkbox]').isChecked(),true);
    info=await inspect(page);
    await assert.rejects(run(page,'answer',{snapshot:info.snapshot,questionId:info.questions[1].id,value:'New York',replace:true}),/requested choice/);
    assert.equal(await page.locator('[type=radio]').first().isChecked(),true);
  });
});

test('custom choice and combobox option buttons cannot submit outside the nonce workflow',async()=>{
  await fixture('<form id="application"><label>Name<input value="Alex"></label><div class="ashby-application-form-field-entry"><label class="ashby-application-form-question-title">Office preference</label><div class="ashby-application-form-input-yesno"><button aria-pressed="false">Yes</button><button type="button" aria-pressed="false">No</button></div></div></form>',async page=>{
    await page.evaluate(()=>{window.submits=0;document.querySelector('form').onsubmit=e=>{e.preventDefault();window.submits++;};});
    const info=await inspect(page);
    await assert.rejects(run(page,'answer',{snapshot:info.snapshot,questionId:info.questions.find(item=>item.type==='choice').id,value:'Yes'}),/submit or reset/);
    assert.equal(await page.evaluate(()=>window.submits),0);
  });
  await fixture('<form id="application"><label for="office">Office</label><input id="office" role="combobox" aria-controls="offices"><div id="offices" role="listbox"><button role="option">New York</button></div></form>',async page=>{
    await page.evaluate(()=>{window.submits=0;document.querySelector('form').onsubmit=e=>{e.preventDefault();window.submits++;};});
    const info=await inspect(page);
    await assert.rejects(run(page,'answer',{snapshot:info.snapshot,questionId:info.questions[0].id,value:'New York'}),/submit or reset/);
    assert.equal(await page.evaluate(()=>window.submits),0);
  });
});

test('custom accessible list accepts only a unique exact offered choice',async()=>{
  await fixture(`<form id="application"><label for="office">Office</label><input id="office" role="combobox" aria-controls="offices" aria-expanded="false">
    <div id="offices" role="listbox" hidden><div role="option">New York</div></div></form>`,async page=>{
    await page.evaluate(()=>{
      const input=document.querySelector('input'),list=document.querySelector('[role=listbox]');
      input.addEventListener('input',()=>{list.hidden=false;input.setAttribute('aria-expanded','true');});
      list.firstElementChild.onclick=()=>{input.value='New York';input.setAttribute('aria-expanded','false');list.hidden=true;};
    });
    const info=await inspect(page);
    await run(page,'answer',{snapshot:info.snapshot,questionId:info.questions[0].id,value:'New York'});
    assert.equal(await page.locator('input').inputValue(),'New York');
  });
});

test('choice replacement during asynchronous lookup aborts without selecting',async()=>{
  await fixture('<form id="application"><label for="office">Office</label><input id="office" role="combobox" aria-controls="offices"><div id="offices" role="listbox"><div role="option">New York</div></div></form>',async page=>{
    await page.evaluate(()=>{
      window.selected=0;
      document.querySelector('[role=option]').onclick=()=>window.selected++;
      document.querySelector('input').oninput=()=>setTimeout(()=>{document.querySelector('input').outerHTML='<input id="office" role="combobox" aria-controls="offices">';},20);
    });
    const info=await inspect(page);
    await assert.rejects(run(page,'answer',{snapshot:info.snapshot,questionId:info.questions[0].id,value:'New York'}),/changed/);
    assert.equal(await page.evaluate(()=>window.selected),0);
  });
});

test('submit checks required values, CAPTCHA, ambiguity, nonce and duplicate attempts',async()=>{
  await fixture(base,async page=>{
    await page.evaluate(()=>document.querySelector('form').onsubmit=event=>{event.preventDefault();window.clicks=(window.clicks||0)+1;});
    let info=await inspect(page);
    await assert.rejects(run(page,'prepare-submit',{snapshot:info.snapshot}),/unanswered/);
    await page.locator('input').fill('Alex'); info=await inspect(page);
    await page.evaluate(()=>document.querySelector('form').insertAdjacentHTML('beforeend','<div id="captcha-challenge">Verify</div>'));
    await assert.rejects(run(page,'prepare-submit',{snapshot:info.snapshot}),/CAPTCHA/);
    await page.locator('#captcha-challenge').evaluate(el=>el.remove());
    await page.evaluate(()=>document.querySelector('form').insertAdjacentHTML('beforeend','<button>Submit application</button>'));
    await assert.rejects(run(page,'prepare-submit',{snapshot:info.snapshot}),/unique/);
    await page.locator('button').last().evaluate(el=>el.remove());
    const prepared=await run(page,'prepare-submit',{snapshot:info.snapshot});
    await assert.rejects(run(page,'submit',{snapshot:info.snapshot,nonce:prepared.nonce,confirm:false}),/confirmation/);
    const ready=await run(page,'prepare-submit',{snapshot:info.snapshot});
    const result=await run(page,'submit',{snapshot:info.snapshot,nonce:ready.nonce,confirm:true});
    assert.equal(result.attempted,true);assert.equal(result.confirmed,false);
    assert.equal(await page.evaluate(()=>window.clicks),1);
    info=await inspect(page);
    await assert.rejects(run(page,'prepare-submit',{snapshot:info.snapshot}),/already attempted/);
  });
});

test('receipt needs narrow visible confirmation with no remaining application form',async()=>{
  await fixture(base+'<h2>Thank you for applying!</h2><h3 hidden>Application submitted</h3>',async page=>{
    assert.equal((await run(page,'receipt')).confirmed,false);
    await page.locator('form').evaluate(el=>el.remove());
    assert.equal((await run(page,'receipt')).confirmed,true);
    await page.locator('h2').evaluate(el=>el.textContent='Your application is processing');
    assert.equal((await run(page,'receipt')).confirmed,false);
  });
});

test('prepared file bytes do not satisfy accepted résumé evidence',async()=>{
  await fixture('<form id="application"><div class="field"><label for="resume">Resume *</label><input id="resume" type="file" required></div><button>Submit application</button></form>',async page=>{
    await page.locator('input').setInputFiles({name:'Fixture.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-1.4 fixture')});
    const info=await inspect(page);
    await assert.rejects(run(page,'prepare-submit',{snapshot:info.snapshot}),/acceptance evidence/);
  });
});

test('observed accepted résumé enables preparation, while replacement during upload is held',async()=>{
  const body='<form id="application"><div class="field"><label for="resume">Resume *</label><input id="resume" type="file" required><span id="filename"></span></div><button>Submit application</button></form>';
  const bytes=Buffer.from('%PDF-1.4 fixture');
  const resume={metadata:{name:'Fixture.pdf',type:'application/pdf',size:bytes.length},dataUrl:'data:application/pdf;base64,'+bytes.toString('base64')};
  await fixture(body,async page=>{
    await page.evaluate(()=>document.querySelector('input').onchange=event=>document.querySelector('#filename').textContent=event.target.files[0].name);
    let info=await inspect(page);
    const result=await run(page,'resume',{snapshot:info.snapshot,resume});
    assert.equal(result.report.resume.status,'accepted');
    info=await inspect(page);
    assert.equal((await run(page,'prepare-submit',{snapshot:info.snapshot})).prepared,true);
  });
  await fixture(body,async page=>{
    await page.evaluate(()=>document.querySelector('input').onchange=()=>setTimeout(()=>{document.querySelector('form').outerHTML=document.querySelector('form').outerHTML;},20));
    let info=await inspect(page);
    const result=await run(page,'resume',{snapshot:info.snapshot,resume});
    assert.notEqual(result.report.resume.status,'accepted');
    info=await inspect(page);
    await assert.rejects(run(page,'prepare-submit',{snapshot:info.snapshot}),/unanswered|acceptance/);
  });
});
