#!/usr/bin/env python3
"""Generate the extension's metadata-only privacy classifier and collector.

Run after changing product/privacy.py or product/static/privacy-capture.js.
The application classifier owns all detection patterns and explanation text;
this build step never maintains a second list of sensitive questions.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _literal(node: ast.AST) -> object:
    return ast.literal_eval(node)


def classifier_constants(source: str) -> dict:
    tree = ast.parse(source)
    constants = {}
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    regex_names = {"_DECLINE", "_PRECISE_ADDRESS", "_COARSE_LOCATION", "_COORDINATES",
                   "_OCCUPATIONAL_PEOPLE", "_PERSONAL_FAMILY", "_PERSONAL_HEALTH"}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        target = node.targets[0] if isinstance(node, ast.Assign) else node.target
        if not isinstance(target, ast.Name):
            continue
        name, value = target.id, node.value
        if name in {"_RULES", "_LIMITATION", "MAX_FIELDS", "MAX_OPTIONS"}:
            constants[name] = _literal(value)
        elif name in regex_names:
            if not isinstance(value, ast.Call) or not value.args:
                raise ValueError(f"Unsupported classifier pattern: {name}")
            constants[name] = _literal(value.args[0])
    # These are the remaining inline patterns in the Python classifier. Derive
    # them too, so exclusions cannot silently diverge between the two UIs.
    def regexes(function: str, operation: str) -> list[str]:
        calls = sorted((node for node in ast.walk(functions[function])
                        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name) and node.func.value.id == "re"
                        and node.func.attr == operation), key=lambda node: (node.lineno, node.col_offset))
        return [_literal(node.args[0]) for node in calls]
    constants["_INLINE_ANALYZE_SEARCH"] = regexes("analyze_fields", "search")
    constants["_INLINE_ANALYZE_SUB"] = regexes("analyze_fields", "sub")
    constants["_INLINE_OCCUPATIONAL_SEARCH"] = regexes("_occupational_reference", "search")
    constants["_LOCATION_FINDINGS"] = [
        _literal(node.args[0]) for node in sorted(ast.walk(functions["analyze_fields"]), key=lambda node: getattr(node, "lineno", 0))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name) and node.func.value.id == "matches"
        and node.func.attr == "append"
    ]
    if (len(constants["_INLINE_ANALYZE_SEARCH"]) != 2
            or len(constants["_INLINE_ANALYZE_SUB"]) != 1
            or len(constants["_INLINE_OCCUPATIONAL_SEARCH"]) != 1):
        raise ValueError("The Python classifier changed shape; update the generator and parity tests.")
    return constants


ANALYZER = r'''
const regex = pattern => new RegExp(pattern, 'i');
const rules = C._RULES.map(([category, pattern, level, explanation]) => [category, regex(pattern), level, explanation]);
const decline = regex(C._DECLINE), precise = regex(C._PRECISE_ADDRESS), coarse = regex(C._COARSE_LOCATION), coordinates = regex(C._COORDINATES);
const occupational = regex(C._OCCUPATIONAL_PEOPLE), personalFamily = regex(C._PERSONAL_FAMILY), personalHealth = regex(C._PERSONAL_HEALTH);
const [nonPhysicalAddress, nonLocation] = C._INLINE_ANALYZE_SEARCH.map(regex);
const stripUnitedStates = new RegExp(C._INLINE_ANALYZE_SUB[0], 'ig');
const otherPeopleHealth = regex(C._INLINE_OCCUPATIONAL_SEARCH[0]);
export class PrivacyScanError extends Error {}
function text(value, limit, name) {
  if (value === null || value === undefined) return '';
  if (typeof value !== 'string' || value.length > limit) throw new PrivacyScanError(`${name} must be text within the scan size limit.`);
  return value.trim();
}
export function publicPageUrl(value) {
  value = text(value, 4096, 'Source URL');
  if (!value) return '';
  try {
    const parsed = new URL(value);
    if (!['http:', 'https:'].includes(parsed.protocol) || !parsed.hostname || parsed.username || parsed.password) return '';
    return parsed.origin + parsed.pathname;
  } catch { return ''; }
}
function normalizeField(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new PrivacyScanError('Each field must contain question metadata.');
  const label = text(raw.label, 4000, 'Field label');
  if (!label) throw new PrivacyScanError('Each field needs a non-empty label.');
  const required = raw.required === undefined ? null : raw.required;
  if (required !== null && typeof required !== 'boolean') throw new PrivacyScanError('Field requiredness must be true, false, or unknown.');
  const options = raw.options === undefined ? [] : raw.options;
  if (!Array.isArray(options) || options.length > C.MAX_OPTIONS) throw new PrivacyScanError('Field options exceed the scan size limit or are not a list.');
  // Copy only metadata. Never read value, answer, selected, checked, files or defaults.
  return {label, required, options: options.map(option => text(option, 4000, 'Option label')),
    description: text(raw.description === undefined ? '' : raw.description, 12000, 'Field description')};
}
function optionality(field) {
  if (field.required === null) return 'unknown';
  if (field.required === false) return 'optional';
  return field.options.some(option => decline.test(option)) ? 'response_required_disclosure_optional' : 'required';
}
function occupationalReference(category, label) {
  if (!['marital_family', 'health_disability'].includes(category)) return false;
  const context = occupational.exec(label);
  if (!context) return false;
  if (category === 'marital_family') return !personalFamily.test(label);
  return otherPeopleHealth.test(label.slice(context.index)) && !personalHealth.test(label);
}
export function analyzeFields(fields, {source_url = '', coverage = 'visible_form', notices = null} = {}) {
  if (!['visible_form', 'public_ats_form'].includes(coverage)) throw new PrivacyScanError('Unsupported scan coverage.');
  if (!Array.isArray(fields) || fields.length > C.MAX_FIELDS) throw new PrivacyScanError(`Provide at most ${C.MAX_FIELDS} application fields.`);
  const normalized = fields.map(normalizeField), findings = [];
  for (const field of normalized) {
    const {label} = field, matches = [];
    if (precise.test(label) && !nonPhysicalAddress.test(label)) matches.push(C._LOCATION_FINDINGS[0]);
    else if (coordinates.test(label)) matches.push(C._LOCATION_FINDINGS[1]);
    else if (coarse.test(label.replace(stripUnitedStates, '')) && !nonLocation.test(label)) matches.push(C._LOCATION_FINDINGS[2]);
    for (const [category, pattern, level, explanation] of rules) {
      if (pattern.test(label) && !occupationalReference(category, label)) matches.push([category, level, explanation]);
    }
    for (const [category, concern_level, explanation] of matches) findings.push({label, category, required: field.required,
      options: [...field.options], optionality: optionality(field), concern_level, employer_stated_purpose: field.description, explanation});
  }
  if (notices !== null && (!Array.isArray(notices) || notices.length > 30)) throw new PrivacyScanError('Scan notices exceed the size limit or are not a list.');
  const reportNotices = (notices || []).map(notice => text(notice, 12000, 'Scan notice'));
  if (findings.some(finding => finding.optionality === 'unknown')) reportNotices.push('Requiredness is unknown for one or more flagged fields; verify it in the application form.');
  reportNotices.push(C._LIMITATION);
  return {source_url: publicPageUrl(source_url), coverage, complete: false, field_count: normalized.length,
    fields: normalized, findings, notices: [...new Set(reportNotices)], coverage_note: C._LIMITATION, title: ''};
}
'''


def generated_files(root: Path = ROOT) -> dict[Path, str]:
    classifier = (root / "product/privacy.py").read_text()
    capture = (root / "product/static/privacy-capture.js").read_text()
    fingerprint = hashlib.sha256((classifier + "\0" + capture).encode()).hexdigest()
    header = ("// Generated by scripts/build_extension_privacy.py; do not edit.\n"
              f"// Product privacy source SHA256: {fingerprint}\n")
    constants = classifier_constants(classifier)
    # Python's regex spelling for \Z is unsupported in JavaScript, but none of
    # the field-classification patterns currently use it. Fail on future changes.
    if any("\\Z" in str(value) for value in constants.values()):
        raise ValueError("Unsupported Python regex anchor in browser classifier")
    analyzer = header + "const C = " + json.dumps(constants, ensure_ascii=False, indent=2) + ";\n" + ANALYZER.lstrip()
    start = capture.index('  const clean = ')
    end = capture.index('  const dialog = document.createElement("dialog");')
    collector = capture[start:end]
    # Each accessible frame is captured once using Chrome's document ID. Do not
    # walk child documents here: doing so duplicates fields and loses provenance.
    frames_start = collector.index('    for (const frame of doc.querySelectorAll("iframe")) {')
    frames_end = collector.index('  };\n  inspect(document);', frames_start)
    collector = collector[:frames_start] + collector[frames_end:]
    collector = collector.replace('  if (documents.size === 10) notices.push("The capture stopped at ten embedded documents. Coverage is incomplete.");\n', '')
    collector += '  return payload;\n'
    content = (header + '(() => {\n"use strict";\n'
               + 'globalThis.__jobpilotPrivacyCapture = function captureApplicationFields() {\n'
               + collector + '};\n'
               + '// Injection returns only public document metadata, never form values.\n'
               + 'return {source_url: location.origin + location.pathname, document_type: document.contentType};\n'
               + '})();\n')
    return {root / "extension/privacy-analyzer.js": analyzer, root / "extension/privacy-content.js": content}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail when committed generated files are stale")
    args = parser.parse_args()
    stale = []
    for path, content in generated_files().items():
        if args.check:
            if not path.exists() or path.read_text() != content:
                stale.append(str(path.relative_to(ROOT)))
        else:
            path.write_text(content)
    if stale:
        parser.exit(1, "Regenerate extension privacy files: " + ", ".join(stale) + "\n")


if __name__ == "__main__":
    main()
