export function el(tag, content, className) {
  const node = document.createElement(tag);
  if (content !== undefined) node.textContent = content;
  if (className) node.className = className;
  return node;
}
export function field(label, {tag = 'input', type = 'text', required = false, maxLength = 1000, rows = 3} = {}) {
  const wrap = el('label', label), input = el(tag);
  if (tag === 'input') input.type = type;
  if (tag === 'textarea') input.rows = rows;
  if (tag !== 'select') input.maxLength = maxLength;
  input.required = required;
  wrap.append(input);
  return {wrap, input};
}
export function button(label, action, className = '') {
  const node = el('button', label, className); node.type = 'button';
  node.addEventListener('click', action);
  return node;
}
export function option(select, value, label) {
  const node = el('option', label); node.value = value; select.append(node); return node;
}
export function dated(value) { return value ? new Date(value).toLocaleString() : 'Not recorded'; }
export function download(content, filename, type = 'application/json') {
  const url = URL.createObjectURL(new Blob([content], {type}));
  const link = el('a'); link.href = url; link.download = filename; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}
export function downloadAttachment(value) {
  const binary = atob(value.dataUrl.split(',')[1]);
  download(Uint8Array.from(binary, c => c.charCodeAt(0)), value.name, value.type);
}
export async function guard(root, notify, action) {
  if (root.dataset.busy) return;
  root.dataset.busy = 'true'; root.setAttribute('aria-busy', 'true');
  const disabled = [...root.querySelectorAll('button, input, textarea, select')].map(control => [control, control.disabled]);
  for (const [control] of disabled) control.disabled = true;
  try { return await action(); }
  catch (error) { notify(error.message || 'This change could not be saved.', true); }
  finally {
    delete root.dataset.busy; root.removeAttribute('aria-busy');
    for (const [control, previous] of disabled) if (control.isConnected) control.disabled = previous;
  }
}
