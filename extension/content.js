// Injected into Chrome's isolated world only after the applicant clicks a panel action.
(() => {
  const hosts = new Set(['boards.greenhouse.io','job-boards.greenhouse.io','boards-eu.greenhouse.io','job-boards.eu.greenhouse.io','jobs.lever.co','jobs.eu.lever.co','jobs.ashbyhq.com']);
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  const norm = value => String(value || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9+]+/g, ' ').trim();
  const visible = el => {
    if (!el?.getClientRects().length) return false;
    for (let node=el; node?.nodeType===1; node=node.parentElement) {
      const style=getComputedStyle(node);
      if (style.display==='none' || style.visibility==='hidden' || style.opacity==='0') return false;
    }
    return true;
  };
  function workdayPage() {
    const url = new URL(location.href), path = url.pathname.split('/').filter(Boolean);
    if (url.protocol !== 'https:' || url.username || url.password || url.port) return false;
    if (/^[a-z]{2,3}(?:-[a-z0-9]{2,8}){1,2}$/i.test(path[0] || '')) path.shift();
    const index = /^[a-z0-9][a-z0-9-]*\.wd\d+\.myworkdayjobs\.com$/.test(url.hostname) ? 1
      : /^wd\d+\.myworkdaysite\.com$/.test(url.hostname) && path[0] === 'recruiting' && path[1] ? 3 : -1;
    return index > 0 && !!path[index - 1] && path[index] === 'job' && !!path[index + 1] && path[index + 1] !== 'apply';
  }
  const WORKDAY_STEPS = ['applyFlowMyInfoPage', 'applyFlowMyExpPage', 'applyFlowAutoFillPage', 'applyFlowPrimaryQuestionsPage',
    'applyFlowSecondaryQuestionsPage', 'applyFlowSupplementaryQuestionsPage', 'applyFlowSelfIdentifyPage',
    'applyFlowVoluntaryDisclosuresPage', 'applyFlowMyAvailabilityPage', 'applyFlowReviewPage'];
  const workdayStepIds = new WeakMap();
  let nextWorkdayStep = 1;
  function workdayState() {
    const empty = message => ({hasForm: false, canFillDetails: false, canAttachResume: false, stepKey: '', message, root: null, step: null});
    if (!workdayPage()) return empty('Open a supported Workday job application.');
    const auth = [...document.querySelectorAll('[data-automation-id="signInContent"], [data-automation-id="signInFormo"], input[type="password"], [role="dialog"]')]
      .some(el => visible(el) && (el.matches('input[type="password"], [data-automation-id="signInContent"], [data-automation-id="signInFormo"]')
        || /\b(sign in|log in|create (?:an? )?account|forgot password)\b/i.test(el.getAttribute('aria-label') || el.querySelector('h1, h2, [data-automation-id="dialogLabel"]')?.textContent || '')));
    if (auth) return empty('Complete Workday sign-in or account steps yourself, then inspect the visible application step.');
    const roots = [...document.querySelectorAll('[data-automation-id="applyFlowPage"]')].filter(visible);
    if (roots.length !== 1) return empty('Open one visible Workday application step before using autofill.');
    const steps = [...roots[0].querySelectorAll(WORKDAY_STEPS.map(id => `[data-automation-id="${id}"]`).join(','))].filter(visible);
    if (steps.length !== 1) return empty('The current Workday application step could not be verified. Wait for it to load and inspect again.');
    const step = steps[0], name = step.getAttribute('data-automation-id');
    if (!workdayStepIds.has(step)) workdayStepIds.set(step, nextWorkdayStep++);
    const progress = roots[0].querySelector('[data-automation-id="progressBarActiveStep"]');
    const stepKey = `${name}:${workdayStepIds.get(step)}:${norm(progress?.textContent).slice(0,200)}`;
    return {hasForm: true, canFillDetails: ['applyFlowMyInfoPage', 'applyFlowMyExpPage'].includes(name),
      canAttachResume: ['applyFlowMyExpPage', 'applyFlowAutoFillPage'].includes(name), stepKey, root: roots[0], step,
      message: ['applyFlowMyInfoPage', 'applyFlowMyExpPage', 'applyFlowAutoFillPage'].includes(name)
        ? 'Only the current visible Workday step is supported. Review it and move to the next step yourself.'
        : 'Complete Workday questions, disclosures, availability and final review yourself.'};
  }
  globalThis.__jobpilotWorkdayScope = () => {
    const {root, step, ...metadata} = workdayState(); return metadata;
  };
  const wrapper = el => (workdayPage() && (el.closest('[data-automation-id="resumeUpload"]') || el.closest('[data-automation-id^="formField-"]')))
    || el.closest(".ashby-application-form-field-entry, .file-upload, .field, .field-wrapper, .form-field, [data-field], [class*='FieldWrapper'], [class*='FileUpload'], fieldset") || el.parentElement;
  const ashbyQuestion = el => el.closest('.ashby-application-form-field-entry, fieldset')?.querySelector('.ashby-application-form-question-title');
  function labelText(node) {
    if (!node) return '';
    const copy = node.cloneNode(true);
    for (const control of copy.querySelectorAll('input, textarea, select, button, [role="combobox"], [role="listbox"]')) control.remove();
    return copy.textContent;
  }
  function labels(el) {
    if (workdayPage() && el.matches('button[aria-haspopup="listbox"]')) {
      const field = el.closest('[data-automation-id^="formField-"]');
      const local = node => !!field && field.contains(node) && node.closest('[data-automation-id^="formField-"]') === field;
      const associated = el.id ? [...(field?.querySelectorAll('label') || [])].filter(node => local(node) && node.htmlFor === el.id) : [];
      const references = (el.getAttribute('aria-labelledby') || '').split(/\s+/).map(id => document.getElementById(id))
        .filter(node => node && node.matches('label, legend') && local(node));
      // Workday's button aria-label includes its selected answer. Keep only a
      // verified fixed question label in reports and field matching.
      if (associated.length > 1) return [];
      const fixed = associated.length ? associated : references;
      if (fixed.length) return fixed.map(labelText).map(text => text.trim()).filter(Boolean);
      const fieldset = el.closest('fieldset');
      if (!fieldset || !local(fieldset)) return [];
      const choices = [...fieldset.querySelectorAll('input, textarea, select, button[aria-haspopup="listbox"], [role="combobox"]')]
        .filter(node => visible(node) && local(node) && node.closest('fieldset') === fieldset);
      const legends = [...fieldset.querySelectorAll(':scope > legend')].filter(visible);
      if (choices.length !== 1 || choices[0] !== el || legends.length !== 1) return [];
      return [labelText(legends[0]).trim()].filter(Boolean);
    }
    const items = [el.getAttribute('aria-label')];
    for (const id of (el.getAttribute('aria-labelledby') || '').split(/\s+/)) if (id) items.push(labelText(document.getElementById(id)));
    for (const label of el.labels || []) items.push(labelText(label));
    if (el.type === 'file') items.unshift(labelText(wrapper(el)?.querySelector(".upload-label, [id^='upload-label'], label, legend")));
    if (!items.some(Boolean)) items.push(labelText(ashbyQuestion(el) || wrapper(el)?.querySelector("label, legend, [class*='Label'], [class*='label'], h3, h4")));
    return items.filter(Boolean).map(s => s.trim()).filter(Boolean);
  }
  const WORKDAY_FIELDS = {firstName:'First name',lastName:'Last name',fullName:'Full name',email:'Email',phoneNumber:'Phone number',
    addressLine1:'Address line 1',city:'City',postalCode:'Postal code',country:'Country',countryRegion:'State',
    companyName:'Company',jobTitle:'Job title',schoolName:'School',school:'School',degree:'Degree',fieldOfStudy:'Field of study',
    startDate:'Start date',endDate:'End date'};
  const identifiers = el => {
    if (!workdayPage()) return [...labels(el), el.name, el.id, el.placeholder, el.getAttribute('autocomplete')].filter(Boolean).map(norm);
    const named = labels(el);
    if (named.length) return named.map(norm); // A custom question must never inherit an unrelated technical ID.
    if (el.matches('button[aria-haspopup="listbox"]')) return []; // An unlabeled choice needs manual review.
    const field = el.getAttribute('data-automation-id') || wrapper(el)?.getAttribute('data-automation-id')?.replace(/^formField-/, '');
    return WORKDAY_FIELDS[field] ? [norm(WORKDAY_FIELDS[field])] : [];
  };
  const labelOf = el => (labels(el)[0] || el.name || el.id || el.type || 'Unlabeled field').trim().slice(0,180);
  const required = el => {
    if (el.required || el.getAttribute('aria-required')==='true' || labels(el).some(t=>/\*|\brequired\b/i.test(t))) return true;
    // Ashby's asterisk is generated by CSS, outside label.textContent.
    const question=ashbyQuestion(el);
    return !!question && ['::before','::after'].some(pseudo=>getComputedStyle(question,pseudo).content.includes('*'));
  };
  const reactControl = el => el.closest("[class*='select__control'], [class*='Select__control'], [class*='react-select__control']");
  const selectedReact = el => reactControl(el)?.querySelector("[class*='single-value'], [class*='multi-value'], [class*='singleValue'], [class*='multiValue']")?.textContent?.trim() || '';
  const workdaySelect = el => workdayPage() && el.tagName === 'BUTTON' && el.getAttribute('aria-haspopup') === 'listbox';
  const selectedCombo = el => workdaySelect(el) ? (/^(select|choose)(\b|$)|^[-–—]+$/i.test(el.textContent.trim()) ? '' : el.textContent.trim()) : selectedReact(el);
  const combo = el => el.getAttribute('role') === 'combobox' || !!reactControl(el) || workdaySelect(el);
  const placeholderOption = o => !o || o.disabled || !o.value || /^(select|choose|please select|please choose)(\b|$)/i.test(o.text.trim());
  const attachedText = container => workdayPage()
    ? [...(container?.querySelectorAll('[data-automation-id="file-upload-item-name"]') || [])].some(el => visible(el) && el.textContent.trim())
    : /\b[^\s]+\.(pdf|docx?|rtf)\b|remove (file|resume)|replace (file|resume)|resume.*uploaded/i.test((container?.innerText || '').replace(/Accepted file types:[^\n]*/gi, ''));
  function workdayMultiSelect(el) {
    if (!workdayPage() || el.tagName !== 'INPUT' || ['hidden','file','radio','checkbox','password','submit','button','reset'].includes(el.type)) return null;
    const promptSelector = '[data-automation-id="multiSelectContainer"]', fieldSelector = '[data-automation-id^="formField-"]';
    const prompt = el.closest(promptSelector);
    if (!prompt || !workdayState().step?.contains(prompt)) return null;
    // Search text is not a selected answer. Ambiguous or empty prompts stay manual.
    const manual = {selected:false}, field = el.closest(fieldSelector);
    if (!field || !field.contains(prompt) || prompt.closest(fieldSelector) !== field || !visible(prompt)) return manual;
    const prompts = [...field.querySelectorAll(promptSelector)].filter(node => visible(node) && node.closest(fieldSelector) === field);
    const inputs = [...prompt.querySelectorAll('input')].filter(node => visible(node) && !node.disabled && node.closest(promptSelector) === prompt && node.closest(fieldSelector) === field);
    if (prompts.length !== 1 || prompts[0] !== prompt || inputs.length !== 1 || inputs[0] !== el) return manual;
    const listSelector = '[data-automation-id="selectedItemList"][role="listbox"]', itemSelector = '[data-automation-id="selectedItem"][role="option"]';
    const lists = [...prompt.querySelectorAll(listSelector)].filter(node => visible(node) && node.closest(fieldSelector) === field && node.closest(promptSelector) === prompt);
    if (lists.length !== 1) return manual;
    const selected = [...lists[0].querySelectorAll(itemSelector)].some(item => {
      if (!visible(item) || item.closest(listSelector) !== lists[0] || item.closest(fieldSelector) !== field || item.closest(promptSelector) !== prompt) return false;
      const options = [...item.querySelectorAll('[data-automation-id="promptOption"]')].filter(node => visible(node)
        && node.closest(itemSelector) === item && node.closest(fieldSelector) === field && node.closest(promptSelector) === prompt);
      return options.length === 1 && !!options[0].textContent.trim();
    });
    return {selected};
  }
  function answered(el) {
    const prompt = workdayMultiSelect(el);
    if (prompt) return prompt.selected;
    if (['checkbox','radio'].includes(el.type)) return el.checked;
    if (el.type === 'file') return !!el.files?.length || attachedText(wrapper(el));
    if (el.tagName === 'SELECT') return !placeholderOption(el.selectedOptions[0]);
    if (combo(el)) return !!selectedCombo(el) || (!workdaySelect(el) && !reactControl(el) && !!el.value && el.getAttribute('aria-expanded') !== 'true');
    return !!String(el.value || '').trim();
  }
  function workdayRadioQuestion(el) {
    if (!workdayPage() || !el.name) return null;
    const field = el.closest('[data-automation-id^="formField-"]');
    const question = el.closest('[aria-required][aria-labelledby]');
    if (!field || question?.closest('[data-automation-id^="formField-"]') !== field) return null;
    const radios = [...question.querySelectorAll('input[type="radio"]')];
    if (!radios.length || radios.some(other => other.name !== el.name || other.form !== el.form
      || other.closest('[data-automation-id^="formField-"]') !== field
      || other.closest('[aria-required][aria-labelledby]') !== question)) return null;
    const references = question.getAttribute('aria-labelledby').trim().split(/\s+/).map(id => document.getElementById(id));
    if (!references.length || references.some(node => !node || !field.contains(node))) return null;
    const label = references.map(labelText).join(' ').trim();
    return label ? {label,required:question.getAttribute('aria-required') === 'true',radios,question,references} : null;
  }
  function workdayRadioProxy(el, metadata) {
    if (!metadata || !el.getClientRects().length || getComputedStyle(el).opacity !== '0') return false;
    // Workday paints the radio beside a transparent native input. Allow only
    // this input's opacity; hidden questions, labels and ancestors stay hidden.
    for (let node=el; node?.nodeType===1; node=node.parentElement) {
      const style=getComputedStyle(node);
      if (style.display==='none' || style.visibility==='hidden' || node!==el && style.opacity==='0') return false;
    }
    return visible(metadata.question) && metadata.references.every(visible)
      && [...(el.labels || [])].some(label => metadata.question.contains(label) && visible(label) && labelText(label).trim());
  }
  function workdayCheckboxProxy(el) {
    if (el.type !== 'checkbox' || !workdayPage() || !el.id || getComputedStyle(el).opacity !== '0'
      || ![...el.getClientRects()].some(rect => rect.width > 0 && rect.height > 0)) return false;
    const field = el.closest('[data-automation-id^="formField-"]');
    if (!field || !workdayState().step?.contains(el)) return false;
    for (let node=el; node?.nodeType===1; node=node.parentElement) {
      const style=getComputedStyle(node);
      if (style.display==='none' || style.visibility==='hidden' || node!==el && style.opacity==='0') return false;
    }
    const associated = [...(el.labels || [])].filter(visible);
    return associated.length === 1 && associated[0].htmlFor === el.id && field.contains(associated[0])
      && associated[0].closest('[data-automation-id^="formField-"]') === field && !!labelText(associated[0]).trim();
  }
  function inventory(onError = () => {}) {
    const scopes = applicationScopes();
    const controls = [...document.querySelectorAll("input, textarea, select, [role='combobox']:not(input), button[aria-haspopup='listbox'], [role='checkbox']:not(input), [role='radiogroup'], .ashby-application-form-input-yesno")]
      .filter(el => (!scopes.length || scopes.some(scope => scope.contains(el))) && !unrelatedControl(el));
    const seenRadio = new Set();
    return controls.flatMap(el => { try {
      const metadata=el.type === 'radio' ? workdayRadioQuestion(el) : null;
      const choiceProxy=['radio','checkbox'].includes(el.type) && visible(el.closest('.ashby-application-form-input-radio-group-option, .ashby-application-form-input-checkbox-group-option'))
        || workdayRadioProxy(el,metadata) || workdayCheckboxProxy(el);
      if (el.disabled || el.matches(':disabled') || el.readOnly || ['hidden','submit','reset'].includes(el.type) || el.type === 'button' && !workdaySelect(el) || !(visible(el) || choiceProxy || (el.type === 'file' && visible(wrapper(el))))) return [];
      if (el.matches('.ashby-application-form-input-yesno')) return [{el,label:labelOf(el),type:'choice',required:required(el),answered:!!el.querySelector("button[aria-pressed='true']")}];
      if (el.type === 'radio') {
        if (seenRadio.has(el)) return [];
        const workday = workdayPage(), field = workday ? el.closest('[data-automation-id^="formField-"]') : null;
        const group = (metadata ? metadata.radios.filter(other => controls.includes(other)) : el.name ? controls.filter(other => other.type === 'radio' && other.name === el.name && other.form === el.form
          && (!workday || other.closest('[data-automation-id^="formField-"]') === field)) : [el]);
        group.forEach(radio=>seenRadio.add(radio));
        const question = metadata?.label || labelText(ashbyQuestion(el)) || el.closest('fieldset')?.querySelector('legend')?.textContent;
        return [{el,label:(question || labelOf(el)).trim().slice(0,180),type:'radio',required:!!metadata?.required || group.some(required),answered:group.some(r => r.checked)}];
      }
      if (el.getAttribute('role') === 'radiogroup') {
        if (el.querySelector("input[type='radio']")) return [];
        return [{el,label:labelOf(el),type:'radio',required:required(el),answered:!!el.querySelector("[aria-checked='true']")}];
      }
      if (el.getAttribute('role') === 'checkbox' && el.tagName !== 'INPUT') return [{el,label:labelOf(el),type:'checkbox',required:required(el),answered:el.getAttribute('aria-checked') === 'true'}];
      if (el.getAttribute('role') === 'combobox' && el.tagName !== 'INPUT' && el.querySelector("input[role='combobox']")) return [];
      return [{el,label:labelOf(el),type:combo(el) ? 'combobox' : el.type || el.tagName.toLowerCase(),required:required(el),answered:answered(el)}];
    } catch (_) { onError('A form control could not be inspected. Review the page.'); return []; } });
  }
  const DENY = /\breferr|\breferred\b|pronoun|citizen|nationality|ethnic|race\b|racial|gender|sex\b|disabil|veteran|consent|agree|certif|signature|privacy|salary|compensation|desired pay|relocat|how.*hear|years.*experience|how many years|why\b|describe|explain|tell (us|me)|cover letter|reason for leaving|emergency|spouse|recruiter|employee (id|number|name)|sponsorship.*type|type.*sponsorship|not authorized/;
  function historyContext(el, profile) {
    if (workdayPage()) {
      for (let group = el.parentElement; group; group = group.parentElement) {
        if (!group.matches('[role="group"][aria-labelledby], fieldset')) continue;
        const title = norm((group.getAttribute('aria-labelledby') || '').split(/\s+/).map(id => document.getElementById(id)?.textContent || '').join(' ')
          || group.querySelector(':scope > legend, :scope > h2, :scope > h3')?.textContent);
        const numbered = /^(work experience|employment history|work history|professional experience|education) (\d+)$/.exec(title);
        if (!numbered) continue;
        const type = numbered[1] === 'education' ? 'education' : 'work_history', index = Number(numbered[2]) - 1;
        return {type,index,row:profile[type]?.[index]};
      }
      return null; // Do not shift saved history based on which rows happen to be visible.
    }
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
        : [['school',/^(school|school name|school or university|university|institution|institution name)$/],['degree',/^(degree|degree type)$/],['field',/^(field|field of study|major)$/],['start_date',/^start date$/],['end_date',/^(end date|graduation date)$/]];
      const match = rules.find(([,re]) => exact(re));
      if (!match) return {reason:'History field needs review'};
      const key = match[0], value = history.row[key] || '';
      if (key === 'end_date' && history.row.current) return {reason:'Current job: leave end date to you'};
      if (/date$/.test(key) && (workdayPage() && !['date','month'].includes(el.type) || (el.type === 'date' && !/^\d{4}-\d{2}-\d{2}$/.test(value)) || (el.type === 'month' && !/^\d{4}-\d{2}$/.test(value)))) return {reason:'Saved date does not match this field format'};
      return {key:`${history.type}.${history.index}.${key}`,value};
    }
    if (ids.some(id => /company|employer|school|university|institution|education|start date|end date|graduation/.test(id))) return {reason:'History context is not clear'};
    const usProfile = /^(us|u s|usa|united states|united states of america)$/.test(norm(profile.country));
    if (exact(/^are you (currently )?located in (the )?(united states|us|u s|usa)$/)) return {key:'located_in_us',value:profile.country ? (usProfile ? 'Yes' : 'No') : ''};
    if (exact(/^(are you )?(legally )?(authorized|authorised|eligible) to work in (the )?(united states|us|u s|usa)( of america)?$/) || exact(/^(do you have( the)? )?(legal )?(right|authorization|authorisation) to work in (the )?(united states|us|u s|usa)$/)) return usProfile ? {key:'work_authorized',value:typeof profile.work_authorized === 'boolean' ? (profile.work_authorized ? 'Yes' : 'No') : ''} : {reason:'Authorization country does not match your saved profile'};
    const sponsorshipLabels = labels(el).map(label => norm(label.replace(/\([^)]*\)/g, '')));
    if (sponsorshipLabels.some(id => /^(do|will|would) you (now or in the future )?(require|need)( visa)? sponsorship( (now|in the future|for employment|to work|now or in the future))* (to legally work |to work )?in (the )?(united states|us|u s|usa)$/.test(id)) || exact(/^(do|will|would) you (now or in the future )?(require|need)( visa)? sponsorship( (now|in the future|for employment|to work|now or in the future))* in (the )?(united states|us|u s|usa)$/)) return usProfile ? {key:'requires_sponsorship',value:typeof profile.requires_sponsorship === 'boolean' ? (profile.requires_sponsorship ? 'Yes' : 'No') : ''} : {reason:'Sponsorship country does not match your saved profile'};
    const full = [profile.first_name,profile.last_name].filter(Boolean).join(' ');
    if (workdayPage() && exact(/^(phone|phone number|mobile|mobile number|telephone|tel)$/)
      && [...(workdayState().step?.querySelectorAll('[data-automation-id], button, select') || [])].some(node => visible(node)
        && (/(?:^|[-_.])countryPhoneCode(?:$|[-_.])/.test(node.getAttribute('data-automation-id') || '')
          || labels(node).some(label => /^(country phone code|phone country code|country calling code)$/.test(norm(label)))))) return {reason:'This phone field has a separate country code. Review its format and enter it yourself.'};
    const rules = [
      ['preferred_name',/^(what is your )?preferred name$/,profile.preferred_name],
      ['first_name',/^(first name|given names?|firstname|givenname|given name s)$/,profile.first_name],['last_name',/^(last name|family name|surname|lastname|familyname)$/,profile.last_name],['full_name',/^(full name|your name|applicant name|name|name full|(what is your )?(legal )?first and last name)$/,full],
      ['email',/^(email|e mail|email address)$/,profile.email],['phone',/^(what is your )?(phone|phone number|mobile|mobile number|cell phone|telephone|tel)$/,profile.phone],
      ['address_line1',/^(address line 1|address line1|street address|address line one|what is the first line of your address)$/,profile.address_line1],
      ['address_line2',/^(address line 2|address line2|address line two|what is the second line of your address( if relevant)?)$/,profile.address_line2],
      ['city',/^(city|current city|address level2)$/,profile.city],['state',/^(state|state region|state province|province|address level1)$/,profile.state],['postal_code',/^(zip|zip code|postal code|post code|postcode|zipcode)$/,profile.postal_code],
      ['country',/^(country|country of residence|residence country|country name)$/,profile.country],['location',/^(current location|location|location city|your location|what is your current location city state)$/,[profile.city,profile.state].filter(Boolean).join(', ')],
      ['linkedin_url',/^(linkedin|linkedin profile|linkedin url|linkedin profile url|please provide your linkedin profile)$/,profile.linkedin_url],['github_url',/^(github|github profile|github url)$/,profile.github_url],['portfolio_url',/^(portfolio|portfolio url|website|personal website|personal site)$/,profile.portfolio_url],['current_title',/^(current title|current job title|current role)$/,profile.current_title],
    ];
    const found = rules.find(([,re]) => exact(re));
    return found ? {key:found[0],value:found[2] || ''} : {reason:'No verified mapping for this question'};
  }
  const stateNames = {AL:'Alabama',AK:'Alaska',AZ:'Arizona',AR:'Arkansas',CA:'California',CO:'Colorado',CT:'Connecticut',DE:'Delaware',FL:'Florida',GA:'Georgia',HI:'Hawaii',ID:'Idaho',IL:'Illinois',IN:'Indiana',IA:'Iowa',KS:'Kansas',KY:'Kentucky',LA:'Louisiana',ME:'Maine',MD:'Maryland',MA:'Massachusetts',MI:'Michigan',MN:'Minnesota',MS:'Mississippi',MO:'Missouri',MT:'Montana',NE:'Nebraska',NV:'Nevada',NH:'New Hampshire',NJ:'New Jersey',NM:'New Mexico',NY:'New York',NC:'North Carolina',ND:'North Dakota',OH:'Ohio',OK:'Oklahoma',OR:'Oregon',PA:'Pennsylvania',RI:'Rhode Island',SC:'South Carolina',SD:'South Dakota',TN:'Tennessee',TX:'Texas',UT:'Utah',VT:'Vermont',VA:'Virginia',WA:'Washington',WV:'West Virginia',WI:'Wisconsin',WY:'Wyoming',DC:'District of Columbia'};
  const usCountry = value => /^(us|u s|usa|united states|united states of america)$/.test(norm(value));
  const sameCountry = (a,b) => norm(a) === norm(b) || (usCountry(a) && usCountry(b));
  const stateValue = value => norm(stateNames[String(value || '').toUpperCase()] || value);
  const bachelorOfArts = new Set(['ba','b a','bachelor of arts','bachelor of arts ba','bachelor of arts b a']);
  function optionMatches(text, rule, profile) {
    if (rule.key === 'country') return sameCountry(text.replace(/\s*\+\s*\d+\s*$/, ''),rule.value);
    if (rule.key === 'state') return stateValue(text) === stateValue(rule.value);
    if (workdayPage() && /^education\.\d+\.degree$/.test(rule.key) && bachelorOfArts.has(norm(rule.value))) return bachelorOfArts.has(norm(text));
    if (rule.key === 'location') {
      const parts = text.split(',').map(s => s.trim());
      if (norm(parts[0]) !== norm(profile.city)) return false;
      if (parts.length === 1) return !profile.state && !profile.country;
      if (!profile.state || stateValue(parts[1]) !== stateValue(profile.state)) return false;
      return parts.length === 2 || (parts.length === 3 && !!profile.country && sameCountry(parts[2],profile.country));
    }
    return norm(text) === norm(rule.value);
  }
  function exactOption(options, rule, profile) {
    const matches = options.filter(o => !o.disabled && o.getAttribute('aria-disabled') !== 'true' && optionMatches(o.textContent,rule,profile));
    return matches.length === 1 ? matches[0] : null;
  }
  function setText(el, value, guard = () => true) {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto,'value')?.set;
    if (!setter || !guard()) return false;
    setter.call(el,value);
    if (!guard()) return false;
    el.dispatchEvent(new Event('input',{bubbles:true}));
    if (!guard()) return false;
    el.dispatchEvent(new Event('change',{bubbles:true}));
    return true;
  }
  function menuFor(el) {
    const ctrl = reactControl(el) || el, input = el.tagName === 'INPUT' ? el : ctrl.querySelector('input');
    const listId = el.getAttribute('aria-controls') || el.getAttribute('aria-owns') || input?.getAttribute('aria-controls') || input?.getAttribute('aria-owns');
    if (listId) return document.getElementById(listId);
    // An unassociated portal menu is deliberately not searched globally.
    const scope = (workdayPage() && ctrl.closest('[data-automation-id^="formField-"]'))
      || ctrl.closest('.ashby-application-form-field-entry, .select-shell, .field, .field-wrapper, .form-field, [data-field], fieldset') || ctrl.parentElement;
    const menus = [...scope.querySelectorAll("[role='listbox']")].filter(visible);
    return menus.length === 1 ? menus[0] : null;
  }
  async function fillCombo(el, rule, profile, guard) {
    const value = rule.value;
    const ctrl = reactControl(el) || el, input = el.tagName === 'INPUT' ? el : ctrl.querySelector('input');
    const before = input?.value || '';
    if (!guard()) return 'interrupted';
    input?.focus();
    if (!guard()) return 'interrupted';
    ctrl.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,button:0}));
    if (!guard()) return 'interrupted';
    ctrl.dispatchEvent(new MouseEvent('mouseup',{bubbles:true,button:0}));
    if (!guard()) return 'interrupted';
    if (workdaySelect(el)) { ctrl.click(); if (!guard()) return 'interrupted'; }
    if (input && !/^(Yes|No)$/.test(value)) setText(input,rule.key === 'location' ? profile.city : value,guard);
    const query = input?.value || '';
    const applicantChanged = () => !!selectedCombo(el) || (input && input.value !== query);
    let option = null;
    for (let attempt=0; attempt<35; attempt++) {
      await pause(100);
      if (!guard()) return 'interrupted';
      if (!el.isConnected) break;
      if (applicantChanged()) return 'preserved';
      const menu = menuFor(el);
      const options = [...(menu?.querySelectorAll("[role='option'], [class*='select__option']") || [])].filter(visible);
      option = exactOption(options,rule,profile);
      if (option) break;
    }
    if (option) {
      if (!guard()) return 'interrupted';
      if (applicantChanged()) return 'preserved';
      option.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,button:0}));
      if (!guard()) return 'interrupted';
      option.dispatchEvent(new MouseEvent('mouseup',{bubbles:true,button:0}));
      if (!guard()) return 'interrupted';
      option.click();
      await pause(100);
      if (!guard()) return 'interrupted';
    }
    const ok = !!option && el.getAttribute('aria-expanded')!=='true' && (optionMatches(selectedCombo(el),rule,profile) || (!reactControl(el) && optionMatches(el.value || el.textContent,rule,profile)));
    if (!ok && applicantChanged()) return 'preserved';
    if (!guard()) return 'interrupted';
    (input || ctrl).dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',code:'Escape',bubbles:true}));
    if (!guard()) return 'interrupted';
    input?.blur();
    if (!ok && input?.isConnected && !selectedCombo(el) && input.value === query) setText(input,before,guard);
    return ok;
  }
  const unrelatedControl = el => {
    const form = el.closest('form, [role="form"]') || el.form;
    const formName = [form?.getAttribute('aria-label'), ...(form?.getAttribute('aria-labelledby') || '').split(/\s+/).map(id => document.getElementById(id)?.textContent),
      form?.querySelector(':scope > legend, :scope > h1, :scope > h2, :scope > h3')?.textContent].filter(Boolean).join(' ');
    return !!el.closest('header, footer, nav, [role="search"], [role="banner"], [role="contentinfo"], [role="navigation"]')
      || /(?:^|[\s_-])(?:newsletter|subscribe|subscription|search|login|sign[\s_-]?in)(?:$|[\s_-])/i.test(`${form?.id || ''} ${form?.className || ''}`.replace(/([a-z])([A-Z])/g, '$1 $2'))
      || /\b(newsletter|subscribe|subscription)\b|^(?:search|log[ -]?in|sign[ -]?in)\b/i.test(formName);
  };
  function applicationScopes() {
    if (workdayPage()) { const state = workdayState(); return state.step ? [state.step] : []; }
    const matches = [...document.querySelectorAll('form, [role="form"], #application-form')].filter(scope =>
      (/application|ashby/i.test(`${scope.id} ${scope.className}`) || [...scope.querySelectorAll("input[type='file']")].some(el =>
        identifiers(el).some(id => /\b(resume|cv|curriculum vitae)\b/.test(id)) && !identifiers(el).some(id => /cover letter/.test(id))))
      && [...scope.querySelectorAll('input, textarea, select, [role="combobox"]')].some(el => !unrelatedControl(el) && !el.disabled && !el.readOnly
        && !['hidden', 'submit', 'button', 'reset'].includes(el.type) && (visible(el) || el.type === 'file' && visible(wrapper(el)))));
    return matches.filter(scope => !matches.some(other => other !== scope && other.contains(scope)));
  }
  function workdayResumeScopes() {
    const state = workdayState(), step = state.step;
    if (!step || !state.canAttachResume) return [];
    const scopes = [...step.querySelectorAll('[data-automation-id="resumeUpload"]')].filter(visible);
    if (step.getAttribute('data-automation-id') === 'applyFlowMyExpPage') {
      const groupSelector = '[role="group"][aria-labelledby]';
      const sections = [...step.querySelectorAll(groupSelector)].filter(group => {
        if (!visible(group)) return false;
        const references = group.getAttribute('aria-labelledby').trim().split(/\s+/).map(id => document.getElementById(id));
        if (!references.length || references.some(node => !node || !group.contains(node))) return false;
        const title = norm(references.map(labelText).join(' ')), heading = group.querySelector(':scope > h4');
        return /^(resume(?: cv)?|cv|curriculum vitae)$/.test(title) && !!heading && norm(labelText(heading)) === title;
      });
      if (sections.length > 1) return [];
      if (sections.length === 1) scopes.push(...[...sections[0].querySelectorAll('[data-automation-id="attachments-FileUpload"]')]
        .filter(scope => visible(scope) && scope.closest(groupSelector) === sections[0]));
    }
    const unique = [...new Set(scopes)];
    return unique.filter(scope => !unique.some(other => other !== scope && scope.contains(other)));
  }
  function resumeTarget() {
    if (workdayPage()) {
      const scopes = workdayResumeScopes();
      if (scopes.length !== 1) return null;
      const matches = [...scopes[0].querySelectorAll('input[type="file"]')].filter(el => !el.disabled && !el.matches(':disabled') && !unrelatedControl(el) && (visible(el) || visible(wrapper(el)))
        && (!identifiers(el).some(id => /cover letter/.test(id)) || identifiers(el).some(id => /\b(resume|cv|curriculum vitae)\b/.test(id))));
      return matches.length === 1 ? {el:matches[0],wrapper:scopes[0]} : null;
    }
    const scopes = applicationScopes();
    const matches = [...document.querySelectorAll("input[type='file']")].filter(el => !el.disabled && !unrelatedControl(el)
      && (!scopes.length || scopes.some(scope => scope.contains(el))) && (visible(el) || visible(wrapper(el)))
      && (workdayPage() && el.closest('[data-automation-id="resumeUpload"]') || identifiers(el).some(id => /\b(resume|cv|curriculum vitae)\b/.test(id)))
      && (!identifiers(el).some(id => /cover letter/.test(id)) || workdayPage() && identifiers(el).some(id => /\b(resume|cv|curriculum vitae)\b/.test(id))));
    return matches.length === 1 ? {el:matches[0],wrapper:wrapper(matches[0])} : null;
  }
  function hasExistingWorkdayResume() {
    const scopes = workdayResumeScopes();
    if (scopes.length !== 1) return false;
    const scope = scopes[0];
    if ([...scope.querySelectorAll('[data-automation-id="file-upload-failed"], [data-automation-id="file-loading-dots"]')].some(visible)) return false;
    return [...scope.querySelectorAll('[data-automation-id="file-upload-item"]')].some(item => visible(item)
      && [...item.querySelectorAll('[data-automation-id="file-upload-item-name"]')].some(el => visible(el) && el.textContent.trim())
      && [...item.querySelectorAll('[data-automation-id="file-upload-successful"]')].some(visible));
  }
  async function attachResume(resume, guard) {
    if (!guard()) return {status:'not-requested',message:'No file was selected because the active application changed.'};
    // Workday can remove its file input once the explicit résumé item succeeds.
    if (workdayPage() && hasExistingWorkdayResume()) return {status:'already-attached',message:'An existing attachment was preserved.'};
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
      if (!guard()) return {status:'not-requested',message:'No file was selected because the active application changed.'};
      target.el.files = transfer.files;
      const interrupted = () => ({status:'prepared',filename,message:'File selected before the page or active tab changed. Its acceptance could not be verified. Inspect the current page.'});
      if (!guard()) return interrupted();
      target.el.dispatchEvent(new Event('input',{bubbles:true}));
      if (!guard()) return interrupted();
      target.el.dispatchEvent(new Event('change',{bubbles:true}));
      for (let attempt=0; attempt<16; attempt++) {
        await pause(150);
        if (!guard()) return interrupted();
        const scope = target.wrapper?.isConnected ? target.wrapper : resumeTarget()?.wrapper, text = scope?.innerText || '';
        if (workdayPage() && [...(scope?.querySelectorAll('[data-automation-id="file-upload-failed"]') || [])].some(visible)) return {status:'failed',filename,message:'The form reported an upload error. Review the page.'};
        const alerts = [...(scope?.querySelectorAll("[role='alert'], .error, [class*='Error'], [class*='error']") || [])].filter(visible).map(el => el.textContent).join(' ');
        if (/failed|invalid|too large|unsupported|not (allowed|accepted)|error|unable/i.test(alerts)) return {status:'failed',filename,message:'The form reported an upload error. Review the page.'};
        const accepted = !workdayPage() || ![...(scope?.querySelectorAll('[data-automation-id="file-loading-dots"]') || [])].some(visible)
          && [...(scope?.querySelectorAll('[data-automation-id="file-upload-successful"]') || [])].some(visible)
          && [...(scope?.querySelectorAll('[data-automation-id="file-upload-item-name"]') || [])].some(el => visible(el) && el.textContent.trim() === filename);
        if (accepted && text.includes(filename) && !/uploading|processing|please wait/i.test(text)) return {status:'accepted',filename,message:'The form displays this résumé attachment. Review it before submitting.'};
      }
      return {status:'prepared',filename,message:'File selected; the form has not confirmed acceptance. Check the attachment on the page.'};
    } catch (error) { return {status:'failed',filename,message:error.message || 'The file could not be attached. Upload it manually.'}; }
  }
  function fieldToken({el,label,type}, profile) {
    const history = historyContext(el,profile);
    const section = history ? `${history.type}:${history.index}` : '';
    return JSON.stringify([el.form?.id || '',section,el.id || '',el.name || '',norm(label),type]);
  }
  function acceptedValue(el, rule, profile) {
    if (!el?.isConnected) return false;
    if ((el.validity && !el.validity.valid) || el.getAttribute('aria-invalid') === 'true') return false;
    if (el.tagName === 'SELECT') return !placeholderOption(el.selectedOptions[0]) && optionMatches(el.selectedOptions[0].textContent,rule,profile);
    if (combo(el)) return el.getAttribute('aria-expanded')!=='true' && optionMatches(selectedCombo(el) || (!reactControl(el) ? el.value || el.textContent : ''),rule,profile);
    return el.value === String(rule.value);
  }
  // Re-injecting the script must not let a second click race an existing run.
  const runtime = globalThis.__jobpilotAutofillRuntime ||= {running:false};
  globalThis.__jobpilotRun = async ({action='inspect',profile={},resume=null,requireVisible=false,expectedWorkdayStep=''}={}) => {
    if (!['inspect','details','resume'].includes(action)) throw new Error('Unknown JobPilot action');
    const workday = workdayPage();
    const report = {action,status:'complete',warnings:[],filled:[],skipped:[],failed:[],required:[],resume:{status:'not-requested',message:''},provider:workday ? 'Workday' : location.hostname.includes('greenhouse') ? 'Greenhouse' : location.hostname.includes('lever') ? 'Lever' : location.hostname.includes('ashby') ? 'Ashby' : 'Unsupported',pageUrl:location.href};
    if ((!hosts.has(location.hostname) && !workday) || location.protocol !== 'https:') { report.skipped.push({label:'Page',reason:'This frame is outside the supported application hosts'}); return report; }
    const warn = message => { if (!report.warnings.includes(message)) report.warnings.push(message); };
    const initialWorkday = workday ? workdayState() : null;
    if (initialWorkday && (!initialWorkday.hasForm || action === 'details' && !initialWorkday.canFillDetails || action === 'resume' && !initialWorkday.canAttachResume
      || expectedWorkdayStep && expectedWorkdayStep !== initialWorkday.stepKey)) {
      report.status='interrupted'; warn(expectedWorkdayStep && expectedWorkdayStep !== initialWorkday.stepKey
        ? 'The Workday application step changed. Inspect the current step before retrying.' : initialWorkday.message); return report;
    }
    let cancelled = false;
    const noticeChange = () => {
      if (location.href !== report.pageUrl || requireVisible && document.visibilityState !== 'visible') cancelled = true;
      if (initialWorkday) {
        const current = workdayState();
        if (!current.hasForm || current.stepKey !== initialWorkday.stepKey || current.root !== initialWorkday.root || current.step !== initialWorkday.step) cancelled = true;
      }
    };
    const noticePageHide = () => { cancelled = true; };
    const guard = () => {
      noticeChange();
      if (!cancelled) return true;
      report.status = 'interrupted'; warn('The application page changed or its tab is no longer active. Existing progress was preserved; inspect the current page before retrying.');
      return false;
    };
    if (runtime.running) { report.status='interrupted'; warn('Autofill is already running on this page. Wait for its report before retrying.'); return report; }
    if (action !== 'inspect' && applicationScopes().length > 1) { report.status='interrupted'; warn('Several application forms are visible in this document. Open the intended form separately before autofilling.'); return report; }
    runtime.running=true;
    let workdayObserver;
    if (action !== 'inspect') {
      document.addEventListener('visibilitychange', noticeChange);
      window.addEventListener('pagehide', noticePageHide);
      window.addEventListener('popstate', noticeChange);
      window.addEventListener('hashchange', noticeChange);
      if (initialWorkday) {
        workdayObserver = new MutationObserver(noticeChange);
        workdayObserver.observe(initialWorkday.root, {subtree:true,childList:true,characterData:true,attributes:true,
          attributeFilter:['data-automation-id','aria-current','aria-hidden','hidden','style']});
      }
    }
    const outcomes = new Map(), attempts = new Map();
    const record = (token,kind,item,rule=null,retryable=false) => outcomes.set(token,{kind,item,rule,retryable});
    try {
      if (action === 'details') {
        const deadline = Date.now()+20000;
        let quietSince=Date.now();
        while (Date.now() < deadline) {
          if (!guard()) break;
          const entries = inventory(warn);
          let pending;
          for (const entry of entries) {
            try {
              const token=fieldToken(entry,profile), previous=outcomes.get(token);
              if (!previous || ((previous.kind==='filled' || previous.retryable) && !entry.answered && (attempts.get(token)||0)<2)) { pending={entry,token}; break; }
            } catch (_) { warn('A field could not be identified. Review the page.'); }
          }
          if (!pending) {
            if (Date.now()-quietSince >= 1200) break;
            await pause(100); continue;
          }
          quietSince=Date.now();
          const {entry,token}=pending, {label,type}=entry;
          attempts.set(token,(attempts.get(token)||0)+1);
          try {
            // Resolve immediately before writing: React can replace any input.
            const live=inventory(warn).filter(item => fieldToken(item,profile)===token);
            if (live.length !== 1) { record(token,'failed',{label,reason:'This field changed or is ambiguous. Review it manually.'}); continue; }
            const {el}=live[0];
            if (['radio','checkbox','choice','file','password'].includes(type)) { record(token,'skipped',{label,reason:type==='file' ? 'Use Attach résumé separately' : 'Answer this question yourself'}); continue; }
            const prompt=workdayMultiSelect(el);
            if (prompt) { record(token,'skipped',{label,reason:prompt.selected ? 'Your existing answer was preserved' : 'This Workday multi-select needs a manual choice. Review it on the application page.'}); continue; }
            if (answered(el)) { record(token,'skipped',{label,reason:'Your existing answer was preserved'}); continue; }
            if (combo(el) && !workdaySelect(el) && String(el.value || '').trim()) { record(token,'skipped',{label,reason:'An existing dropdown search was preserved. Choose its option on the page.'}); continue; }
            const rule=ruleFor(el,profile);
            if (rule.reason) { record(token,'skipped',{label,reason:rule.reason}); continue; }
            if (!rule.value) { record(token,'skipped',{label,reason:'No saved profile value'}); continue; }
            if (el.tagName==='SELECT') {
              const option=exactOption([...el.options].filter(o => !placeholderOption(o)),rule,profile);
              if (option) {
                if (!guard()) break;
                el.value=option.value;
                if (!guard()) break;
                el.dispatchEvent(new Event('input',{bubbles:true}));
                if (!guard()) break;
                el.dispatchEvent(new Event('change',{bubbles:true})); await pause(100);
              }
            } else if (type==='combobox') {
              const result=await fillCombo(el,rule,profile,guard);
              if (result==='interrupted') break;
              if (result==='preserved') { record(token,'skipped',{label,reason:'Your dropdown answer or search changed during autofill and was preserved.'}); continue; }
            }
            else { setText(el,rule.value,guard); await pause(100); }
            if (!guard()) break;
            const after=inventory(warn).filter(item => fieldToken(item,profile)===token);
            if (after.length===1 && acceptedValue(after[0].el,rule,profile)) record(token,'filled',{label,key:rule.key},rule);
            else record(token,'failed',{label,reason:'The form did not retain a valid, unambiguous match. Review this field.'},rule,!el.isConnected && after.length===1 && !after[0].answered);
          } catch (_) { record(token,'failed',{label,reason:'This control could not be filled. Other supported fields were still checked.'}); }
        }
        if (Date.now() >= deadline) { report.status='interrupted'; warn('The form kept changing or took too long. Review progress and retry remaining empty fields.'); }
        const finalEntries=guard() ? inventory(warn) : [];
        for (const [token,outcome] of cancelled ? [] : outcomes) {
          if (outcome.kind==='filled') {
            const matches=finalEntries.filter(item => fieldToken(item,profile)===token);
            if (matches.length!==1 || !acceptedValue(matches[0].el,outcome.rule,profile)) record(token,'failed',{label:outcome.item.label,reason:'The form changed after filling this field. Review its current value.'});
          }
        }
      } else if (action==='resume') report.resume=await attachResume(resume,guard);
      if (action==='inspect' || guard()) report.required=inventory(warn).filter(item => item.required && !item.answered).map(({label,type})=>({label,type}));
    } catch (_) {
      report.status='interrupted'; warn('Autofill was interrupted. Review the preserved progress and inspect the form before retrying.');
    } finally {
      runtime.running=false;
      document.removeEventListener('visibilitychange', noticeChange);
      window.removeEventListener('pagehide', noticePageHide);
      window.removeEventListener('popstate', noticeChange);
      window.removeEventListener('hashchange', noticeChange);
      workdayObserver?.disconnect();
    }
    for (const outcome of outcomes.values()) report[outcome.kind].push(outcome.item);
    if (report.status==='complete' && (report.failed.length || report.warnings.length || ['failed','no-target','prepared'].includes(report.resume.status))) report.status='partial';
    return report;
  };
})();
