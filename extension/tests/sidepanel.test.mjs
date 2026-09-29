import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdir, readFile } from "node:fs/promises";
import { before, after, test } from "node:test";
import { chromium, expect } from "@playwright/test";

const root = new URL("../", import.meta.url);
let server, browser, base;
before(async () => {
  server = createServer(async (req, res) => {
    const name = new URL(req.url, "http://localhost").pathname.slice(1);
    if (!/^[a-z0-9.-]+$/.test(name)) { res.writeHead(404).end(); return; }
    try {
      const body = await readFile(new URL(name, root));
      res.setHeader("Content-Type", name.endsWith(".js") ? "text/javascript" : name.endsWith(".css") ? "text/css" : "text/html");
      res.end(body);
    } catch { res.writeHead(404).end(); }
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  base = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch();
});
after(async () => { await browser?.close(); await new Promise(resolve => server?.close(resolve)); });

async function workspace(run, { width = 400, seed = null } = {}) {
  const context = await browser.newContext({ viewport: { width, height: 900 }, reducedMotion: "reduce" });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/*", route => {
    const url = route.request().url();
    if (!url.startsWith(base)) return route.abort();
    if (seed && url.endsWith("/initial-data.json")) return route.fulfill({ contentType: "application/json", body: JSON.stringify(seed) });
    if (seed && url.endsWith("/initial-resume.pdf")) return route.fulfill({ contentType: "application/pdf", body: "%PDF-1.4\nfixture seeded resume\n%%EOF" });
    return route.continue();
  });
  await page.addInitScript(() => {
    const data = JSON.parse(localStorage.getItem("__testExtensionData") || "{}");
    globalThis.__storageData = data;
    globalThis.__extensionCalls = [];
    const event = { addListener() {}, removeListener() {} };
    let modelQueue = Promise.resolve();
    globalThis.chrome = {
      runtime: {
        getURL: path => new URL(path, location.href).href,
        onMessage: event,
        sendMessage: async message => {
          if (message.type !== "jobpilot:model") throw new Error("Unknown mocked worker message");
          const result = modelQueue.then(async () => {
            try {
              const model = await import("./model.js");
              try { await model.initializeLocalData(); } catch { /* Optional seed cannot block the panel. */ }
              return { ok: true, result: await model.executeModelOperation(message.operation, message.args) };
            } catch (error) { return { ok: false, error: error.message }; }
          });
          modelQueue = result.then(() => {}, () => {});
          return result;
        },
      },
      storage: {
        local: {
          get: async keys => {
            if (keys == null) return structuredClone(data);
            if (typeof keys === "string") return { [keys]: structuredClone(data[keys]) };
            return Object.fromEntries((Array.isArray(keys) ? keys : Object.keys(keys)).map(key => [key, structuredClone(data[key] ?? keys[key])]));
          },
          set: async values => {
            Object.assign(data, structuredClone(values));
            localStorage.setItem("__testExtensionData", JSON.stringify(data));
          },
          remove: async keys => {
            for (const key of [keys].flat()) delete data[key];
            localStorage.setItem("__testExtensionData", JSON.stringify(data));
          },
        },
        onChanged: event,
      },
      tabs: {
        query: async () => [{ id: 17, url: "https://boards.greenhouse.io/example/jobs/123", title: "Software Engineer at Example Co" }],
        onActivated: event, onUpdated: event,
      },
      scripting: {
        executeScript: async request => {
          globalThis.__extensionCalls.push({ files: request.files, args: request.args });
          if (request.files) return [{ frameId: 0 }];
          if (request.args?.[0]?.action) return [{ frameId: 0, result: {
            action: request.args[0].action, provider: "Greenhouse", pageUrl: "https://boards.greenhouse.io/example/jobs/123",
            filled: [], skipped: [], failed: [], required: [{ label: "Salary expectations", type: "text" }],
            resume: { status: "not-requested", message: "" },
          } }];
          return [{ frameId: 0, result: { company: "Example Co", title: "Software Engineer", url: "https://boards.greenhouse.io/example/jobs/123" } }];
        },
      },
    };
  });
  await page.goto(base + "/sidepanel.html");
  try { await run(page); assert.deepEqual(errors, [], "side panel must not throw browser errors"); }
  finally { await context.close(); }
}

test("profile edits persist and an unanswered work-authorization question remains unknown", async () => {
  await workspace(async page => {
    await page.locator('[data-tab="profile"]').click();
    await page.locator('[name="first_name"]').fill("Alex");
    await page.locator('[name="last_name"]').fill("Example");
    await page.locator('[name="email"]').fill("alex@example.org");
    await page.locator('[name="city"]').fill("Austin");
    await page.locator("#save-profile").click();
    await expect(page.locator("#notice")).toContainText(/saved/i);
    await page.reload();
    await page.locator('[data-tab="profile"]').click();
    await expect(page.locator('[name="email"]')).toHaveValue("alex@example.org");
    await expect(page.locator('[name="work_authorized"]')).toHaveValue("");
    await expect(page.locator('[name="requires_sponsorship"]')).toHaveValue("");
    assert.equal(await page.evaluate(() => __extensionCalls.length), 0, "opening and editing the panel cannot inject into the job page");
  });
});

test("first-use seeded PDF enters real local IndexedDB and edited details survive restart", async () => {
  await workspace(async page => {
    await page.locator('[data-tab="profile"]').click();
    await expect(page.locator('[name="email"]')).toHaveValue("seeded@example.org");
    await expect(page.locator("#resume-library")).toContainText("Seeded-Resume.pdf");
    const storedFile = await page.evaluate(async () => {
      const model = await import("./model.js");
      const profile = await model.getProfile();
      return model.getResume(profile.selected_resume_id);
    });
    assert.equal(storedFile.metadata.name, "Seeded-Resume.pdf");
    assert.ok(storedFile.dataUrl.startsWith("data:application/pdf;base64,"));
    assert.equal(Buffer.from(storedFile.dataUrl.split(",")[1], "base64").toString(), "%PDF-1.4\nfixture seeded resume\n%%EOF");
    await page.locator('[name="email"]').fill("edited@example.org");
    await page.locator("#save-profile").click();
    await expect(page.locator("#resume-upload")).toBeEnabled();
    await page.reload();
    await page.locator('[data-tab="profile"]').click();
    await expect(page.locator('[name="email"]')).toHaveValue("edited@example.org");
    await expect(page.locator("#resume-library")).toContainText("Seeded-Resume.pdf");
  }, { seed: { profile: { first_name: "Seeded", last_name: "Example", email: "seeded@example.org" },
    resumes: [{ path: "initial-resume.pdf", name: "Seeded-Resume.pdf", label: "Software" }] } });
});

test("saving started then submitted updates one record and stores the selected resume snapshot", async () => {
  await workspace(async page => {
    await page.locator('[data-tab="profile"]').click();
    await page.locator("#resume-label").fill("Software engineering");
    await page.locator("#resume-upload").setInputFiles({ name: "Alex-Resume.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4\nfixture\n%%EOF") });
    await expect(page.locator("#resume-library")).toContainText("Alex-Resume.pdf");
    await page.locator('[data-tab="apply"]').click();
    await page.locator("#job-title").fill("Software Engineer");
    await page.locator("#job-company").fill("Example Co");
    await page.locator("#job-url").fill("https://boards.greenhouse.io/example/jobs/123?utm_source=test");
    const selected = await page.locator("#apply-resume option").evaluateAll(options => options.find(option => option.value)?.value);
    assert.ok(selected);
    await page.locator("#apply-resume").selectOption(selected);
    await page.locator("#save-started").click();
    await page.locator("#save-submitted").click();
    await page.locator('[data-tab="applications"]').click();
    await expect(page.locator("#application-list")).toContainText("Example Co");
    await expect(page.locator("#application-count")).toHaveText("1");
    await expect(page.locator("#application-list")).toContainText(/submitted/i);
    assert.equal(await page.evaluate(() => __extensionCalls.length), 0, "tracker controls do not submit or modify employer pages");
  });
});

test("local export contains records without embedding PDF bytes", async () => {
  await workspace(async page => {
    await page.locator('[data-tab="profile"]').click();
    await page.locator('[name="first_name"]').fill("Alex");
    await page.locator("#save-profile").click();
    await expect(page.locator("#resume-upload")).toBeEnabled();
    await page.locator("#resume-upload").setInputFiles({ name: "Alex-Resume.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4\nfixture\n%%EOF") });
    await expect(page.locator("#resume-library")).toContainText("Alex-Resume.pdf");
    const downloadPromise = page.waitForEvent("download");
    await page.locator("#export-data").click();
    const download = await downloadPromise;
    const contents = await readFile(await download.path(), "utf8");
    assert.ok(contents.includes("Alex"));
    assert.ok(contents.includes("Alex-Resume.pdf"));
    assert.ok(!contents.includes("data:application/pdf"));
    assert.ok(!contents.includes(Buffer.from("%PDF-1.4\nfixture\n%%EOF").toString("base64")));
  });
});

test("autofill results show zero fills and the required answers that still need the applicant", async () => {
  await workspace(async page => {
    await page.locator("#capture-job").click();
    await expect(page.locator("#job-company")).toHaveValue("Example Co");
    await expect(page.locator("#fill-details")).toBeEnabled();
    await page.locator("#fill-details").click();
    await expect(page.locator("#report-summary")).toContainText(/0.*filled|filled.*0/i);
    await expect(page.locator("#report-details")).toContainText("Salary expectations");
    const calls = await page.evaluate(() => __extensionCalls);
    assert.equal(calls.filter(call => call.args?.[0]?.action === "details").length, 1);
    assert.equal(calls.find(call => call.args?.[0]?.action === "details").args[0].resume, null);
  });
});

test("panel fits narrow widths and keeps keyboard navigation available", async () => {
  await workspace(async page => {
    const overflowing = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
    assert.equal(overflowing, false);
    await page.locator('[data-tab="apply"]').focus();
    await page.keyboard.press("ArrowRight");
    await expect(page.locator('[data-tab="profile"]')).toBeFocused();
    await expect(page.locator('[data-tab="profile"]')).toHaveAttribute("aria-selected", "true");
    const path = process.env.JOBPILOT_TEST_ARTIFACTS || "/tmp/jobpilot-extension-checks";
    await mkdir(path, { recursive: true });
    await page.screenshot({ path: path + "/jobpilot-profile-narrow.png", fullPage: true });
  }, { width: 320 });
});
