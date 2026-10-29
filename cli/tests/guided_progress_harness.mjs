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
    append: () => {},
    replaceChildren: () => {},
    scrollIntoView: () => {},
    focus: () => {},
    querySelectorAll: () => [],
    attributes,
  };
}

const elements = new Map();
const document = {
  getElementById: (id) => {
    if (!elements.has(id)) elements.set(id, makeElement(id));
    return elements.get(id);
  },
  createElement: (tag) => makeElement(`created:${tag}`),
  querySelectorAll: () => [],
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
process.stdout.write(JSON.stringify(results));
