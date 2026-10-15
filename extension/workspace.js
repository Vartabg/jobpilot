import {workspaceCall as call} from './workspace-client.js';
import {mountMemory} from './memory-view.js';
import {mountCapsules} from './capsule-view.js';
import {mountInbox} from './inbox-view.js';
import {download, guard} from './workspace-ui.js';

const notice = document.querySelector('#workspace-notice');
function notify(message, error = false) {
  notice.textContent = message; notice.hidden = false; notice.classList.toggle('error', error);
  notice.setAttribute('role', error ? 'alert' : 'status');
}
const api = {call, notify, download};
const views = {memory: mountMemory(document.querySelector('#memory'), api), capsules: mountCapsules(document.querySelector('#capsules'), api), inbox: mountInbox(document.querySelector('#inbox'), api)};
let sequence = 0, current = '';
async function show(name) {
  if (!Object.hasOwn(views, name)) name = 'memory';
  current = name; const revision = ++sequence;
  for (const button of document.querySelectorAll('[data-view]')) {
    const selected = button.dataset.view === name;
    button.setAttribute('aria-selected', String(selected)); button.tabIndex = selected ? 0 : -1;
    document.getElementById(button.dataset.view).hidden = !selected;
  }
  history.replaceState(null, '', `#${name}`);
  try { await views[name].refresh(); }
  catch (error) { if (revision === sequence) notify(error.message, true); }
}
for (const button of document.querySelectorAll('[data-view]')) {
  button.addEventListener('click', () => void show(button.dataset.view));
  button.addEventListener('keydown', event => {
    const buttons = [...document.querySelectorAll('[data-view]')]; let index = buttons.indexOf(button);
    if (['ArrowDown', 'ArrowRight'].includes(event.key)) index = (index + 1) % buttons.length;
    else if (['ArrowUp', 'ArrowLeft'].includes(event.key)) index = (index + buttons.length - 1) % buttons.length;
    else if (event.key === 'Home') index = 0;
    else if (event.key === 'End') index = buttons.length - 1;
    else return;
    event.preventDefault(); buttons[index].focus(); void show(buttons[index].dataset.view);
  });
}
const tools = document.querySelector('.backup-tools');
document.querySelector('#backup-workspace').addEventListener('click', () => void guard(tools, notify, async () => {
  const backup = await call('exportWorkspace'); download(JSON.stringify(backup, null, 2), 'jobpilot-career-workspace.json');
  notify('Backup downloaded with stories, capsule files, and inbox review history. Keep it somewhere private.');
}));
let incoming;
const preview = document.querySelector('#restore-preview'), input = document.querySelector('#restore-workspace');
input.addEventListener('change', () => void guard(tools, notify, async () => {
  incoming = null; preview.hidden = true; const file = input.files[0]; if (!file) return;
  if (file.size > 50 * 1024 * 1024) throw new Error('Workspace backup imports must be 50 MiB or smaller. Restore individual capsules for larger workspaces.');
  const value = JSON.parse(await file.text());
  if (value.schema !== 'jobpilot.workspace/v1' || !['stories', 'capsules', 'inbox'].every(key => Array.isArray(value[key]))) throw new Error('Choose a valid career workspace backup.');
  incoming = value;
  document.querySelector('#restore-summary').textContent = `${value.stories.length} stories · ${value.capsules.length} capsules · ${value.inbox.length} inbox records. Capsules are checksum-validated. Stories and tracker updates return to Inbox for review. Current records are preserved.`;
  preview.hidden = false;
}));
document.querySelector('#cancel-restore').addEventListener('click', () => { incoming = null; preview.hidden = true; input.value = ''; });
document.querySelector('#confirm-restore').addEventListener('click', () => void guard(tools, notify, async () => {
  if (!incoming) throw new Error('Choose a backup first.');
  const result = await call('restoreWorkspace', incoming); incoming = null; preview.hidden = true; input.value = '';
  await views[current].refresh(); notify(`${result.capsules} capsules restored; ${result.pending} items staged for review. Existing records preserved.`);
}));
window.addEventListener('hashchange', () => void show(location.hash.slice(1)));
void show(location.hash.slice(1));
