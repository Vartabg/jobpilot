import assert from "node:assert/strict";
import { readFile, mkdir } from "node:fs/promises";
import { chromium, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const base = process.env.PRODUCT_TEST_URL || "http://127.0.0.1:3182";
const out = process.env.PRODUCT_ARTIFACTS || "/tmp/jobpilot-product-checks";
await mkdir(out, { recursive: true });
const browser = await chromium.launch({
  executablePath: process.env.CHROME_PATH || undefined,
});
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
  reducedMotion: "reduce",
});
const page = await context.newPage();
const errors = [],
  external = [];
page.on("pageerror", (e) => errors.push(e.message));
page.on("request", (r) => {
  if (!r.url().startsWith(base)) external.push(r.url());
});
async function a11y(label) {
  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .analyze();
  assert.deepEqual(
    result.violations.map((v) => ({
      id: v.id,
      nodes: v.nodes.map((n) => ({
        target: n.target,
        summary: n.failureSummary,
      })),
    })),
    [],
    label,
  );
  await expect(page.locator("main")).toHaveCount(1);
  await expect(page.locator("h1")).toHaveCount(1);
}
try {
  await page.goto(base, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: /Your profile/ }).click();
  await a11y("Profile");
  await page.getByRole("link", { name: "Skip to content" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#main")).toBeFocused();
  await page
    .getByLabel("Resume text", { exact: true })
    .fill(
      "Alex Example\nI built Python scripts for a volunteer group.\nI repaired equipment and helped people troubleshoot problems.",
    );
  await page.getByLabel("Skills to look for").fill("Python, troubleshooting");
  await page.getByLabel("Roles or keywords").fill("support");
  await page
    .getByLabel("Company hiring-board links")
    .fill("https://jobs.ashbyhq.com/example");
  for (const [label, text] of [
    ["Full name", "Alex Example"],
    ["First name", "Alex"],
    ["Last name", "Example"],
    ["Email", "alex@example.org"],
    ["Phone", "555-010-1200"],
    ["City", "Austin"],
    ["Country", "United States"],
    ["LinkedIn", "https://www.linkedin.com/in/example"],
  ])
    await page.getByLabel(label, { exact: true }).fill(text);
  await page.getByRole("button", { name: "Save profile", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("status")).toContainText("Profile saved");
  await page
    .getByRole("button", { name: "Find openings", exact: true })
    .click();
  await expect(page.getByRole("status")).toContainText("1 matching openings");
  await expect(page.locator("#source-status")).toContainText(
    "Board unavailable",
  );
  await page
    .getByRole("button", { name: /Example Co Support technician/ })
    .click();
  await expect(page.locator("#job-detail")).toContainText("five years");
  await a11y("Job details");
  await page.screenshot({ path: out + "/product-jobs.png", fullPage: true });
  await page.getByRole("button", { name: "Prepare application" }).click();
  const draft = page.getByRole("textbox", {
    name: "Application draft",
    exact: true,
  });
  await expect(draft).toHaveValue(/I built Python scripts/);
  await draft.fill("My edited application, using only my own experience.");
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.getByRole("button", { name: "Back to job" }).click();
  await expect(draft).toHaveValue(
    "My edited application, using only my own experience.",
  );
  const pending = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "Download draft", exact: true })
    .click();
  const downloaded = await pending;
  await downloaded.saveAs(out + "/application.txt");
  assert.match(
    await readFile(out + "/application.txt", "utf8"),
    /My edited application/,
  );
  await page.getByRole("button", { name: "Back to job" }).click();
  await page.getByLabel("Application status").selectOption("applied");
  await page.getByLabel("Your notes").fill("Submitted by me.");
  await page.getByLabel("Follow-up date").fill("2026-09-14");
  await page
    .getByRole("button", { name: "Save progress", exact: true })
    .click();
  await expect(page.getByRole("status")).toContainText("Progress saved");
  await page.reload({ waitUntil: "networkidle" });
  await page.getByRole("button", { name: /Applications 02/ }).click();
  await page
    .getByRole("button", { name: /Example Co Support technician/ })
    .click();
  await expect(page.getByLabel("Your notes")).toHaveValue("Submitted by me.");
  await expect(page.getByLabel("Application status")).toHaveValue("applied");
  await page.getByRole("button", { name: "Prepare application" }).click();
  await expect(draft).toHaveValue(
    "My edited application, using only my own experience.",
  );
  await page.getByRole("button", { name: /Application helper 04/ }).click();
  await expect(
    page.getByRole("button", { name: "Copy Email", exact: true }),
  ).toBeVisible();
  const bookmark = await page.locator("#fill-bookmark").getAttribute("href");
  assert.ok(bookmark.startsWith("javascript:"));
  assert.ok(!bookmark.includes("api-token"));
  await a11y("Application helper");
  await page.screenshot({ path: out + "/product-helper.png", fullPage: true });
  for (const width of [640, 320, 390, 844]) {
    await page.setViewportSize({ width, height: width === 844 ? 390 : 844 });
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth + 1,
      ),
      false,
      `Reflow ${width}`,
    );
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await a11y("Mobile helper");
  await page.screenshot({ path: out + "/product-mobile.png", fullPage: true });
  await page.emulateMedia({ forcedColors: "active" });
  await expect(
    page.getByRole("button", { name: "Copy Email", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Copy Email", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("status")).toContainText(/Copied|Clipboard/);
  await page.screenshot({
    path: out + "/product-forced-colors.png",
    fullPage: true,
  });
  assert.deepEqual(errors, [], "No runtime errors");
  assert.deepEqual(
    external,
    [],
    "No frontend profile transmission outside local app",
  );
  // Exercise the generated bookmark only on a synthetic application page.
  const fixture = await context.newPage();
  await fixture.setContent(
    await readFile(
      new URL("../tests/product/fill-fixture.html", import.meta.url),
      "utf8",
    ),
  );
  await fixture.evaluate(() => {
    document.querySelector("#ref_email").setAttribute("autocomplete", "email");
    const input = document.createElement("input");
    input.id = "extension";
    input.setAttribute("autocomplete", "tel-extension");
    document.body.append(input);
    const hidden = document.createElement("div");
    hidden.style.display = "none";
    hidden.innerHTML = '<input id="hidden-email" autocomplete="email">';
    document.body.append(hidden);
  });
  await fixture.evaluate(
    (code) => (0, eval)(code),
    decodeURIComponent(bookmark.slice("javascript:".length)),
  );
  await expect(fixture.locator("#email")).toHaveValue("alex@example.org");
  await expect(fixture.locator("#first_name")).toHaveValue("Alex");
  await expect(fixture.locator("#email_prefilled")).toHaveValue(
    "already@example.com",
  );
  for (const id of [
    "ref_email",
    "emergency_name",
    "prev_phone",
    "school_name",
    "citizenship_country",
    "linkedin_password",
    "years",
    "country_select",
    "cover",
    "extension",
    "hidden-email",
  ])
    await expect(fixture.locator("#" + id)).toHaveValue("");
  await expect(fixture.locator("#agree")).not.toBeChecked();
  await expect(fixture.locator("input[type=radio]:checked")).toHaveCount(0);
  assert.equal(await fixture.evaluate(() => window.__submitCount), 0);
  assert.ok((await fixture.evaluate(() => window.__inputEvents)) > 0);
  await fixture.evaluate(
    (code) => (0, eval)(code),
    decodeURIComponent(bookmark.slice("javascript:".length)),
  );
  await expect(fixture.locator("#jobpilot-fill-status")).toHaveCount(1);
  await fixture.screenshot({
    path: out + "/autofill-test.png",
    fullPage: true,
  });
  console.log(
    "PASS: product profile, search, source failure, draft/edit/export, persistence, tracking, accessibility/reflow, privacy, and safe basic autofill.",
  );
} finally {
  await browser.close();
}
