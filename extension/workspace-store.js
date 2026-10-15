import {normalizeStory, STORY_LIMIT} from './memory.js';
import {sealCapsule, validateCapsule, capsuleSummary, digest, text} from './capsule.js';

const STORES = ['stories', 'capsules', 'inbox', 'capsuleSummaries'];
const STATUSES = new Set(['started', 'submitted', 'interview', 'rejected', 'offer', 'withdrawn']);
let opening;
function database() {
  if (!opening) opening = new Promise((resolve, reject) => {
    const request = indexedDB.open('jobpilot-career-workspace', 1);
    request.onupgradeneeded = () => { for (const name of STORES) request.result.createObjectStore(name, {keyPath: 'id'}); };
    request.onsuccess = () => {
      const db = request.result;
      db.onversionchange = () => { db.close(); opening = null; };
      resolve(db);
    };
    request.onerror = request.onblocked = () => { opening = null; reject(new Error('Could not open the career workspace. Close other panels and retry.')); };
  });
  return opening;
}
async function read(store, id) {
  const db = await database();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(store, 'readonly');
    const request = id === undefined ? tx.objectStore(store).getAll() : tx.objectStore(store).get(id);
    tx.oncomplete = () => resolve(request.result);
    tx.onabort = tx.onerror = () => reject(new Error('Could not read the saved workspace.'));
  });
}
async function write(changes) {
  if (!changes.length) return;
  const db = await database();
  return new Promise((resolve, reject) => {
    const tx = db.transaction([...new Set(changes.map(c => c.store))], 'readwrite');
    for (const change of changes) tx.objectStore(change.store)[change.remove ? 'delete' : change.add ? 'add' : 'put'](change.value);
    tx.oncomplete = () => resolve();
    tx.onabort = tx.onerror = () => reject(new Error('The workspace could not save this change. Check browser storage and retry; existing records were preserved.'));
  });
}
function records(value, max = 2000) {
  if (!Array.isArray(value) || value.length > max) throw new Error(`Import must contain at most ${max} records.`);
  return value;
}
function newest(a, b) { return (b.updatedAt || b.sealedAt || b.importedAt || '').localeCompare(a.updatedAt || a.sealedAt || a.importedAt || ''); }
async function stage(items) {
  const existing = new Set((await read('inbox')).map(v => v.id));
  const fresh = items.filter(item => !existing.has(item.id) && existing.add(item.id));
  await write(fresh.map(value => ({store: 'inbox', value, add: true})));
  return {added: fresh.length, duplicates: items.length - fresh.length};
}
function source(value, fallback) {
  return {label: text(value.sourceLabel || fallback, 1000) || 'Local import', row: text(String(value.sourceRow ?? ''), 100),
    observedAt: text(value.observedAt, 100)};
}
function storyContent(story) {
  const content = {...story};
  for (const key of ['id', 'createdAt', 'updatedAt', 'reviewedAt', 'revision']) delete content[key];
  return content;
}
function capsuleWrites(capsule) {
  return [{store: 'capsules', value: capsule, add: true}, {store: 'capsuleSummaries', value: capsuleSummary(capsule), add: true}];
}

// The existing worker calls this on its queue. Legacy access stays on that same
// queue, so accepting an inbox update cannot race a side-panel tracker update.
export async function executeWorkspaceOperation(operation, args, {legacy, normalizeJobUrl}) {
  if (!Array.isArray(args)) throw new Error('Invalid workspace request.');
  const handlers = {
    async getContext() {
      return {applications: await legacy('listApplications', []), resumes: await legacy('listResumes', []),
        selectedResumeId: (await legacy('getProfile', [])).selected_resume_id, stories: await handlers.listStories()};
    },
    async listStories() { return (await read('stories')).sort(newest); },
    async saveStory(value) {
      const previous = value?.id ? await read('stories', value.id) : null;
      if (previous && value.revision !== previous.revision) throw new Error('This story changed in another workspace. Refresh before saving; your edits have not replaced it.');
      if (!previous && value?.revision) throw new Error('This story was removed in another workspace. Save it as a new story instead.');
      if (!previous && (await read('stories')).length >= STORY_LIMIT) throw new Error(`The vault holds at most ${STORY_LIMIT} stories. Export or remove one before adding another.`);
      const story = normalizeStory(value, previous);
      await write([{store: 'stories', value: story}]);
      return story;
    },
    async deleteStory(id, revision) {
      const previous = await read('stories', text(id, 100));
      if (!previous || previous.revision !== revision) throw new Error('This story changed or was removed. Refresh before deleting.');
      await write([{store: 'stories', value: id, remove: true}]);
      return true;
    },
    async stageStories(values, label) {
      const items = await Promise.all(records(values).map(async value => {
        const story = normalizeStory({...value, reviewed: false, reviewedAt: ''});
        const content = storyContent(story);
        const provenance = source(value, label);
        return {id: await digest(JSON.stringify({kind: 'story', content, provenance})), kind: 'story', story,
          source: provenance, importedAt: new Date().toISOString(), state: 'pending'};
      }));
      return stage(items);
    },
    async stageImport(values, label) {
      const items = await Promise.all(records(values).map(async value => {
        const job = {...normalizeJobUrl(value.url), company: text(value.company), title: text(value.title),
          status: text(value.status, 100) || 'started', rawStatus: text(value.rawStatus, 1000), notes: text(value.notes, 10000)};
        if (!job.company || !job.title || !STATUSES.has(job.status)) throw new Error('Every imported job needs a company, role, URL, and supported status.');
        const provenance = source(value, label);
        return {id: await digest(JSON.stringify({kind: 'opportunity', job, provenance})), kind: 'opportunity', job,
          source: provenance, importedAt: new Date().toISOString(), state: 'pending'};
      }));
      return stage(items);
    },
    async getInbox() {
      const applications = await legacy('listApplications', []);
      const stories = await read('stories');
      const entries = await Promise.all((await read('inbox')).sort(newest).map(async entry => {
        const current = entry.kind === 'story' ? stories.find(v => v.id === entry.story.id)
          : applications.find(v => v.identity === normalizeJobUrl(entry.job.url).identity);
        return {...entry, current: current || null, currentToken: await digest(JSON.stringify(current || null))};
      }));
      return {entries, applications};
    },
    async resolveInbox(id, action, expectedToken) {
      if (!['accept', 'keep', 'dismiss'].includes(action)) throw new Error('Choose an inbox review action.');
      const entry = await read('inbox', text(id, 100));
      if (!entry || entry.state !== 'pending') throw new Error('This inbox item has already been reviewed. Refresh the inbox.');
      const current = entry.kind === 'story' ? await read('stories', entry.story.id)
        : await legacy('findApplication', [entry.job.url]);
      if (await digest(JSON.stringify(current || null)) !== expectedToken) throw new Error('The saved record changed while you reviewed it. Refresh and compare again.');
      const changes = [];
      let accepted = current || null;
      if (action === 'accept' && entry.kind === 'story') {
        if (!current && (await read('stories')).length >= STORY_LIMIT) throw new Error(`The vault holds at most ${STORY_LIMIT} stories.`);
        accepted = normalizeStory({...entry.story, reviewed: false}, current || null);
        changes.push({store: 'stories', value: accepted});
      } else if (action === 'accept') {
        const job = entry.job;
        // Preserve local notes and résumé choices. The original imported text
        // remains in the inbox record even when the user keeps current data.
        const sourceNote = `[${entry.source.label}${entry.source.row ? `, row ${entry.source.row}` : ''}] ${job.notes}`;
        const notes = [current?.notes, sourceNote].filter(Boolean).join('\n').slice(0, 10000);
        accepted = current ? await legacy('updateApplication', [current.id, {company: job.company, title: job.title, status: job.status, notes}])
          : await legacy('saveApplication', [{...job, notes, source: 'career-inbox'}, job.status]);
      }
      const resolved = {...entry, state: action === 'dismiss' ? 'dismissed' : 'reviewed', decision: action,
        reviewedAt: new Date().toISOString(), previous: current || null, accepted};
      changes.push({store: 'inbox', value: resolved});
      try { await write(changes); }
      catch (error) {
        if (action === 'accept' && entry.kind === 'opportunity') throw new Error('The tracker was updated, but the review record could not be saved. Refresh the inbox before retrying.');
        throw error;
      }
      return resolved;
    },
    async listCapsules() { return (await read('capsuleSummaries')).sort(newest); },
    async getCapsule(id) {
      const capsule = await read('capsules', text(id, 100));
      if (!capsule) throw new Error('This capsule was not found.');
      return validateCapsule(capsule);
    },
    async sealCapsule(value) {
      const input = structuredClone(value);
      input.application = {...input.application, ...normalizeJobUrl(input.application?.url)};
      if (input.resumeId) {
        const file = await legacy('getResume', [input.resumeId]);
        input.resume = {...file.metadata, dataUrl: file.dataUrl};
      }
      input.stories = [];
      for (const id of records(input.storyIds ?? [], 100)) {
        const story = await read('stories', text(id, 100));
        if (!story?.reviewedAt) throw new Error('Review each selected story in Career memory before sealing.');
        if (input.storyVersions?.[id] !== `${story.revision}:${story.reviewedAt}`) throw new Error('A selected story changed. Refresh and review the capsule again.');
        input.stories.push(story);
      }
      const capsule = await sealCapsule(input);
      await write(capsuleWrites(capsule));
      return capsuleSummary(capsule);
    },
    async importCapsule(value) {
      const capsule = await validateCapsule(value);
      const existing = await read('capsules', capsule.id);
      if (existing && existing.integrity.sha256 !== capsule.integrity.sha256) throw new Error('A different capsule already uses this ID. Existing evidence was preserved.');
      if (!existing) await write(capsuleWrites(capsule));
      return {added: existing ? 0 : 1};
    },
    async exportWorkspace() {
      const [stories, capsules, inbox] = await Promise.all(['stories', 'capsules', 'inbox'].map(name => read(name)));
      return {schema: 'jobpilot.workspace/v1', exportedAt: new Date().toISOString(), stories, capsules, inbox,
        note: 'Includes private career stories, final answers, résumé bytes and receipt attachments. Store and share deliberately. Profile and the live tracker use the side-panel backup.'};
    },
    async restoreWorkspace(value) {
      if (value?.schema !== 'jobpilot.workspace/v1') throw new Error('Choose a career workspace backup.');
      const stories = records(value.stories).map(v => normalizeStory({...v, reviewed: false, reviewedAt: ''}));
      const capsules = await Promise.all(records(value.capsules).map(validateCapsule));
      // Restored inbox data is re-staged, rather than importing executable
      // decisions or accepting another source's claims about submissions.
      const entries = records(value.inbox);
      for (const entry of entries) {
        if (!['story', 'opportunity'].includes(entry.kind)) throw new Error('Unsupported inbox entry in backup.');
        if (entry.kind === 'opportunity') {
          normalizeJobUrl(entry.job?.url);
          if (!STATUSES.has(entry.job?.status) || !text(entry.job?.company) || !text(entry.job?.title)) throw new Error('Invalid job in backup.');
          text(entry.job.notes, 10000); text(entry.source?.label, 1000);
        } else normalizeStory({...entry.story, reviewed: false, reviewedAt: ''});
      }
      const changes = [];
      for (const capsule of capsules) {
        const existing = await read('capsules', capsule.id);
        if (existing && existing.integrity.sha256 !== capsule.integrity.sha256) throw new Error('A capsule conflicts with this backup. Nothing was restored.');
        if (!existing) changes.push(...capsuleWrites(capsule));
      }
      // Validate the full backup before any durable writes. Memory and inbox
      // imports require another review; existing stories are never overwritten.
      const pending = [];
      for (const story of stories) {
        const provenance = {label: 'Workspace backup', row: '', observedAt: ''};
        const id = await digest(JSON.stringify({kind: 'story', content: storyContent(story), provenance}));
        pending.push({id, kind: 'story', story, source: provenance, importedAt: new Date().toISOString(), state: 'pending'});
      }
      for (const entry of entries) {
        const item = entry.kind === 'story' ? {story: normalizeStory({...entry.story, reviewed: false, reviewedAt: ''})}
          : {job: {...normalizeJobUrl(entry.job.url), company: text(entry.job.company), title: text(entry.job.title), status: entry.job.status,
            notes: text(entry.job.notes, 10000), rawStatus: text(entry.job.rawStatus, 1000)}};
        const provenance = source({...entry.source, sourceLabel: entry.source?.label, sourceRow: entry.source?.row}, 'Workspace backup');
        const fingerprint = entry.kind === 'story' ? {kind: 'story', content: storyContent(item.story), provenance}
          : {kind: 'opportunity', job: item.job, provenance};
        const id = await digest(JSON.stringify(fingerprint));
        pending.push({id, kind: entry.kind, ...item, source: provenance, importedAt: new Date().toISOString(), state: 'pending'});
      }
      const known = new Set((await read('inbox')).map(v => v.id));
      for (const value of pending) if (!known.has(value.id)) { known.add(value.id); changes.push({store: 'inbox', value, add: true}); }
      await write(changes);
      return {capsules: changes.filter(c => c.store === 'capsules').length, pending: changes.filter(c => c.store === 'inbox').length};
    },
  };
  if (!Object.hasOwn(handlers, operation)) throw new Error('Unknown career workspace operation.');
  return handlers[operation](...args);
}
