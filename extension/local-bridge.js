import {localJob, routeLocalRequest} from './local-actions.js';

export const LOCAL_HOST = 'com.jobpilot.local';
export const ATTEMPTS_KEY = 'jobpilot_local_attempts_v1';
const MAX_JSON_BYTES = 900 * 1024;
const jsonSize = value => new TextEncoder().encode(JSON.stringify(value)).length;
const object = value => value && typeof value === 'object' && !Array.isArray(value);

export function installLocalBridge({route = routeLocalRequest, now = Date.now} = {}) {
  let port = null, operations = Promise.resolve(), pending = 0;
  let lastError = '';
  const preparations = new Map();
  const status = () => ({connected: !!port, extensionId: chrome.runtime.id, host: LOCAL_HOST, pending, ...(lastError ? {error: lastError} : {})});

  async function attempted(identity) {
    const saved = await chrome.storage.local.get(ATTEMPTS_KEY);
    const attempts = saved?.[ATTEMPTS_KEY];
    if (attempts !== undefined && !object(attempts)) throw new Error('Local attempt records need reconciliation before submitting.');
    if (Object.hasOwn(attempts || {}, identity)) throw new Error('This job has a recorded submission attempt. Verify its employer receipt and reconcile the tracker before any further attempt.');
    return attempts || {};
  }

  async function dispatch(request) {
    if (request.op === 'status') return status();
    let job;
    if (['prepare-submit', 'submit'].includes(request.op)) {
      job = localJob(request.expectedUrl);
      if (!job) throw new Error('A supported expected job URL is required.');
      const attempts = await attempted(job.identity);
      if (request.op === 'submit') {
        const prepared = preparations.get(request.nonce);
        if (!prepared || request.confirm !== true || prepared.expiresAt <= now() || prepared.identity !== job.identity
          || prepared.tabId !== request.tabId || prepared.documentId !== request.documentId || prepared.snapshot !== request.snapshot || prepared.expectedUrl !== job.url) {
          throw new Error('Submission preparation is missing, expired or belongs to another document. Inspect and prepare again.');
        }
        preparations.delete(request.nonce);
        if (request.deadline <= now()) throw new Error('The request deadline expired before submission dispatch.');
        // Persist before dispatch. Even an unknown browser result must survive reload.
        await chrome.storage.local.set({[ATTEMPTS_KEY]: {...attempts, [job.identity]: {attemptedAt: new Date(now()).toISOString(), tabId: request.tabId, documentId: request.documentId}}});
      }
    }
    if (request.deadline <= now()) throw new Error('The request deadline expired before dispatch.');
    const result = await route(request);
    if (request.op === 'prepare-submit' && result?.prepared === true) {
      if (typeof result.nonce !== 'string' || !result.nonce || result.nonce.length > 256 || !Number.isFinite(result.expiresAt) || result.expiresAt <= now() || result.expiresAt > now() + 5 * 60_000) throw new Error('The document did not return valid submission preparation.');
      for (const [nonce, record] of preparations) if (record.expiresAt <= now() || record.identity === job.identity) preparations.delete(nonce);
      if (preparations.size >= 64) throw new Error('Too many pending preparations. Disconnect and inspect the intended job again.');
      preparations.set(result.nonce, {identity: job.identity, expectedUrl: job.url, tabId: request.tabId, documentId: request.documentId, snapshot: request.snapshot, expiresAt: result.expiresAt});
    }
    return result;
  }

  function post(connection, response) {
    if (port !== connection) return;
    try {
      if (jsonSize(response) > MAX_JSON_BYTES) response = {id: response.id, ok: false, error: 'The response exceeds 900 KiB. Narrow the application inventory before retrying.'};
      connection.postMessage(response);
    } catch { lastError = 'The native connection stopped. Action outcomes may be unknown; nothing is replayed.'; }
  }

  function connect() {
    if (port) return status();
    lastError = '';
    const connection = chrome.runtime.connectNative(LOCAL_HOST);
    port = connection;
    const seen = new Set();
    connection.onMessage.addListener(request => {
      let error = '';
      if (!object(request) || typeof request.id !== 'string' || !request.id || request.id.length > 128 || typeof request.op !== 'string') error = 'A request needs a unique string id and an operation.';
      else if (seen.has(request.id)) error = 'This request id was already received. Requests are never replayed.';
      else if (!Number.isFinite(request.deadline) || request.deadline <= now()) error = 'The request deadline expired before dispatch.';
      else if (seen.size >= 10_000) error = 'This connection reached its request limit. Disconnect and connect again.';
      else {
        try { if (jsonSize(request) > MAX_JSON_BYTES) error = 'The request exceeds the 900 KiB limit.'; }
        catch { error = 'The native request is not valid JSON.'; }
      }
      if (error) { post(connection, {id: typeof request?.id === 'string' ? request.id.slice(0, 128) : '', ok: false, error}); return; }
      seen.add(request.id);
      if (pending >= 64) { post(connection, {id: request.id, ok: false, error: 'The local request queue is full.'}); return; }
      pending++;
      operations = operations.catch(() => {}).then(async () => {
        if (port !== connection) return;
        let response, started = false;
        try {
          if (request.deadline <= now()) throw new Error('The queued request deadline expired before dispatch.');
          started = true;
          const result = await dispatch(request);
          if (request.deadline <= now()) throw new Error('The request deadline expired while the action was running. Its outcome is unknown; nothing was replayed.');
          response = {id: request.id, ok: true, result};
        } catch (error) {
          response = {id: request.id, ok: false, error: started && request.deadline <= now()
            ? 'The request deadline expired while the action was running. Its outcome is unknown; nothing was replayed.'
            : error.message || 'The local action failed. Its outcome may be unknown.'};
        }
        post(connection, response);
      }).finally(() => { pending--; });
    });
    connection.onDisconnect.addListener(() => {
      // Access lastError to acknowledge Chrome's native-host diagnostic without logging it.
      const failed = chrome.runtime.lastError;
      if (port !== connection) return;
      port = null; preparations.clear();
      lastError = failed ? 'The local host is unavailable. Check installation, then click Connect.' : 'The local host disconnected. Nothing was replayed.';
    });
    return status();
  }

  function disconnect() {
    const previous = port;
    port = null; preparations.clear(); lastError = '';
    try { previous?.disconnect(); } catch { /* Already disconnected. */ }
    return status();
  }

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message?.type !== 'jobpilot:local') return false;
    if (sender?.id !== chrome.runtime.id || sender.url !== chrome.runtime.getURL('sidepanel.html') || sender.tab) {
      sendResponse({ok: false, error: 'The local controller is available only from the JobPilot side panel.'});
      return false;
    }
    try {
      if (!['connect', 'disconnect', 'status'].includes(message.op)) throw new Error('Unknown local panel operation.');
      const result = message.op === 'connect' ? connect() : message.op === 'disconnect' ? disconnect() : status();
      sendResponse({ok: true, result});
    } catch { sendResponse({ok: false, error: 'The local host could not connect. Check its installation and try Connect again.'}); }
    return false;
  });
  return {status, disconnect};
}
