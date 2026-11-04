import {parseInboxFile, parseInboxText} from './inbox-import.js';
import {el, field, button, dated, guard} from './workspace-ui.js';

export function mountInbox(root, {call, notify}) {
  const run = action => guard(root, notify, action);
  let pending = null, filename = '', refreshVersion = 0;
  const file = field('Import a tracker or shortlist (XLSX, CSV, TSV, or JSON)', {type: 'file'});
  file.input.accept = '.xlsx,.csv,.tsv,.json';
  const paste = field('Or paste job records as JSON, CSV, or TSV', {tag: 'textarea', maxLength: 1000000, rows: 4});
  const format = field('Pasted format', {tag: 'select'});
  for (const value of ['json', 'csv', 'tsv']) { const option = el('option', value.toUpperCase()); option.value = value; format.input.append(option); }
  const preview = el('div', undefined, 'import-preview'), entries = el('div'), tracker = el('div');
  const show = field('Show inbox items', {tag: 'select'});
  for (const [value, label] of [['pending', 'Pending review'], ['all', 'All imports and decisions']]) { const option = el('option', label); option.value = value; show.input.append(option); }
  const importCard = el('section', undefined, 'card');
  importCard.append(el('h3', 'Bring your search together'), file.wrap, paste.wrap, format.wrap,
    button('Preview pasted records', () => void run(async () => { clearPreview(); pending = parseInboxText(paste.input.value, {format: format.input.value, filename: 'Pasted records'}); filename = 'Pasted records'; renderPreview(); })), preview);
  root.replaceChildren(el('h2', 'Unified inbox'), el('p', 'Review shortlists and tracker updates from your other tools alongside your current application records. Imported claims stay separate until you accept them.'),
    importCard, show.wrap, button('Refresh inbox and tracker', () => void run(refresh)), entries, el('h3', 'Current application tracker'), tracker);
  file.input.addEventListener('change', () => void run(async () => {
    clearPreview(); const selected = file.input.files[0]; if (!selected) return;
    try { pending = await parseInboxFile(selected); filename = selected.name; renderPreview(); }
    finally { file.input.value = ''; }
  }));
  function clearPreview() { pending = null; preview.replaceChildren(); }
  paste.input.addEventListener('input', clearPreview);
  format.input.addEventListener('change', clearPreview);
  function renderPreview() {
    preview.replaceChildren(el('h3', `Review import: ${pending.length} job records`),
      el('p', 'Staging creates pending inbox items. It does not change your application tracker.'));
    if (!pending.length) return;
    const samples = el('ul');
    for (const record of pending.slice(0, 20)) samples.append(el('li', `${record.company} · ${record.title} — ${record.rawStatus || record.status}`));
    preview.append(samples);
    if (pending.length > 20) preview.append(el('p', `Showing the first 20 of ${pending.length} records.`));
    preview.append(button('Stage records for review', () => void run(async () => {
      const result = await call('stageImport', pending, filename); pending = null; preview.replaceChildren(); file.input.value = ''; paste.input.value = '';
      await refresh(); notify(`${result.added} items staged; ${result.duplicates} repeated records skipped.`);
    }), 'primary'), button('Cancel import', () => { pending = null; preview.replaceChildren(); file.input.value = ''; }));
  }
  show.input.addEventListener('change', () => void run(refresh));
  function currentSummary(entry) {
    if (!entry.current) return entry.kind === 'story' ? 'No saved story with this identifier.' : 'Not yet in your application tracker.';
    return entry.kind === 'story' ? `Saved: ${entry.current.title}\n${entry.current.summary}\nRevision ${entry.current.revision}; ${entry.current.reviewedAt ? 'wording reviewed' : 'needs wording review'}`
      : `Saved: ${entry.current.company} · ${entry.current.title}\nStatus: ${entry.current.status}\n${entry.current.notes || 'No saved notes.'}`;
  }
  async function refresh() {
    const version = ++refreshVersion, data = await call('getInbox');
    if (version !== refreshVersion) return;
    entries.replaceChildren(); tracker.replaceChildren();
    const visible = data.entries.filter(entry => show.input.value === 'all' || entry.state === 'pending');
    if (!visible.length) entries.append(el('p', 'No imports need review. Your current tracker is below.', 'empty'));
    for (const entry of visible) {
      const record = entry.kind === 'story' ? entry.story : entry.job;
      const card = el('article', undefined, 'card');
      card.append(el('h3', entry.kind === 'story' ? record.title : `${record.company} · ${record.title}`),
        el('p', `${entry.kind === 'story' ? 'Career story' : 'Opportunity'} · ${entry.state}`, 'badge'),
        el('p', `Source: ${entry.source.label}${entry.source.row ? ` · row ${entry.source.row}` : ''}\nImported ${dated(entry.importedAt)}`, 'fine-print'));
      const compare = el('div', undefined, 'comparison'); compare.append(el('p', currentSummary(entry)),
        el('p', entry.kind === 'story' ? `Imported: ${record.summary}\nSource: ${record.source || 'Not provided'}\nImported wording needs review.`
          : `Imported status: ${record.status}\nOriginal status: ${record.rawStatus || 'Not supplied'}\n${record.notes || ''}`));
      if (entry.kind === 'story' && record.truth_boundaries.length) compare.append(el('p', `Truth boundaries:\n${record.truth_boundaries.join('\n')}`));
      if (entry.kind === 'opportunity') {
        const link = el('a', 'Open job posting'); link.href = record.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; compare.append(link);
      }
      card.append(compare);
      if (entry.state === 'pending') {
        const actions = el('div', undefined, 'actions');
        for (const [action, label] of [['accept', entry.current ? 'Use imported update' : entry.kind === 'story' ? 'Add to career memory' : 'Add to tracker'],
          ['keep', 'Keep current record'], ['dismiss', 'Dismiss this import']]) {
          if (action === 'keep' && !entry.current) continue;
          actions.append(button(label, () => void run(async () => {
            await call('resolveInbox', entry.id, action, entry.currentToken); await refresh(); notify(action === 'accept' ? 'Import accepted. Source and review decision saved.' : 'Review decision saved.');
          }), action === 'accept' ? 'primary' : ''));
        }
        card.append(actions);
      } else card.append(el('p', `Decision: ${entry.decision} · ${dated(entry.reviewedAt)}`));
      entries.append(card);
    }
    if (!data.applications.length) tracker.append(el('p', 'Applications saved in the side panel or accepted from this inbox will appear here.', 'empty'));
    for (const app of data.applications) {
      const card = el('article', undefined, 'card');
      card.append(el('h3', `${app.company} · ${app.title}`), el('p', app.status, 'badge'), el('p', app.notes || 'No notes.'));
      const link = el('a', 'Open application'); link.href = app.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; card.append(link); tracker.append(card);
    }
  }
  return {refresh};
}
