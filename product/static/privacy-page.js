import { $, api, download, node, tell } from "./util.js";

let reports = [];
let working = false;

const optionalityLabels = {
  optional: "Optional",
  response_required_disclosure_optional: "Response required; disclosure optional",
  required: "Required; no decline option identified",
  unknown: "Requirement unclear",
};

function textValue(value, fallback = "Not stated") {
  if (typeof value === "string" && value.trim()) return value;
  return fallback;
}

function safeLink(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

function declineText(finding) {
  if (finding.optionality === "optional") return "Yes — this question may be left unanswered.";
  if (finding.optionality === "response_required_disclosure_optional")
    return "Yes — a response is required, but a decline option is offered.";
  if (finding.optionality === "required") return "No separate way to withhold an answer was identified.";
  return "Unclear in the inspected fields.";
}

function requiredText(required) {
  if (required === true) return "Marked required";
  if (required === false) return "Marked optional";
  return "Not identified";
}

function pair(list, label, value) {
  list.append(node("dt", label), node("dd", value));
}

function findingCard(finding) {
  const card = node("article", undefined, "finding");
  const heading = node("div", undefined, "finding-heading");
  heading.append(node("h4", textValue(finding.label, "Unnamed question")));
  const badge = node("span", optionalityLabels[finding.optionality] || optionalityLabels.unknown,
    "finding-badge" + (finding.optionality === "required" ? " review" : ""));
  heading.append(badge);
  card.append(heading);
  card.append(node("p", textValue(finding.category, "Personal information request").replaceAll("_", " "), "finding-category"));
  const facts = node("dl");
  pair(facts, "Form requirement", requiredText(finding.required));
  pair(facts, "Can I withhold this answer?", declineText(finding));
  pair(facts, "Employer-provided help or purpose", textValue(finding.employer_stated_purpose,
    "No specific purpose stated in the inspected field."));
  card.append(facts);
  if (finding.explanation) card.append(node("p", String(finding.explanation), "finding-explanation"));
  const options = Array.isArray(finding.options) ? finding.options : [];
  if (options.length) {
    const details = node("details");
    details.append(node("summary", "Choices shown on the form"));
    const choices = node("ul");
    options.forEach((option) => choices.append(node("li", typeof option === "string" ? option : textValue(option?.label, "Unlabeled option"))));
    details.append(choices);
    card.append(details);
  }
  return card;
}

function reportCard(report, index) {
  const card = node("article", undefined, "panel privacy-report");
  const heading = node("div", undefined, "report-heading");
  const title = node("h3", textValue(report.title, `Application ${index + 1}`));
  title.id = `report-title-${index}`;
  card.setAttribute("aria-labelledby", title.id);
  heading.append(title);
  card.append(heading);
  const url = safeLink(report.source_url);
  if (url) {
    const link = node("a", url, "report-link");
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    card.append(link);
  }
  if (report.error) {
    const error = typeof report.error === "string" ? report.error : textValue(report.error.message, "The form could not be inspected.");
    card.append(node("p", `Could not inspect this application: ${error}`, "error"));
    card.append(node("p", "Coverage incomplete. Use the page scanner on the application form to inspect its visible questions.", "meta"));
    return card;
  }
  const findings = Array.isArray(report.findings) ? report.findings : [];
  const selected = $("finding-filter").value === "required"
    ? findings.filter((finding) => finding.optionality === "required")
    : findings;
  const count = Number.isFinite(report.field_count) ? `${report.field_count} fields inspected` : "Field count unavailable";
  const coverage = {
    visible_form: "visible form metadata",
    public_ats_form: "public application questions",
    greenhouse_public_questions: "public Greenhouse questions",
    public_questions: "public form questions",
  }[report.coverage] || "available form metadata";
  card.append(node("p", `${count} · ${findings.length} flagged request${findings.length === 1 ? "" : "s"} · Partial coverage: ${coverage}.`, "report-meta"));
  if (!selected.length) {
    card.append(node("p", findings.length
      ? "No findings match the required-responses filter. Review all flagged requests for optional questions and unclear requirements."
      : "No flagged requests found in the inspected fields. Additional steps or conditional questions may still ask for personal information.", "empty"));
  } else {
    const list = node("div", undefined, "report-findings");
    selected.forEach((finding) => list.append(findingCard(finding)));
    card.append(list);
  }
  const notices = Array.isArray(report.notices) ? report.notices : [];
  if (notices.length) {
    const section = node("section", undefined, "report-notices");
    section.append(node("h4", "Notices in the inspected material"));
    const list = node("ul", undefined, "notice-list");
    notices.forEach((notice) => {
      const text = typeof notice === "string" ? notice : notice?.text || notice?.description || notice?.label;
      if (text) list.append(node("li", String(text)));
    });
    if (list.childElementCount) {
      section.append(list);
      card.append(section);
    }
  }
  return card;
}

function renderResults(focus = false) {
  $("reports").replaceChildren(...reports.map(reportCard));
  $("results-section").hidden = !reports.length;
  const scanned = reports.filter((report) => !report.error).length;
  $("result-summary").textContent = `${scanned} of ${reports.length} application${reports.length === 1 ? "" : "s"} inspected. Every result has incomplete coverage.`;
  if (focus && reports.length) $("results-heading").focus();
}

async function run(message, action) {
  if (working) return;
  working = true;
  $("scan-button").disabled = true;
  $("analyze-button").disabled = true;
  $("error").hidden = true;
  $("results-section").setAttribute("aria-busy", "true");
  tell(message);
  try {
    await action();
    renderResults(true);
    tell("Scan finished. Review each application’s questions and coverage limits.");
  } catch (error) {
    $("error").textContent = error.message || "The scan could not finish. Try again.";
    $("error").hidden = false;
    tell("The scan could not finish.", true);
  } finally {
    working = false;
    $("scan-button").disabled = false;
    $("analyze-button").disabled = false;
    $("results-section").setAttribute("aria-busy", "false");
  }
}

$("scan-form").addEventListener("submit", (event) => {
  event.preventDefault();
  run("Inspecting public application questions…", async () => {
    const urls = $("job-urls").value.split(/\r?\n/).map((url) => url.trim()).filter(Boolean);
    if (!urls.length || urls.length > 10) throw new Error("Enter between 1 and 10 job links, one per line.");
    const result = await api("privacy/scan", { method: "POST", body: JSON.stringify({ urls }) });
    if (!Array.isArray(result.reports) || !result.reports.length) throw new Error("No application reports were returned. Try the page scanner.");
    reports = result.reports;
  });
});

function capturedMetadata(text) {
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    throw new Error("Paste valid JSON from the page scanner.");
  }
  if (!data || !Array.isArray(data.fields)) throw new Error("The captured metadata must contain a fields list.");
  if (data.fields.some((field) => field && typeof field === "object"
      && ("value" in field || "answer" in field || "checked" in field || "selected" in field)))
    throw new Error("The pasted data includes answer fields. Remove applicant answers or capture the questions again with the page scanner.");
  // Only send metadata, even if pasted JSON contains extra properties.
  return {
    source_url: typeof data.source_url === "string" ? data.source_url : "",
    coverage: "visible_form",
    fields: data.fields.map((field) => ({
      label: typeof field?.label === "string" ? field.label : "",
      required: typeof field?.required === "boolean" ? field.required : null,
      options: Array.isArray(field?.options) ? field.options.filter((option) => typeof option === "string") : [],
      description: typeof field?.description === "string" ? field.description : "",
    })),
    notices: Array.isArray(data.notices) ? data.notices.filter((notice) => typeof notice === "string") : [],
  };
}

$("capture-form").addEventListener("submit", (event) => {
  event.preventDefault();
  run("Analyzing captured question metadata…", async () => {
    const metadata = capturedMetadata($("captured-fields").value);
    const result = await api("privacy/analyze", { method: "POST", body: JSON.stringify(metadata) });
    const report = result.report || result;
    if (!report || !Array.isArray(report.findings)) throw new Error("No question analysis was returned.");
    reports = [...reports, report];
    $("captured-fields").value = "";
  });
});

$("finding-filter").addEventListener("change", () => renderResults());
$("download-report").addEventListener("click", () => {
  download(JSON.stringify({ reports, complete: false }, null, 2), "jobpilot-application-privacy.json", "application/json");
  tell("Downloaded the report with its coverage limits.");
});
$("scan-bookmark").addEventListener("click", (event) => {
  event.preventDefault();
  tell("Drag this link to your bookmarks bar, then use the bookmark on the application page.");
});

const queryUrl = new URLSearchParams(location.search).get("url");
if (queryUrl && safeLink(queryUrl)) $("job-urls").value = queryUrl;

api("privacy/bookmark").then((data) => {
  if (typeof data.url !== "string" || !data.url.startsWith("javascript:")) throw new Error("The page scanner is unavailable.");
  $("scan-bookmark").href = data.url;
  $("scan-bookmark").hidden = false;
  $("bookmark-status").hidden = true;
  if (typeof data.instruction === "string") $("bookmark-instruction").textContent = data.instruction;
}).catch(() => {
  $("bookmark-status").textContent = "The page scanner could not load. Reload this page to try again.";
});
