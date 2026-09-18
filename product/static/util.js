export const $ = (id) => document.getElementById(id);
export function tell(message, failed = false) {
  $("status").textContent = message;
  $("status").className = failed ? "error" : "";
}
export async function api(path, options = {}) {
  const response = await fetch("/api/" + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok)
    throw new Error(
      [data.error || "The request could not finish.", data.action || ""].join(
        " ",
      ),
    );
  return data;
}
export function node(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
}
export function button(text, action, className = "") {
  const el = node("button", text, className);
  el.type = "button";
  el.addEventListener("click", action);
  return el;
}
export function download(text, filename, type = "text/plain") {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = node("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export async function busy(el, message, fn) {
  el.disabled = true;
  tell(message);
  try {
    await fn();
  } catch (e) {
    tell(e.message || "Something went wrong. Try again.", true);
  } finally {
    el.disabled = false;
  }
}
export async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
    tell("Copied. Paste it where you choose.");
  } catch {
    const field = node("textarea");
    field.value = text;
    field.setAttribute("aria-label", "Text to copy");
    $("copy-fallback").replaceChildren(field);
    field.focus();
    field.select();
    tell(
      "Clipboard access is unavailable. The text is selected for you to copy.",
    );
  }
}
