// Injected into Chrome's isolated world only after the applicant clicks a panel action.
(() => {
  const hosts = new Set(['boards.greenhouse.io','job-boards.greenhouse.io','boards-eu.greenhouse.io','job-boards.eu.greenhouse.io','jobs.lever.co','jobs.eu.lever.co','jobs.ashbyhq.com']);
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  const norm = value => String(value || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9+]+/g, ' ').trim();
  const visible = el => {
    if (!el) return false;
    const style = getComputedStyle(el);
    return !!el.getClientRects().length && style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
  };
  const wrapper = el => el.closest(".field, .field-wrapper, .form-field, [data-field], [class*='FieldWrapper'], [class*='FileUpload'], fieldset") || el.parentElement;
  function labelText(node) {
    if (!node) return '';
    const copy = node.cloneNode(true);
    for (const control of copy.querySelectorAll('input, textarea, select, button, [role="combobox"], [role="listbox"]')) control.remove();
    return copy.textContent;
  }
  function labels(el) {
    const items = [el.getAttribute('aria-label')];
    for (const id of (el.getAttribute('aria-labelledby') || '').split(/\s+/)) if (id) items.push(labelText(document.getElementById(id)));
    for (const label of el.labels || []) items.push(labelText(label));
    if (!items.some(Boolean)) items.push(labelText(wrapper(el)?.querySelector("label, legend, [class*='Label'], [class*='label']")));
    return items.filter(Boolean).map(s => s.trim()).filter(Boolean);
  }
  const identifiers = el => [...labels(el), el.name, el.id, el.placeholder, el.getAttribute('autocomplete')].filter(Boolean).map(norm);
  const labelOf = el => (labels(el)[0] || el.name || el.id || el.type || 'Unlabeled field').trim().slice(0,180);
  const required = el => el.required || el.getAttribute('aria-required') === 'true' || labels(el).some(t => /\*|\brequired\b/i.test(t));
  const reactControl = el => el.closest("[class*='select__control'], [class*='Select__control'], [class*='react-select__control']");
  const selectedReact = el => reactControl(el)?.querySelector("[class*='single-value'], [class*='multi-value'], [class*='singleValue'], [class*='multiValue']")?.textContent?.trim() || '';
  const combo = el => el.getAttribute('role') === 'combobox' || !!reactControl(el);
  const placeholderOption = o => !o || o.disabled || !o.value || /^(select|choose|please select|please choose)(\b|$)/i.test(o.text.trim());
  const attachedText = container => /\.(pdf|docx?|rtf)\b|remove (file|resume)|replace (file|resume)|resume.*uploaded/i.test(container?.innerText || '');
  function answered(el) {
    if (['checkbox','radio'].includes(el.type)) return el.checked;
    if (el.type === 'file') return !!el.files?.length || attachedText(wrapper(el));
    if (el.tagName === 'SELECT') return !placeholderOption(el.selectedOptions[0]);
    if (combo(el)) return !!selectedReact(el) || (!reactControl(el) && !!el.value && el.getAttribute('aria-expanded') !== 'true');
    return !!String(el.value || '').trim();
  }
  function inventory() {
    const controls = [...document.querySelectorAll("input, textarea, select, [role='combobox']:not(input), [role='checkbox']:not(input), [role='radiogroup']")];
    const seenRadio = new Set();
    return controls.filter(el => !el.disabled && !el.readOnly && !['hidden','submit','button','reset'].includes(el.type) && (visible(el) || (el.type === 'file' && visible(wrapper(el))))).flatMap(el => {
      if (el.type === 'radio') {
        const key = (el.form?.id || '') + ':' + el.name;
        if (el.name && seenRadio.has(key)) return [];
        if (el.name) seenRadio.add(key);
        const group = el.name ? controls.filter(other => other.type === 'radio' && other.name === el.name && other.form === el.form) : [el];
        const legend = el.closest('fieldset')?.querySelector('legend')?.textContent;
        return [{el,label:(legend || labelOf(el)).slice(0,180),type:'radio',required:group.some(required),answered:group.some(r => r.checked)}];
      }
      if (el.getAttribute('role') === 'radiogroup') {
        if (el.querySelector("input[type='radio']")) return [];
        return [{el,label:labelOf(el),type:'radio',required:required(el),answered:!!el.querySelector("[aria-checked='true']")}];
      }
      if (el.getAttribute('role') === 'checkbox' && el.tagName !== 'INPUT') return [{el,label:labelOf(el),type:'checkbox',required:required(el),answered:el.getAttribute('aria-checked') === 'true'}];
      if (el.getAttribute('role') === 'combobox' && el.tagName !== 'INPUT' && el.querySelector("input[role='combobox']")) return [];
      return [{el,label:labelOf(el),type:combo(el) ? 'combobox' : el.type || el.tagName.toLowerCase(),required:required(el),answered:answered(el)}];
    });
  }
  const DENY = /referr|referred|pronoun|citizen|nationality|ethnic|race\b|racial|gender|sex\b|disabil|veteran|consent|agree|certif|signature|privacy|salary|compensation|desired pay|relocat|how.*hear|years.*experience|how many years|why\b|describe|explain|tell (us|me)|cover letter|reason for leaving|emergency|spouse|recruiter|employee (id|number|name)|sponsorship.*type|type.*sponsorship|not authorized/;
  function historyContext(el, profile) {
    const groups = [...document.querySelectorAll('fieldset, [data-jobpilot-section]')];
    const classify = group => {
      const title = norm(group.getAttribute('data-jobpilot-section') || group.querySelector(':scope > legend, :scope > h2, :scope > h3')?.textContent);
      return /^(work experience|employment history|work history|professional experience)(\s+\d+)?$/.test(title) ? 'work_history' : /^(education|education history)(\s+\d+)?$/.test(title) ? 'education' : null;
    };
    const group = el.closest('fieldset, [data-jobpilot-section]');
    const type = group && classify(group);
    if (!type) return null;
    const explicitNumber = norm(group.getAttribute('data-jobpilot-section') || group.querySelector(':scope > legend, :scope > h2, :scope > h3')?.textContent).match(/\s(\d+)$/);
    const index = explicitNumber ? Number(explicitNumber[1]) - 1 : groups.filter(g => classify(g) === type).indexOf(group);
    return {type,index,row:profile[type]?.[index]};
  }
  function ruleFor(el, profile) {
    const ids = identifiers(el);
    if (ids.some(id => DENY.test(id))) return {reason:'Answer this question yourself'};
    const exact = re => ids.some(id => re.test(id.replace(/\s+required$/, '')));
    const history = historyContext(el, profile);
    if (history) {
      if (!history.row) return {reason:'No saved history for this row'};
      const rules = history.type === 'work_history'
        ? [['company',/^(company|company name|employer|employer name)$/],['title',/^(title|job title|position)$/],['start_date',/^start date$/],['end_date',/^end date$/]]
        : [['school',/^(school|school name|university|institution|institution name)$/],['degree',/^(degree|degree type)$/],['field',/^(field|field of study|major)$/],['start_date',/^start date$/],['end_date',/^(end date|graduation date)$/]];
      const match = rules.find(([,re]) => exact(re));
      if (!match) return {reason:'History field needs review'};
      const key = match[0], value = history.row[key] || '';
      if (key === 'end_date' && history.row.current) return {reason:'Current job: leave end date to you'};
      if (/date$/.test(key) && ((el.type === 'date' && !/^\d{4}-\d{2}-\d{2}$/.test(value)) || (el.type === 'month' && !/^\d{4}-\d{2}$/.test(value)))) return {reason:'Saved date does not match this field format'};
      return {key:`${history.type}.${history.index}.${key}`,value};
    }
    if (ids.some(id => /company|employer|school|university|institution|education|start date|end date|graduation/.test(id))) return {reason:'History context is not clear'};
    const usProfile = /^(us|u s|usa|united states|united states of america)$/.test(norm(profile.country));
    if (exact(/^(are you )?(legally )?(authorized|authorised|eligible) to work in (the )?(united states|us|u s|usa)( of america)?$/) || exact(/^(do you have( the)? )?(legal )?(right|authorization|authorisation) to work in (the )?(united states|us|u s|usa)$/)) return usProfile ? {key:'work_authorized',value:typeof profile.work_authorized === 'boolean' ? (profile.work_authorized ? 'Yes' : 'No') : ''} : {reason:'Authorization country does not match your saved profile'};
    if (exact(/^(do|will|would) you (now or in the future )?(require|need)( visa)? sponsorship( (now|in the future|for employment|to work|now or in the future))* in (the )?(united states|us|u s|usa)$/)) return usProfile ? {key:'requires_sponsorship',value:typeof profile.requires_sponsorship === 'boolean' ? (profile.requires_sponsorship ? 'Yes' : 'No') : ''} : {reason:'Sponsorship country does not match your saved profile'};
    const full = [profile.first_name,profile.last_name].filter(Boolean).join(' ');
    const rules = [
      ['first_name',/^(first name|given name|firstname|givenname)$/,profile.first_name],['last_name',/^(last name|family name|surname|lastname|familyname)$/,profile.last_name],['full_name',/^(full name|your name|applicant name|name|name full)$/,full],
      ['email',/^(email|e mail|email address)$/,profile.email],['phone',/^(phone|phone number|mobile|mobile number|cell phone|telephone|tel)$/,profile.phone],
      ['city',/^(city|current city|address level2)$/,profile.city],['state',/^(state|state province|province|address level1)$/,profile.state],['postal_code',/^(zip|zip code|postal code|zipcode)$/,profile.postal_code],
      ['country',/^(country|country of residence|residence country|country name)$/,profile.country],['location',/^(current location|location|your location)$/,[profile.city,profile.state].filter(Boolean).join(', ')],
      ['linkedin_url',/^(linkedin|linkedin profile|linkedin url|linkedin profile url)$/,profile.linkedin_url],['github_url',/^(github|github profile|github url)$/,profile.github_url],['portfolio_url',/^(portfolio|portfolio url|website|personal website|personal site)$/,profile.portfolio_url],['current_title',/^(current title|current job title|current role)$/,profile.current_title],
    ];
    const found = rules.find(([,re]) => exact(re));
    return found ? {key:found[0],value:found[2] || ''} : {reason:'No verified mapping for this question'};
  }
  function exactOption(options, value) {
    const n = norm(value);
    const aliases = /^(us|usa|united states|united states of america)$/.test(n) ? new Set(['us','usa','united states','united states of america']) : new Set([n]);
    const matches = options.filter(o => !o.disabled && o.getAttribute('aria-disabled') !== 'true' && aliases.has(norm(o.textContent)));
    return matches.length === 1 ? matches[0] : null;
  }
  function setText(el, value) {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto,'value')?.set;
    if (!setter) return false;
    setter.call(el,value);
    el.dispatchEvent(new Event('input',{bubbles:true}));
    el.dispatchEvent(new Event('change',{bubbles:true}));
    return true;
  }
  async function fillCombo(el, value) {
    const ctrl = reactControl(el) || el, input = el.tagName === 'INPUT' ? el : ctrl.querySelector('input');
    const before = input?.value || '';
    input?.focus();
    ctrl.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,button:0}));
    ctrl.dispatchEvent(new MouseEvent('mouseup',{bubbles:true,button:0}));
    if (input && !/^(Yes|No)$/.test(value)) setText(input,value);
    let options = [];
    for (let attempt=0; attempt<10; attempt++) {
      await pause(100);
      const listId = el.getAttribute('aria-controls') || el.getAttribute('aria-owns') || input?.getAttribute('aria-controls');
      const menu = listId ? document.getElementById(listId) : document;
      options = [...(menu || document).querySelectorAll("[role='option'], [class*='select__option']")].filter(visible);
      if (options.length) break;
    }
    const option = exactOption(options,value);
    if (option) {
      option.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,button:0}));
      option.dispatchEvent(new MouseEvent('mouseup',{bubbles:true,button:0}));
      option.click();
      await pause(100);
    }
    ctrl.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
    input?.blur();
    const ok = !!option && (norm(selectedReact(el)) === norm(value) || (!reactControl(el) && norm(el.value || el.textContent) === norm(value)));
    if (!ok && input) setText(input,before);
    return ok;
  }
  function resumeTarget() {
    const matches = [...document.querySelectorAll("input[type='file']")].filter(el => identifiers(el).some(id => /\b(resume|cv|curriculum vitae)\b/.test(id)) && !identifiers(el).some(id => /cover letter/.test(id)));
    return matches.length === 1 ? {el:matches[0],wrapper:wrapper(matches[0])} : null;
  }
  async function attachResume(resume) {
    const target = resumeTarget();
    if (!target) return {status:'no-target',message:'No unique résumé upload field found. Attach the file manually.'};
    if (target.el.files?.length || target.el.value || attachedText(target.wrapper)) return {status:'already-attached',message:'An existing attachment was preserved.'};
    if (!resume?.dataUrl || !resume?.metadata?.name) return {status:'failed',message:'Choose a résumé with a local file first.'};
    const filename = resume.metadata.name;
    try {
      if (!/^data:application\/pdf;base64,/.test(resume.dataUrl)) throw new Error('A PDF résumé is required');
      const raw = atob(resume.dataUrl.split(',')[1]), bytes = Uint8Array.from(raw,char => char.charCodeAt(0));
      const transfer = new DataTransfer();
      transfer.items.add(new File([bytes],filename,{type:'application/pdf'}));
      target.el.files = transfer.files;
      target.el.dispatchEvent(new Event('input',{bubbles:true}));
      target.el.dispatchEvent(new Event('change',{bubbles:true}));
      for (let attempt=0; attempt<16; attempt++) {
        await pause(150);
        const scope = target.el.isConnected ? wrapper(target.el) : target.wrapper, text = scope?.innerText || '';
        const alerts = [...(scope?.querySelectorAll("[role='alert'], .error, [class*='Error'], [class*='error']") || [])].filter(visible).map(el => el.textContent).join(' ');
        if (/failed|invalid|too large|unsupported|not (allowed|accepted)|error|unable/i.test(alerts)) return {status:'failed',filename,message:'The form reported an upload error. Review the page.'};
        if (text.includes(filename) && !/uploading|processing|please wait/i.test(text)) return {status:'accepted',filename,message:'The form displays this résumé attachment. Review it before submitting.'};
      }
      return {status:'prepared',filename,message:'File selected; the form has not confirmed acceptance. Check the attachment on the page.'};
    } catch (error) { return {status:'failed',filename,message:error.message || 'The file could not be attached. Upload it manually.'}; }
  }
  globalThis.__jobpilotRun = async ({action='inspect',profile={},resume=null}={}) => {
    if (!['inspect','details','resume'].includes(action)) throw new Error('Unknown JobPilot action');
    const report = {action,filled:[],skipped:[],failed:[],required:[],resume:{status:'not-requested',message:''},provider:location.hostname.includes('greenhouse') ? 'Greenhouse' : location.hostname.includes('lever') ? 'Lever' : location.hostname.includes('ashby') ? 'Ashby' : 'Unsupported',pageUrl:location.href};
    if (!hosts.has(location.hostname) || location.protocol !== 'https:') { report.skipped.push({label:'Page',reason:'This frame is outside the supported application hosts'}); return report; }
    if (action === 'details') {
      for (const entry of inventory()) {
        const {el,label,type} = entry;
        if (['radio','checkbox','file','password'].includes(type)) { report.skipped.push({label,reason:type === 'file' ? 'Use Attach résumé separately' : 'Answer this question yourself'}); continue; }
        if (entry.answered) { report.skipped.push({label,reason:'Your existing answer was preserved'}); continue; }
        const rule = ruleFor(el,profile);
        if (rule.reason) { report.skipped.push({label,reason:rule.reason}); continue; }
        if (!rule.value) { report.skipped.push({label,reason:'No saved profile value'}); continue; }
        try {
          let ok = false;
          if (el.tagName === 'SELECT') {
            const option = exactOption([...el.options].filter(o => !placeholderOption(o)),rule.value);
            if (option) { el.value=option.value; el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); await pause(75); ok=el.value === option.value; }
          } else if (type === 'combobox') ok = await fillCombo(el,rule.value);
          else { setText(el,rule.value); await pause(75); ok=el.value === String(rule.value); }
          if (ok) report.filled.push({label,key:rule.key});
          else report.failed.push({label,reason:'The form did not accept an exact match. Review this field.'});
        } catch (_) { report.failed.push({label,reason:'This control needs manual entry'}); }
      }
    } else if (action === 'resume') report.resume = await attachResume(resume);
    report.required = inventory().filter(item => item.required && !item.answered).map(({label,type}) => ({label,type}));
    return report;
  };
})();
