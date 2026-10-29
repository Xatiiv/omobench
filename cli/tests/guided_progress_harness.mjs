// Loads the real cli/src/omobench/web/guided.js in a VM context with a minimal
// DOM stand-in, drives its progress rendering through the four states that
// matter for "100% means finished", and prints the resulting bar state as JSON.
//
// Run by cli/tests/test_guided_progress.py, which asserts on that JSON.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { createContext, runInContext } from "node:vm";

const guidedPath = process.argv[2];
const source = readFileSync(guidedPath, "utf8");

function makeElement(id) {
  const classes = new Set();
  const attributes = id === "chart-svg" ? { hidden: "" } : {};
  const listeners = {};
  return {
    id,
    textContent: "",
    hidden: false,
    disabled: false,
    checked: false,
    value: "",
    children: [],
    dataset: {},
    style: {},
    scrollTop: 0,
    scrollHeight: 0,
    clientHeight: 0,
    classList: {
      add: (name) => classes.add(name),
      remove: (name) => classes.delete(name),
      toggle: (name, force) => (force === undefined ? (classes.has(name) ? classes.delete(name) : classes.add(name)) : force ? classes.add(name) : classes.delete(name)),
      contains: (name) => classes.has(name),
    },
    setAttribute: (name, v) => { attributes[name] = String(v); },
    getAttribute: (name) => (name in attributes ? attributes[name] : null),
    removeAttribute: (name) => { delete attributes[name]; },
    addEventListener: (name, handler) => { listeners[name] = handler; },
    trigger: (name) => listeners[name]?.(),
    append(...items) { this.children.push(...items); },
    replaceChildren(...items) { this.children = items; },
    scrollIntoView: () => {},
    focus: () => {},
    querySelectorAll: () => [],
    attributes,
  };
}

const elements = new Map();
const stepTabs = ["choose", "run", "results", "share"].map((screen) => {
  const tab = makeElement(`step-${screen}`);
  tab.dataset.screen = screen;
  return tab;
});
const document = {
  getElementById: (id) => {
    if (!elements.has(id)) elements.set(id, makeElement(id));
    return elements.get(id);
  },
  createElement: (tag) => makeElement(`created:${tag}`),
  querySelectorAll: (selector) => selector === ".step-tab" ? stepTabs : [],
  addEventListener: () => {},
};

const context = createContext({
  document,
  console,
  URLSearchParams,
  Date,
  Math,
  JSON,
  Number,
  Set,
  Map,
  Object,
  Array,
  String,
  Boolean,
  Promise,
  Blob,
  Error,
  isNaN,
  parseInt,
  parseFloat,
  setTimeout: () => 0,
  clearTimeout: () => {},
  location: { hash: "", pathname: "/guided", href: "http://127.0.0.1:8765/guided" },
  history: { replaceState: () => {} },
  sessionStorage: { getItem: () => "test-token", setItem: () => {}, removeItem: () => {} },
  // init() runs on load; a rejecting fetch just routes it to notice().
  fetch: () => Promise.reject(new Error("no network in this harness")),
});

runInContext(readFileSync(join(dirname(guidedPath), "community.js"), "utf8"), context);
document.getElementById("submission-mode").value = "community";
runInContext(source, context, { filename: guidedPath });

function barState() {
  const fill = document.getElementById("progress-bar-fill");
  const label = document.getElementById("progress-bar-label");
  const wrap = document.getElementById("progress-bar-wrap");
  return {
    width: fill.style.width ?? "",
    indeterminate: fill.classList.contains("indeterminate"),
    label: label.textContent,
    ariaValueNow: wrap.getAttribute("aria-valuenow"),
  };
}

function renderProgressWith(telemetry) {
  // A managed run whose benchmark job exists, so currentStageKey() consults
  // the telemetry stage rather than reporting startup.
  context.renderProgress(
    null,
    { status: "running_benchmark", benchmark_job_id: "job-aaaaaaaaaaaaaaaa", created_at: new Date().toISOString() },
    null,
    telemetry,
  );
}

const results = {};

// 1. Evidence finalization in progress: execution is done, but compression,
//    environment.json, and reproduce.md are still being written.
renderProgressWith({ available: true, stage: "saving", elapsed_seconds: 120, remaining_fraction: 0 });
results.saving = barState();

// 2. Active run very close to done: 99.6% must not round up to a finished 100%.
renderProgressWith({ available: true, stage: "benchmarking", elapsed_seconds: 100, remaining_fraction: 0.004 });
results.activeNearComplete = barState();

// 3. Past the time budget while a request is still streaming.
renderProgressWith({
  available: true, stage: "benchmarking", elapsed_seconds: 5401,
  remaining_fraction: 0, duration_budget_exhausted: true,
});
results.overrunning = barState();

// 4. Failure during finalization: freeze at what was really reached (99),
//    never at a finished-looking 100.
renderProgressWith({ available: true, stage: "saving", elapsed_seconds: 120, remaining_fraction: 0 });
context.renderProgressBarFrozen();
results.failedDuringFinalization = barState();

// 5. Genuine success: execution and evidence finalization both completed.
context.renderProgressBar(100, { complete: true });
results.completed = barState();

// 6. The primitive itself must never reach 100 without the explicit flag.
context.renderProgressBar(99.999);
results.primitiveNearComplete = barState();

// Observed telemetry display: zero is a real observation, missing is not.
function chartState() {
  const get = (id) => document.getElementById(id);
  return {
    throughput: get("live-throughput").textContent,
    ttft: get("live-ttft").textContent,
    requestTime: get("live-request-latency").textContent,
    successful: get("live-successful").textContent,
    hidden: get("chart-svg").getAttribute("hidden") !== null,
    inspectorHidden: get("chart-inspector").hidden,
    points: get("chart-line").getAttribute("points"),
    area: get("chart-area").getAttribute("points"),
    readout: get("chart-readout").textContent,
    accessibleReadout: get("chart-scrub").getAttribute("aria-valuetext"),
  };
}
context.renderChart(null);
results.missingMetrics = chartState();
const observed = {
  available: true,
  latest_metrics: { output_tokens_per_second: 7.25, time_to_first_token_ms: 0, request_latency_seconds: 12.5 },
  requests: { successful: 0, errored: 0, cancelled: 0 },
  chart_history: [
    { elapsed_seconds: 20, output_tokens_per_second: 8 },
    { elapsed_seconds: 10, output_tokens_per_second: 5 },
    { elapsed_seconds: 30, output_tokens_per_second: NaN },
    { elapsed_seconds: -5, output_tokens_per_second: 4 },
    { elapsed_seconds: 40, output_tokens_per_second: -2 },
    null,
  ],
};
const historyBefore = JSON.stringify(observed.chart_history);
context.renderChart(observed);
results.observedMetrics = chartState();
results.historyPreserved = JSON.stringify(observed.chart_history) === historyBefore;
document.getElementById("chart-scrub").value = "0";
document.getElementById("chart-scrub").trigger("input");
results.inspectedSample = chartState();
context.renderChart({ ...observed, chart_history: [...observed.chart_history, { elapsed_seconds: 25, output_tokens_per_second: 9 }] });
results.retainedSelection = chartState();
context.renderChart({ available: true, latest_metrics: { output_tokens_per_second: 0 }, chart_history: [{ elapsed_seconds: 0, output_tokens_per_second: 0 }] });
results.zeroMetrics = chartState();
context.renderChart({ ...observed, available: false });
results.unavailableMetrics = chartState();

// Export exactly the reviewed normalized result, without a network request or
// any mutation of the evidence. A raw local summary cannot use this action.
const downloads = [];
context.captureComparisonDownload = (blob, name) => downloads.push({ blob, name });
runInContext("download = captureComparisonDownload; preview = null;", context);
try { context.downloadComparisonResult(); } catch (error) { results.comparisonMissingPreview = error.message; }
const reviewedFixture = { schema_version: "1.0", run_id: "run-fixture-1234", results: { output_tokens_per_second: 12 }, fixture: "not benchmark evidence" };
context.reviewedFixture = reviewedFixture;
runInContext("preview = { result: reviewedFixture };", context);
const fixtureBefore = JSON.stringify(reviewedFixture);
context.downloadComparisonResult();
results.comparisonDownload = {
  count: downloads.length,
  name: downloads[0].name,
  type: downloads[0].blob.type,
  content: await downloads[0].blob.text(),
  unchanged: fixtureBefore === JSON.stringify(reviewedFixture),
};
// Reopening history drives the shipped navigation and sharing code. No POST
// (package, upload or execution) may be caused by opening a saved result.
await Promise.resolve();
const savedSummary = { results: {
  latency_ms: { ttft: { p50: 10, p95: 20 }, inter_token: { p50: 30 } },
  output_tokens_per_second: 2, total_tokens_per_second: 4, requests_per_second: 0.1,
  requests: { succeeded: 29, attempted: 34 }, error_rate_percent: 14.71,
} };
const saved = {
  id: "saved-job-aaaaaaaaaaaaaaaa", benchmark_job_id: "job-aaaaaaaaaaaaaaaa",
  kind: "execute", saved_local: true, recipe: null, storage_dir: "", status: "completed",
  created_at: "2026-10-26T10:00:00Z", finished_at: "2026-10-26T11:00:00Z",
  model: "Fixture/Model", profile: "chat-small-v1", execution_device: "gpu", result: savedSummary,
};
let savedDetail = {
  ...saved, id: saved.benchmark_job_id, summary: savedSummary, framework: "vllm", framework_version: "fixture",
  validation: { valid: false, errors: ["acceptance: not enough successful requests"] },
};
const historyRequests = [];
context.fetch = async (path, options) => {
  historyRequests.push({ path, method: options?.method || "GET" });
  let data;
  if (path === "/api/managed/runs") data = [saved];
  else if (path === "/api/managed/setup-jobs") data = [];
  else if (path === `/api/managed/runs/${saved.id}`) data = saved;
  else if (path.endsWith("/telemetry")) data = { available: false };
  else if (path === `/api/runs/${saved.benchmark_job_id}`) data = savedDetail;
  else throw new Error(`unexpected request: ${path}`);
  return { ok: true, json: async () => data };
};
await context.loadRecentRuns();
const get = (id) => document.getElementById(id);
results.historyRow = get("recent-list").children[0].textContent;
await get("recent-list").children[0].trigger("click");
results.historyOpened = {
  resultVisible: !get("screen-results").hidden,
  status: get("results-status").textContent,
  jobId: runInContext("benchmarkJobId", context),
};
await context.enterShareScreen();
results.historyRejected = {
  message: get("validation-result").textContent,
  reviewHidden: get("prepare-review-button").hidden,
  facts: get("package-facts").children.map((el) => el.textContent),
  packageDisabled: get("package-button").disabled,
  advancedNoteVisible: !get("advanced-package-note").hidden,
};
// A different, not-yet-validated run must not inherit a visible review button.
get("prepare-review-button").hidden = false;
savedDetail = { ...savedDetail, validation: null };
await context.enterShareScreen();
results.historyUncheckedReviewHidden = get("prepare-review-button").hidden;
savedDetail = { ...savedDetail, execution_device: "cpu", framework: "llama.cpp" };
await context.enterShareScreen();
results.historyCpuPackageEnabled = !get("package-button").disabled;
// Reopening a stopped run clears the previous completed selection and preview.
saved.status = "interrupted";
saved.finished_at = null;
saved.error = "The panel stopped before this managed run finished.";
await context.openTerminalRun(saved.id);
results.historyInterrupted = {
  heading: get("progress-heading").textContent,
  endTime: get("elapsed").textContent,
  finishedJob: runInContext("finishedManagedJob", context),
  preview: runInContext("preview", context),
  bar: barState(),
};
saved.kind = "smoke"; saved.status = "completed"; saved.benchmark_job_id = null;
saved.result = { message: "No inference was performed" };
await context.openTerminalRun(saved.id);
results.historySmoke = { heading: get("progress-heading").textContent, resultVisible: !get("screen-results").hidden };
results.historyReadOnly = historyRequests.every((request) => request.method === "GET");

// Real click handlers: history -> tab 2 -> recipe card. The next-run draft
// must remain separate from historical metadata, including absent old paths.
saved.kind = "execute"; saved.status = "completed";
saved.benchmark_job_id = "job-aaaaaaaaaaaaaaaa"; saved.result = savedSummary;
const recipeFixture = {
  id: "fixture-new-test", title: "New test", model_id: "Fixture/New", quantization: "Q4",
  execution_device: "cpu", benchmark_profile: "chat-small-v1", max_duration_seconds: 900,
};
const historyFetch = context.fetch;
const reviewRequests = [];
context.fetch = async (path, options) => {
  if (path === "/api/managed/recipes") return { ok: true, json: async () => ({ recipes: [recipeFixture], default_storage_dir: "/fixture/default" }) };
  if (path === "/api/managed/review") {
    const body = JSON.parse(options.body);
    reviewRequests.push(body);
    return { ok: true, json: async () => ({
      recipe: recipeFixture, storage_dir: body.storage_dir, storage_dir_exists: true,
      files: [], already_installed: { installed: true, valid: true },
      platform: { ok: true, message: "Fixture ready" }, has_enough_space: true,
      free_space_bytes: 100000, required_bytes: 1000,
    }) };
  }
  return historyFetch(path, options);
};
await context.loadRecipes();
runInContext("selectedRecipeId = null;", context);
await context.loadRecentRuns();
await get("recent-list").children[0].trigger("click");
await stepTabs[1].trigger("click");
results.historyPrepareWithoutSelection = {
  choosing: !get("screen-choose").hidden,
  notice: get("notice").textContent,
  currentTab: stepTabs.find((tab) => tab.classList.contains("current"))?.dataset.screen,
};
await get("recipe-list").children[0].trigger("click");
results.historyDefaultStorage = {
  value: get("storage-dir").value,
  review: reviewRequests.at(-1),
  startEnabled: !get("start-button").disabled,
  storageError: !get("storage-error").hidden,
};
// A user-reviewed custom location must survive both a legacy saved row and
// a managed row whose historic recipe and storage belong to a different run.
get("storage-dir").value = "/fixture/custom";
await get("recheck-button").trigger("click");
for (const historicRecipe of [null, "removed-historic-recipe"]) {
  saved.recipe = historicRecipe;
  saved.storage_dir = historicRecipe ? "/fixture/old-run" : "";
  await context.loadRecentRuns();
  await get("recent-list").children[0].trigger("click");
  await stepTabs[1].trigger("click");
  results[historicRecipe ? "historyManagedDraft" : "historyLegacyDraft"] = {
    selectedRecipe: runInContext("selectedRecipeId", context),
    value: get("storage-dir").value,
    summaryVisible: !get("summary-panel").hidden && !get("screen-run").hidden,
    progressHidden: get("progress-panel").hidden,
    review: reviewRequests.at(-1),
  };
}
results.historyNoExecution = historyRequests.every((request) => request.method === "GET");
// Historical display/export must not borrow metadata from the selected new test.
saved.recipe = null;
delete saved.model; delete saved.profile; delete saved.execution_device;
await context.openTerminalRun(saved.id);
results.historyUnknownCeiling = get("fact-max-duration").textContent;
await get("download-summary-button").trigger("click");
results.historyUnknownExport = JSON.parse(await downloads.at(-1).blob.text());
// Returning to a live dashboard must not replace it with a preparation review.
runInContext('activeKind = "run"; activeJobId = "mrun-fixture";', context);
const reviewsBeforeActiveTab = reviewRequests.length;
get("summary-panel").hidden = true;
get("progress-panel").hidden = false;
await stepTabs[1].trigger("click");
results.historyActiveTab = {
  noReview: reviewRequests.length === reviewsBeforeActiveTab,
  progressVisible: !get("progress-panel").hidden && !get("screen-run").hidden,
};
process.stdout.write(JSON.stringify(results));
