// Loads the real cli/src/omobench/web/app.js (the advanced "connect your own
// server" panel) in a VM context with the shared DOM stand-in, and drives it
// through the lifecycle of a run against a server the contributor already has
// running. Prints the resulting dashboard state as JSON.
//
// Run by cli/tests/test_advanced_dashboard.py, which asserts on that JSON.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { createContext, runInContext } from "node:vm";
import { makeDocument } from "./dom_stub.mjs";

const appPath = process.argv[2];
const webDirectory = dirname(appPath);

const document = makeDocument();
const get = (id) => document.getElementById(id);

// The package form, driven through values the harness sets directly.
get("package-form").formValues = {};
class HarnessFormData {
  constructor(form) { this.entries = Object.entries(form.formValues || {}); }
  [Symbol.iterator]() { return this.entries[Symbol.iterator](); }
}

const downloads = [];
let scheduledTimers = 0;

// Clearly synthetic fixtures: an LM Studio-style alias, an uncatalogued model.
const ALIAS = "qwen/qwen3-vl-4b:3";
const SUMMARY = {
  runner_version: "0.7.3",
  results: {
    latency_ms: { ttft: { p50: 1200, p95: 2400 }, inter_token: { p50: 90 } },
    output_tokens_per_second: 11.5, total_tokens_per_second: 40,
    requests_per_second: 0.2, requests: { succeeded: 48, attempted: 48 },
    error_rate_percent: 0,
  },
};
const SETUP = {
  environment: { cpu: "Fixture CPU", ram_gb: 32, gpus: [], operating_system: "Fixture Linux", guidellm: "0.7.3" },
  profiles: { "chat-small-v1": { profile: "concurrent,streams=1", input_tokens: 512, output_tokens: 128, max_requests: 60, max_duration_seconds: 900 } },
  frameworks: ["llama.cpp", "vllm", "openai-compatible-other"],
  catalog: ["Qwen/Qwen3-4B"],
  can_execute: true, can_validate: true, runner_message: "GuideLLM 0.7.3 ready.",
};
const REQUIREMENTS = {
  model: "Qwen/Qwen3-VL-4B-Instruct", catalog: ["Qwen/Qwen3-4B"], catalogued: false,
  ready: false, local_export_available: true,
  items: [
    { field: null, label: "Benchmark completed with measurements", ok: true, detail: "This run produced measurements." },
    { field: "model", label: "Canonical model ID is in the OmoBench catalog", ok: false, detail: "Qwen/Qwen3-VL-4B-Instruct is not currently eligible for the community registry." },
    { field: "model_revision", label: "Immutable revision of the actual weights source", ok: false, detail: "A commit SHA of the weights repository." },
    { field: "framework_version", label: "Exact serving runtime version", ok: false, detail: "The runtime build, not the application version." },
  ],
};
const LOCAL_REPORT = { kind: "omobench-local-report", format_version: 1, measurements: SUMMARY.results };

const state = {
  jobs: [],
  details: {},        // job id -> detail document
  telemetry: {},      // job id -> telemetry snapshot or null
  pendingDetail: null, // when set, /api/runs/<id> returns this promise instead
};
const requests = [];

const context = createContext({
  document, console, URLSearchParams, Date, Math, JSON, Number, Set, Map, Object,
  Array, String, Boolean, Promise, Blob, Error, isNaN, parseInt, parseFloat,
  FormData: HarnessFormData,
  URL: { createObjectURL: () => "blob:fixture", revokeObjectURL: () => {} },
  setTimeout: () => { scheduledTimers += 1; return 0; },
  clearTimeout: () => {},
  location: { hash: "", pathname: "/advanced", href: "http://127.0.0.1:8765/advanced" },
  history: { replaceState: () => {} },
  sessionStorage: { getItem: () => "test-token", setItem: () => {}, removeItem: () => {} },
  fetch: async (path, options) => {
    const method = options?.method || "GET";
    requests.push({ path, method });
    let data;
    const detail = /^\/api\/runs\/(job-[a-f0-9]{16})$/.exec(path);
    const telemetry = /^\/api\/runs\/(job-[a-f0-9]{16})\/telemetry$/.exec(path);
    const report = /^\/api\/runs\/(job-[a-f0-9]{16})\/local-report$/.exec(path);
    const needs = /^\/api\/runs\/(job-[a-f0-9]{16})\/requirements$/.exec(path);
    const cancel = /^\/api\/runs\/(job-[a-f0-9]{16})\/cancel$/.exec(path);
    if (path === "/api/setup") data = SETUP;
    else if (path === "/api/runs" && method === "GET") data = state.jobs;
    else if (path === "/api/github/auth") data = { status: "signed_out", enabled: true, generation: "g1", account: null, repository: "Xatiiv/omobench" };
    else if (path === "/api/tokenizer-check") data = { ok: true, kind: "huggingface", source: "Qwen/Qwen3-VL-4B-Instruct", cached: false, message: "fixture tokenizer message" };
    else if (detail) {
      if (state.pendingDetail && state.pendingDetail.id === detail[1]) {
        return state.pendingDetail.promise.then((value) => ({ ok: true, json: async () => value }));
      }
      data = state.details[detail[1]];
    } else if (telemetry) data = state.telemetry[telemetry[1]] ?? { available: false };
    else if (report) data = LOCAL_REPORT;
    else if (needs) data = REQUIREMENTS;
    else if (cancel) data = { ...state.details[cancel[1]], status: "cancelling" };
    else throw new Error(`unexpected request: ${path}`);
    if (data === undefined) throw new Error(`no fixture for ${path}`);
    return { ok: true, json: async () => data };
  },
});

runInContext(readFileSync(join(webDirectory, "dashboard.js"), "utf8"), context);
runInContext(readFileSync(join(webDirectory, "community.js"), "utf8"), context);
get("submission-mode").value = "community";
runInContext(readFileSync(appPath, "utf8"), context, { filename: appPath });
context.download = (blob, name) => downloads.push({ blob, name });

async function settle() {
  for (let i = 0; i < 20; i += 1) await Promise.resolve();
}

function job(changes = {}) {
  return {
    id: "job-" + "a".repeat(16), created_at: "2026-10-29T10:00:00Z", finished_at: null,
    status: "running", model: ALIAS, profile: "chat-small-v1", execution_device: "cpu",
    tokenizer: "Qwen/Qwen3-VL-4B-Instruct", summary: null, error: null, validation: null,
    submission: null, log: "runner starting", run_path: ".omobench-runs/ui/job-a/run-fixture",
    ...changes,
  };
}

function telemetry(changes = {}) {
  return {
    available: true, stage: "benchmarking", elapsed_seconds: 120,
    requests: { processed: 12, successful: 11, errored: 1, cancelled: 0, total_budget: 60 },
    measured_requests_target: 50, remaining_fraction: 0.6,
    remaining_seconds_duration_limit: 500, eta_seconds: 400,
    duration_budget_exhausted: false,
    latest_metrics: { output_tokens_per_second: 11.5, time_to_first_token_ms: 1200, request_latency_seconds: 44 },
    chart_history: [{ elapsed_seconds: 30, output_tokens_per_second: 9 }, { elapsed_seconds: 120, output_tokens_per_second: 11.5 }],
    updated_at: Date.now() / 1000,
    observer_error: null,
    ...changes,
  };
}

function snapshot() {
  const fill = get("progress-bar-fill");
  return {
    heading: get("progress-heading").textContent,
    subtitle: get("dashboard-subtitle").textContent,
    runStatus: get("run-status").textContent,
    meta: get("run-meta").textContent,
    bar: {
      width: fill.style.width ?? "",
      indeterminate: fill.classList.contains("indeterminate"),
      label: get("progress-bar-label").textContent,
      ariaValueNow: get("progress-bar-wrap").getAttribute("aria-valuenow"),
    },
    stages: get("stage-list").children.map((item) => `${item.textContent}:${item.className.replace("stage-item ", "")}`),
    facts: {
      elapsed: get("fact-elapsed").textContent,
      eta: get("fact-eta").textContent,
      requests: get("fact-requests").textContent,
      maxDuration: get("fact-max-duration").textContent,
    },
    live: {
      throughput: get("live-throughput").textContent,
      ttft: get("live-ttft").textContent,
      requestLatency: get("live-request-latency").textContent,
      successful: get("live-successful").textContent,
      errored: get("live-errors").textContent,
      cancelled: get("live-cancelled").textContent,
    },
    chartHidden: get("chart-svg").getAttribute("hidden") !== null,
    chartPoints: get("chart-line").getAttribute("points"),
    chartEmpty: get("chart-empty").textContent,
    logStatus: get("log-connection-status").textContent,
    log: get("benchmark-log").textContent,
    telemetryFacts: get("telemetry-technical-facts").children.map((el) => el.textContent),
    cancelHidden: get("cancel-button").hidden,
    startOverHidden: get("start-over-button").hidden,
    errorHidden: get("run-error").hidden,
    error: get("run-error").textContent,
    metricsHidden: get("metrics").hidden,
    metricCount: get("metrics").children.length,
    exportHidden: get("download-summary").hidden,
  };
}

// Drives one poll tick with the given job state and telemetry.
async function tick(jobDocument, telemetrySnapshot) {
  state.jobs = [jobDocument];
  state.details[jobDocument.id] = jobDocument;
  state.telemetry[jobDocument.id] = telemetrySnapshot;
  await context.poll();
  await settle();
}

// Opens a run the way a person does: select it, then let the loop poll. Each
// scenario below starts from a fresh selection so none inherits another's
// stage history.
async function openRun(jobDocument, telemetrySnapshot) {
  state.jobs = [jobDocument];
  state.details[jobDocument.id] = jobDocument;
  state.telemetry[jobDocument.id] = telemetrySnapshot;
  await context.selectRun(jobDocument.id);
  await settle();
  await tick(jobDocument, telemetrySnapshot);
}

const results = {};

// init() ran on load against an empty run list.
await settle();
// The advanced panel mounts the shared component itself: the markup it
// injected is the component's own, not a copy maintained beside it.
const mountedMarkup = get("dashboard-mount").innerHTML;
results.mounted = {
  isSharedComponent: mountedMarkup === context.BenchmarkDashboard.markup({
    stageNote: "Your server is already running; these are OmoBench's own runner stages",
    downloadProgress: false,
    serverLog: false,
  }),
  ids: ["progress-bar-fill", "chart-svg", "chart-scrub", "stage-list", "fact-eta",
        "live-throughput", "live-ttft", "live-request-latency", "live-successful",
        "live-errors", "live-cancelled", "telemetry-technical-facts", "benchmark-log",
        "cancel-button", "run-error"].filter((id) => mountedMarkup.includes(`id="${id}"`)),
  // Stages OmoBench cannot observe against someone else's server are absent.
  mentionsDownloading: mountedMarkup.includes("Downloading verified files"),
  hasManagedServerLog: mountedMarkup.includes("server-log"),
  hasDownloadProgress: mountedMarkup.includes("download-progress"),
};
results.externalStages = context.BenchmarkDashboard.EXTERNAL_STAGES.map((stage) => stage.label);
results.managedStages = context.BenchmarkDashboard.MANAGED_STAGES.map((stage) => stage.label);

const running = job();
state.jobs = [running];
state.details[running.id] = running;
state.telemetry[running.id] = null;
scheduledTimers = 0;
await context.selectRun(running.id);
await settle();
results.selected = snapshot();
results.selectTimers = scheduledTimers; // selecting a run must start no loop

// 1. Started, but the runner has reported nothing yet.
scheduledTimers = 0;
await tick(job(), null);
results.starting = snapshot();
results.pollTimersPerTick = scheduledTimers;

// 2..5 the stages GuideLLM itself reports.
await tick(job(), telemetry({ stage: "warmup", remaining_fraction: 0.95, chart_history: [] }));
results.warmup = snapshot();
await tick(job(), telemetry());
results.measuring = snapshot();
await tick(job(), telemetry({ stage: "cooldown", remaining_fraction: 0.05 }));
results.cooldown = snapshot();
await tick(job(), telemetry({ stage: "saving", remaining_fraction: 0 }));
results.saving = snapshot();

// 6. Completed: measurements plus finalized evidence.
await tick(job({ status: "completed", finished_at: "2026-10-29T10:20:00Z", summary: SUMMARY, log: "done" }),
           telemetry({ stage: "saving", remaining_fraction: 0 }));
results.completed = snapshot();

// 7. Time budget spent while a request is still streaming.
await openRun(job(), telemetry({ remaining_fraction: 0, duration_budget_exhausted: true }));
results.overrunning = snapshot();

// 8. Telemetry that has not been updated for a long time.
await openRun(job(), telemetry({ updated_at: Date.now() / 1000 - 300 }));
results.stale = snapshot();

// 9. Malformed telemetry must not crash the dashboard or invent values.
await openRun(job(), {
  available: true, stage: 7, elapsed_seconds: "soon", remaining_fraction: "half",
  requests: "many", latest_metrics: { output_tokens_per_second: "fast" },
  chart_history: "not a list", updated_at: "never",
});
results.malformed = snapshot();

// 10. No telemetry at all (an older run, or an observer that disabled itself).
await openRun(job({ status: "completed", finished_at: "2026-10-29T10:20:00Z", summary: SUMMARY }), null);
results.missingTelemetry = snapshot();

// 11. Failure before any measurement.
await openRun(job({ status: "failed", finished_at: "2026-10-29T10:01:00Z",
                     error: "The benchmark exited with code 1. See the log below." }), null);
results.failedBeforeMeasurements = snapshot();

// 12. Failure while writing the evidence files: frozen at what was reached.
await openRun(job(), telemetry({ stage: "saving", remaining_fraction: 0 }));
await tick(job({ status: "failed", finished_at: "2026-10-29T10:20:00Z",
                 error: "The benchmark ran, but its required evidence log could not be finalized." }),
           telemetry({ stage: "saving", remaining_fraction: 0 }));
results.failedDuringFinalization = snapshot();

// 13. Cancellation with partial metrics.
await openRun(job(), telemetry({ remaining_fraction: 0.6 }));
await tick(job({ status: "cancelled", finished_at: "2026-10-29T10:05:00Z" }), telemetry({ remaining_fraction: 0.6 }));
results.cancelledWithPartialMetrics = snapshot();

// 14. Cancelling asks the panel to stop its own runner, and nothing else.
state.details[running.id] = job();
state.jobs = [job()];
await context.selectRun(running.id);
await settle();
const beforeCancel = requests.length;
await get("cancel-button").trigger("click");
await settle();
results.cancelRequests = requests.slice(beforeCancel).map((entry) => `${entry.method} ${entry.path}`);

// 15. A late response for a run that is no longer selected must be ignored.
const second = job({ id: "job-" + "b".repeat(16), model: "second/model", execution_device: "gpu", status: "completed",
                     finished_at: "2026-10-29T09:00:00Z", summary: SUMMARY, tokenizer: null, log: "second run log" });
state.details[second.id] = second;
state.telemetry[second.id] = null;
state.jobs = [job(), second];
let releaseFirst;
state.pendingDetail = { id: running.id, promise: new Promise((resolve) => { releaseFirst = resolve; }) };
const slowSelect = context.selectRun(running.id);
await settle();
state.pendingDetail = null;
await context.selectRun(second.id);
await settle();
releaseFirst(job({ model: "STALE/model", log: "stale log" }));
await slowSelect.catch(() => {});
await settle();
results.afterStaleResponse = {
  title: get("run-title").textContent,
  subtitle: get("dashboard-subtitle").textContent,
  log: get("benchmark-log").textContent,
};

// 16. Local export and the publication checklist on this page.
const exportRequestsBefore = requests.length;
await get("download-summary").trigger("click");
await settle();
results.localExport = {
  requests: requests.slice(exportRequestsBefore).map((entry) => `${entry.method} ${entry.path}`),
  name: downloads[downloads.length - 1]?.name,
  content: JSON.parse(await downloads[downloads.length - 1].blob.text()),
};
get("package-form").formValues = { model: "Qwen/Qwen3-VL-4B-Instruct", model_revision: "", framework: "llama.cpp", framework_version: "", submitter: "" };
await get("check-requirements").trigger("click");
await settle();
results.checklist = {
  items: get("publication-checklist").children.map((item) => `${item.className.replace("status-item ", "")}:${item.children[0].textContent}`),
  summary: get("publication-summary").textContent,
};

// 17. The tokenizer check is a separate action from the connection test.
await get("check-tokenizer").trigger("click");
await settle();
results.tokenizerCheck = get("tokenizer-result").textContent;

results.allRequests = [...new Set(requests.map((entry) => `${entry.method} ${entry.path.replace(/job-[a-f0-9]{16}/, "<job>")}`))];
console.log(JSON.stringify(results));
