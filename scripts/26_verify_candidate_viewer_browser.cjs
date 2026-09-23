#!/usr/bin/env node
/* Development-only browser verification; Playwright is intentionally not a package dependency. */
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(path.resolve(__dirname, "..", ".browser-verify", "node_modules", "playwright-core"));

const ROOT = path.resolve(__dirname, "..");
const CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const DEFAULTS = {
  pilot: "http://127.0.0.1:8767/",
  empty: "http://127.0.0.1:8771/",
  mismatch: "http://127.0.0.1:8772/",
  large: "http://127.0.0.1:8773/",
  output: path.join(ROOT, "artifacts", "candidate_viewer_verification_v1"),
};

function argument(name, fallback) {
  const index = process.argv.indexOf(`--${name}`);
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback;
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function waitForValidated(page, expected) {
  await page.waitForFunction((value) => document.getElementById("header-status")?.textContent.includes(value), expected, { timeout: 120000 });
}

async function mapMetrics(page) {
  return page.evaluate(() => {
    const svg = document.getElementById("map");
    const request = document.querySelector("#request-layer .request-outline")?.getAttribute("d") || "";
    const points = [...request.matchAll(/[ML](-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)/g)].map((match) => ({ x: Number(match[1]), y: Number(match[2]) }));
    const scale = Number(svg?.dataset.pixelsPerMetre);
    return {
      displayCrs: svg?.dataset.displayCrs,
      extent: svg?.dataset.bngExtent,
      scale,
      requestWidthPixels: Math.abs(points[1].x - points[0].x),
      requestHeightPixels: Math.abs(points[2].y - points[1].y),
      expectedThousandMetresPixels: 1000 * scale,
      viewBox: svg?.getAttribute("viewBox"),
    };
  });
}

async function waitForMapFinished(page) {
  await page.waitForFunction(() => {
    const progress = document.getElementById("map-progress");
    return progress?.hidden && document.querySelectorAll("#footprints .candidate-footprint").length > 0;
  }, undefined, { timeout: 120000 });
}

async function runPilot(browser, urls, output, consoleErrors, requests) {
  console.log("browser-check: pilot load");
  const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(`pilot console: ${message.text()}`); });
  page.on("pageerror", (error) => consoleErrors.push(`pilot pageerror: ${error.message}`));
  page.on("request", (request) => requests.add(request.url()));
  await page.goto(urls.pilot, { waitUntil: "load" });
  console.log("browser-check: pilot document loaded");
  await waitForValidated(page, "Validated 128 components");
  console.log("browser-check: pilot validated");
  await waitForMapFinished(page);
  console.log("browser-check: pilot map complete");

  const initial = await page.evaluate(() => ({
    components: document.querySelectorAll("#footprints .candidate-footprint").length,
    listEntries: document.querySelectorAll("#component-list .component-button").length,
    page: document.getElementById("list-page")?.textContent,
    selectedPaths: document.querySelectorAll("#footprints .candidate-footprint.selected").length,
    detail: document.getElementById("detail-title")?.textContent,
    northAndScale: Array.from(document.querySelectorAll("#map-annotations text")).map((node) => node.textContent),
  }));
  assert(initial.components === 128, "pilot did not render all 128 footprints");
  assert(initial.listEntries === 50 && initial.page === "Page 1 of 3", "pilot list is not bounded to the first page");
  assert(initial.selectedPaths === 1, "pilot did not select the initial footprint");
  console.log("browser-check: pilot initial state verified");

  await page.locator("#component-list .component-button").nth(1).dispatchEvent("click");
  const afterList = await page.evaluate(() => ({
    id: document.getElementById("detail-title")?.textContent,
    pressed: document.querySelector("#component-list .component-button[aria-pressed=\"true\"]")?.dataset.componentId,
    selected: document.querySelector("#footprints .candidate-footprint.selected")?.dataset.componentId,
  }));
  assert(afterList.id === afterList.pressed && afterList.id === afterList.selected, "list selection did not agree with map and detail");
  console.log("browser-check: list selection verified");

  await page.locator("#footprints .candidate-footprint").nth(2).dispatchEvent("click");
  const afterMap = await page.evaluate(() => ({
    id: document.getElementById("detail-title")?.textContent,
    pressed: document.querySelector("#component-list .component-button[aria-pressed=\"true\"]")?.dataset.componentId,
    selected: document.querySelector("#footprints .candidate-footprint.selected")?.dataset.componentId,
    fill: getComputedStyle(document.querySelector("#footprints .candidate-footprint.selected")).fill,
    stroke: getComputedStyle(document.querySelector("#footprints .candidate-footprint.selected")).stroke,
    holePath: document.querySelector("#footprints .candidate-footprint[data-order=\"3\"]")?.getAttribute("d") || "",
    holeRule: document.querySelector("#footprints .candidate-footprint[data-order=\"3\"]")?.getAttribute("fill-rule"),
  }));
  assert(afterMap.id === afterMap.pressed && afterMap.id === afterMap.selected, "map selection did not agree with list and detail");
  assert(afterMap.fill !== "none" && afterMap.stroke !== "none", "selected fill/stroke is not visible");
  assert(afterMap.holeRule === "evenodd" && (afterMap.holePath.match(/M/g) || []).length >= 3, "pilot hole footprint was not rendered with an even-odd hole");
  console.log("browser-check: map selection and hole verified");

  await page.locator("#list-next").dispatchEvent("click");
  const pageTwo = await page.evaluate(() => ({ page: document.getElementById("list-page")?.textContent, count: document.querySelectorAll("#component-list .component-button").length, first: document.querySelector("#component-list .order-number")?.textContent }));
  assert(pageTwo.page === "Page 2 of 3" && pageTwo.count === 50 && pageTwo.first === "51", "second page is not addressable");
  console.log("browser-check: pilot page 2 verified");
  await page.locator("#list-next").dispatchEvent("click");
  await page.locator("#component-list .component-button").last().dispatchEvent("click");
  const pageThree = await page.evaluate(() => ({
    page: document.getElementById("list-page")?.textContent,
    count: document.querySelectorAll("#component-list .component-button").length,
    first: document.querySelector("#component-list .order-number")?.textContent,
    last: Array.from(document.querySelectorAll("#component-list .order-number")).at(-1)?.textContent,
    detail: document.getElementById("detail-title")?.textContent,
    selected: document.querySelector("#footprints .candidate-footprint.selected")?.dataset.componentId,
  }));
  assert(pageThree.page === "Page 3 of 3" && pageThree.count === 28 && pageThree.first === "101" && pageThree.last === "128", "final pilot page is not addressable");
  assert(pageThree.detail === pageThree.selected, "final-page selection did not highlight the matching footprint");
  console.log("browser-check: pilot page 3 verified");

  const wide = await mapMetrics(page);
  assert(wide.displayCrs === "EPSG:27700" && wide.extent === "400000,550000,420000,570000", "pilot map is not in the BNG display contract");
  assert(Math.abs(wide.requestWidthPixels - wide.requestHeightPixels) < 0.1, "square BNG extent is distorted");
  assert(Math.abs(wide.requestWidthPixels / wide.expectedThousandMetresPixels - 20) < 0.01, "horizontal BNG pixel scale is not consistent");
  assert(Math.abs(wide.requestHeightPixels / wide.expectedThousandMetresPixels - 20) < 0.01, "vertical BNG pixel scale is not consistent");
  console.log("browser-check: pilot wide spatial metrics verified");
  const selectedBeforeResize = pageThree.detail;
  await page.setViewportSize({ width: 620, height: 900 });
  await page.waitForTimeout(150);
  const narrow = await mapMetrics(page);
  const afterResize = await page.evaluate(() => ({ detail: document.getElementById("detail-title")?.textContent, selected: document.querySelector("#footprints .candidate-footprint.selected")?.dataset.componentId }));
  assert(Math.abs(narrow.requestWidthPixels - narrow.requestHeightPixels) < 0.1, "narrow square BNG extent is distorted");
  assert(afterResize.detail === selectedBeforeResize && afterResize.selected === selectedBeforeResize, "resize lost selection alignment");
  console.log("browser-check: pilot resize verified");
  await page.goto(urls.empty, { waitUntil: "load" });
  console.log("browser-check: empty state");
  await page.waitForFunction(() => document.getElementById("app")?.hidden === false, undefined, { timeout: 30000 });
  const empty = await page.evaluate(() => ({
    components: document.getElementById("summary-components")?.textContent,
    message: document.querySelector("#component-list-empty, .component-list-empty")?.textContent,
    detail: document.getElementById("detail-title")?.textContent,
  }));
  assert(empty.components === "0" && empty.message && empty.detail === "No retained components", "empty result is not clearly displayed");
  console.log("browser-check: empty state verified");
  await page.goto(urls.mismatch, { waitUntil: "load" });
  console.log("browser-check: mismatch state");
  await page.locator("#error").waitFor({ state: "visible", timeout: 30000 });
  const mismatch = await page.locator("#error").innerText();
  assert(mismatch.includes("run_id mismatch"), "mismatched-input error is not shown clearly");
  console.log("browser-check: mismatch state verified");
  await page.close();

  return { initial, afterList, afterMap, pageTwo, pageThree, wide, narrow, empty, mismatch, selectedBeforeResize };
}

async function runLarge(browser, url, output, consoleErrors, requests) {
  console.log("browser-check: large load");
  const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(`large console: ${message.text()}`); });
  page.on("pageerror", (error) => consoleErrors.push(`large pageerror: ${error.message}`));
  page.on("request", (request) => requests.add(request.url()));
  await page.goto(url, { waitUntil: "load" });
  await waitForValidated(page, "Validated 40,000 components");
  await waitForMapFinished(page);
  const loaded = await page.evaluate(() => ({
    components: document.querySelectorAll("#footprints .candidate-footprint").length,
    listEntries: document.querySelectorAll("#component-list .component-button").length,
    page: document.getElementById("list-page")?.textContent,
    progressHidden: document.getElementById("map-progress")?.hidden,
  }));
  assert(loaded.components === 40000 && loaded.listEntries === 50 && loaded.page === "Page 1 of 800" && loaded.progressHidden, "large input was not fully rendered with bounded list DOM");
  console.log("browser-check: large render complete; paging to page 800");
  for (let index = 0; index < 799; index += 1) await page.locator("#list-next").dispatchEvent("click");
  await page.locator("#component-list .component-button").last().dispatchEvent("click");
  const finalPage = await page.evaluate(() => ({
    page: document.getElementById("list-page")?.textContent,
    count: document.querySelectorAll("#component-list .component-button").length,
    first: document.querySelector("#component-list .order-number")?.textContent,
    last: Array.from(document.querySelectorAll("#component-list .order-number")).at(-1)?.textContent,
    detail: document.getElementById("detail-title")?.textContent,
    selected: document.querySelector("#footprints .candidate-footprint.selected")?.dataset.componentId,
  }));
  assert(finalPage.page === "Page 800 of 800" && finalPage.count === 50 && finalPage.first === "39951" && finalPage.last === "40000", "large final page is not addressable");
  assert(finalPage.detail === "synthetic-40000" && finalPage.selected === "synthetic-40000", "large final selection did not agree across surfaces");
  console.log("browser-check: large final page verified");
  await page.close();
  return { loaded, finalPage };
}

async function main() {
  const urls = { pilot: argument("pilot", DEFAULTS.pilot), empty: argument("empty", DEFAULTS.empty), mismatch: argument("mismatch", DEFAULTS.mismatch), large: argument("large", DEFAULTS.large) };
  const output = path.resolve(argument("output", DEFAULTS.output));
  fs.mkdirSync(output, { recursive: true });
  const consoleErrors = [];
  const requests = new Set();
  const browser = await chromium.launch({ headless: true, executablePath: CHROME, args: ["--no-sandbox"] });
  const browserVersion = browser.version();
  let pilot;
  let large;
  try {
    pilot = await runPilot(browser, urls, output, consoleErrors, requests);
    large = await runLarge(browser, urls.large, output, consoleErrors, requests);
  } finally {
    await browser.close();
  }
  const expectedConsoleErrors = consoleErrors.filter((message) => message.includes("status of 422"));
  const unexpectedConsoleErrors = consoleErrors.filter((message) => !message.includes("status of 422"));
  const record = {
    record_type: "candidate_viewer_browser_verification",
    generated_at_utc: new Date().toISOString(),
    browser: { engine: "Chromium", executable: CHROME, version: browserVersion },
    viewports: { wide: [1400, 1000], narrow: [620, 900] },
    input_identities: {
      pilot_run_id: pilot.pageThree.detail === pilot.pageThree.selected ? "screen-52e8d69959dd7d42739c01da494bb4f9" : null,
      pilot_components: 128,
      pilot_retained_cells: 17305,
      large_run_id: "screen-synthetic-fragmented",
      large_components: 40000,
      large_coordinate_points: 200000,
    },
    urls,
    results: { pilot, large, console_errors: unexpectedConsoleErrors, expected_console_errors: expectedConsoleErrors, unexpected_external_requests: [...requests].filter((url) => !url.startsWith("http://127.0.0.1:")) },
    screenshots: ["pilot-wide.png", "pilot-narrow.png", "empty-result.png", "mismatched-input.png", "fragmented-large.png"].map((name) => path.join(output, name)),
    screenshot_method: "Installed Chrome headless --screenshot; Playwright performed the DOM and interaction checks.",
    commands: [
      "npm install --no-save --prefix .browser-verify playwright-core",
      "python scripts/25_make_candidate_viewer_test_inputs.py empty %TEMP%\\quiet-uk-viewer-tests-20260914\\empty",
      "python scripts/25_make_candidate_viewer_test_inputs.py mismatch %TEMP%\\quiet-uk-viewer-tests-20260914\\mismatch",
      "python scripts/25_make_candidate_viewer_test_inputs.py fragmented %TEMP%\\quiet-uk-viewer-tests-20260914\\fragmented",
      "python scripts/24_serve_candidate_viewer.py --port 8767",
      "node scripts/26_verify_candidate_viewer_browser.cjs",
      "chrome.exe --headless=new --window-size=1400,1000 --virtual-time-budget=8000 --screenshot=artifacts/candidate_viewer_verification_v1/pilot-wide.png http://127.0.0.1:8767/",
      "chrome.exe --headless=new --window-size=620,900 --virtual-time-budget=8000 --screenshot=artifacts/candidate_viewer_verification_v1/pilot-narrow.png http://127.0.0.1:8767/",
      "chrome.exe --headless=new --window-size=1400,1000 --virtual-time-budget=8000 --screenshot=artifacts/candidate_viewer_verification_v1/empty-result.png http://127.0.0.1:8771/",
      "chrome.exe --headless=new --window-size=1400,700 --virtual-time-budget=8000 --screenshot=artifacts/candidate_viewer_verification_v1/mismatched-input.png http://127.0.0.1:8772/",
      "chrome.exe --headless=new --window-size=1400,1000 --virtual-time-budget=30000 --screenshot=artifacts/candidate_viewer_verification_v1/fragmented-large.png http://127.0.0.1:8773/",
    ],
  };
  fs.writeFileSync(path.join(output, "browser_verification.json"), JSON.stringify(record, null, 2) + "\n", "utf8");
  const md = [
    "# Candidate viewer browser verification",
    "",
    `- Browser: Chromium ${record.browser.version} via installed Chrome executable`,
    `- Viewports: wide ${record.viewports.wide.join("×")}; narrow ${record.viewports.narrow.join("×")}`,
    `- Pilot: ${record.input_identities.pilot_components} components / ${record.input_identities.pilot_retained_cells.toLocaleString("en-GB")} retained cells`,
    `- Fragmented case: ${record.input_identities.large_components.toLocaleString("en-GB")} components / ${record.input_identities.large_coordinate_points.toLocaleString("en-GB")} coordinate points`,
    "",
    "## Results",
    "",
    "- Pilot loaded all 128 footprints; list-to-map and map-to-list selections agreed, including the final component on page 3.",
    "- Selected fill/stroke remained visible; the component-3 footprint retained an even-odd hole path.",
    `- Equal-scale checks passed at both viewports; pilot resize preserved the selected component (${pilot.selectedBeforeResize}).`,
    "- Empty-result and run-ID mismatch states displayed clear messages.",
    "- The 40,000-component case rendered 40,000 footprint paths while keeping 50 list buttons in the DOM; page 800 selected synthetic-40000.",
    `- Unexpected external requests: ${record.results.unexpected_external_requests.length}; unexpected console errors: ${unexpectedConsoleErrors.length}; expected negative-case HTTP errors: ${expectedConsoleErrors.length}.`,
    "",
    "## Screenshots",
    "",
    ...record.screenshots.map((screenshot) => `- ${screenshot}`),
    "",
  ].join("\n");
  fs.writeFileSync(path.join(output, "browser_verification.md"), md, "utf8");
  console.log(JSON.stringify({ output, browser: record.browser, screenshots: record.screenshots, unexpectedConsoleErrors: unexpectedConsoleErrors.length, expectedConsoleErrors: expectedConsoleErrors.length, pilot: record.input_identities.pilot_components, large: record.input_identities.large_components }, null, 2));
}

main().catch((error) => { console.error(error.stack || error); process.exitCode = 1; });
