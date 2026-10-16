function captureApplicationFields() {
  const previous = document.getElementById("jobpilot-privacy-capture");
  if (previous) { previous.showModal(); return; }
  const clean = (text) => String(text || "").replace(/\s+/g, " ").trim();
  const answerWidgets = "input, textarea, select, button[aria-haspopup=listbox], [contenteditable], [role=combobox], [role=textbox], [role=searchbox], [role=listbox], [role=option], [role=spinbutton]";
  // Workday may describe an empty search input with its current selection.
  // These announcements and tokens are answer state, not employer help text.
  const answerState = "[role=alert], [aria-live], [data-automation-id=promptAriaInstruction], [data-automation-id=selectedItemList], [data-automation-id=selectedItem]";
  const textOf = (element) => {
    if (!element || element.closest(answerWidgets + ", " + answerState)) return "";
    const clone = element.cloneNode(true);
    clone.querySelectorAll(answerWidgets + ", " + answerState + ", script, style, [data-jobpilot-private]").forEach((n) => n.remove());
    return clean(clone.textContent);
  };
  const labelled = (element) => {
    const refs = (element.getAttribute("aria-labelledby") || "").split(/\s+/).filter(Boolean);
    return clean(refs.map((id) => {
      const target = element.ownerDocument.getElementById(id);
      return target && target !== element && !element.contains(target) ? textOf(target) : "";
    }).join(" ")) ||
      clean(element.getAttribute("aria-label"));
  };
  const visible = (element) => {
    const win = element.ownerDocument.defaultView;
    const style = win.getComputedStyle(element);
    return element.getClientRects().length > 0 && style.display !== "none" && style.visibility !== "hidden";
  };
  const visibleText = (element) => {
    if (!visible(element)) return false;
    for (let node = element; node; node = node.parentElement) {
      const style = element.ownerDocument.defaultView.getComputedStyle(node);
      if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") return false;
    }
    return true;
  };
  const fieldSelector = '[data-automation-id^="formField-"]';
  const fieldsetQuestion = (control) => {
    const field = control.closest(fieldSelector), fieldset = control.closest("fieldset");
    if (!field || !fieldset || fieldset.closest(fieldSelector) !== field || !visibleText(fieldset)) return "";
    const legends = Array.from(fieldset.children).filter(node => node.tagName === "LEGEND");
    if (legends.length !== 1 || legends[0].closest(fieldSelector) !== field || !visibleText(legends[0])) return "";
    const controls = Array.from(fieldset.querySelectorAll("input, textarea, select, button[aria-haspopup=listbox], [role=combobox]"))
      .filter(node => node.type !== "hidden" && visibleText(node));
    return controls.length === 1 && controls[0] === control ? textOf(legends[0]) : "";
  };
  const dropdownQuestion = (control) => {
    const field = control.closest(fieldSelector), id = control.getAttribute("id");
    if (!field || !id) return "";
    const labels = Array.from(field.querySelectorAll("label")).filter(label => label.getAttribute("for") === id
      && label.closest(fieldSelector) === field && visibleText(label));
    return labels.length ? labels.length === 1 ? textOf(labels[0]) : "" : fieldsetQuestion(control);
  };
  const radioQuestion = (control) => {
    if (control.type !== "radio" || !control.name) return null;
    const field = control.closest(fieldSelector), question = control.closest("[aria-required][aria-labelledby]");
    if (!field || !question || question.closest(fieldSelector) !== field || !visibleText(question)) return null;
    const ids = (question.getAttribute("aria-labelledby") || "").split(/\s+/).filter(Boolean);
    if (!ids.length || ids.length > 20) return null;
    const references = ids.map(id => control.ownerDocument.getElementById(id));
    if (references.some(node => !node || node.closest(fieldSelector) !== field || !visibleText(node))) return null;
    const label = clean(references.map(textOf).join(" "));
    const peers = Array.from(question.querySelectorAll('input[type="radio"]'));
    if (!label || !peers.length || peers.length > 500 || peers.some(peer => peer.name !== control.name || peer.form !== control.form
      || peer.closest(fieldSelector) !== field || peer.closest("[aria-required][aria-labelledby]") !== question)) return null;
    const options = peers.map(peer => clean(Array.from(peer.labels || []).filter(option => question.contains(option)
      && option.closest(fieldSelector) === field && visibleText(option)).map(textOf).join(" ")));
    if (options.some(option => !option)) return null;
    return {question, label, peers, options};
  };
  const fields = [], notices = [], seen = new Set(), documents = new Set();
  const inspect = (doc) => {
    if (documents.has(doc) || documents.size >= 10) return;
    documents.add(doc);
    const seenQuestions = new Set();
    for (const control of Array.from(doc.querySelectorAll("input, textarea, select, button[aria-haspopup=listbox], [role=combobox], [role=radiogroup]")).slice(0, 4000)) {
      const dropdown = control.tagName === "BUTTON" && control.getAttribute("aria-haspopup") === "listbox";
      if (!visible(control) || !dropdown && /^(hidden|submit|button|reset|image)$/i.test(control.type || "")) continue;
      const metadata = radioQuestion(control);
      if (metadata && seenQuestions.has(metadata.question)) continue;
      if (metadata) seenQuestions.add(metadata.question);
      const group = metadata?.question || control.closest("fieldset, [role=radiogroup]");
      const legend = group && textOf(group.querySelector("legend"));
      const radio = /^(radio|checkbox)$/i.test(control.type || "");
      const groupLabel = metadata?.label || group && (legend || labelled(group));
      const labels = dropdown ? [] : Array.from(control.labels || []).map(textOf).filter(Boolean);
      const questionLabel = dropdown ? dropdownQuestion(control) : "";
      if (dropdown && !questionLabel) notices.push("A dropdown question could not be linked to a visible local label. Its current selection was not collected; inspect this question on the form.");
      // A Workday button's accessible name can contain the selected answer.
      const label = (dropdown ? questionLabel || "Unlabelled field" : (radio && groupLabel) || labelled(control) || labels.join(" ") || control.getAttribute("placeholder") || control.getAttribute("name") || (!radio && fieldsetQuestion(control)) || "Unlabelled field").slice(0, 2000);
      const options = [];
      if (metadata) options.push(...metadata.options.map(option => option.slice(0, 500)));
      else if (control.tagName === "SELECT") {
        for (const option of control.options) if (clean(option.textContent)) options.push(clean(option.textContent).slice(0, 500));
      } else if (groupLabel && (radio || control.getAttribute("role") === "radiogroup")) {
        for (const input of group.querySelectorAll("input[type=radio], input[type=checkbox], [role=radio]")) {
          const choice = labelled(input) || Array.from(input.labels || []).map(textOf).join(" ");
          if (choice) options.push(choice.slice(0, 500));
        }
      }
      // Native required and ARIA are evidence. Missing markers are uncertain:
      // many ATS pages enforce their requirements only in later validation.
      const aria = control.getAttribute("aria-required") || group?.getAttribute("aria-required");
      const radioPeers = control.type === "radio" && group
        ? Array.from(group.querySelectorAll("input[type=radio]")).filter((peer) => peer.name === control.name) : [];
      const required = control.hasAttribute("required") || radioPeers.some((peer) => peer.hasAttribute("required") || peer.getAttribute("aria-required") === "true") || aria === "true" || /\*\s*$/.test(label)
        ? true : aria === "false" || /\boptional\b/i.test(label) ? false : null;
      const description = [...new Set([control, metadata?.question].filter(Boolean).flatMap(node => (node.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean)))]
        .map((id) => {
          const target = doc.getElementById(id);
          return target && target !== control && !control.contains(target) ? textOf(target) : "";
        }).filter(Boolean).join(" ").slice(0, 8000);
      const signature = JSON.stringify([label, required, options]);
      if (!seen.has(signature) && fields.length < 500) {
        fields.push({label, required, options: options.slice(0, 500), description});
        if (options.length > 500) notices.push("A choice list was truncated at 500 options; inspect the complete choices on the form.");
        seen.add(signature);
      }
    }
    for (const frame of doc.querySelectorAll("iframe")) {
      if (!visible(frame)) continue;
      try {
        if (frame.contentDocument?.documentElement) inspect(frame.contentDocument);
        else notices.push("An embedded frame could not be inspected. Open the application frame directly and scan again.");
      } catch { notices.push("A cross-origin embedded frame could not be inspected. Open the application frame directly and scan again."); }
    }
  };
  inspect(document);
  notices.unshift("Only currently visible fields were inspected. Later steps, collapsed choices, cross-origin frames and sign-in can hide additional requests. Rescan each step.");
  if (fields.length === 500) notices.push("The capture stopped at 500 fields. Coverage is incomplete.");
  if (documents.size === 10) notices.push("The capture stopped at ten embedded documents. Coverage is incomplete.");
  const payload = {source_url: location.origin + location.pathname, coverage: "visible_form", fields, notices: [...new Set(notices)].slice(0, 30)};
  const dialog = document.createElement("dialog");
  dialog.id = "jobpilot-privacy-capture";
  dialog.setAttribute("aria-labelledby", "jobpilot-privacy-capture-title");
  dialog.style.cssText = "box-sizing:border-box;max-width:42rem;width:90vw;max-height:85vh;overflow:auto;padding:24px;color:#17271f;background:white;border:2px solid #35624d;border-radius:12px;font:16px/1.5 system-ui";
  const add = (tag, text) => { const el = document.createElement(tag); el.textContent = text; dialog.append(el); return el; };
  add("h2", "Application questions captured").id = "jobpilot-privacy-capture-title";
  add("p", `${fields.length} field labels captured. Entered answers were not read. No information was sent to JobPilot or any other service.`);
  add("p", "Copy this metadata, then paste it into JobPilot’s Application privacy scan. This capture covers only what is visible now.");
  const label = add("label", "Captured field metadata");
  label.htmlFor = "jobpilot-privacy-metadata";
  const output = add("textarea", "");
  output.id = "jobpilot-privacy-metadata";
  output.readOnly = true;
  output.rows = 9;
  output.style.cssText = "display:block;width:100%;box-sizing:border-box;font:14px/1.5 monospace";
  output.value = JSON.stringify(payload, null, 2);
  const status = add("p", ""); status.setAttribute("role", "status");
  const copy = add("button", "Copy field metadata");
  copy.type = "button"; copy.style.minHeight = "44px";
  copy.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(output.value); status.textContent = "Copied. Paste it into JobPilot’s privacy scan."; }
    catch { output.focus(); output.select(); status.textContent = "Copy access is unavailable. The metadata is selected; press your normal Copy shortcut."; }
  });
  const close = add("button", "Close"); close.type = "button"; close.style.minHeight = "44px";
  close.addEventListener("click", () => dialog.close());
  const focusBefore = document.activeElement;
  dialog.addEventListener("close", () => { dialog.remove(); focusBefore?.focus(); });
  document.body.append(dialog); dialog.showModal(); copy.focus();
}
