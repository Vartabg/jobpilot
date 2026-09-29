// Local domain storage. UI callers use the worker broker so multiple panels
// cannot overwrite one another's application/resume read-modify-write updates.
export const APPLICATION_STATUSES = ["started", "submitted", "interview", "rejected", "offer", "withdrawn"];
export const SCHEMA_VERSION = 1;
const KEYS = { profile: "jobpilot_profile_v1", applications: "jobpilot_applications_v1", resumes: "jobpilot_resumes_v1" };
const INITIALIZATION_MARKER = "jobpilot_initialized_v1";
const MAX_RESUME_BYTES = 10 * 1024 * 1024;
const PROFILE_FIELDS = ["first_name", "last_name", "email", "phone", "city", "state", "country", "postal_code", "linkedin_url", "github_url", "portfolio_url", "current_title", "selected_resume_id"];
const MIME_TYPES = { pdf: "application/pdf" };

export function emptyProfile() {
  return { ...Object.fromEntries(PROFILE_FIELDS.map((key) => [key, ""])), work_authorized: null,
    requires_sponsorship: null, work_history: [], education: [] };
}

const uid = () => crypto.randomUUID();
const now = () => new Date().toISOString();
function object(value, name) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${name} must be an object.`);
  return value;
}
function string(value, max = 1000) {
  if (value === undefined || value === null) return "";
  if (typeof value !== "string") throw new Error("Text fields must contain text.");
  if (value.length > max) throw new Error(`A text field exceeds ${max} characters.`);
  return value.trim();
}
function identifier(value) {
  const result = string(value, 100);
  if (result && !/^[a-zA-Z0-9_-]+$/.test(result)) throw new Error("Invalid record identifier.");
  return result || uid();
}
function date(value) {
  const result = string(value, 10);
  if (result && !/^\d{4}(?:-(?:0[1-9]|1[0-2]))?(?:-(?:0[1-9]|[12]\d|3[01]))?$/.test(result)) {
    throw new Error("Dates must use YYYY, YYYY-MM, or YYYY-MM-DD.");
  }
  return result;
}
function isoDate(value, fallback = "") {
  const result = string(value, 50);
  if (!result) return fallback;
  if (Number.isNaN(Date.parse(result))) throw new Error("Invalid timestamp in backup.");
  return new Date(result).toISOString();
}
function nullableBoolean(value) {
  if (value === undefined || value === null || value === "") return null;
  if (typeof value !== "boolean") throw new Error("Work authorization answers must be yes, no, or unanswered.");
  return value;
}
function rows(value, name, normalize) {
  if (value === undefined) return [];
  if (!Array.isArray(value) || value.length > 100) throw new Error(`${name} must contain at most 100 entries.`);
  return value.map((row) => normalize(object(row, name)));
}
function profileData(value) {
  object(value, "Profile");
  const result = emptyProfile();
  for (const key of PROFILE_FIELDS) result[key] = string(value[key]);
  for (const key of ["linkedin_url", "github_url", "portfolio_url"]) {
    if (result[key]) result[key] = safeUrl(result[key]);
  }
  result.work_authorized = nullableBoolean(value.work_authorized);
  result.requires_sponsorship = nullableBoolean(value.requires_sponsorship);
  result.work_history = rows(value.work_history, "Work history", (row) => ({
    id: identifier(row.id), company: string(row.company), title: string(row.title),
    start_date: date(row.start_date), end_date: date(row.end_date), current: row.current === true,
    description: string(row.description, 8000),
  }));
  result.education = rows(value.education, "Education", (row) => ({
    id: identifier(row.id), school: string(row.school), degree: string(row.degree),
    field: string(row.field ?? row.field_of_study), start_date: date(row.start_date), end_date: date(row.end_date),
  }));
  return result;
}
function hasProfile(profile) {
  return PROFILE_FIELDS.some((key) => Boolean(profile[key])) || profile.work_authorized !== null ||
    profile.requires_sponsorship !== null || profile.work_history.length > 0 || profile.education.length > 0;
}
function safeUrl(value) {
  let url;
  try { url = new URL(string(value, 5000)); } catch { throw new Error("Enter a complete http or https URL."); }
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) throw new Error("Only public http or https URLs are supported.");
  return url.href;
}

export function normalizeJobUrl(value) {
  const url = new URL(safeUrl(value));
  url.hash = "";
  const path = url.pathname.split("/").filter(Boolean);
  const host = url.hostname.toLowerCase();
  let provider = "website";
  let identity;
  if (["boards.greenhouse.io", "job-boards.greenhouse.io", "boards-eu.greenhouse.io", "job-boards.eu.greenhouse.io"].includes(host)) {
    provider = "greenhouse";
    const jobsIndex = path.indexOf("jobs");
    const job = url.searchParams.get("gh_jid") || (jobsIndex >= 0 ? path[jobsIndex + 1] : "");
    if (job && /^\d+$/.test(job)) {
      identity = `${provider}:${path[0] || ""}:${job}`;
      url.pathname = `/${path[0]}/jobs/${job}`;
      url.search = "";
    }
  } else if (["jobs.lever.co", "jobs.eu.lever.co"].includes(host) && path.length >= 2) {
    provider = "lever";
    identity = `${provider}:${path[0]}:${path[1]}`;
    url.pathname = `/${path[0]}/${path[1]}`;
    url.search = "";
  } else if (host === "jobs.ashbyhq.com" && path.length >= 2) {
    provider = "ashby";
    identity = `${provider}:${path[0]}:${path[1]}`;
    url.pathname = `/${path[0]}/${path[1]}`;
    url.search = "";
  }
  if (!identity) {
    for (const key of [...url.searchParams.keys()]) {
      if (/^(utm_|ref$|source$|src$|referrer$|gh_src$|lever-source$|fbclid$|gclid$)/i.test(key)) url.searchParams.delete(key);
    }
    url.searchParams.sort();
    url.pathname = url.pathname.replace(/\/+$/, "") || "/";
    identity = `${provider}:${url.href}`;
  }
  return { url: url.href, identity, provider };
}
function applicationData(value) {
  object(value, "Application");
  if (!APPLICATION_STATUSES.includes(value.status)) throw new Error("Unknown application status.");
  const normalized = normalizeJobUrl(value.url);
  return { id: identifier(value.id), company: string(value.company), title: string(value.title), ...normalized,
    status: value.status, createdAt: isoDate(value.createdAt, now()), updatedAt: isoDate(value.updatedAt, now()),
    submittedAt: isoDate(value.submittedAt), resume_id: string(value.resume_id, 100),
    resume_name: string(value.resume_name, 500), notes: string(value.notes, 10000), source: string(value.source, 100) || "extension" };
}
function resumeData(value, missingFile = false) {
  object(value, "Resume metadata");
  const name = string(value.name, 500);
  const extension = name.split(".").pop().toLowerCase();
  if (!MIME_TYPES[extension]) throw new Error("This release supports PDF résumés only.");
  if (!Number.isInteger(value.size) || value.size < 1 || value.size > MAX_RESUME_BYTES) throw new Error("Résumé file must be between 1 byte and 10 MB.");
  return { id: identifier(value.id), name, label: string(value.label, 500) || name,
    type: MIME_TYPES[extension], size: value.size, createdAt: isoDate(value.createdAt, now()),
    ...(missingFile || value.missingFile ? { missingFile: true } : {}) };
}
async function read(key, fallback) {
  const data = await chrome.storage.local.get(key);
  return data[key] ?? fallback;
}
async function write(key, value) { await chrome.storage.local.set({ [key]: value }); }

let dbPromise;
function database() {
  if (!dbPromise) dbPromise = new Promise((resolve, reject) => {
    const request = indexedDB.open("jobpilot-resumes", 1);
    request.onupgradeneeded = () => request.result.createObjectStore("files", { keyPath: "id" });
    request.onsuccess = () => {
      const db = request.result;
      db.onversionchange = () => { db.close(); dbPromise = undefined; };
      resolve(db);
    };
    request.onerror = () => { dbPromise = undefined; reject(new Error("Could not open the local résumé library.")); };
    request.onblocked = () => { dbPromise = undefined; reject(new Error("Close other JobPilot panels and retry the résumé operation.")); };
  });
  return dbPromise;
}
async function fileOperation(mode, operation, value) {
  const db = await database();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction("files", mode);
    const request = transaction.objectStore("files")[operation](value);
    transaction.oncomplete = () => resolve(request.result);
    transaction.onerror = transaction.onabort = () => reject(new Error("Could not save the local résumé file. Check available browser storage."));
  });
}
async function blobDataUrl(blob) {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let binary = "";
  for (let start = 0; start < bytes.length; start += 32768) binary += String.fromCharCode(...bytes.subarray(start, start + 32768));
  return `data:${blob.type || "application/octet-stream"};base64,${btoa(binary)}`;
}
function dataUrlBlob(value, metadata) {
  if (typeof value !== "string" || value.length > MAX_RESUME_BYTES * 1.4 + 200) throw new Error("Invalid résumé file data.");
  const match = /^data:([^;,]*);base64,([a-zA-Z0-9+/=]*)$/.exec(value);
  if (!match) throw new Error("Invalid résumé file data.");
  let binary;
  try { binary = atob(match[2]); } catch { throw new Error("Invalid résumé file encoding."); }
  if (binary.length !== metadata.size) throw new Error("Résumé file size does not match its metadata.");
  return new Blob([Uint8Array.from(binary, (char) => char.charCodeAt(0))], { type: metadata.type });
}

// A private unpacked package may carry an optional first-install seed. These
// files are not web-accessible and no employer page can request them. Only the
// broker calls this initializer, on its existing queue before UI operations.
export async function initializeLocalData() {
  const stored = await chrome.storage.local.get([INITIALIZATION_MARKER, ...Object.values(KEYS)]);
  if (stored[INITIALIZATION_MARKER]) return { seeded: false, reason: "already_initialized" };
  const existingProfile = profileData(stored[KEYS.profile] ?? emptyProfile());
  if (hasProfile(existingProfile) || stored[KEYS.applications]?.length || stored[KEYS.resumes]?.length) {
    await write(INITIALIZATION_MARKER, true);
    return { seeded: false, reason: "existing_data" };
  }
  let response;
  try { response = await fetch(chrome.runtime.getURL("initial-data.json")); }
  catch {
    await write(INITIALIZATION_MARKER, true);
    return { seeded: false, reason: "no_seed" };
  }
  if (!response.ok) {
    await write(INITIALIZATION_MARKER, true);
    return { seeded: false, reason: "no_seed" };
  }
  const seed = object(await response.json(), "Initial data");
  const profile = profileData(seed.profile ?? emptyProfile());
  const files = [];
  if (seed.resumes !== undefined && (!Array.isArray(seed.resumes) || seed.resumes.length > 20)) {
    throw new Error("Initial data contains an invalid résumé list.");
  }
  // Load and validate the complete seed before changing stored domain data.
  for (const entry of seed.resumes || []) {
    object(entry, "Initial résumé");
    const path = string(entry.path, 500);
    if (!/^[a-zA-Z0-9_./-]+\.pdf$/i.test(path) || path.startsWith("/") || path.split("/").includes("..")) {
      throw new Error("Initial résumé must reference a PDF inside the extension package.");
    }
    const fileResponse = await fetch(chrome.runtime.getURL(path));
    if (!fileResponse.ok) throw new Error("The packaged résumé could not be loaded.");
    const blob = await fileResponse.blob();
    const metadata = resumeData({ name: entry.name || path.split("/").pop(), label: entry.label,
      size: blob.size, id: uid(), createdAt: now() });
    files.push({ metadata, blob: new Blob([blob], { type: "application/pdf" }) });
  }
  profile.selected_resume_id = files[0]?.metadata.id || "";
  const writtenIds = [];
  try {
    for (const file of files) {
      await fileOperation("readwrite", "put", { id: file.metadata.id, blob: file.blob });
      writtenIds.push(file.metadata.id);
    }
    await chrome.storage.local.set({ [KEYS.profile]: profile, [KEYS.resumes]: files.map((file) => file.metadata),
      [INITIALIZATION_MARKER]: true });
  } catch (error) {
    for (const id of writtenIds) await fileOperation("readwrite", "delete", id).catch(() => {});
    throw error;
  }
  return { seeded: true, resumesImported: files.length };
}

const handlers = {
  async getProfile() { return profileData(await read(KEYS.profile, emptyProfile())); },
  async saveProfile(value) {
    const profile = profileData(value);
    await write(KEYS.profile, profile);
    return profile;
  },
  async listResumes() { return read(KEYS.resumes, []); },
  async addResume(value, dataUrl) {
    const metadata = resumeData({ ...value, id: uid(), createdAt: now() });
    const blob = dataUrlBlob(dataUrl, metadata);
    await fileOperation("readwrite", "put", { id: metadata.id, blob });
    try { await write(KEYS.resumes, [...await handlers.listResumes(), metadata]); }
    catch (error) { await fileOperation("readwrite", "delete", metadata.id); throw error; }
    return metadata;
  },
  async getResume(id) {
    const metadata = (await handlers.listResumes()).find((resume) => resume.id === id);
    if (!metadata) throw new Error("This résumé is no longer in the library.");
    const file = await fileOperation("readonly", "get", id);
    if (!file?.blob) throw new Error("This backup includes résumé metadata only. Add the file again before attaching it.");
    return { metadata, dataUrl: await blobDataUrl(file.blob) };
  },
  async deleteResume(id) {
    await write(KEYS.resumes, (await handlers.listResumes()).filter((resume) => resume.id !== id));
    const profile = await handlers.getProfile();
    if (profile.selected_resume_id === id) await handlers.saveProfile({ ...profile, selected_resume_id: "" });
    await fileOperation("readwrite", "delete", id);
    return true;
  },
  async listApplications() { return read(KEYS.applications, []); },
  async findApplication(url) {
    const { identity } = normalizeJobUrl(url);
    return (await handlers.listApplications()).find((application) => application.identity === identity) || null;
  },
  async saveApplication(job, status = "started") {
    object(job, "Job");
    const applications = await handlers.listApplications();
    const identity = normalizeJobUrl(job.url).identity;
    const index = applications.findIndex((application) => application.identity === identity);
    const existing = index >= 0 ? applications[index] : null;
    // Capturing an already-submitted job must not reset its recorded progress.
    const nextStatus = existing && status === "started" ? existing.status : status;
    const record = applicationData({ ...existing, ...job, id: existing?.id || uid(), status: nextStatus,
      createdAt: existing?.createdAt || now(), updatedAt: now(),
      submittedAt: existing?.submittedAt || (nextStatus === "submitted" ? now() : "") });
    if (index >= 0) applications[index] = record;
    else applications.unshift(record);
    await write(KEYS.applications, applications);
    return record;
  },
  async updateApplication(id, patch) {
    object(patch, "Application update");
    const applications = await handlers.listApplications();
    const index = applications.findIndex((application) => application.id === id);
    if (index < 0) throw new Error("This application is no longer in the tracker.");
    const existing = applications[index];
    const record = applicationData({ ...existing, ...patch, id: existing.id, createdAt: existing.createdAt,
      updatedAt: now(), submittedAt: existing.submittedAt || (patch.status === "submitted" ? now() : "") });
    if (applications.some((application, other) => other !== index && application.identity === record.identity)) throw new Error("That job is already recorded in your tracker.");
    applications[index] = record;
    await write(KEYS.applications, applications);
    return record;
  },
  async exportData() {
    return { schemaVersion: SCHEMA_VERSION, exportedAt: now(), profile: await handlers.getProfile(),
      applications: await handlers.listApplications(), resumes: await handlers.listResumes(),
      note: "Résumé file bytes are not included. Keep the original files for restoring or moving browsers." };
  },
  async importData(value, options = {}) {
    object(value, "Backup");
    object(options, "Import options");
    if (value.schemaVersion !== SCHEMA_VERSION) throw new Error("This is not a supported JobPilot backup version.");
    const profile = value.profile === undefined ? null : profileData(value.profile);
    if (!Array.isArray(value.applications) || value.applications.length > 10000 || !Array.isArray(value.resumes) || value.resumes.length > 1000) throw new Error("Backup must contain valid application and résumé lists.");
    // Validate every record before making any storage change.
    const importedApplications = value.applications.map(applicationData);
    const importedResumes = value.resumes.map((resume) => resumeData(resume, true));
    const currentProfile = await handlers.getProfile();
    const profileImported = Boolean(profile && (!hasProfile(currentProfile) || options.replaceProfile === true));
    const applications = await handlers.listApplications();
    for (const imported of importedApplications) {
      const index = applications.findIndex((record) => record.identity === imported.identity);
      if (index < 0) {
        if (applications.some((record) => record.id === imported.id)) imported.id = uid();
        applications.push(imported);
      } else if (Date.parse(imported.updatedAt) > Date.parse(applications[index].updatedAt)) {
        applications[index] = { ...imported, id: applications[index].id };
      }
    }
    applications.sort((a, b) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt));
    const resumes = await handlers.listResumes();
    let resumesImported = 0;
    for (const metadata of importedResumes) {
      if (!resumes.some((record) => record.id === metadata.id)) { resumes.push(metadata); resumesImported++; }
    }
    await chrome.storage.local.set({ [KEYS.applications]: applications, [KEYS.resumes]: resumes,
      ...(profileImported ? { [KEYS.profile]: profile } : {}) });
    return { profileImported, profilePreserved: Boolean(profile && !profileImported),
      applicationsMerged: importedApplications.length, resumesImported };
  },
};

export async function executeModelOperation(operation, args) {
  if (!Object.hasOwn(handlers, operation) || !Array.isArray(args)) throw new Error("Unknown JobPilot storage operation.");
  return handlers[operation](...args);
}
async function invoke(operation, ...args) {
  const response = await chrome.runtime.sendMessage({ type: "jobpilot:model", operation, args });
  if (!response?.ok) throw new Error(response?.error || "JobPilot storage did not respond. Reopen the panel and retry.");
  return response.result;
}
export const getProfile = () => invoke("getProfile");
export const saveProfile = (profile) => invoke("saveProfile", profile);
export const listResumes = () => invoke("listResumes");
export async function addResume(file, label = "") {
  if (!file || typeof file.arrayBuffer !== "function") throw new Error("Choose a résumé file first.");
  // Validate before reading bytes or sending a potentially oversized message.
  const metadata = resumeData({ name: file.name, label, size: file.size, type: file.type });
  return invoke("addResume", metadata, await blobDataUrl(file));
}
export const getResume = (id) => invoke("getResume", id);
export const deleteResume = (id) => invoke("deleteResume", id);
export const listApplications = () => invoke("listApplications");
export const saveApplication = (job, status = "started") => invoke("saveApplication", job, status);
export const updateApplication = (id, patch) => invoke("updateApplication", id, patch);
export const findApplication = (url) => invoke("findApplication", url);
export const exportData = () => invoke("exportData");
export const importData = (data, options = {}) => invoke("importData", data, options);
