// Runs only in Chrome's isolated world, in the explicit top document.
(() => {
  const state = globalThis.__jobpilotLocalState ||= {ids: new WeakMap(), next: 1, snapshot: null, prepared: null, attempted: false, busy: false};
  const tools = () => globalThis.__jobpilotLocalTools;
  const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
  const resumeLabel = value => /^(resume(?: cv)?|cv|curriculum vitae)$/.test(clean(value).normalize('NFKD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z]+/g,' ').trim());
  const ashbyRoute = () => location.hostname==='jobs.ashbyhq.com' && /^\/[^/]+\/[^/]+\/application\/?$/.test(location.pathname);
  function challengeIndicators() {
    return [...document.querySelectorAll('iframe,[class*="captcha"],[id*="captcha"],input[autocomplete="one-time-code"]')]
      .filter(el=>tools().visible(el) && (/captcha|challenge|recaptcha|hcaptcha|turnstile|verification/i.test([el.src,el.id,el.className,el.getAttribute('title')].join(' ')) || el.autocomplete==='one-time-code'))
      .map(el=>{
        const badge=el.closest('.grecaptcha-badge');
        let invisibleBadge=false;
        if(badge) {
          const frames=[...badge.querySelectorAll('iframe')].filter(tools().visible), frame=frames.length===1?frames[0]:null;
          const interactive=[...badge.querySelectorAll('button,input:not([type="hidden"]),textarea,[role="button"],[role="checkbox"]')].some(tools().visible);
          try {const url=new URL(frame?.src); invisibleBadge=['https://www.google.com','https://www.recaptcha.net'].includes(url.origin)
            && !url.username && !url.password && !interactive && /^\/recaptcha\/(?:api2|enterprise)\/anchor$/.test(url.pathname)
            && url.searchParams.getAll('size').length===1 && url.searchParams.get('size')==='invisible'
            && (el===badge || el===frame || el.tagName==='DIV' && (el.classList.contains('grecaptcha-logo') && el.contains(frame)
              || el.classList.contains('grecaptcha-error') && !el.children.length && !clean(el.textContent)));} catch { /* Keep unknown security widgets as holds. */ }
        }
        return {type:invisibleBadge?'invisible-recaptcha-badge':el.autocomplete==='one-time-code'?'security-code':el.tagName==='IFRAME'?'challenge-frame':'challenge-marker',
          ...(invisibleBadge?{}:{tag:el.tagName,inBadge:!!badge,containsBadge:!!el.querySelector('.grecaptcha-badge'),
            widgetClasses:['g-recaptcha','grecaptcha-logo','grecaptcha-error','g-recaptcha-response','recaptcha'].filter(name=>el.classList.contains(name)),
            interactive:el.matches('button,input,textarea,[role="button"],[role="checkbox"]') || !!el.querySelector('button,input:not([type="hidden"]),[role="button"],[role="checkbox"]')})};
      });
  }
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
    if (type === 'checkbox') return [{node:el,label:clean([...(el.labels || [])].filter(node=>scope.contains(node)).map(optionText).join(' ')),disabled:el.disabled}];
    if (type === 'radio' && el.type === 'radio') return [...scope.querySelectorAll('input[type="radio"]')]
      .filter(node => node.name === el.name && node.form === el.form && (node.name || node === el))
      .map(node => ({node,label:clean([...(node.labels || [])].filter(label=>scope.contains(label)).map(optionText).join(' ')),disabled:node.disabled}));
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
  function context(probeScope=null) {
    if (!tools()) throw new Error('The form inspector is unavailable.');
    const scopes = probeScope ? [probeScope] : tools().applicationScopes().filter(tools().visible);
    const containers=ashbyRoute() ? [...document.querySelectorAll('.ashby-application-form-container')].filter(tools().visible) : [];
    if (!probeScope && (containers.length>1 || containers.some(root=>scopes.some(scope=>!scope.contains(root) && !root.contains(scope))))) throw new Error('Several application scopes match this page. Inspect the intended form manually.');
    const ashby=!probeScope && !scopes.length ? tools().localAshbyForm?.() : null;
    if (ashby) scopes.push(ashby.root);
    if (scopes.length !== 1) throw new Error('A unique top-document application form is required. Embedded, missing or ambiguous forms are held.');
    const scope = scopes[0], warnings = [];
    const entries = tools().inventory(message => warnings.push(message),ashby?scope:null).filter(entry => scope.contains(entry.el));
    if (!entries.length || warnings.length) throw new Error('The complete application controls could not be inspected.');
    const questions = entries.map(entry => {
      const label = ashby ? tools().localFixedLabel(entry.el,scope) : fixedLabel(entry), offered = choices(entry,scope);
      if (label.length > 4000 || offered.length > 150 || offered.some(choice => choice.label.length > 4000)) throw new Error('Question inventory exceeds the export bounds; no partial inventory was returned.');
      return {id:id(entry.el),label:label || '[Unverified fixed question label]',labelVerified:!!label,
        type:entry.type,required:entry.required === true,answered:entry.answered === true,
        personalDecision:personal.test(label),choices:offered.map(({label,disabled}) => ({label,disabled})),
        constraints:Object.fromEntries(['min','max','minlength','maxlength','pattern','accept'].filter(key => entry.el.hasAttribute(key)).map(key => [key,entry.el.getAttribute(key)]))};
    });
    // State is private to this isolated document. Only opaque snapshot IDs and
    // public question metadata cross the extension boundary.
    const shape = JSON.stringify([id(scope),ashby && scope.parentElement ? id(scope.parentElement) : null,
      questions.map(({answered,...question})=>question),entries.map(entry => choices(entry,scope).map(({node})=>id(node)))]);
    const values = JSON.stringify(entries.map(entry => valueOf(entry,scope)));
    return {scope,ashby,entries,questions,shape,values};
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
  function dispatchText(el,value,guard) {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    if (!guard()) throw new Error('The form changed before the answer. Inspect again.');
    Object.getOwnPropertyDescriptor(proto,'value').set.call(el,value);
    if (!guard()) throw new Error('The form changed during the answer. Inspect again.');
    el.dispatchEvent(new Event('input',{bubbles:true}));
    if (!guard()) throw new Error('The form changed during the answer. Inspect again.');
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
    const guard=()=>el.isConnected && current.scope.contains(el) && location.href===request.expectedUrl && guardedContext(current);
    if (['file','password','security-code'].includes(type)) throw new Error('Use the dedicated file or manual security-code workflow.');
    if (!guardedContext(current)) throw new Error('The form changed before the answer.');
    if (el.tagName === 'SELECT') {
      if (el.multiple) throw new Error('Multiple-select answers require manual input.');
      const offered=choices(entry,current.scope).filter(item=>item.label===value && !item.disabled);
      if (offered.length !== 1) throw new Error('No unique offered choice matches that exact answer.');
      el.selectedIndex=[...el.options].indexOf(offered[0].node);
      if (!guard()) throw new Error('The form changed during the answer. Inspect again.');
      el.dispatchEvent(new Event('change',{bubbles:true}));
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
      if (el.tagName === 'INPUT') dispatchText(el,value,guard);
      await wait(400);
      // Opening a menu changes aria-expanded/answered metadata; bind the
      // original nodes/labels/options while permitting this transient state.
      if (!guardedContext(current) || location.href !== request.expectedUrl || el.tagName==='INPUT' && el.value !== value) throw new Error('The choice control or its answer changed. Inspect again.');
      const listIds=[el.getAttribute('aria-controls'),el.getAttribute('aria-owns')].filter(Boolean).flatMap(item=>item.split(/\s+/));
      const lists=[...new Set(listIds.flatMap(item=>[...document.querySelectorAll('[id]')].filter(node=>node.id===item)))].filter(node=>node.getAttribute('role')==='listbox' && tools().visible(node));
      if (lists.length !== 1) throw new Error('An unambiguous accessible choice list is required. Complete this control manually.');
      const owners=[...document.querySelectorAll('[role="combobox"]')].filter(node=>[node.getAttribute('aria-controls'),node.getAttribute('aria-owns')].filter(Boolean).some(attr=>attr.split(/\s+/).includes(lists[0].id)));
      if(owners.length!==1 || owners[0]!==el || lists[0].closest('form,[role="form"],.ashby-application-form-container') && !current.scope.contains(lists[0])) throw new Error('The choice list is not uniquely associated with this form.');
      const offered=[...lists[0].querySelectorAll('[role="option"]')].filter(node=>tools().visible(node) && optionText(node) === value && node.getAttribute('aria-disabled') !== 'true');
      if (offered.length !== 1) throw new Error('No unique offered choice matches that exact answer.');
      if(!guard() || !lists[0].contains(offered[0]) || offered[0].form && !current.scope.contains(offered[0].form)) throw new Error('The choice changed. Inspect again.');
      safeChoiceClick(offered[0]);
    } else if (el.matches('input,textarea') && typeof value === 'string' && value.length <= 20000) dispatchText(el,value,guard);
    else throw new Error('This control requires manual input.');
    await wait(150);
    if (!el.isConnected || location.href !== request.expectedUrl) throw new Error('The form changed during the answer. Result is uncertain; inspect again.');
    const fresh=tools().inventory(undefined,current.ashby?current.scope:null).find(item=>item.el===el);
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
    const expected=state.resumeExpected, proof=state.resumeProof;
    const liveProof=expected?.scope===current.scope && proof?.scope===current.scope && proof.name===expected.name
      && tools().renderedResume?.(current.scope,proof.name)===proof.region;
    if ((current.ashby || expected?.scope===current.scope) && !liveProof) throw new Error('The résumé upload has no current verified acceptance evidence.');
    for (const entry of current.entries.filter(item=>item.type==='file' && resumeLabel(current.questions.find(q=>q.id===id(item.el))?.label))) {
      if ((entry.required || entry.answered) && !liveProof) throw new Error('The résumé upload has no verified acceptance evidence.');
    }
    if ([...current.scope.querySelectorAll('input,textarea,select')].some(el=>!el.disabled && el.type!=='hidden' && el.validity && !el.validity.valid)
      || [...current.scope.querySelectorAll('[aria-invalid="true"],[role="alert"]')].some(tools().visible)) throw new Error('Employer validation errors require review.');
    // An invisible reCAPTCHA badge is passive UI. The site's verification still
    // runs when its own submit control is clicked; any displayed challenge holds.
    if (challengeIndicators().some(item=>item.type!=='invisible-recaptcha-badge'))
      throw new Error('A CAPTCHA or security challenge requires manual completion.');
    const buttonLabel=current.ashby ? /^submit(?: application)?$/i : /^(submit(?: application)?|send application|apply(?: now)?)$/i;
    const eligible=el=>el.isConnected && tools().visible(el) && !el.matches(':disabled') && !el.closest('[inert],[aria-disabled="true"]');
    const buttons=[...current.scope.querySelectorAll('button,input[type="submit"]')].filter(el=>eligible(el)
      && (!el.form || el.form===current.scope || current.scope.contains(el.form)) && buttonLabel.test(clean(el.textContent || el.value)));
    const parent=current.scope.parentElement;
    if (current.ashby && parent && !['BODY','HTML'].includes(parent.tagName)) {
      buttons.push(...[...parent.querySelectorAll('button.ashby-application-form-submit-button')].filter(el=>eligible(el)
        && !current.scope.contains(el) && !el.form && !el.closest('form,[role="form"],#application-form,.ashby-application-form-container')
        && /^submit application$/i.test(clean(el.textContent))));
    }
    if (buttons.length!==1) throw new Error('A unique final application submit control could not be verified.');
    return buttons[0];
  }
  function receiptNodes() {
    return [...document.querySelectorAll('h1,h2,h3,[role="status"],#application_confirmation,.ashby-application-form-success')].filter(node=>tools().visible(node)
      && /^(?:thank you for applying[!.]?|thanks for applying[!.]?|application (?:successfully )?submitted[!.]?|your application has been (?:successfully )?submitted[!.]?|we have received your application[!.]?)$/i.test(clean(node.textContent)));
  }
  function receipt() {
    const remaining=tools().applicationScopes().some(tools().visible) || ashbyRoute() && [...document.querySelectorAll('.ashby-application-form-container')].some(tools().visible);
    const match=!remaining && receiptNodes().find(node=>!ashbyRoute() || state.attempted && state.receiptBefore && !state.receiptBefore.has(node));
    return {confirmed:!!match,confirmation:match ? clean(match.textContent) : '',evidenceType:'visible_employer_confirmation',pageUrl:location.href,observedAt:new Date().toISOString()};
  }
  function inspectionDiagnostics(current) {
    const flags=['valid','valueMissing','typeMismatch','patternMismatch','badInput','customError','tooLong','tooShort','rangeUnderflow','rangeOverflow','stepMismatch'];
    const finalControls=[...document.querySelectorAll('button,input[type="submit"]')].filter(el=>tools().visible(el) && /^(submit(?: (?:your )?application)?|send application|apply(?: now)?)$/i.test(clean(el.textContent || el.value)))
      .map(el=>({label:clean(el.textContent || el.value),inScope:current.scope?.contains(el)===true,disabled:el.disabled || el.getAttribute('aria-disabled')==='true',
        ashbyClass:[...el.classList].filter(name=>/^ashby-application-form-[a-z-]+$/.test(name)),
        inScopeParent:current.scope?.parentElement?.contains(el)===true,externalFormOwner:!!el.form && el.form!==current.scope && !current.scope?.contains(el.form)}));
    return {visible:document.visibilityState==='visible',focused:document.hasFocus(),challenges:challengeIndicators(),finalControls,controls:current.entries.map(({el})=>{
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
  function unscopedInspection() {
    // No verified form means no mutation snapshot and no research inventory.
    // A document probe can still identify the two controls under investigation
    // using fixed labels, without returning values or changing the document.
    const containers=location.hostname==='jobs.ashbyhq.com'
      ? [...document.querySelectorAll('.ashby-application-form-container')].filter(tools().visible) : [];
    const container=containers.length===1 ? containers[0] : null;
    let readOnlyContext=null;
    if(container) {try {readOnlyContext=context(container);} catch { /* Remain a diagnostic probe. */ }}
    const entries=readOnlyContext?.entries || tools().inventory().filter(entry=>/^(?:country|gender)\s*\*?$/i.test(fixedLabel(entry)));
    const diagnostics=inspectionDiagnostics({entries});
    diagnostics.targetLabels=entries.map(entry=>({questionId:id(entry.el),label:fixedLabel(entry)}));
    diagnostics.readyState=document.readyState;
    diagnostics.formCount=[...document.querySelectorAll('form,[role="form"],#application-form')].filter(tools().visible).length;
    diagnostics.inputCount=[...document.querySelectorAll('input,textarea,select,[role="combobox"]')].filter(tools().visible).length;
    diagnostics.ashbyContainers=Object.fromEntries(['.ashby-application-form-container','.ashby-application-form','.ashby-application-form-field-entry']
      .map(selector=>[selector,[...document.querySelectorAll(selector)].filter(tools().visible).length]));
    diagnostics.visibleFrameCount=[...document.querySelectorAll('iframe')].filter(tools().visible).length;
    return {inspectionComplete:false,snapshot:'',questions:readOnlyContext?.questions || [],pageUrl:location.href,diagnostics,
      source:{url:location.href,capturedAt:new Date().toISOString(),evidenceType:readOnlyContext?'live_extension_read_only_container_inventory':'live_extension_document_probe',
        wording:readOnlyContext?'exact_fixed_label':'no_question_inventory',scope:readOnlyContext?'read_only_ashby_container':'no_verified_application_form'}};
  }
  globalThis.__jobpilotLocalRun=async request=>{
    if (!request || request.expectedUrl !== location.href) throw new Error('The application URL changed.');
    if (state.busy) throw new Error('A local form action is already running.');
    state.busy=true;
    try {
      if (request.op==='receipt') return receipt();
      if (request.op==='inspect') {
        let current;
        try {current=context();}
        catch(error) {
          if (tools() && tools().applicationScopes().filter(tools().visible).length!==1) {
            state.snapshot=null;state.prepared=null;return unscopedInspection();
          }
          throw error;
        }
        const snapshot=crypto.randomUUID(); state.prepared=null;
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
        state.receiptBefore=new Set(receiptNodes());
        state.snapshot=null; state.attempted=true; button.click();
        return {attempted:true,confirmed:false,pageUrl:request.expectedUrl,message:'One click was dispatched. Check the employer receipt before counting this application.'};
      }
      state.snapshot=null; state.prepared=null;
      if (request.op==='answer') return await answer(request,current);
      if (['details','resume'].includes(request.op)) {
        const profile={...(request.profile || {})}; delete profile.disclosure_preferences;
        if (request.op==='resume') {
          const name=request.resume?.metadata?.name;
          if(typeof name!=='string' || !/^[^/\\\r\n]{1,250}\.pdf$/i.test(name)) throw new Error('A named PDF résumé is required.');
          state.resumeExpected={scope:current.scope,name};state.resumeProof=null;
          if(request.resume.metadata.verifyOnly===true) {
            const region=tools().renderedResume?.(current.scope,name);
            if(region) state.resumeProof={scope:current.scope,name,region};
            return {report:{status:region?'complete':'partial',resume:{status:region?'accepted':'prepared',message:region?'The intended résumé is visibly attached.':'No exact rendered résumé attachment was verified. Nothing was uploaded again.'}},pageUrl:request.expectedUrl};
          }
        }
        const report=await globalThis.__jobpilotRun({action:request.op,profile:request.op==='details'?profile:{},resume:request.op==='resume'?request.resume:null,
          requireVisible:true,localScope:current.ashby?current.scope:null,localGuard:()=>location.href===request.expectedUrl && guardedContext(current)});
        if (request.op==='resume' && location.href===request.expectedUrl && current.scope.isConnected) {
          const name=state.resumeExpected.name, region=tools().renderedResume?.(current.scope,name);
          let sameScope=false;
          try {sameScope=context().scope===current.scope;} catch { /* The replacement is held. */ }
          if (region && sameScope) {
            state.resumeProof={scope:current.scope,name,region};
            report.resume={status:'accepted',message:'The intended résumé is visibly attached.'};
          }
        }
        return {report,pageUrl:request.expectedUrl};
      }
      throw new Error('Unknown local form operation.');
    } finally {state.busy=false;}
  };
})();
