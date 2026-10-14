// Loads the real cli/src/omobench/web/guided.js in a VM context with a minimal
// DOM stand-in, drives its progress rendering through the four states that
// matter for "100% means finished", and prints the resulting bar state as JSON.
//
// Run by cli/tests/test_guided_progress.py, which asserts on that JSON.
import { readFileSync } from "node:fs";
import { createContext, runInContext } from "node:vm";

const guidedPath = process.argv[2];
const source = readFileSync(guidedPath, "utf8");

function makeElement(id) {
  const classes = new Set();
  const attributes = {};
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
    addEventListener: () => {},
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

process.stdout.write(JSON.stringify(results));
