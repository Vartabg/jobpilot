import { importStories, searchStories, storyMarkdown, storyPacket } from './memory.js';

// This view never opens source links or sends content to an assistant. Exports
// are explicit downloads, and imports become reviewable Inbox items.
export function mountMemory(root, { call, notify, download }) {
  const doc = root.ownerDocument;
  let stories = [], selected = null, imported = null, busy = false;
  const node = (tag, value, className) => {
    const item = doc.createElement(tag);
    if (value !== undefined) item.textContent = value;
    if (className) item.className = className;
    return item;
  };
  const button = (label, action) => {
    const item = node('button', label); item.type = 'button';
    item.addEventListener('click', () => void perform(action)); return item;
  };
  const error = node('p', ''); error.setAttribute('role', 'alert');
  const status = node('p', ''); status.setAttribute('role', 'status');
  const controls = node('div', undefined, 'actions');
  const contextLabel = node('label', 'Question or role context');
  const context = node('textarea'); context.rows = 3; context.maxLength = 6000;
  context.placeholder = 'Example: a time you debugged a difficult customer problem'; contextLabel.append(context);
  const results = node('div', undefined, 'memory-results');
  const heading = node('h2', 'Story Vault');
  const form = node('form'), fields = {};
  const formHeading = node('h3', 'Add a career story'); form.append(formHeading);
  const definitions = [
    ['title', 'Story title', false, 300], ['timeframe', 'Timeframe', false, 200], ['summary', 'What happened and your contribution', true, 8000],
    ['details', 'Details — one per line', true, 100000], ['skills', 'Skills — one per line', true, 15000],
    ['question_fit', 'Useful for questions about — one per line', true, 25000],
    ['truth_boundaries', 'Truth boundaries — one per line', true, 50000], ['source', 'Source or reference (displayed as text)', false, 2000],
  ];
  for (const [key, label, multiline, max] of definitions) {
    const wrapper = node('label', label), input = node(multiline ? 'textarea' : 'input');
    if (!multiline) input.type = 'text'; else input.rows = key === 'summary' ? 4 : 2;
    input.maxLength = max; input.name = key; input.required = key === 'title'; fields[key] = input;
    wrapper.append(input); form.append(wrapper);
  }
  const reviewedLabel = node('label'), reviewed = node('input'); reviewed.type = 'checkbox'; reviewed.name = 'reviewed';
  reviewedLabel.append(reviewed, doc.createTextNode(' I reviewed this wording and its truth boundaries.'));
  form.append(reviewedLabel, node('p', 'Wording review records your approval. It does not verify qualifications. Editing a story clears its approval.'));
  const save = node('button', 'Save story'); save.type = 'submit';
  const cancel = button('Cancel editing', () => resetForm()); form.append(save, cancel);
  const importLabel = node('label', 'Import story JSON');
  const file = node('input'); file.type = 'file'; file.accept = '.json,application/json'; importLabel.append(file);
  const preview = node('div', undefined, 'memory-import-preview');
  controls.append(button('New story', () => { resetForm(); fields.title.focus(); }),
    button('Export matching stories as JSON', () => exportStories('json')),
    button('Export matching stories as Markdown', () => exportStories('md')));
  root.replaceChildren(heading, node('p', 'Save real work accounts once, find relevant stories for each role, and export only when you choose. Everything stays in your local workspace.'),
    contextLabel, controls, status, error, results, form, importLabel, preview);

  async function perform(action) {
    if (busy) return;
    busy = true; error.textContent = '';
    for (const control of root.querySelectorAll('button, input[type="file"]')) control.disabled = true;
    try { await action(); } catch (failure) { error.textContent = failure?.message || 'The story operation could not be completed.'; }
    finally { busy = false; for (const control of root.querySelectorAll('button, input[type="file"]')) control.disabled = false; }
  }
  function resetForm(story = null) {
    selected = story; formHeading.textContent = story ? 'Edit career story' : 'Add a career story';
    for (const [key, input] of Object.entries(fields)) input.value = Array.isArray(story?.[key]) ? story[key].join('\n') : story?.[key] || '';
    reviewed.checked = false; save.textContent = story ? 'Save changes' : 'Save story';
  }
  form.addEventListener('input', event => { if (event.target !== reviewed) reviewed.checked = false; });
  form.addEventListener('submit', event => {
    event.preventDefault();
    void perform(async () => {
      const value = { ...(selected || {}), reviewed: reviewed.checked };
      for (const [key, input] of Object.entries(fields)) value[key] = ['details', 'skills', 'question_fit', 'truth_boundaries'].includes(key)
        ? input.value.split(/\r?\n/).map(line => line.trim()).filter(Boolean) : input.value;
      await call('saveStory', value); resetForm(); await refresh(); notify('Story saved locally.');
    });
  });
  function listItems(parent, label, values) {
    if (!values?.length) return;
    parent.append(node('p', label)); const list = node('ul');
    for (const value of values) list.append(node('li', value)); parent.append(list);
  }
  function render() {
    const matches = searchStories(stories, context.value); results.replaceChildren();
    status.textContent = `${matches.length} of ${stories.length} stories shown. Matches describe text relevance, not qualification.`;
    if (!matches.length) results.append(node('p', stories.length ? 'No stories match this context. Try another term or add a relevant account.' : 'Add your first real work account or import your existing true_accounts.json.'));
    for (const { story, matchedTerms } of matches) {
      const card = node('article', undefined, 'memory-card'); card.append(node('h3', story.title),
        node('p', story.reviewedAt ? `Wording reviewed · revision ${story.revision}` : `Needs wording review · revision ${story.revision}`));
      if (matchedTerms.length) card.append(node('p', `Matches: ${matchedTerms.join(', ')}. Related terms may be included.`));
      if (story.timeframe) card.append(node('p', story.timeframe));
      if (story.summary) card.append(node('p', story.summary));
      listItems(card, 'Details', story.details); listItems(card, 'Skills', story.skills);
      listItems(card, 'Useful for questions about', story.question_fit); listItems(card, 'Truth boundaries', story.truth_boundaries);
      if (story.source) card.append(node('p', `Source (not opened or verified): ${story.source}`));
      card.append(button('Edit story', () => { resetForm(story); fields.title.focus(); }), button('Delete story', async () => {
        await call('deleteStory', story.id, story.revision); if (selected?.id === story.id) resetForm(); await refresh(); notify('Story deleted locally.');
      })); results.append(card);
    }
  }
  context.addEventListener('input', () => { try { render(); } catch (failure) { error.textContent = failure.message; } });
  function exportStories(format) {
    const packet = storyPacket(stories, { query: context.value });
    if (!packet.stories.length) throw new Error('There are no matching stories to export.');
    const isJSON = format === 'json';
    download(isJSON ? JSON.stringify(packet, null, 2) : storyMarkdown(packet.stories), `jobpilot-stories.${format}`, isJSON ? 'application/json' : 'text/markdown');
    notify('Story export prepared. Share it with an assistant only when you choose.');
  }
  file.addEventListener('change', () => void perform(async () => {
    imported = null; preview.replaceChildren(); const selectedFile = file.files?.[0];
    if (!selectedFile) return;
    if (selectedFile.size > 2 * 1024 * 1024) throw new Error('Story imports must be 2 MiB or smaller.');
    const records = importStories(JSON.parse(await selectedFile.text()));
    if (!records.length) throw new Error('This file contains no stories.');
    imported = records;
    preview.append(node('h3', `Preview: ${records.length} imported stories`),
      node('p', 'Imported wording approvals are cleared. Stage these stories in Inbox to review before adding them to the vault.'));
    const list = node('ul');
    for (const story of records) list.append(node('li', `${story.title}${story.summary ? ` — ${story.summary.slice(0, 180)}` : ''}`));
    preview.append(list, button('Stage stories in Inbox', async () => {
      const count = await call('stageStories', imported, selectedFile.name);
      imported = null; preview.replaceChildren(node('p', `${count.added} stories staged; ${count.duplicates} repeated records skipped. Review them in Inbox.`));
      file.value = ''; notify('Imported stories are ready to review in Inbox.');
    }));
  }));
  async function refresh() { stories = await call('listStories'); render(); }
  return { refresh };
}
