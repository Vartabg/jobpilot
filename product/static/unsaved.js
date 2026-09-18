let changed = false;
export const dirty = () => {
  changed = true;
};
export const clean = () => {
  changed = false;
};
export function watch(element) {
  element.addEventListener("input", dirty);
  element.addEventListener("change", dirty);
}
export function allowLeave() {
  if (
    changed &&
    !window.confirm(
      "You have unsaved changes. Discard them and leave this view?",
    )
  )
    return false;
  if (changed)
    document.getElementById("job-detail").textContent =
      "Select an opening to review it.";
  clean();
  return true;
}
window.addEventListener("beforeunload", (event) => {
  if (changed) {
    event.preventDefault();
    event.returnValue = "";
  }
});
