// Local, side-effect-free inbox import. Imported text is data, never instructions.
// ZIP/OOXML is intentionally limited to ordinary, unencrypted .xlsx workbooks.
const MAX_FILE_BYTES = 5 * 1024 * 1024;
const MAX_UNCOMPRESSED_BYTES = 20 * 1024 * 1024;
const MAX_ZIP_ENTRIES = 500;
const MAX_ROWS = 2000;
const MAX_COLUMNS = 100;
const MAX_SCANNED_ROWS = MAX_ROWS + 100;
const encoder = new TextEncoder();
const decoder = new TextDecoder('utf-8', { fatal: true });
const ALIASES = {
  url: ['url', 'joburl', 'joblink', 'applicationurl', 'applyurl', 'officialroleurl', 'officialurl', 'link', 'postingurl'],
  company: ['company', 'companyname', 'employer', 'organization', 'organisation'],
  title: ['title', 'jobtitle', 'role', 'position', 'positiontitle', 'rolename'],
  status: ['status', 'submissionstatus', 'applicationstatus'],
  rawStatus: ['rawstatus'],
  notes: ['notes', 'note', 'comments', 'comment', 'description'],
  observedAt: ['observedat', 'updatedat', 'lastupdated', 'lastchecked', 'datechecked'],
};
const key = value => String(value).toLowerCase().replace(/[^a-z0-9]/g, '');
const present = value => value !== undefined && value !== null && String(value).trim() !== '';
function fail(context, message) { throw new Error(`${context}: ${message}`); }
function textValue(value, context, name, max = 1000) {
  if (value === undefined || value === null) return '';
  if (typeof value !== 'string') fail(context, `${name} must be text.`);
  const result = value.trim();
  if (result.length > max) fail(context, `${name} exceeds ${max} characters.`);
  return result;
}
function sourceName(filename) {
  const name = String(filename || 'import').split(/[\\/]/).pop().trim() || 'import';
  if (name.length > 200) throw new Error('Import filename exceeds 200 characters.');
  return name;
}
function sizeCheck(text) {
  if (typeof text !== 'string') throw new Error('Import content must be text.');
  if (text.length > MAX_FILE_BYTES || encoder.encode(text).length > MAX_FILE_BYTES) throw new Error('Import file exceeds 5 MiB.');
}
function field(row, name, context) {
  const entries = Object.entries(row).filter(([label, value]) => ALIASES[name].includes(key(label)) && present(value));
  if (entries.length > 1 && entries.some(([, value]) => value !== entries[0][1])) fail(context, `Conflicting ${name} columns.`);
  return entries[0]?.[1];
}
function statusValue(raw) {
  const value = raw.toLowerCase().replace(/[_-]/g, ' ').replace(/\s+/g, ' ').trim();
  // Check negation first: "paused; not submitted" must never count as applied.
  if (/\b(?:not|never|un)\s*(?:yet\s+)?(?:submitted|applied|sent)\b|\b(?:didn['’]?t|hasn['’]?t|haven['’]?t)\s+(?:apply|submit|send)\b/.test(value)) return 'started';
  if (!value || ['started', 'saved', 'bookmarked', 'draft', 'prepared', 'cleared', 'paused', 'ready', 'ready to apply', 'in progress', 'not started'].includes(value)) return 'started';
  if (['submitted', 'applied', 'application submitted', 'application sent', 'submitted successfully', 'confirmed submitted'].includes(value)) return 'submitted';
  if (['interview', 'interviewing', 'interview scheduled', 'phone screen', 'screening'].includes(value)) return 'interview';
  if (['rejected', 'not selected', 'unsuccessful', 'application rejected'].includes(value)) return 'rejected';
  if (['offer', 'offered', 'offer received', 'accepted', 'hired'].includes(value)) return 'offer';
  if (['withdrawn', 'withdrew', 'application withdrawn'].includes(value)) return 'withdrawn';
  return null;
}
function blankOrHeading(row) {
  const populated = Object.entries(row).filter(([, value]) => present(value));
  if (!populated.length) return true;
  return populated.every(([label]) => ['heading', 'section', 'category'].includes(key(label)));
}
function normalizeRecord(row, { sourceLabel, sourceRow, heading = false }) {
  const context = `${sourceLabel}, row ${sourceRow}`;
  if (!row || typeof row !== 'object' || Array.isArray(row)) fail(context, 'Expected a job object.');
  if (Object.keys(row).length > MAX_COLUMNS) fail(context, 'More than 100 columns.');
  if (blankOrHeading(row)) return null;
  const rawUrl = field(row, 'url', context);
  if (!present(rawUrl)) {
    if (heading) return null;
    fail(context, 'Missing job URL. Only blank rows or clearly marked headings may omit it.');
  }
  const inputUrl = textValue(rawUrl, context, 'URL', 5000);
  let url;
  try { url = new URL(inputUrl); } catch { fail(context, 'Job URL must be a complete http or https URL.'); }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) fail(context, 'Job URL must use http or https without credentials.');
  const status = textValue(field(row, 'status', context), context, 'Status');
  const rawStatus = textValue(field(row, 'rawStatus', context), context, 'Original status') || status;
  const normalized = statusValue(status || rawStatus);
  const notes = [textValue(field(row, 'notes', context), context, 'Notes', 10000)];
  for (const [label, value] of Object.entries(row)) {
    const name = key(label);
    const mapped = Object.values(ALIASES).some(aliases => aliases.includes(name));
    // Preserve source-specific columns (including instructions) as inert notes.
    if (!mapped && present(value)) {
      const metadata = typeof value === 'string' ? value : JSON.stringify(value);
      notes.push(`${label}: ${textValue(metadata, context, label, 3000)}`);
    }
  }
  if (normalized === null) notes.push(`Unrecognized imported status “${rawStatus}”; kept as started.`);
  const result = {
    url: url.href,
    company: textValue(field(row, 'company', context), context, 'Company'),
    title: textValue(field(row, 'title', context), context, 'Title'),
    status: normalized || 'started', notes: notes.filter(Boolean).join('\n'), sourceLabel, sourceRow, rawStatus,
  };
  if (!result.company || !result.title) fail(context, 'Company and title are required for every job row.');
  if (result.notes.length > 10000) fail(context, 'Combined notes exceed 10000 characters.');
  const observed = textValue(field(row, 'observedAt', context), context, 'Observation date', 100);
  if (observed) {
    if (Number.isNaN(Date.parse(observed))) fail(context, 'Invalid observation date. Use an ISO date or timestamp.');
    result.observedAt = new Date(observed).toISOString();
  }
  return result;
}
function jsonRecords(text, sourceLabel) {
  let value;
  try { value = JSON.parse(text.replace(/^\uFEFF/, '')); } catch (error) { throw new Error(`${sourceLabel}: Invalid JSON (${error.message}).`); }
  let rows = value;
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const containers = ['jobs', 'applications', 'cleared_jobs', 'clearedJobs', 'records'].filter(name => Object.hasOwn(value, name));
    if (containers.length !== 1) throw new Error(`${sourceLabel}: Expected a JSON array or one jobs/applications array.`);
    rows = value[containers[0]];
  }
  if (!Array.isArray(rows)) throw new Error(`${sourceLabel}: Expected a JSON array of jobs.`);
  if (rows.length > MAX_ROWS) throw new Error(`${sourceLabel}: Import contains more than 2000 rows.`);
  return rows.map((row, index) => normalizeRecord(row, { sourceLabel, sourceRow: index + 1 })).filter(Boolean);
}
function delimitedRows(text, delimiter, sourceLabel) {
  const rows = [];
  let cells = [], cell = '', quoted = false, closedQuote = false, line = 1, startLine = 1;
  const pushCell = () => {
    if (cells.length >= MAX_COLUMNS) fail(`${sourceLabel}, row ${startLine}`, 'More than 100 columns.');
    cells.push(cell); cell = ''; closedQuote = false;
  };
  const pushRow = () => {
    pushCell(); rows.push({ values: cells, sourceRow: startLine }); cells = [];
    if (rows.length > MAX_SCANNED_ROWS) throw new Error(`${sourceLabel}: Import contains too many rows (maximum 2000 jobs).`);
  };
  text = text.replace(/^\uFEFF/, '');
  for (let index = 0; index < text.length; index++) {
    const char = text[index];
    if (quoted) {
      if (char === '"') {
        if (text[index + 1] === '"') { cell += '"'; index++; } else { quoted = false; closedQuote = true; }
      } else if (char === '\r' || char === '\n') {
        cell += '\n'; if (char === '\r' && text[index + 1] === '\n') index++; line++;
      } else cell += char;
    } else if (char === delimiter) pushCell();
    else if (char === '\r' || char === '\n') {
      pushRow(); if (char === '\r' && text[index + 1] === '\n') index++; line++; startLine = line;
    } else if (char === '"') {
      if (cell !== '' || closedQuote) fail(`${sourceLabel}, row ${startLine}`, 'Unexpected quote in an unquoted field.');
      quoted = true;
    } else if (closedQuote) {
      if (!/[ \t]/.test(char)) fail(`${sourceLabel}, row ${startLine}`, 'Unexpected text after a closing quote.');
    } else cell += char;
    if (cell.length > 10000) fail(`${sourceLabel}, row ${startLine}`, 'A cell exceeds 10000 characters.');
  }
  if (quoted) fail(`${sourceLabel}, row ${startLine}`, 'Unclosed quoted field.');
  if (cell !== '' || cells.length || closedQuote) pushRow();
  return rows;
}
function headerRow(values) {
  const labels = values.map(key);
  return labels.some(label => ALIASES.url.includes(label)) && labels.some(label => [...ALIASES.company, ...ALIASES.title].includes(label));
}
function explicitHeading(values) {
  const populated = values.filter(present);
  return populated.length === 1 && /^(?:#{1,6}\s|(?:section|category|batch|week|month)\b|(?:cleared|paused|saved|submitted|rejected|priority)\s+(?:jobs|roles|applications)\b)/i.test(populated[0].trim());
}
function tableRecords(rows, sourceLabel, { skipNonJobSheet = false } = {}) {
  const hasHeader = rows.some(row => headerRow(row.values));
  if (!hasHeader && skipNonJobSheet) return [];
  if (!hasHeader) throw new Error(`${sourceLabel}: No header row found. Include URL and Company or Title columns.`);
  let headers;
  const result = [];
  for (const { values, sourceRow, mergedHeading = false } of rows) {
    if (!values.some(present)) continue;
    if (headerRow(values)) {
      headers = values.map((value, index) => value.trim() || `Column ${index + 1}`);
      const unique = headers.map(key);
      if (new Set(unique).size !== unique.length) fail(`${sourceLabel}, row ${sourceRow}`, 'Duplicate column names.');
      continue;
    }
    if (!headers) {
      if (values.filter(present).length === 1) continue;
      fail(`${sourceLabel}, row ${sourceRow}`, 'Unexpected data before the job header.');
    }
    if (values.length > headers.length && values.slice(headers.length).some(present)) fail(`${sourceLabel}, row ${sourceRow}`, 'More data columns than the header.');
    const row = Object.fromEntries(headers.map((label, index) => [label, values[index] || '']));
    const record = normalizeRecord(row, { sourceLabel, sourceRow, heading: mergedHeading || explicitHeading(values) });
    if (record) result.push(record);
    if (result.length > MAX_ROWS) throw new Error(`${sourceLabel}: Import contains more than 2000 jobs.`);
  }
  return result;
}

export function parseInboxText(text, { format = 'json', filename = 'import' } = {}) {
  sizeCheck(text);
  const sourceLabel = sourceName(filename);
  const type = format.toLowerCase().replace(/^\./, '');
  if (type === 'json') return jsonRecords(text, sourceLabel);
  if (type === 'csv' || type === 'tsv') return tableRecords(delimitedRows(text, type === 'tsv' ? '\t' : ',', sourceLabel), sourceLabel);
  throw new Error('Supported imports are JSON, CSV, TSV, and XLSX files.');
}

function safeZipPath(name) {
  if (!name || name.startsWith('/') || name.includes('\\') || /[\u0000-\u001f]/.test(name) || name.split('/').some(part => part === '.' || part === '..') || /^[a-z]:/i.test(name)) throw new Error('XLSX contains an unsafe ZIP entry path.');
  return name;
}
function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
  }
  return (crc ^ 0xffffffff) >>> 0;
}
async function unzip(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const u16 = offset => view.getUint16(offset, true);
  const u32 = offset => view.getUint32(offset, true);
  let end = -1;
  for (let offset = bytes.length - 22; offset >= Math.max(0, bytes.length - 65557); offset--) {
    if (u32(offset) === 0x06054b50 && offset + 22 + u16(offset + 20) === bytes.length) { end = offset; break; }
  }
  if (end < 0) throw new Error('XLSX is not a valid ZIP workbook.');
  const count = u16(end + 10), directorySize = u32(end + 12), directoryOffset = u32(end + 16);
  if (u16(end + 4) || u16(end + 6) || u16(end + 8) !== count || count === 0xffff || directorySize === 0xffffffff || directoryOffset === 0xffffffff) throw new Error('Multi-disk and ZIP64 XLSX files are not supported.');
  if (!count || count > MAX_ZIP_ENTRIES) throw new Error('XLSX must contain between 1 and 500 ZIP entries.');
  if (directoryOffset + directorySize !== end) throw new Error('XLSX ZIP directory is malformed.');
  const entries = [], names = new Set();
  let cursor = directoryOffset, declaredTotal = 0;
  for (let index = 0; index < count; index++) {
    if (cursor + 46 > end || u32(cursor) !== 0x02014b50) throw new Error('XLSX ZIP entry is malformed.');
    const flags = u16(cursor + 8), method = u16(cursor + 10), compressed = u32(cursor + 20), size = u32(cursor + 24);
    const nameLength = u16(cursor + 28), extraLength = u16(cursor + 30), commentLength = u16(cursor + 32), localOffset = u32(cursor + 42);
    const next = cursor + 46 + nameLength + extraLength + commentLength;
    if (next > end || size === 0xffffffff || compressed === 0xffffffff || localOffset === 0xffffffff || u16(cursor + 34)) throw new Error('XLSX ZIP entry bounds are invalid.');
    let name;
    try { name = safeZipPath(decoder.decode(bytes.subarray(cursor + 46, cursor + 46 + nameLength))); } catch (error) { throw new Error(`XLSX entry name is invalid: ${error.message}`); }
    if (names.has(name)) throw new Error('XLSX contains duplicate ZIP paths.');
    names.add(name);
    if (flags & 1 || ![0, 8].includes(method)) throw new Error('Encrypted or unsupported ZIP compression in XLSX.');
    declaredTotal += size;
    if (declaredTotal > MAX_UNCOMPRESSED_BYTES) throw new Error('XLSX expands beyond the 20 MiB limit.');
    if (localOffset + 30 > directoryOffset || u32(localOffset) !== 0x04034b50) throw new Error('XLSX local ZIP header is invalid.');
    const localNameLength = u16(localOffset + 26), localExtraLength = u16(localOffset + 28);
    const dataOffset = localOffset + 30 + localNameLength + localExtraLength;
    if (dataOffset + compressed > directoryOffset || u16(localOffset + 6) !== flags || u16(localOffset + 8) !== method || decoder.decode(bytes.subarray(localOffset + 30, localOffset + 30 + localNameLength)) !== name) throw new Error('XLSX ZIP headers disagree.');
    entries.push({ name, method, size, compressed, dataOffset, crc: u32(cursor + 16) }); cursor = next;
  }
  if (cursor !== end) throw new Error('XLSX ZIP directory size is invalid.');
  const files = new Map();
  let actualTotal = 0;
  for (const entry of entries) {
    const compressed = bytes.subarray(entry.dataOffset, entry.dataOffset + entry.compressed);
    let output;
    if (entry.method === 0) {
      output = compressed;
      actualTotal += output.length;
    } else {
      if (typeof DecompressionStream !== 'function') throw new Error('XLSX import requires a browser with local deflate decompression.');
      let stream;
      try { stream = new Blob([compressed]).stream().pipeThrough(new DecompressionStream('deflate-raw')); }
      catch { throw new Error('This browser does not support local XLSX decompression.'); }
      const reader = stream.getReader(), chunks = [];
      let length = 0;
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          length += value.length; actualTotal += value.length;
          if (length > entry.size || actualTotal > MAX_UNCOMPRESSED_BYTES) {
            await reader.cancel(); throw new Error('XLSX expands beyond its declared size or the 20 MiB limit.');
          }
          chunks.push(value);
        }
      } catch (error) { throw new Error(`Could not decompress XLSX entry ${entry.name}: ${error.message}`); }
      output = new Uint8Array(length);
      let offset = 0;
      for (const chunk of chunks) { output.set(chunk, offset); offset += chunk.length; }
    }
    if (actualTotal > MAX_UNCOMPRESSED_BYTES || output.length !== entry.size || crc32(output) !== entry.crc) throw new Error(`XLSX entry ${entry.name} has an invalid size or checksum.`);
    files.set(entry.name, output);
  }
  return files;
}
function xmlFile(files, path) {
  const bytes = files.get(path);
  if (!bytes) throw new Error(`XLSX is missing ${path}.`);
  let text;
  try { text = decoder.decode(bytes); } catch { throw new Error(`XLSX XML ${path} is not valid UTF-8.`); }
  if (/<!\s*(?:DOCTYPE|ENTITY)\b/i.test(text)) throw new Error(`XLSX XML ${path} contains a prohibited DTD or entity declaration.`);
  if (typeof DOMParser !== 'function') throw new Error('XLSX import requires browser XML parsing.');
  const xml = new DOMParser().parseFromString(text, 'application/xml');
  if (xml.getElementsByTagNameNS('*', 'parsererror').length || !xml.documentElement) throw new Error(`XLSX XML ${path} is malformed.`);
  return xml;
}
const descendants = (element, name) => Array.from(element.getElementsByTagNameNS('*', name));
const children = (element, name) => Array.from(element.children).filter(child => child.localName === name);
function relationships(files, path) {
  const xml = xmlFile(files, path);
  if (xml.documentElement.localName !== 'Relationships') throw new Error(`Invalid XLSX relationships: ${path}.`);
  const result = new Map();
  for (const rel of children(xml.documentElement, 'Relationship')) {
    const id = rel.getAttribute('Id');
    if (!id || result.has(id)) throw new Error(`Duplicate or missing XLSX relationship ID in ${path}.`);
    result.set(id, { type: rel.getAttribute('Type'), target: rel.getAttribute('Target'), external: rel.getAttribute('TargetMode')?.toLowerCase() === 'external' });
  }
  return result;
}
function relationPath(base, relation) {
  if (!relation || !relation.target || relation.external || relation.target.includes('\\') || /[\u0000-\u0020]/.test(relation.target)) throw new Error('External or invalid XLSX workbook relationship.');
  // Resolve package paths only; this URL is never fetched.
  let target;
  try { target = decodeURIComponent(relation.target); } catch { throw new Error('Invalid XLSX relationship path encoding.'); }
  if (/^(?:[a-z][a-z0-9+.-]*:|\/\/)/i.test(target) || target.includes('\\')) throw new Error('External XLSX relationships are not supported.');
  const parts = target.startsWith('/') ? [] : base.split('/').slice(0, -1);
  for (const part of target.split('/')) {
    if (part === '..') { if (!parts.length) throw new Error('XLSX relationship escapes the package.'); parts.pop(); }
    else if (part && part !== '.') parts.push(part);
  }
  const root = 'https://xlsx.invalid/';
  let resolved;
  try { resolved = new URL(relation.target, root + base); } catch { throw new Error('Invalid XLSX relationship target.'); }
  if (resolved.origin !== 'https://xlsx.invalid' || resolved.search || resolved.hash || resolved.username || resolved.password) throw new Error('External XLSX relationships are not supported.');
  const path = decodeURIComponent(resolved.pathname.slice(1));
  return safeZipPath(path);
}
function stringContent(element) {
  // Ignore phonetic annotations; read displayed text without evaluating formulas.
  return descendants(element, 't').filter(node => !node.closest('rPh')).map(node => node.textContent).join('');
}
function columnIndex(reference) {
  const match = /^([A-Z]{1,3})([1-9]\d*)$/.exec(reference || '');
  if (!match) throw new Error('Invalid XLSX cell reference.');
  let index = 0;
  for (const char of match[1]) index = index * 26 + char.charCodeAt(0) - 64;
  if (index > MAX_COLUMNS) throw new Error('XLSX contains more than 100 columns.');
  return { column: index - 1, row: Number(match[2]) };
}
function worksheetRows(xml, strings, sourceLabel, budget) {
  const rows = [], data = children(xml.documentElement, 'sheetData')[0];
  if (!data) return rows;
  const merged = new Set();
  for (const range of descendants(xml, 'mergeCell')) {
    const [first, last] = (range.getAttribute('ref') || '').split(':');
    if (first && last) {
      const start = columnIndex(first), end = columnIndex(last);
      if (start.row === end.row && end.column > start.column) merged.add(start.row);
    }
  }
  let previousRow = 0;
  for (const row of children(data, 'row')) {
    if (++budget.scanned > MAX_SCANNED_ROWS) throw new Error('XLSX contains too many rows (maximum 2000 jobs).');
    const sourceRow = Number(row.getAttribute('r') || previousRow + 1);
    if (!Number.isInteger(sourceRow) || sourceRow <= previousRow || sourceRow > 1048576) throw new Error(`${sourceLabel}: Invalid or duplicate worksheet row.`);
    previousRow = sourceRow;
    const values = [], seen = new Set();
    for (const cell of children(row, 'c')) {
      const reference = cell.getAttribute('r');
      const position = reference ? columnIndex(reference) : { column: values.length, row: sourceRow };
      if (position.row !== sourceRow || position.column >= MAX_COLUMNS || seen.has(position.column)) fail(`${sourceLabel}, row ${sourceRow}`, 'Invalid or duplicate cell position.');
      seen.add(position.column);
      let value = children(cell, 'v')[0]?.textContent || '';
      const type = cell.getAttribute('t');
      if (type === 's') {
        if (!/^\d+$/.test(value) || Number(value) >= strings.length) fail(`${sourceLabel}, row ${sourceRow}`, 'Invalid shared-string reference.');
        value = strings[Number(value)];
      } else if (type === 'inlineStr') value = stringContent(cell);
      else if (type === 'b') value = value === '1' ? 'TRUE' : 'FALSE';
      if (value.length > 10000) fail(`${sourceLabel}, row ${sourceRow}`, 'A cell exceeds 10000 characters.');
      values[position.column] = value;
    }
    // Fill sparse cells so header and row column positions stay aligned.
    rows.push({ values: Array.from({ length: values.length }, (_, index) => values[index] || ''), sourceRow, mergedHeading: merged.has(sourceRow) && values.filter(present).length === 1 });
  }
  return rows;
}
async function xlsxRecords(bytes, filename) {
  const files = await unzip(bytes);
  const root = [...relationships(files, '_rels/.rels').values()].filter(rel => /\/officeDocument$/.test(rel.type || ''));
  if (root.length !== 1) throw new Error('XLSX must contain one workbook relationship.');
  const workbookPath = relationPath('', root[0]);
  const workbook = xmlFile(files, workbookPath);
  const split = workbookPath.lastIndexOf('/');
  const relsPath = `${workbookPath.slice(0, split + 1)}_rels/${workbookPath.slice(split + 1)}.rels`;
  const rels = relationships(files, relsPath);
  // Never resolve linked/external workbooks or execute their formulas.
  if ([...rels.values()].some(rel => rel.external || /\/externalLink$/.test(rel.type || ''))) throw new Error('External workbook references are not supported in XLSX imports.');
  const shared = [...rels.values()].filter(rel => /\/sharedStrings$/.test(rel.type || ''));
  if (shared.length > 1) throw new Error('XLSX has multiple shared-string tables.');
  let strings = [];
  if (shared.length) {
    const table = xmlFile(files, relationPath(workbookPath, shared[0]));
    strings = children(table.documentElement, 'si').map(stringContent);
    if (strings.length > MAX_ROWS * MAX_COLUMNS || strings.some(value => value.length > 10000)) throw new Error('XLSX shared strings exceed the import limits.');
  }
  const results = [], budget = { scanned: 0 }, visited = new Set();
  for (const sheet of descendants(workbook, 'sheet')) {
    const id = sheet.getAttributeNS('http://schemas.openxmlformats.org/officeDocument/2006/relationships', 'id') || sheet.getAttributeNS('http://purl.oclc.org/ooxml/officeDocument/relationships', 'id') || sheet.getAttribute('r:id');
    const relation = rels.get(id);
    if (!relation) throw new Error('XLSX sheet is missing its workbook relationship.');
    if (!/\/worksheet$/.test(relation.type || '')) continue;
    const path = relationPath(workbookPath, relation);
    if (visited.has(path)) throw new Error('XLSX contains duplicate worksheet targets.');
    visited.add(path);
    const name = sheet.getAttribute('name') || 'Sheet';
    if (name.length > 100) throw new Error('XLSX worksheet name exceeds 100 characters.');
    const sourceLabel = `${filename} · ${name}`;
    const xml = xmlFile(files, path);
    if (xml.documentElement.localName !== 'worksheet') throw new Error(`Invalid XLSX worksheet ${name}.`);
    const rows = worksheetRows(xml, strings, sourceLabel, budget);
    results.push(...tableRecords(rows, sourceLabel, { skipNonJobSheet: true }));
    if (results.length > MAX_ROWS) throw new Error('XLSX contains more than 2000 jobs across its sheets.');
  }
  if (!results.length) throw new Error('XLSX has no job rows. Include URL and Company or Title headers on each job sheet.');
  return results;
}

export async function parseInboxFile(file) {
  if (!file || typeof file.arrayBuffer !== 'function' || !Number.isInteger(file.size) || file.size < 1) throw new Error('Choose a non-empty local import file.');
  if (file.size > MAX_FILE_BYTES) throw new Error('Import file exceeds 5 MiB.');
  const filename = sourceName(file.name);
  const format = filename.split('.').pop().toLowerCase();
  if (!['json', 'csv', 'tsv', 'xlsx'].includes(format)) throw new Error('Supported imports are JSON, CSV, TSV, and XLSX files.');
  const bytes = new Uint8Array(await file.arrayBuffer());
  if (bytes.length > MAX_FILE_BYTES || bytes.length !== file.size) throw new Error('Import file size changed or exceeds 5 MiB.');
  if (format === 'xlsx') return xlsxRecords(bytes, filename);
  let text;
  try { text = decoder.decode(bytes); } catch { throw new Error(`${filename}: Text files must use UTF-8 encoding.`); }
  return parseInboxText(text, { format, filename });
}
