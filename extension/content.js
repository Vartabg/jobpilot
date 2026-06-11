(async () => {
  const PROFILE_URL = chrome.runtime.getURL("profile.json");
  let profile;
  try {
    profile = await (await fetch(PROFILE_URL)).json();
  } catch (err) {
    console.error("[JobPilot] failed to load profile.json", err);
    toast("JobPilot: couldn't load profile.json");
    return;
  }

  const fullName = [profile.first_name, profile.last_name].filter(Boolean).join(" ");
  const location = [profile.city, profile.state].filter(Boolean).join(", ");

  // ---- Deny patterns: fields we should never auto-fill ----
  const DENY_PATTERNS = [
    /referral.*(name|employee)/i,
    /referred by|who referred/i,
    /brex employee.?s? name/i,
    /pronouns/i,
    /employee id|employee number/i,
    /what.*interest|why.*(interest|position|role|apply|want)/i,
    /describe|tell us about|tell me about|explain/i,
    /cover letter|additional information/i,
    /salary|compensation|expected (pay|salary)|desired salary/i,
    /reason for leaving/i,
    /current employer|present employer|last employer/i,
    /how many years/i,
    /if you.?re not authorized/i,
    /employee.?s name/i,
  ];

  // ---- Text-input rules ----
  const TEXT_RULES = [
    { key: "first_name",    value: profile.first_name,    tokens: [["first", "name"], ["given", "name"], ["firstname"]] },
    { key: "last_name",     value: profile.last_name,     tokens: [["last", "name"], ["family", "name"], ["surname"], ["lastname"]] },
    { key: "full_name",     value: fullName,              tokens: [["full", "name"], ["your", "name"], ["applicant", "name"]] },
    { key: "full_name",     value: fullName,              tokens: [["name"]], strict: true },
    { key: "email",         value: profile.email,         tokens: [["email"], ["e", "mail"], ["email", "address"]] },
    { key: "phone",         value: profile.phone,         tokens: [["phone"], ["mobile", "number"], ["cell", "phone"], ["telephone"]] },
    { key: "city",          value: profile.city,          tokens: [["city"], ["current", "city"], ["location", "city"]] },
    { key: "location",      value: location,              tokens: [["current", "location"], ["your", "location"]] },
    { key: "linkedin",      value: profile.linkedin_url,  tokens: [["linkedin"]] },
    { key: "github",        value: profile.github_url,    tokens: [["github"]] },
    { key: "website_or_github", value: profile.github_url || profile.portfolio_url, tokens: [["website", "or", "github"], ["github", "or", "website"]] },
    { key: "portfolio",     value: profile.portfolio_url, tokens: [["portfolio"], ["website"], ["personal", "site"], ["personal", "website"]] },
    { key: "sponsorship_na", value: "Not applicable — authorized to work in the U.S.", tokens: [["what", "sponsorship"], ["sponsorship", "would", "you", "require"], ["type", "of", "sponsorship"]] },
    { key: "current_title", value: profile.current_title, tokens: [["current", "title"], ["current", "role"], ["job", "title"]] },
  ];

  // ---- Select rules (applies to native <select> AND react-select) ----
  // Demographic/EEO answers come from profile.json when the user sets them; otherwise they
  // default to the decline-to-answer family. Keep these defaults in sync with the bookmarklet
  // rules in core/server.py (BOOKMARKLET_TEMPLATE -> yesNoFor) until both surfaces are
  // generated from a single source.
  // phone_country MUST come before country so bare "country" label (phone widget) hits it first.
  const locationAnswer = profile.location || [profile.city, profile.state, profile.country].filter(Boolean).join(", ");
  const SELECT_RULES = [
    { id: "work_auth",          re: /authori[sz]ed to work|legally authori[sz]ed|eligible to work|right to work/i,                answer: "Yes" },
    { id: "sponsorship",        re: /require sponsorship|sponsorship.*required|visa sponsorship|need sponsorship|future sponsorship/i, answer: "No" },
    { id: "phone_country",      re: /country code|dialing code|phone.*country|country.*phone|^country$/i,                         answer: "+1", searchTerm: "" },
    { id: "country",            re: /country.*(based|residence|located)|what country/i,                                            answer: profile.country || "USA", searchTerm: "" },
    { id: "candidate_location", re: /^location$|^location city$|location.*city|current city|candidate location|city.*location|locate me/i, answer: locationAnswer, searchTerm: location || locationAnswer },
    { id: "relocate",           re: /currently live|currently located|relocate to|planning to relocate|plan to relocate|in-office requirement|meet this in-office/i,
                                  answer: "Yes, I'm currently located here",
                                  answerIfRelocating: "Yes, I'd relocate prior to the start of the role",
                                  contextDependent: true },
    { id: "hybrid_ack",          re: /acknowledge.*in-office|agree.*in-office|willing to work.*(office|on-site|onsite)|commute to.*office|three days per week/i,
                                  answer: "Yes, I'm currently located here",
                                  answerIfRelocating: "Yes, I'd relocate prior to the start of the role",
                                  contextDependent: true },
    { id: "privacy_consent",    re: /consent.*process|consent to.*privacy|applicant privacy|data.*consent/i,                       answer: "Consent", searchTerm: "" },
    { id: "capital_one",        re: /(currently|previously|ever).*worked.*(at|for) capital one|capital one.*(employee|contractor)/i, answer: "No" },
    { id: "veteran",            re: /veteran status|protected veteran/i,                                                           answer: profile.veteran_status || "I don't wish to answer", searchTerm: "" },
    { id: "gender",             re: /^gender$|gender identity|what is your gender/i,                                               answer: profile.gender || "Decline To Self Identify", searchTerm: "" },
    { id: "hispanic_latino",    re: /hispanic.*latin|^hispanic$|are you hispanic|hispanic\/latino/i,                               answer: profile.hispanic_latino || "Decline To Self Identify", searchTerm: "" },
    { id: "race",               re: /^race$|^ethnicity$|race.*ethnicity|racial/i,                                                  answer: profile.race || "Decline To Self Identify", searchTerm: "" },
    { id: "disability",         re: /disability status|do you have a disability/i,                                                 answer: profile.disability_status || "I do not want to answer", searchTerm: "" },
    { id: "how_heard",          re: /how did you hear|how.*heard about|referral source/i,                                          answer: "LinkedIn", searchTerm: "" },
  ];

  // ---- Resume upload (drag-drop simulation, embedded PDF) ----
  // Display name the employer sees — set profile.resume_filename to control it;
  // falls back to the bundled web-accessible resource name.
  const RESUME_FILENAME = profile.resume_filename || "resume.pdf";

  const loadResumeFile = async () => {
    const url = chrome.runtime.getURL("resume.pdf");
    const resp = await fetch(url);
    const blob = await resp.blob();
    return new File([blob], RESUME_FILENAME, { type: "application/pdf" });
  };

  const findResumeTarget = () => {
    // Strategy 1: look for <input type="file"> whose identifiers mention resume/cv
    const fileInputs = Array.from(document.querySelectorAll('input[type="file"]'));
    const labeled = fileInputs.find((el) => {
      const ids = getIdentifiers(el);
      return ids.some((id) => /resume|\bcv\b/i.test(id));
    });
    if (labeled) return { kind: "input", el: labeled };

    // Strategy 2: look for a wrapper containing "Resume" text with a file input inside it
    const headings = Array.from(document.querySelectorAll("label, h1, h2, h3, h4, legend, [class*='label']"));
    const resumeHeader = headings.find((h) => /\bresume\b|\bcv\b/i.test((h.textContent || "").trim()));
    if (resumeHeader) {
      const wrapper = resumeHeader.closest(
        ".field, .field-wrapper, [class*='field'], [class*='FieldWrapper'], [class*='FileUpload'], fieldset, section, div"
      );
      if (wrapper) {
        const inp = wrapper.querySelector('input[type="file"]');
        if (inp) return { kind: "input", el: inp, wrapper };
        // Drop zone within wrapper
        const drop = wrapper.querySelector("[class*='dropzone'], [class*='drop-zone'], [class*='DropZone'], [class*='upload']");
        if (drop) return { kind: "dropzone", el: drop, wrapper };
      }
    }

    // Strategy 3: any single visible file input as a last resort
    if (fileInputs.length === 1) return { kind: "input", el: fileInputs[0] };

    return null;
  };

  const uploadResume = async () => {
    let file;
    try {
      file = await loadResumeFile();
    } catch (err) {
      return { ok: false, reason: "load-failed", err: String(err) };
    }

    const target = findResumeTarget();
    if (!target) return { ok: false, reason: "no-target" };

    const dt = new DataTransfer();
    dt.items.add(file);

    if (target.kind === "input") {
      // Direct file-input assignment via DataTransfer — works in modern Chrome when initiated from user gesture
      try {
        target.el.files = dt.files;
        target.el.dispatchEvent(new Event("input", { bubbles: true }));
        target.el.dispatchEvent(new Event("change", { bubbles: true }));
        return { ok: true, method: "files-assignment", target: "input" };
      } catch (err) {
        console.warn("[JobPilot] files-assignment failed, trying drop simulation", err);
      }
    }

    // Drag-drop simulation on the dropzone (or the input's parent if that's all we have)
    const dropTarget = target.kind === "dropzone" ? target.el : (target.el.closest("[class*='drop']") || target.wrapper || target.el.parentElement);
    if (!dropTarget) return { ok: false, reason: "no-drop-target" };

    const events = ["dragenter", "dragover", "drop"];
    for (const type of events) {
      const ev = new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: dt });
      // Some sites block drag events by default — need to prevent the default stopping it
      try { dropTarget.dispatchEvent(ev); } catch (_) {}
      await sleep(60);
    }
    return { ok: true, method: "drag-drop", target: target.kind };
  };

  // ---- Location context (for contextDependent rules) ----
  // Resolves once per page session. Cached on window so multiple runs don't re-prompt.
  const resolveLocationContext = () => {
    if (window.__jobpilotLocationContext) return window.__jobpilotLocationContext;
    const isLocal = window.confirm(
      "JobPilot: Is this job in your current city (" + (profile.city || "your city") + ")?\n\n" +
      "• OK = Yes — fill location questions with \"I'm currently located here\"\n" +
      "• Cancel = No — fill with \"I'd relocate prior to the start of the role\""
    );
    window.__jobpilotLocationContext = isLocal ? "local" : "relocating";
    return window.__jobpilotLocationContext;
  };

  // ---- Helpers ----
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const normalize = (s) => (s || "").toString().toLowerCase().replace(/[^a-z0-9+]+/g, " ").trim();
  const wordsOf = (s) => normalize(s).split(" ").filter(Boolean);

  const isVisible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) return false;
    const cs = getComputedStyle(el);
    return cs.display !== "none" && cs.visibility !== "hidden" && cs.opacity !== "0";
  };

  const findLabelTexts = (el) => {
    const out = [];
    const aria = el.getAttribute?.("aria-label");
    if (aria) out.push(aria);
    const ariaBy = el.getAttribute?.("aria-labelledby");
    if (ariaBy) {
      for (const id of ariaBy.split(/\s+/)) {
        const lab = document.getElementById(id);
        if (lab?.textContent) out.push(lab.textContent);
      }
    }
    if (el.id) {
      try {
        const label = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
        if (label?.textContent) out.push(label.textContent);
      } catch (_) {}
    }
    const parentLabel = el.closest?.("label");
    if (parentLabel?.textContent) out.push(parentLabel.textContent);
    const wrapper = el.closest?.(".field, .form-field, [class*='field'], [class*='FormField'], fieldset, [class*='question'], [class*='Question']");
    if (wrapper) {
      const labelEl = wrapper.querySelector("label, legend, [class*='label'], [class*='Label'], [class*='question']:not(input):not(select):not(textarea)");
      if (labelEl?.textContent) out.push(labelEl.textContent);
    }
    return out;
  };

  const getIdentifiers = (el) => {
    const out = [];
    if (el.name) out.push(el.name);
    if (el.id) out.push(el.id);
    if (el.placeholder) out.push(el.placeholder);
    out.push(...findLabelTexts(el));
    const ac = el.getAttribute?.("autocomplete");
    if (ac) out.push(ac);
    return out.map(normalize).filter(Boolean);
  };

  const isRequired = (el) => {
    if (el.required || el.getAttribute?.("aria-required") === "true") return true;
    const labels = findLabelTexts(el);
    return labels.some((t) => /\*/.test(t) || /\brequired\b/i.test(t));
  };

  const isDenied = (ids) => ids.some((id) => DENY_PATTERNS.some((pat) => pat.test(id)));

  const isReactSelectLike = (el) => {
    if (el.getAttribute?.("role") === "combobox") return true;
    if (el.getAttribute?.("aria-autocomplete") === "list") return true;
    if (el.getAttribute?.("aria-haspopup") === "listbox") return true;
    const container = el.closest?.(
      "[class*='react-select']:not(select), [class*='Select__']:not(select), [class*='dropdown']:not(select), [class*='Dropdown']:not(select)"
    );
    return !!container;
  };

  const matchTextRule = (ids) => {
    for (const rule of TEXT_RULES) {
      for (const tokenSet of rule.tokens) {
        if (rule.strict) {
          if (ids.some((id) => id === tokenSet.join(" "))) return rule;
        } else {
          const ok = ids.some((id) => {
            const idw = wordsOf(id);
            return tokenSet.every((tok) => idw.includes(tok));
          });
          if (ok) return rule;
        }
      }
    }
    return null;
  };

  const setReactValue = (el, value) => {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const descriptor = Object.getOwnPropertyDescriptor(proto, "value");
    if (descriptor?.set) descriptor.set.call(el, value);
    else el.value = value;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  };

  const fillNativeSelect = (selectEl, answer) => {
    const options = Array.from(selectEl.options).filter((o) => !o.disabled && o.value !== "");
    if (!options.length) return false;
    const ans = normalize(answer);
    const match =
      options.find((o) => normalize(o.text) === ans) ||
      options.find((o) => normalize(o.text).startsWith(ans)) ||
      options.find((o) => normalize(o.text).includes(ans)) ||
      (ans === "yes" ? options.find((o) => /^yes\b/i.test(o.text)) : null) ||
      (ans === "no"  ? options.find((o) => /^no\b/i.test(o.text))  : null) ||
      (ans === "united states" ? options.find((o) => /united states|^us\b|\(\+1\)/i.test(o.text)) : null) ||
      (ans === "other" ? options.find((o) => /^other\b/i.test(o.text)) : null) ||
      (/decline|prefer not|do(n'?t| not) (want|wish)/i.test(answer)
        ? options.find((o) => /decline|prefer not|choose not|do(n.?t| not) (want|wish)/i.test(o.text)) : null);
    if (!match) return false;
    selectEl.value = match.value;
    selectEl.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  };

  // ---- react-select handling ----
  // Find all top-level react-select control containers on the page (not the hidden inputs).
  const findReactSelectControls = () => {
    const set = new Set();
    document.querySelectorAll("[class*='react-select__control'], [class*='select__control'], [class*='Select__control']").forEach((n) => {
      if (isVisible(n)) set.add(n);
    });
    return [...set];
  };

  // For a given react-select control, walk up to get the question label text.
  // Strategy (in priority order):
  //   1. <label for="ID"> where ID matches an <input id> inside the control (canonical Greenhouse pattern)
  //   2. aria-label / aria-labelledby on the inner input
  //   3. aria-labelledby on the control itself
  //   4. Nearest visible (non-.visually-hidden) <label>/<legend> in an ancestor wrapper
  const getRsLabel = (controlEl) => {
    const texts = [];
    const innerInputs = controlEl.querySelectorAll("input");
    for (const inp of innerInputs) {
      if (inp.id) {
        try {
          const lbl = document.querySelector(`label[for="${CSS.escape(inp.id)}"]`);
          if (lbl?.textContent && !lbl.classList.contains("visually-hidden")) {
            texts.push(lbl.textContent);
          }
        } catch (_) {}
      }
      const ariaLbl = inp.getAttribute?.("aria-label");
      if (ariaLbl) texts.push(ariaLbl);
      const ariaBy = inp.getAttribute?.("aria-labelledby");
      if (ariaBy) {
        for (const id of ariaBy.split(/\s+/)) {
          const lab = document.getElementById(id);
          if (lab?.textContent && !lab.classList.contains("visually-hidden")) texts.push(lab.textContent);
        }
      }
    }
    const ariaBy = controlEl.getAttribute?.("aria-labelledby");
    if (ariaBy) {
      for (const id of ariaBy.split(/\s+/)) {
        const lab = document.getElementById(id);
        if (lab?.textContent && !lab.classList.contains("visually-hidden")) texts.push(lab.textContent);
      }
    }
    // Walk up ancestors for a visible label/legend
    const wrappers = [
      controlEl.closest(".select__container"),
      controlEl.closest(".field-wrapper"),
      controlEl.closest(".field, .form-field, [class*='field'], [class*='FormField'], [class*='question'], [class*='Question']"),
      controlEl.closest("fieldset"),
    ].filter(Boolean);
    for (const w of wrappers) {
      const candidates = w.querySelectorAll("label, legend, [class*='label']:not([class*='select__label']):not([class*='react-select']), [class*='Label']:not([class*='react-select'])");
      for (const c of candidates) {
        if (c.classList.contains("visually-hidden")) continue;
        if (!c.textContent) continue;
        texts.push(c.textContent);
        break;
      }
      if (texts.length) break;
    }
    return texts.map(normalize).filter(Boolean);
  };

  const fillReactSelect = async (controlEl, rule) => {
    const answer = rule.answer;
    // Use explicit-undefined check so an empty-string searchTerm ("") means "don't type anything"
    const searchTerm = rule.searchTerm !== undefined ? rule.searchTerm : answer.split(" ")[0];

    // 1. Open the menu via mousedown/mouseup only (react-select v5 opens on mousedown;
    //    a following .click() can re-trigger and close it)
    const innerInput = controlEl.querySelector("input");
    if (innerInput) innerInput.focus();
    controlEl.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, button: 0 }));
    await sleep(40);
    controlEl.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, button: 0 }));
    await sleep(200);

    // 2. Type the search term into the control's input (skip if searchTerm is empty string — "")
    const input = controlEl.querySelector("input");
    if (input && searchTerm && searchTerm.length > 0) {
      input.focus();
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
      setter.call(input, "");
      input.dispatchEvent(new Event("input", { bubbles: true }));
      await sleep(60);
      setter.call(input, searchTerm);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    }

    // 3. Poll for options to render (handles async autocomplete like Brex's geocoding location field)
    const menuOptionsSel =
      "[class*='react-select__option']:not([class*='is-disabled'])," +
      "[class*='select__option']:not([class*='is-disabled'])," +
      "[role='option']";
    // Location-specific: longer timeout for geocoding API roundtrip
    const isLocationLookup = rule?.id === "candidate_location";
    const maxWaitMs = isLocationLookup ? 3000 : 1200;
    const pollMs = 100;
    const startedAt = Date.now();
    let options = [];
    while (Date.now() - startedAt < maxWaitMs) {
      options = Array.from(document.querySelectorAll(menuOptionsSel)).filter(isVisible);
      // For async location lookups, also ensure results are "settled" — wait for options that look like places
      if (isLocationLookup) {
        const placeLike = options.filter((o) => /,/.test(o.textContent));
        if (placeLike.length > 0) { options = placeLike; break; }
      } else {
        if (options.length > 0) break;
      }
      await sleep(pollMs);
    }
    const pickOption = () => {
      const ans = normalize(answer);
      // 1) Exact normalized match (best — profile-specified answers should hit this)
      const exact = options.find((o) => normalize(o.textContent) === ans);
      if (exact) return exact;

      // 2) Case-specific fallbacks for common Greenhouse option-text variants
      if (ans === "yes") {
        const y = options.find((o) => /^yes\b/i.test(o.textContent.trim()));
        if (y) return y;
      }
      if (ans === "no") {
        const n = options.find((o) => /^no\b/i.test(o.textContent.trim()));
        if (n) return n;
      }
      if (ans === "+1" || /^\+1\b/.test(ans)) {
        // Phone country code dropdown — options are typically "🇺🇸 United States (+1)" or "+1 US"
        const p = options.find((o) => /\+1\b|\(\+1\)|united states/i.test(o.textContent));
        if (p) return p;
      }
      if (ans === "usa" || ans === "us" || ans === "united states") {
        const us = options.find((o) => /^usa\b|^u\.?s\.?a\.?\b|^united states\b|united states of america|\(\+1\)/i.test(o.textContent.trim()));
        if (us) return us;
      }
      if (ans === "consent") {
        const c = options.find((o) => /^consent\b|^i consent\b|^i agree\b|^agree\b|^yes,? i consent/i.test(o.textContent.trim()));
        if (c) return c;
      }
      // Decline-to-answer family: any decline-style answer matches any decline-style option
      if (/decline|prefer not|do(n'?t| not) (want|wish)/i.test(answer)) {
        const d = options.find((o) => /decline|prefer not|choose not|do(n.?t| not) (want|wish) to answer/i.test(o.textContent));
        if (d) return d;
      }
      if (/identify.*protected veteran/i.test(answer)) {
        // Avoid matching "I am NOT a protected veteran" — require "identify" keyword
        const v = options.find((o) => /identify.*protected veteran/i.test(o.textContent));
        if (v) return v;
      }
      if (ans === "linkedin") {
        const l = options.find((o) => /\blinkedin\b/i.test(o.textContent));
        if (l) return l;
      }
      if (/currently located|currently here|i live here|based here/i.test(answer)) {
        const c = options.find((o) => /currently (located|here)|already here|i live (here|near)|based (here|near)|yes,? i.?m (currently|here|local)/i.test(o.textContent));
        if (c) return c;
      }
      if (/i.?d relocate|would relocate|willing to relocate|relocate prior/i.test(answer)) {
        const r = options.find((o) => /i.?d relocate|would relocate|willing to relocate|yes,? i.?d relocate|relocate prior|planning to relocate/i.test(o.textContent));
        if (r) return r;
      }

      // 3) Location autocomplete — rank by prefix specificity to avoid "South San Francisco, California" outranking "San Francisco, CA"
      if (isLocationLookup) {
        // Build progressive prefixes: longest first
        const parts = ans.split(",").map((s) => s.trim()).filter(Boolean);
        const prefixes = [];
        for (let i = parts.length; i > 0; i--) {
          prefixes.push(parts.slice(0, i).join(", "));
        }
        // e.g. ans="san francisco, ca, usa" -> prefixes = ["san francisco, ca, usa", "san francisco, ca", "san francisco"]
        for (const pre of prefixes) {
          const opt = options.find((o) => normalize(o.textContent).startsWith(pre));
          if (opt) return opt;
        }
        // Word-anchored fallback for location
        const rxFirst = new RegExp("^" + ans.split(",")[0].replace(/\s+/g, "\\s+") + "\\b", "i");
        const opt = options.find((o) => rxFirst.test(o.textContent.trim()));
        if (opt) return opt;
      }

      // 4) Generic starts-with (non-location)
      const starts = options.find((o) => normalize(o.textContent).startsWith(ans));
      if (starts) return starts;

      // 5) Looser substring match (last resort — can pick a near-miss suburb when ans is just a city name)
      const inc = options.find((o) => normalize(o.textContent).includes(ans));
      return inc || null;
    };
    const opt = pickOption();

    if (!opt) {
      // Close menu explicitly (Escape + blur + click outside) and bail
      controlEl.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      if (innerInput) innerInput.blur();
      document.body.click();
      await sleep(80);
      return false;
    }

    // 4. Click the option. react-select uses mousedown, not click.
    opt.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, button: 0 }));
    opt.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, button: 0 }));
    opt.dispatchEvent(new MouseEvent("click", { bubbles: true, button: 0 }));
    await sleep(120);
    // Defensive close in case option click didn't dismiss
    document.body.click();
    await sleep(60);
    return true;
  };

  // ---- Main pass ----
  const filled = [];
  const skipped = [];
  const failed = [];
  const requiredAll = [];

  // Pass A: native <select>
  const selects = Array.from(document.querySelectorAll("select")).filter(isVisible);
  for (const el of selects) {
    if (el.disabled) continue;
    const ids = getIdentifiers(el);
    if (isRequired(el)) requiredAll.push(el);
    if (isDenied(ids)) { skipped.push({ reason: "denied", ids }); continue; }
    if (el.selectedIndex > 0 && el.value) { skipped.push({ reason: "already-selected", ids }); continue; }

    // Per-label matching: rule fires if ANY individual identifier matches the regex
    const rule = SELECT_RULES.find((r) => ids.some((id) => r.re.test(id)));
    if (!rule) { failed.push({ reason: "no-select-rule", ids }); continue; }
    if (!rule.answer) { skipped.push({ reason: "no-profile-answer", rule: rule.id, ids }); continue; }

    if (fillNativeSelect(el, rule.answer)) {
      filled.push(`native:${rule.id}`);
    } else {
      failed.push({ reason: "native-option-not-found", ids, answer: rule.answer });
    }
  }

  // Pass B: text inputs + textareas (run BEFORE react-select, so phone gets filled before we open/close menus)
  const inputs = Array.from(document.querySelectorAll("input, textarea")).filter((el) => {
    const t = (el.type || "text").toLowerCase();
    if (["hidden", "file", "submit", "button", "reset", "checkbox", "radio", "password"].includes(t)) return false;
    if (el.readOnly || el.disabled) return false;
    if (!isVisible(el)) return false;
    return true;
  });

  for (const el of inputs) {
    const ids = getIdentifiers(el);
    if (isRequired(el)) requiredAll.push(el);
    if (!ids.length) { skipped.push({ reason: "no-identifiers" }); continue; }
    if (isDenied(ids)) { skipped.push({ reason: "denied", ids }); continue; }

    const elType = (el.type || "text").toLowerCase();
    const intrinsicText = ["tel", "email", "url", "number"].includes(elType);

    // Skip react-select-nested inputs UNLESS intrinsic text type (tel/email/url/number)
    if (!intrinsicText && isReactSelectLike(el)) { skipped.push({ reason: "react-select-input", ids }); continue; }
    if (el.value && el.value.trim() !== "") { skipped.push({ reason: "already-filled", ids }); continue; }

    const rule = matchTextRule(ids);
    if (!rule) { failed.push({ reason: "no-text-rule", ids }); continue; }
    if (!rule.value) { failed.push({ reason: "no-profile-value", key: rule.key, ids }); continue; }
    setReactValue(el, rule.value);
    filled.push(`text:${rule.key}`);
  }

  // Pass C: react-select controls
  const rsControls = findReactSelectControls();
  console.log(`[JobPilot v0.6.0] react-select controls found: ${rsControls.length}`);

  // Pre-resolve location context if any context-dependent rule will fire (prompts ONCE, upfront,
  // before any menus open — avoids the confirm() blocking in the middle of an async menu interaction).
  const needsLocationContext = rsControls.some((ctrl) => {
    const labels = getRsLabel(ctrl);
    const r = SELECT_RULES.find((rr) => labels.some((lbl) => rr.re.test(lbl)));
    return r?.contextDependent;
  });
  if (needsLocationContext) resolveLocationContext();

  for (const ctrl of rsControls) {
    // Check if already has a selected value
    const hasValue = ctrl.querySelector("[class*='single-value']:not([class*='placeholder']), [class*='multi-value']:not([class*='placeholder'])");
    const labels = getRsLabel(ctrl);
    // Per-label matching: rule fires if ANY single label matches the regex (avoids anchor-regex failures on joined strings)
    let rule = SELECT_RULES.find((r) => labels.some((lbl) => r.re.test(lbl)));

    // If rule is context-dependent, swap in the relocate-variant answer when user confirmed "not local"
    if (rule?.contextDependent) {
      const ctx = resolveLocationContext();
      if (ctx === "relocating" && rule.answerIfRelocating) {
        rule = { ...rule, answer: rule.answerIfRelocating };
      }
    }

    console.log(`[JobPilot v0.6.0] rs-control:`, {
      label_primary: labels[0] || "(none)",
      all_labels: labels,
      matched_rule: rule?.id || "(no match)",
      context_dependent: !!rule?.contextDependent,
      effective_answer: rule?.answer || "(no rule)",
      already_selected: !!hasValue,
    });

    if (hasValue) { skipped.push({ reason: "rs-already-selected", labels }); continue; }
    if (!rule) { failed.push({ reason: "no-rs-rule", labels }); continue; }
    if (!rule.answer) { skipped.push({ reason: "no-profile-answer", rule: rule.id, labels }); continue; }

    try {
      const ok = await fillReactSelect(ctrl, rule);
      if (ok) filled.push(`rs:${rule.id}`);
      else failed.push({ reason: "rs-option-not-found", rule: rule.id, labels });
    } catch (err) {
      failed.push({ reason: "rs-error", err: String(err), rule: rule.id });
    }
    await sleep(250);
  }

  // Pass D: resume upload (drag-drop simulation, embedded PDF)
  try {
    const uploadResult = await uploadResume();
    console.log("[JobPilot v0.6.0] resume-upload:", uploadResult);
    if (uploadResult.ok) {
      filled.push(`file:resume (${uploadResult.method})`);
    } else {
      failed.push({ reason: `resume-upload-${uploadResult.reason}`, err: uploadResult.err });
    }
  } catch (err) {
    console.error("[JobPilot v0.6.0] resume-upload error:", err);
    failed.push({ reason: "resume-upload-exception", err: String(err) });
  }

  // Required-field coverage check
  const requiredUnfilled = requiredAll.filter((el) => {
    if (el.tagName === "SELECT") return !el.value;
    return !(el.value && el.value.toString().trim());
  });

  console.log("[JobPilot v0.6.0] filled:", [...new Set(filled)]);
  console.log("[JobPilot v0.6.0] failed:", failed);
  console.log("[JobPilot v0.6.0] skipped:", skipped);
  console.log("[JobPilot v0.6.0] required fields still empty:", requiredUnfilled.length, requiredUnfilled);

  // Highlight unfilled required fields
  for (const el of requiredUnfilled) {
    el.style.outline = "2px solid #ff6b6b";
    el.style.outlineOffset = "2px";
  }

  const uniqueFilled = [...new Set(filled)];
  const summary = [
    `JobPilot v0.6.0: filled ${filled.length}`,
    uniqueFilled.length ? `(${uniqueFilled.slice(0, 8).join(", ")}${uniqueFilled.length > 8 ? "…" : ""})` : "",
    requiredUnfilled.length ? `\n⚠ ${requiredUnfilled.length} required field${requiredUnfilled.length === 1 ? "" : "s"} still need you (outlined red)` : "",
    `\nConsole → [JobPilot v0.6.0] for details`,
  ].filter(Boolean).join(" ");
  toast(summary);

  function toast(msg) {
    const existing = document.getElementById("jobpilot-toast");
    if (existing) existing.remove();
    const t = document.createElement("div");
    t.id = "jobpilot-toast";
    t.textContent = msg;
    t.style.cssText =
      "position:fixed;right:16px;bottom:16px;z-index:2147483647;" +
      "background:#0a0a0a;color:#fff;padding:12px 16px;border-radius:8px;" +
      "font:13px/1.45 -apple-system,BlinkMacSystemFont,sans-serif;max-width:440px;" +
      "box-shadow:0 4px 16px rgba(0,0,0,0.25);opacity:0;transition:opacity .2s;" +
      "white-space:pre-line;";
    document.body.appendChild(t);
    requestAnimationFrame(() => (t.style.opacity = "1"));
    setTimeout(() => {
      t.style.opacity = "0";
      setTimeout(() => t.remove(), 300);
    }, 8000);
  }
})();
