// Runs only in Chrome's isolated world, in the explicit top document.
(() => {
  const state = globalThis.__jobpilotLocalState ||= {ids: new WeakMap(), next: 1, snapshot: null, prepared: null, attempted: false, busy: false, acceptedFiles:new WeakMap()};
  const tools = () => globalThis.__jobpilotLocalTools;
  const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
  const id = el => { if (!state.ids.has(el)) state.ids.set(el, `q${state.next++}`); return state.ids.get(el); };
  const personal = /citizen|nationality|gender|pronoun|ethnic|\brace\b|disabil|veteran|consent|agree|certif|signature|privacy|penalty|background check|recording|transcrib|legal|sanction/i;
  const optionText = el => {
    const copy = el.cloneNode(true);
    copy.querySelectorAll('input,textarea,select,[role="combobox"],[role="listbox"]').forEach(node => node.remove());
    return clean(copy.textContent);
  };
  function fixedLabel(entry) {
    const {el, type} = entry;
    const field = el.closest('.ashby-application-form-field-entry,fieldset,.field,.field-wrapper,.form-field,[data-field]');
    const title = field?.querySelector('.ashby-application-form-question-title');
    if (title) return optionText(title);
    const legend=el.closest('fieldset')?.querySelector(':scope > legend');
    if (['radio','choice'].includes(type) && legend) return optionText(legend);
    const refs = (el.getAttribute('aria-labelledby') || '').split(/\s+/).map(value => document.getElementById(value))
      .filter(node => node?.matches('label,legend,h2,h3,h4'));
    const associated = [...(el.labels || [])];
    const nodes = associated.length ? associated : refs;
    if (nodes.length) return [...new Set([...(type==='checkbox' && legend ? [optionText(legend)] : []),...nodes.map(optionText)].filter(Boolean))].join(' / ');
    if (legend) return optionText(legend);
    const label = field?.querySelector('label');
    if (label && !['radio','choice'].includes(type)) return optionText(label);
    // Custom aria-labels can contain the selected applicant answer. Without a
    // fixed question node, report a hold instead of exporting that value.
    return '';
  }
  function choices(entry, scope) {
    const {el,type} = entry;
    if (el.tagName === 'SELECT') return [...el.options].map(node => ({node, label:clean(node.textContent), disabled:node.disabled}));
    if (type === 'checkbox') return [{node:el,label:clean([...(el.labels || [])].map(optionText).join(' ')),disabled:el.disabled}];
    if (type === 'radio' && el.type === 'radio') return [...scope.querySelectorAll('input[type="radio"]')]
      .filter(node => node.name === el.name && node.form === el.form && (node.name || node === el))
      .map(node => ({node,label:clean([...(node.labels || [])].map(optionText).join(' ')),disabled:node.disabled}));
    if (type === 'choice' || el.getAttribute('role') === 'radiogroup') return [...el.querySelectorAll('button,[role="radio"]')]
      .map(node => ({node,label:optionText(node),disabled:node.disabled || node.getAttribute('aria-disabled') === 'true'}));
    return [];
  }
  function valueOf(entry, scope) {
    const {el,type} = entry;
    const offered = choices(entry,scope);
    if (type === 'file') return JSON.stringify([...el.files || []].map(file => [file.name,file.size,file.lastModified]));
    if (['radio','choice'].includes(type)) return JSON.stringify(offered.map(({node}) => [id(node),node.checked || node.getAttribute('aria-checked') === 'true' || node.getAttribute('aria-pressed') === 'true']));
    if (type === 'checkbox') return String(el.checked ?? (el.getAttribute('aria-checked') === 'true'));
    return JSON.stringify([el.value || '',el.getAttribute('aria-expanded'),el.closest('.react-select__control,[class*="select__control"]')?.textContent || '']);
  }
  function context() {
    if (!tools()) throw new Error('The form inspector is unavailable.');
    const scopes = tools().applicationScopes().filter(tools().visible);
    if (scopes.length !== 1) throw new Error('A unique top-document application form is required. Embedded, missing or ambiguous forms are held.');
    const scope = scopes[0], warnings = [];
    const entries = tools().inventory(message => warnings.push(message)).filter(entry => scope.contains(entry.el));
    if (!entries.length || warnings.length) throw new Error('The complete application controls could not be inspected.');
    const questions = entries.map(entry => {
      const label = fixedLabel(entry), offered = choices(entry,scope);
      if (label.length > 4000 || offered.length > 150 || offered.some(choice => choice.label.length > 4000)) throw new Error('Question inventory exceeds the export bounds; no partial inventory was returned.');
      return {id:id(entry.el),label:label || '[Unverified fixed question label]',labelVerified:!!label,
        type:entry.type,required:entry.required === true,answered:entry.answered === true,
        personalDecision:personal.test(label),choices:offered.map(({label,disabled}) => ({label,disabled})),
        constraints:Object.fromEntries(['min','max','minlength','maxlength','pattern','accept'].filter(key => entry.el.hasAttribute(key)).map(key => [key,entry.el.getAttribute(key)]))};
    });
    // State is private to this isolated document. Only opaque snapshot IDs and
    // public question metadata cross the extension boundary.
    const shape = JSON.stringify([id(scope),questions.map(({answered,...question})=>question),entries.map(entry => choices(entry,scope).map(({node})=>id(node)))]);
    const values = JSON.stringify(entries.map(entry => valueOf(entry,scope)));
    return {scope,entries,questions,shape,values};
  }
  function verify(snapshot) {
    const saved = state.snapshot;
    if (!saved || saved.id !== snapshot || Date.now() > saved.expiresAt) throw new Error('Snapshot expired or was consumed. Inspect again.');
    const current = context();
    if (current.scope !== saved.scope || current.shape !== saved.shape || current.values !== saved.values) throw new Error('The form or an answer changed. Inspect again before acting.');
    return current;
  }
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  function safeChoiceClick(el) {
    const native=el.closest('button,input[type="submit"],input[type="image"],input[type="reset"]');
    if (native?.form && !(native.tagName==='BUTTON' && native.type==='button')) throw new Error('This choice can submit or reset the form. Complete it manually.');
    el.click();
  }
  function dispatchText(el,value) {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto,'value').set.call(el,value);
    el.dispatchEvent(new Event('input',{bubbles:true}));
    el.dispatchEvent(new Event('change',{bubbles:true}));
  }
  function guardedContext(before) {
    try { const current=context(); return current.scope === before.scope && current.shape === before.shape; } catch { return false; }
  }
  function answeredNow(entry, scope) {
    if (entry.type === 'combobox') {
      const shell=entry.el.closest('.react-select__control,[class*="select__control"]');
      return entry.el.getAttribute('aria-expanded') !== 'true' && (shell ? !!shell.querySelector('[class*="singleValue"],[class*="single-value"],[class*="multiValue"]') : !!clean(entry.el.value || entry.el.textContent));
    }
    return entry.answered;
  }
  async function answer(request,current) {
    const entry=current.entries.find(item => id(item.el) === request.questionId);
    if (!entry) throw new Error('Unknown question ID.');
    const question=current.questions.find(item=>item.id===request.questionId);
    if (!question.labelVerified) throw new Error('The fixed question label could not be verified. Answer this control manually.');
    if (question.personalDecision && request.explicitPersonalDecision !== true) throw new Error('This question requires a separately supplied personal or legal decision.');
    if (answeredNow(entry,current.scope) && request.replace !== true) throw new Error('An existing answer was preserved. Explicit replacement is required.');
    const {el,type}=entry, value=request.value;
    if (['file','password','security-code'].includes(type)) throw new Error('Use the dedicated file or manual security-code workflow.');
    if (!guardedContext(current)) throw new Error('The form changed before the answer.');
    if (el.tagName === 'SELECT') {
      if (el.multiple) throw new Error('Multiple-select answers require manual input.');
      const offered=choices(entry,current.scope).filter(item=>item.label===value && !item.disabled);
      if (offered.length !== 1) throw new Error('No unique offered choice matches that exact answer.');
      el.selectedIndex=[...el.options].indexOf(offered[0].node); el.dispatchEvent(new Event('change',{bubbles:true}));
    } else if (['radio','choice'].includes(type)) {
      const offered=choices(entry,current.scope).filter(item=>item.label===value && !item.disabled);
      if (offered.length !== 1) throw new Error('No unique offered choice matches that exact answer.');
      safeChoiceClick(offered[0].node);
    } else if (type === 'checkbox') {
      if (typeof value !== 'boolean') throw new Error('A checkbox answer must be true or false.');
      if ((el.checked ?? (el.getAttribute('aria-checked')==='true')) !== value) safeChoiceClick(el);
    } else if (type === 'combobox') {
      if (typeof value !== 'string') throw new Error('A choice must be text.');
      safeChoiceClick(el);
      if (el.tagName === 'INPUT') dispatchText(el,value);
      await wait(400);
      // Opening a menu changes aria-expanded/answered metadata; bind the
      // original nodes/labels/options while permitting this transient state.
      if (!guardedContext(current) || location.href !== request.expectedUrl || el.tagName==='INPUT' && el.value !== value) throw new Error('The choice control or its answer changed. Inspect again.');
      const listIds=[el.getAttribute('aria-controls'),el.getAttribute('aria-owns')].filter(Boolean).flatMap(item=>item.split(/\s+/));
      const lists=listIds.map(item=>document.getElementById(item)).filter(node=>node && tools().visible(node));
      if (lists.length !== 1) throw new Error('An unambiguous accessible choice list is required. Complete this control manually.');
      const offered=[...lists[0].querySelectorAll('[role="option"]')].filter(node=>tools().visible(node) && optionText(node) === value && node.getAttribute('aria-disabled') !== 'true');
      if (offered.length !== 1) throw new Error('No unique offered choice matches that exact answer.');
      safeChoiceClick(offered[0]);
    } else if (el.matches('input,textarea') && typeof value === 'string' && value.length <= 20000) dispatchText(el,value);
    else throw new Error('This control requires manual input.');
    await wait(150);
    if (!el.isConnected || location.href !== request.expectedUrl) throw new Error('The form changed during the answer. Result is uncertain; inspect again.');
    const fresh=tools().inventory().find(item=>item.el===el);
    if (!fresh || !answeredNow(fresh,current.scope) && value !== false || el.validity && !el.validity.valid || el.getAttribute('aria-invalid') === 'true') throw new Error('The employer has not accepted this answer. Inspect again.');
    if (type==='combobox' && clean(tools().selectedCombo(el) || el.value) !== value) throw new Error('The selected choice differs from the supplied answer. Inspect again.');
    if (el.tagName==='SELECT' && clean(el.selectedOptions[0]?.textContent)!==value) throw new Error('The selected choice differs from the supplied answer. Inspect again.');
    if (type==='checkbox' && (el.checked ?? (el.getAttribute('aria-checked')==='true')) !== value) throw new Error('The employer did not accept the requested checkbox state. Inspect again.');
    if (['radio','choice'].includes(type)) {
      const selected=choices(fresh,current.scope).filter(({node})=>node.checked || node.getAttribute('aria-checked')==='true' || node.getAttribute('aria-pressed')==='true');
      if (selected.length!==1 || selected[0].label!==value) throw new Error('The employer did not accept the requested choice. Inspect again.');
    }
    if (el.matches('input,textarea') && !['radio','checkbox','combobox'].includes(type) && el.value !== value) throw new Error('The employer changed the supplied answer. Inspect again.');
    return {answered:true,questionId:request.questionId};
  }
  function submissionGuard(current) {
    if (state.attempted) throw new Error('Submission was already attempted in this document. Reconcile the receipt; do not retry.');
    if (current.questions.some(q=>!q.labelVerified)) throw new Error('Unverified question labels require manual review.');
    if (current.entries.some(entry=>entry.required && !answeredNow(entry,current.scope))) throw new Error('Required questions remain unanswered.');
    for (const entry of current.entries.filter(item=>item.type==='file' && /resume|\bcv\b/i.test(fixedLabel(item)))) {
      if ((entry.required || entry.answered) && state.acceptedFiles.get(entry.el)!==valueOf(entry,current.scope)) throw new Error('The résumé upload has no verified acceptance evidence.');
    }
    if ([...current.scope.querySelectorAll('input,textarea,select')].some(el=>!el.disabled && el.type!=='hidden' && el.validity && !el.validity.valid)
      || [...current.scope.querySelectorAll('[aria-invalid="true"],[role="alert"]')].some(tools().visible)) throw new Error('Employer validation errors require review.');
    if ([...document.querySelectorAll('iframe,[class*="captcha"],[id*="captcha"],input[autocomplete="one-time-code"]')].some(el=>tools().visible(el) && (/captcha|challenge|recaptcha|hcaptcha|turnstile|verification/i.test([el.src,el.id,el.className,el.getAttribute('title')].join(' ')) || el.autocomplete==='one-time-code')))
      throw new Error('A CAPTCHA or security challenge requires manual completion.');
    const buttons=[...current.scope.querySelectorAll('button,input[type="submit"]')].filter(el=>tools().visible(el) && !el.disabled && el.getAttribute('aria-disabled')!=='true'
      && /^(submit(?: application)?|send application|apply(?: now)?)$/i.test(clean(el.textContent || el.value)));
    if (buttons.length!==1) throw new Error('A unique final application submit control could not be verified.');
    return buttons[0];
  }
  function receipt() {
    const scopes=tools()?.applicationScopes().filter(tools().visible) || [];
    const nodes=[...document.querySelectorAll('h1,h2,h3,[role="status"],#application_confirmation,.ashby-application-form-success')].filter(tools().visible);
    const match=nodes.map(node=>clean(node.textContent)).find(text=>/^(?:thank you for applying[!.]?|thanks for applying[!.]?|application (?:successfully )?submitted[!.]?|your application has been (?:successfully )?submitted[!.]?|we have received your application[!.]?)$/i.test(text));
    return {confirmed:!!match && scopes.length===0,confirmation:scopes.length===0 ? match || '' : '',evidenceType:'visible_employer_confirmation',pageUrl:location.href,observedAt:new Date().toISOString()};
  }
  function inspectionDiagnostics(current) {
    const flags=['valid','valueMissing','typeMismatch','patternMismatch','badInput','customError','tooLong','tooShort','rangeUnderflow','rangeOverflow','stepMismatch'];
    return {visible:document.visibilityState==='visible',focused:document.hasFocus(),controls:current.entries.map(({el})=>{
      const listIds=[el.getAttribute('aria-controls'),el.getAttribute('aria-owns')].filter(Boolean).flatMap(value=>value.split(/\s+/));
      const lists=[...new Set(listIds.map(value=>document.getElementById(value)).filter(Boolean))];
      return {questionId:id(el),tag:el.tagName,role:el.getAttribute('role')==='combobox'?'combobox':'',
        disabled:el.disabled===true,readOnly:el.readOnly===true,expanded:el.getAttribute('aria-expanded')==='true',
        selectedIndicatorPresent:!!tools().selectedCombo(el),associatedListCount:lists.length,
        visibleOptionCount:lists.reduce((count,list)=>count+[...list.querySelectorAll('[role="option"]')].filter(tools().visible).length,0),
        validation:{willValidate:el.willValidate===true,ariaInvalid:el.getAttribute('aria-invalid')==='true',
          ...Object.fromEntries(flags.map(flag=>[flag,el.validity ? el.validity[flag]===true : null]))}};
    })};
  }
  globalThis.__jobpilotLocalRun=async request=>{
    if (!request || request.expectedUrl !== location.href) throw new Error('The application URL changed.');
    if (state.busy) throw new Error('A local form action is already running.');
    state.busy=true;
    try {
      if (request.op==='receipt') return receipt();
      if (request.op==='inspect') {
        const current=context(), snapshot=crypto.randomUUID(); state.prepared=null;
        state.snapshot={...current,id:snapshot,expiresAt:Date.now()+300000};
        return {snapshot,expiresAt:state.snapshot.expiresAt,questions:current.questions,pageUrl:location.href,diagnostics:inspectionDiagnostics(current),
          source:{url:location.href,capturedAt:new Date().toISOString(),evidenceType:'live_extension_control_inventory',wording:'exact_fixed_label',scope:'visible_top_document_application_form'}};
      }
      const current=verify(request.snapshot);
      if (request.op==='prepare-submit') {
        const button=submissionGuard(current), nonce=crypto.randomUUID(), expiresAt=Date.now()+30000;
        state.prepared={nonce,expiresAt,snapshot:request.snapshot,button};
        return {prepared:true,nonce,expiresAt,buttonLabel:clean(button.textContent || button.value),pageUrl:location.href};
      }
      if (request.op==='submit') {
        const prepared=state.prepared; state.prepared=null;
        if (!prepared || request.confirm!==true || request.nonce!==prepared.nonce || Date.now()>prepared.expiresAt || request.snapshot!==prepared.snapshot) throw new Error('A current single-use preparation nonce and explicit confirmation are required.');
        const button=submissionGuard(current);
        if (button!==prepared.button) throw new Error('The submit control changed.');
        state.snapshot=null; state.attempted=true; button.click();
        return {attempted:true,confirmed:false,pageUrl:request.expectedUrl,message:'One click was dispatched. Check the employer receipt before counting this application.'};
      }
      state.snapshot=null; state.prepared=null;
      if (request.op==='answer') return await answer(request,current);
      if (['details','resume'].includes(request.op)) {
        const profile={...(request.profile || {})}; delete profile.disclosure_preferences;
        const report=await globalThis.__jobpilotRun({action:request.op,profile:request.op==='details'?profile:{},resume:request.op==='resume'?request.resume:null,
          requireVisible:true,localGuard:()=>location.href===request.expectedUrl && guardedContext(current)});
        if (request.op==='resume' && report.resume?.status==='accepted') {
          for (const entry of current.entries.filter(item=>item.type==='file' && /resume|\bcv\b/i.test(fixedLabel(item)))) {
            const files=[...entry.el.files || []];
            if (files.length===1 && files[0].name===request.resume?.metadata?.name && files[0].size===request.resume?.metadata?.size) state.acceptedFiles.set(entry.el,valueOf(entry,current.scope));
          }
        }
        return {report,pageUrl:request.expectedUrl};
      }
      throw new Error('Unknown local form operation.');
    } finally {state.busy=false;}
  };
})();
