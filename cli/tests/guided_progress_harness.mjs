// Loads the real cli/src/omobench/web/guided.js in a VM context with a minimal
// DOM stand-in, drives its progress rendering through the four states that
// matter for "100% means finished", and prints the resulting bar state as JSON.
//
// Run by cli/tests/test_guided_progress.py, which asserts on that JSON.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { createContext, runInContext } from "node:vm";
import { makeDocument, makeElement } from "./dom_stub.mjs";

const guidedPath = process.argv[2];
const source = readFileSync(guidedPath, "utf8");

// Six stages across four screens, exactly as guided.html declares them: two
// stages can share a screen, so a tab is addressed by the stage it stands for
// rather than by its position.
const STAGE_SCREENS = [["connect", "choose"], ["identify", "choose"], ["check", "run"],
                       ["run", "run"], ["review", "results"], ["submit", "share"]];
const stepTabs = STAGE_SCREENS.map(([stage, screen]) => {
  const tab = makeElement(`step-${stage}`);
  tab.dataset.stage = stage;
  tab.dataset.screen = screen;
  return tab;
});
const tabFor = (stage) => stepTabs.find((tab) => tab.dataset.stage === stage);
const document = makeDocument({ selectors: { ".step-tab": stepTabs } });

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

// The shared dashboard component the page mounts, loaded exactly as the
// browser loads it (dashboard.js, then community.js, then the page script).
runInContext(readFileSync(join(dirname(guidedPath), "dashboard.js"), "utf8"), context);
runInContext(readFileSync(join(dirname(guidedPath), "community.js"), "utf8"), context);
runInContext(readFileSync(join(dirname(guidedPath), "workflow.js"), "utf8"), context);
document.getElementById("submission-mode").value = "community";
runInContext(source, context, { filename: guidedPath });

// The page mounts one dashboard; this is that same component, so asserting on
// its primitives asserts on what the page renders.
const component = new context.BenchmarkDashboard(null, { stages: context.BenchmarkDashboard.MANAGED_STAGES });

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
component.renderProgressBarFrozen();
results.failedDuringFinalization = barState();

// 5. Genuine success: execution and evidence finalization both completed.
component.renderProgressBar(100, { complete: true });
results.completed = barState();

// 6. The primitive itself must never reach 100 without the explicit flag.
component.renderProgressBar(99.999);
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
component.renderChart(null);
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
component.renderChart(observed);
results.observedMetrics = chartState();
results.historyPreserved = JSON.stringify(observed.chart_history) === historyBefore;
document.getElementById("chart-scrub").value = "0";
document.getElementById("chart-scrub").trigger("input");
results.inspectedSample = chartState();
component.renderChart({ ...observed, chart_history: [...observed.chart_history, { elapsed_seconds: 25, output_tokens_per_second: 9 }] });
results.retainedSelection = chartState();
component.renderChart({ available: true, latest_metrics: { output_tokens_per_second: 0 }, chart_history: [{ elapsed_seconds: 0, output_tokens_per_second: 0 }] });
results.zeroMetrics = chartState();
component.renderChart({ ...observed, available: false });
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
// What the panel would be told about publication eligibility. Clearly
// synthetic: an uncatalogued model with no recorded revision.
const requirementsFixture = {
  model: "Fixture/Model", catalog: ["Qwen/Qwen3-4B"], catalogued: false, ready: false,
  local_export_available: true,
  items: [
    { field: null, label: "Benchmark completed with measurements", ok: true, detail: "This run produced measurements." },
    { field: "model", label: "Canonical model ID is in the OmoBench catalog", ok: false, detail: "Fixture/Model is not currently eligible for the community registry." },
    { field: "model_revision", label: "Immutable revision of the actual weights source", ok: false, detail: "A commit SHA or other immutable revision." },
  ],
};
// The local report the panel would export: measurements only, no publication
// metadata and no credential.
const localReportFixture = {
  kind: "omobench-local-report", format_version: 1,
  server: { model_identifier: "Fixture/Model", execution_device: "cpu" },
  measurements: savedSummary.results,
};
// The one workflow computation both pages read: six stages, every check with
// the gate it belongs to, and the six completion states. Shaped like the real
// answer for this rejected fixture run -- packaged, and refused by the
// registry on its own acceptance thresholds.
const workflowFixture = {
  stages: [
    { id: "connect", label: "Connect", state: "done" },
    { id: "identify", label: "Identify", state: "done" },
    { id: "check", label: "Check", state: "done" },
    { id: "run", label: "Run", state: "done" },
    { id: "review", label: "Review", state: "blocked" },
    { id: "submit", label: "Submit", state: "todo" },
  ],
  checks: [
    { id: "measurements", label: "Benchmark completed with measurements", state: "ok",
      observed: "12 successful", source: "this run's evidence", blocks: "package", recovery: "", detail: "" },
    { id: "weights-revision", label: "Immutable revision of the actual weights source", state: "blocked",
      observed: "main", source: "you", blocks: "package", recovery: "Record a commit SHA.", detail: "" },
    { id: "acceptance", label: "Profile acceptance", state: "blocked", observed: "not met",
      source: "this run's evidence", blocks: "submit", recovery: "", detail: "" },
    { id: "local", label: "Saving a local report", state: "ok", observed: null,
      source: "this machine", blocks: null, recovery: "", detail: "" },
  ],
  blocking: { run: [], package: ["weights-revision"], submit: ["acceptance"] },
  proposal: null,
  completion: [
    { id: "benchmark", label: "Benchmark completed", state: "done", detail: "This run produced measurements." },
    { id: "evidence", label: "Evidence saved on this machine", state: "done", detail: "Kept at .omobench-runs/fixture." },
    { id: "package", label: "Bundle validated", state: "failed", detail: "The bundle could not be built." },
    { id: "ready", label: "Ready to submit", state: "blocked", detail: "The registry still needs something." },
    { id: "submitted", label: "Submitted for review", state: "pending", detail: "Not submitted." },
    { id: "published", label: "Accepted and published", state: "pending", detail: "This panel does not observe it." },
  ],
  gates: { run: "run gate note", package: "package gate note", submit: "submit gate note" },
  local_report: "local report note",
};
const evidenceFixture = {
  schema_version: "1.1",
  qualifications: ["Detection is not verification."],
  sections: [{ id: "model", title: "Which model, and which weights",
               rows: [{ label: "Canonical model", value: "Fixture/Model", recorded: true,
                        source: "stated by the contributor", note: null }] }],
};
// Browsing saved runs must execute nothing and upload nothing. The test of
// that is what a request *does*, not which verb it uses: asking the panel
// where a run got to carries the form, so it is a POST that reads, and the
// panel's own request guard classifies it the same way.
const READING_POSTS = new Set(["/api/workflow"]);
const readOnly = (requests) => requests.every(
  (request) => request.method === "GET" || READING_POSTS.has(request.path));

const historyRequests = [];
context.fetch = async (path, options) => {
  historyRequests.push({ path, method: options?.method || "GET" });
  let data;
  if (path === "/api/managed/runs") data = [saved];
  else if (path === "/api/managed/setup-jobs") data = [];
  else if (path === `/api/managed/runs/${saved.id}`) data = saved;
  else if (path.endsWith("/telemetry")) data = { available: false };
  else if (path === `/api/runs/${saved.benchmark_job_id}`) data = savedDetail;
  else if (path.endsWith("/requirements")) data = requirementsFixture;
  else if (path.endsWith("/local-report")) data = localReportFixture;
  else if (path === "/api/workflow") data = workflowFixture;
  else if (path.endsWith("/evidence")) data = evidenceFixture;
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
// The same six completion states and the same stepper the advanced panel
// renders, from the same answer: this page does not keep its own idea of
// where a run got to.
results.sharedWorkflow = {
  completion: get("completion-steps").children.map(
    (li) => li.className.replace("status-item ", "") + ":" + li.children[0].textContent),
  completionVisible: !get("completion").hidden,
  evidenceVisible: !get("evidence-summary").hidden,
  evidenceSections: get("evidence-sections").children.map((node) => node.children[0].textContent),
  stepper: stepTabs.map((tab) => tab.dataset.stage + ":"
    + ["done", "current", "todo", "blocked"].filter((state) => tab.classList.contains(`stage-${state}`)).join(",")),
};
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
results.publicationChecklist = {
  items: get("publication-checklist").children.map((item) => `${item.className.replace("status-item ", "")}:${item.children[0].textContent}`),
  summary: get("publication-summary").textContent,
};
const exportBefore = historyRequests.length;
await get("download-summary-button").trigger("click");
results.localExport = {
  requests: historyRequests.slice(exportBefore).map((entry) => `${entry.method} ${entry.path}`),
  name: downloads[downloads.length - 1]?.name,
  kind: JSON.parse(await downloads[downloads.length - 1].blob.text()).kind,
};
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
results.historyReadOnly = readOnly(historyRequests);

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
await tabFor("check").trigger("click");
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
  await tabFor("check").trigger("click");
  results[historicRecipe ? "historyManagedDraft" : "historyLegacyDraft"] = {
    selectedRecipe: runInContext("selectedRecipeId", context),
    value: get("storage-dir").value,
    summaryVisible: !get("summary-panel").hidden && !get("screen-run").hidden,
    progressHidden: get("progress-panel").hidden,
    review: reviewRequests.at(-1),
  };
}
results.historyNoExecution = readOnly(historyRequests);
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
await tabFor("check").trigger("click");
results.historyActiveTab = {
  noReview: reviewRequests.length === reviewsBeforeActiveTab,
  progressVisible: !get("progress-panel").hidden && !get("screen-run").hidden,
};
process.stdout.write(JSON.stringify(results));
