function captureApplicationFields() {
  const previous = document.getElementById("jobpilot-privacy-capture");
  if (previous) { previous.showModal(); return; }
  const clean = (text) => String(text || "").replace(/\s+/g, " ").trim();
  const answerWidgets = "input, textarea, select, [contenteditable], [role=combobox], [role=textbox], [role=searchbox], [role=listbox], [role=option], [role=spinbutton]";
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
  const fields = [], notices = [], seen = new Set(), documents = new Set();
  const inspect = (doc) => {
    if (documents.has(doc) || documents.size >= 10) return;
    documents.add(doc);
    for (const control of Array.from(doc.querySelectorAll("input, textarea, select, [role=combobox], [role=radiogroup]")).slice(0, 4000)) {
      if (!visible(control) || /^(hidden|submit|button|reset|image)$/i.test(control.type || "")) continue;
      const group = control.closest("fieldset, [role=radiogroup]");
      const legend = group && textOf(group.querySelector("legend"));
      const radio = /^(radio|checkbox)$/i.test(control.type || "");
      const groupLabel = group && (legend || labelled(group));
      const labels = Array.from(control.labels || []).map(textOf).filter(Boolean);
      const label = ((radio && groupLabel) || labelled(control) || labels.join(" ") || control.getAttribute("placeholder") || control.getAttribute("name") || "Unlabelled field").slice(0, 2000);
      const options = [];
      if (control.tagName === "SELECT") {
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
      const description = (control.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean)
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
