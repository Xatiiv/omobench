"use strict";

const $ = (id) => document.getElementById(id);
const activeStates = new Set(["running", "cancelling", "packaging", "preparing", "submitting"]);
let selected = null;
let setup = null;
let jobs = [];
let pending = false;
let preview = null;
const incoming = new URLSearchParams(location.hash.slice(1)).get("token");
if (incoming) {
  sessionStorage.setItem("omobench-token", incoming);
  history.replaceState(null, "", location.pathname);
}
const token = sessionStorage.getItem("omobench-token") || "";

// The same component the guided recipes mount, with the stages OmoBench can
// actually observe here: it did not download these weights, did not start this
// server and did not load its model, so those stages do not exist on this route.
const dashboard = new BenchmarkDashboard($("dashboard-mount"), {
  stages: BenchmarkDashboard.EXTERNAL_STAGES,
  stageNote: "Your server is already running; these are OmoBench's own runner stages",
  downloadProgress: false,
  serverLog: false,
});
let runStartedAt = null;
let lastKnownStageIndex = 0;
let requirements = null;

// GuideLLM's own accumulator.timings.status, mapped in telemetry.py. Without a
// snapshot the only honest statement is that OmoBench started its runner: it
// cannot see warmup or measurement until the runner reports them.
function externalStageKey(job, telemetry) {
  if (!["running", "cancelling"].includes(job.status)) return null;
  if (telemetry && telemetry.available) {
    if (telemetry.stage) return telemetry.stage === "starting" ? "starting_runner" : telemetry.stage;
    return "benchmarking";
  }
  return "starting_runner";
}

function notice(message, error = false) {
  $("notice").textContent = message;
  $("notice").classList.toggle("error", error);
  $("notice").hidden = !message;
}

async function api(path, data, binary = false) {
  const headers = {"X-OmoBench-Token": token};
  const options = {headers};
  if (data !== undefined) {
    headers["Content-Type"] = "application/json";
    options.method = "POST";
    options.body = JSON.stringify(data);
  }
  const response = await fetch(path, options);
  if (!response.ok) {
    const body = await response.json();
    throw new Error(body.error || "The request failed.");
  }
  return binary ? response.blob() : response.json();
}

function updateButtons() {
  const busy = pending || jobs.some((job) => activeStates.has(job.status));
  $("start-run").disabled = busy || !setup?.can_execute;
  // Advisory, never a gate. Only an actual runner constraint stops a run, and
  // an unloaded tokenizer or an uncatalogued model is not one -- the
  // measurements would be just as real. What this does is make sure nobody
  // spends an hour discovering it.
  const outstanding = workflow ? workflow.blocking.run.length : 0;
  $("run-caution").hidden = !outstanding;
  if (outstanding) {
    const labels = workflow.checks
      .filter((item) => item.blocks === "run" && item.state !== "ok")
      .map((item) => item.label);
    $("run-caution").textContent =
      `${outstanding} thing(s) are not established yet: ${labels.join("; ")}. `
      + "You can still start the benchmark; these are the ones that would stop it or waste it.";
  }
  $("test-connection").disabled = busy;
  $("package-button").disabled = busy || !setup?.can_validate;
  $("prepare-button").disabled = busy;
  $("download-bundle").disabled = busy;
  $("submit-button").disabled = busy || !preview?.can_submit || !$("submit-confirmed").checked;
  $("cancel-button").disabled = pending || jobs.find((job) => job.id === selected)?.status === "cancelling";
}

async function action(work) {
  if (pending) return;
  pending = true;
  notice("");
  updateButtons();
  try { await work(); }
  catch (error) { notice(error.message, true); }
  finally { pending = false; updateButtons(); }
}

function option(value, label = value) {
  const el = document.createElement("option");
  el.value = value;
  el.textContent = label;
  return el;
}

function profileDetail() {
  if (!setup) return;
  const spec = setup.profiles[$("profile").value];
  const load = spec.profile.includes("streams=16") ? "16 streams" : "1 stream";
  // max_requests is the runner's total request budget, including warmup and
  // cooldown -- never label it "measured requests" here. The profile's own
  // declared measured-request target (execution.measured_requests) is not
  // part of this setup payload; only show it once it is.
  $("profile-detail").textContent = `${spec.input_tokens.toLocaleString()} input tokens · ${spec.output_tokens} requested output tokens · ${load} · maximum ${spec.max_requests} total requests, including warmup and cooldown · up to ${spec.max_duration_seconds / 60} min`;
}

async function refreshSetup() {
  setup = await api("/api/setup");
  $("runner-status").textContent = setup.runner_message;
  $("runner-status").classList.toggle("error", !setup.can_execute);
  const env = setup.environment;
  $("machine").replaceChildren();
  const facts = [["CPU", env.cpu || "Unknown"], ["RAM", env.ram_gb ? `${env.ram_gb} GB` : "Unknown"],
    ["GPU", (env.gpus || []).map((gpu) => `${gpu.model}${gpu.memory_gb ? ` · ${gpu.memory_gb} GB` : " · VRAM unknown"}`).join("; ") || "None detected"],
    ["Platform", env.operating_system], ["GuideLLM", env.guidellm || "Not installed"]];
  facts.forEach(([label, value]) => {
    const dt = document.createElement("dt"), dd = document.createElement("dd");
    dt.textContent = label; dd.textContent = value; $("machine").append(dt, dd);
  });
  const currentProfile = $("profile").value;
  // A profile whose declared phases its runner does not enforce is offered for
  // reproducing earlier results, labelled, and never pre-selected.
  const contracts = setup.profile_contracts || {};
  const legacy = (id) => contracts[id] && contracts[id].declaration_matches_execution === false;
  const ids = Object.keys(setup.profiles).sort((a, b) => Number(legacy(a)) - Number(legacy(b)));
  $("profile").replaceChildren(...ids.map((id) => option(id, legacy(id) ? `${id} (legacy)` : id)));
  $("profile").value = currentProfile || ids.find((id) => !legacy(id)) || ids[0];
  const framework = $("framework").value;
  $("framework").replaceChildren(...setup.frameworks.map((id) => option(id)));
  $("framework").value = framework || "llama.cpp";
  $("catalog-models").replaceChildren(...setup.catalog.map((id) => option(id)));
  profileDetail(); updateButtons();
}

function renderJobs() {
  $("runs-list").replaceChildren();
  if (!jobs.length) {
    const empty = document.createElement("p"); empty.className = "hint"; empty.textContent = "No runs yet."; $("runs-list").append(empty);
  }
  jobs.forEach((job) => {
    const button = document.createElement("button"), meta = document.createElement("span");
    button.type = "button"; button.className = "run-item";
    button.classList.toggle("selected", selected === job.id); button.textContent = job.model;
    meta.textContent = `${job.execution_device.toUpperCase()} · ${job.status} · ${new Date(job.created_at).toLocaleDateString()}`;
    button.append(meta); button.addEventListener("click", () => action(() => selectRun(job.id))); $("runs-list").append(button);
  });
  updateButtons();
}

function renderRun(job, telemetry = null) {
  // A response that arrived after the selection moved on must never overwrite
  // the run now on screen.
  if (job.id !== selected) return;
  $("run-panel").hidden = false;
  $("run-title").textContent = job.model;
  $("run-transport").hidden = !job.transport;
  $("run-transport").textContent = job.transport
    ? "Measured through the Windows bridge: the model ran on Windows, the benchmark runner in WSL. "
      + "The bridge's own latency is inside every number here and is never subtracted. This run can "
      + "be submitted once you describe the machine that served the model, below."
    : "";
  $("run-status").textContent = `${job.execution_device.toUpperCase()} · ${job.status}`;
  const duration = Math.max(0, Math.floor((new Date(job.finished_at || Date.now()) - new Date(job.created_at)) / 1000));
  $("run-meta").textContent = `${job.profile} · ${Math.floor(duration / 60)}m ${duration % 60}s · ${new Date(job.created_at).toLocaleString()}`
    + (job.tokenizer ? ` · tokenizer ${job.tokenizer}` : "");

  const active = ["running", "cancelling"].includes(job.status);
  const stageKey = externalStageKey(job, telemetry);
  if (stageKey) lastKnownStageIndex = Math.max(lastKnownStageIndex, dashboard.stageIndex(stageKey));
  dashboard.render({
    stageKey,
    lastKnownStageIndex,
    cancelling: job.status === "cancelling",
    runActive: active,
    telemetry,
    maxDurationSeconds: setup?.profiles?.[job.profile]?.max_duration_seconds,
    startedAt: runStartedAt,
    logText: job.log || "Waiting for the runner…",
  });
  $("cancel-button").hidden = !active;
  $("cancel-button").textContent = job.status === "cancelling" ? "Cancelling…" : "Cancel benchmark";
  $("cancel-button").disabled = pending || job.status === "cancelling";
  $("start-over-button").hidden = active;
  if (!active) {
    dashboard.finish({
      ok: job.status === "completed" && !!job.summary,
      status: job.status,
      // The runner on this route is OmoBench's own CLI process, which the
      // controller starts before the job exists at all.
      runnerStarted: true,
      errorMessage: job.error,
      createdAt: job.created_at,
      finishedAt: job.finished_at,
    });
  }

  $("metrics").hidden = !job.summary;
  $("download-summary").hidden = !job.summary;
  $("local-note").hidden = !job.summary || job.execution_device !== "cpu";
  if (job.summary) $("metrics").replaceChildren(...BenchmarkDashboard.metricCards(job.summary));
  $("run-path").textContent = job.run_path ? `Evidence: ${job.run_path}` : "";
  $("package-panel").hidden = !job.summary;
  $("package-panel-device").textContent = `${job.execution_device.toUpperCase()} evidence`;
  $("gpu-memory-field").hidden = job.execution_device === "cpu";
  $("validation-result").hidden = !job.validation;
  if (job.validation) {
    $("validation-result").textContent = job.validation.valid ? "Bundle validation passed. Review the files before submission." : job.validation.errors.join("\n");
    $("validation-result").classList.toggle("error", !job.validation.valid);
  }
  $("prepare-button").hidden = !job.validation?.valid;
  $("submission-link").hidden = true;
  const pr = job.submission?.pull_request_url;
  if (typeof pr === "string" && /^https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/pull\/\d+$/.test(pr)) {
    const link = document.createElement("a"); link.href = pr; link.target = "_blank"; link.rel = "noopener"; link.textContent = "Open pull request on GitHub ↗";
    $("submission-link").replaceChildren(link); $("submission-link").hidden = false;
  }
}

// The whole set of publication requirements at once, before packaging is
// attempted. Local export never depends on any of them.
function renderRequirements(report) {
  requirements = report;
  $("publication-checklist").replaceChildren(...report.items.map((item) => {
    const li = document.createElement("li");
    li.className = `status-item ${item.ok === null ? "pending" : item.ok ? "ok" : "fail"}`;
    const strong = document.createElement("strong"), span = document.createElement("span");
    strong.textContent = item.label; span.textContent = item.detail;
    li.append(strong, span);
    return li;
  }));
  const missing = report.items.filter((item) => item.ok === false).length;
  $("publication-summary").textContent = missing
    ? `${missing} requirement${missing === 1 ? "" : "s"} still missing for a community submission. Your measurements are already saved, and "Save local report" works regardless.`
    : "Every requirement this panel can check is satisfied. Packaging and the registry validator remain the authority.";
}

async function selectRun(id) {
  selected = id; preview = null; requirements = null;
  $("submission-preview").hidden = true; $("submit-confirmed").checked = false;
  $("publication-checklist").replaceChildren(); $("publication-summary").textContent = "";
  const job = await api(`/api/runs/${id}`);
  if (selected !== id) return;
  $("package-form").reset(); $("package-model").value = job.model; $("framework").value = "llama.cpp"; frameworkChanged();
  // Verified managed-run metadata, when present, so packaging never requires typing a source by hand.
  $("package-model-source").value = job.model_source || "";
  runStartedAt = new Date(job.created_at).getTime();
  lastKnownStageIndex = 0;
  dashboard.prepareForRun({
    subtitle: `${job.model} · ${job.profile} · ${job.execution_device.toUpperCase()} declared`
      + (job.tokenizer ? ` · tokenizer ${job.tokenizer}` : ""),
  });
  // Best-effort: a run without telemetry (an older job, or one whose observer
  // disabled itself) still renders, honestly reporting it as unavailable.
  const telemetry = await api(`/api/runs/${id}/telemetry`).catch(() => null);
  if (selected !== id) return;
  renderRun(job, telemetry); renderJobs();
  // Reopening a saved run recomputes where it got to and what its evidence
  // says, rather than leaving the previous run's answers on screen.
  await refreshWorkflow();
  await refreshEvidence(id);
}

function download(blob, name) {
  const url = URL.createObjectURL(blob), a = document.createElement("a");
  a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function frameworkChanged() {
  const generic = $("framework").value === "openai-compatible-other";
  $("description-field").hidden = !generic; $("server-description").required = generic;
}

$("profile").addEventListener("change", profileDetail);
$("framework").addEventListener("change", frameworkChanged);
$("refresh-setup").addEventListener("click", () => action(refreshSetup));
$("new-run").addEventListener("click", () => { $("configure").scrollIntoView({behavior: "smooth"}); $("model").focus(); });
// Staged rather than pass/fail: "the server answered" and "this benchmark can
// run" are different claims, and the report says which one each stage made.
function renderServerCheck(report) {
  const state = { ok: "ok", failed: "fail", unknown: "pending", skipped: "pending" };
  $("server-check").replaceChildren(...report.stages.map((stage) => {
    const li = document.createElement("li");
    li.className = `status-item ${state[stage.state] || "pending"}`;
    const strong = document.createElement("strong"), span = document.createElement("span");
    strong.textContent = stage.title;
    span.textContent = stage.detail;
    li.append(strong, span);
    // The tokenizer stage answers four separate questions; a passing syntax
    // check must not be read as a loaded, matching tokenizer.
    (stage.checks || []).forEach((check) => {
      const line = document.createElement("span");
      line.className = "sub-check";
      line.textContent = `${TOKENIZER_CHECK_LABELS[check.id] || check.id}: ${check.state} — ${check.detail}`;
      li.append(line);
    });
    return li;
  }));
  // Offered by the server itself, so the identifier can be chosen rather than typed.
  const served = report.models || [];
  $("served-models").replaceChildren(...served.map((id) => option(id)));
  const failed = report.stages.filter((stage) => stage.state === "failed");
  $("connection-result").textContent = report.ready
    ? `Ready for the checks performed, from: ${report.checked_from.toLowerCase()}.`
    : failed.length
    ? `${failed.length} check${failed.length === 1 ? "" : "s"} did not pass. ${report.checked_from}.`
    : `Some checks could not be completed. ${report.checked_from}.`;
  $("connection-result").classList.toggle("error", failed.length > 0);
  $("server-check-unknowns").replaceChildren(...report.not_established.map((text) => {
    const li = document.createElement("li");
    li.textContent = text;
    return li;
  }));
  $("server-check-limits").hidden = false;
}


// --- Where you are, and what is in the way --------------------------------
// One request answers all of it: the six stages, and every check grouped by
// whether it would stop a run, a package or a submission. Three surfaces
// deciding for themselves whether a run is ready is three chances to disagree.
let workflow = null;

const GATE_IDS = ["run", "package", "submit"];
const CHECK_CLASS = {ok: "ok", blocked: "fail", attention: "pending", unknown: "pending"};
const STATE_WORD = {ok: "ready", blocked: "needs attention", attention: "worth checking",
                    unknown: "not established"};

function workflowRequest() {
  const data = Object.fromEntries(new FormData($("package-form")));
  const enriched = withProvenance(data);
  enriched.endpoint = $("endpoint").value;
  enriched.tokenizer = $("tokenizer").value;
  enriched.windows_bridge = $("windows-bridge").checked;
  enriched.model = $("package-model").value.trim() || $("model").value.trim();
  if (selected) enriched.job = selected;
  return enriched;
}

function renderCheck(item) {
  const li = document.createElement("li");
  li.className = `status-item ${CHECK_CLASS[item.state] || "pending"}`;
  const strong = document.createElement("strong");
  strong.textContent = item.label;
  li.append(strong);
  // What was looked at, and where the answer came from.
  const observed = document.createElement("span");
  observed.className = "sub-check";
  observed.textContent = item.observed
    ? `Seen: ${item.observed} — from ${item.source}`
    : `${STATE_WORD[item.state] || item.state} — ${item.source}`;
  li.append(observed);
  if (item.detail) {
    const detail = document.createElement("span");
    detail.textContent = item.detail;
    li.append(detail);
  }
  // A specific thing to do, rather than a restatement of the problem.
  if (item.recovery) {
    const recovery = document.createElement("span");
    recovery.className = "recovery";
    recovery.textContent = item.recovery;
    li.append(recovery);
  }
  return li;
}

function renderWorkflow(report) {
  workflow = report;
  $("preflight-local").textContent = report.local_report;
  for (const gate of GATE_IDS) {
    const items = report.checks.filter((item) => item.blocks === gate);
    $(`gate-${gate}`).replaceChildren(...items.map(renderCheck));
    $(`gate-note-${gate}`).textContent = report.gates[gate];
    const waiting = report.blocking[gate].length;
    $(`gate-count-${gate}`).textContent = waiting
      ? `${waiting} outstanding` : items.length ? "nothing outstanding" : "";
    $(`gate-count-${gate}`).className = `gate-count ${waiting ? "waiting" : "clear"}`;
  }
  // Checks that gate nothing get their own place. Listing them under a gate
  // would suggest they stand in its way, which is the opposite of what they say.
  const ungated = report.checks.filter((item) => item.blocks === null);
  $("gate-none-block").hidden = !ungated.length;
  $("gate-none").replaceChildren(...ungated.map(renderCheck));

  report.stages.forEach((stage) => {
    const tab = document.querySelector(`.step-tab[data-stage="${stage.id}"]`);
    if (!tab) return;
    tab.className = `step-tab stage-${stage.state}`;
    tab.setAttribute("aria-current", stage.state === "current" ? "step" : "false");
  });
  renderCatalogStatus(report.proposal);
  renderCompletion(report.completion);
}

async function refreshWorkflow() {
  renderWorkflow(await api("/api/workflow", workflowRequest()));
}

// Moving between stages reveals, it never resets: every field stays exactly as
// it was typed, so fixing something earlier costs nothing later.
document.querySelectorAll(".step-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    const target = $(tab.dataset.target);
    if (!target || target.hidden) {
      notice("That step is not available yet. The checks above say what it is waiting for.");
      return;
    }
    const open = target.closest("details");
    if (open) open.open = true;
    target.scrollIntoView({behavior: "smooth", block: "start"});
  });
});

// --- Catalogued, or proposed ----------------------------------------------
function renderCatalogStatus(proposal) {
  if (!proposal || !proposal.model) { $("catalog-status").hidden = true; return; }
  $("catalog-status").hidden = false;
  $("catalog-status-summary").textContent = proposal.summary;
  $("catalog-status").classList.toggle("error", Boolean(proposal.conflict));
  const rows = [
    ["Is this model in the catalog?", proposal.catalogued ? "Yes" : "No"],
    ["Does it need a proposal to be submitted?", proposal.required ? "Yes" : "No"],
  ];
  if (proposal.conflict) rows.push(["Conflict", proposal.conflict]);
  $("catalog-status-items").replaceChildren(...rows.map(([label, value]) => {
    const li = document.createElement("li");
    li.className = "status-item pending";
    const strong = document.createElement("strong"), span = document.createElement("span");
    strong.textContent = label; span.textContent = value;
    li.append(strong, span);
    return li;
  }));
  $("catalog-status-review").textContent = proposal.review_note;
  // Opened only when it is needed, and never closed over the contributor's work.
  if (proposal.required && $("model-proposal-section").hidden) {
    $("model-proposal-section").hidden = false;
  }
}

$("check-proposal").addEventListener("click", () => action(async () => {
  const data = workflowRequest();
  const status = await api("/api/model-proposal", data);
  $("proposal-status").hidden = false;
  $("proposal-status").classList.toggle("error", Boolean(status.error));
  $("proposal-status-summary").textContent = status.summary;
  $("proposal-missing").replaceChildren(...(status.missing || []).map((field) => {
    const li = document.createElement("li");
    li.className = "status-item fail";
    const strong = document.createElement("strong");
    strong.textContent = `Still needed: ${field.replace(/_/g, " ")}`;
    li.append(strong);
    return li;
  }));
  // What lands on this machine, and what a reviewer would be asked to decide.
  $("proposal-saved").textContent = status.document
    ? `Saved beside the evidence as ${status.saved_as} and uploaded with it. ${status.review_note}`
    : status.review_note;
  $("proposal-preview").hidden = !status.document;
  if (status.document) $("proposal-json").textContent = JSON.stringify(status.document, null, 2);
}));

// --- Six states, none of them assumed -------------------------------------
const COMPLETION_CLASS = {done: "ok", failed: "fail", blocked: "fail", pending: "pending"};

function renderCompletion(steps) {
  $("completion").hidden = !steps || !steps.length;
  if (!steps || !steps.length) return;
  $("completion-steps").replaceChildren(...steps.map((step) => {
    const li = document.createElement("li");
    li.className = `status-item ${COMPLETION_CLASS[step.state] || "pending"}`;
    const strong = document.createElement("strong"), span = document.createElement("span");
    strong.textContent = step.label;
    span.textContent = step.detail;
    li.append(strong, span);
    return li;
  }));
}

// --- What the evidence says ------------------------------------------------
async function refreshEvidence(jobId) {
  const summary = await api(`/api/runs/${jobId}/evidence`).catch(() => null);
  $("evidence-summary").hidden = !summary;
  if (!summary) return;
  $("evidence-qualifications").textContent = summary.qualifications.join(" ");
  $("evidence-sections").replaceChildren(...summary.sections.map((section) => {
    const block = document.createElement("section");
    const heading = document.createElement("h4");
    heading.textContent = section.title;
    const list = document.createElement("dl");
    list.className = "kv";
    section.rows.forEach((row) => {
      const dt = document.createElement("dt"), dd = document.createElement("dd");
      dt.textContent = row.label;
      dd.textContent = row.value;
      if (!row.recorded) dd.classList.add("unrecorded");
      // Where it came from, and what it still does not establish.
      if (row.source) {
        const source = document.createElement("span");
        source.className = "sub-check";
        source.textContent = row.source;
        dd.append(source);
      }
      if (row.note) {
        const note = document.createElement("span");
        note.className = "sub-check";
        note.textContent = row.note;
        dd.append(note);
      }
      list.append(dt, dd);
    });
    block.append(heading, list);
    return block;
  }));
}

$("test-connection").addEventListener("click", () => action(async () => {
  $("connection-result").textContent = "Checking the address, this environment, the server, its model list and one streaming request…";
  renderServerCheck(await api("/api/server-check", {
    endpoint: $("endpoint").value, model: $("model").value, tokenizer: $("tokenizer").value,
    windows_bridge: $("windows-bridge").checked,
  }));
  await refreshWorkflow();
}));
$("run-form").addEventListener("submit", (event) => {
  event.preventDefault(); action(async () => {
    const data = Object.fromEntries(new FormData(event.target)); data.device_confirmed = $("device-confirmed").checked;
    data.windows_bridge = $("windows-bridge").checked;
    const job = await api("/api/runs", data); jobs = await api("/api/runs");
    await selectRun(job.id); $("run-panel").scrollIntoView({behavior: "smooth"});
  });
});
// Cancels OmoBench's own runner process group only. The model server is
// started and owned by the contributor, and is never signalled from here.
$("cancel-button").addEventListener("click", () => action(async () => renderRun(await api(`/api/runs/${selected}/cancel`, {}))));
$("start-over-button").textContent = "Start another run";
$("start-over-button").addEventListener("click", () => { $("configure").scrollIntoView({behavior: "smooth"}); $("model").focus(); });
$("download-summary").addEventListener("click", () => action(async () => {
  const report = await api(`/api/runs/${selected}/local-report`);
  download(new Blob([JSON.stringify(report, null, 2) + "\n"], {type: "application/json"}), `${selected}-local-report.json`);
}));
$("check-requirements").addEventListener("click", () => action(async () => {
  const data = withProvenance(Object.fromEntries(new FormData($("package-form"))));
  renderRequirements(await api(`/api/runs/${selected}/requirements`, data));
}));
const TOKENIZER_CHECK_LABELS = {
  syntax: "Identifier accepted",
  files: "Files located",
  loaded: "Loaded offline",
  compatibility: "Matches the served weights",
};

$("check-tokenizer").addEventListener("click", () => action(async () => {
  const report = await api("/api/tokenizer-check", {tokenizer: $("tokenizer").value, model: $("model").value});
  $("tokenizer-result").replaceChildren(...(report.checks || []).map((check) => {
    const line = document.createElement("span");
    line.className = "sub-check";
    line.textContent = `${TOKENIZER_CHECK_LABELS[check.id] || check.id}: ${check.state} — ${check.detail}`;
    return line;
  }));
  if (!report.checks) $("tokenizer-result").textContent = report.message;
  $("tokenizer-result").classList.toggle("error", !report.ok);
}));
// --- The machine that served the model ---------------------------------
// Where inference happened is not always where this panel is running, and on a
// bridged run it definitely is not. None of this is filled in from the runner:
// what WSL can see of its Windows host says nothing about what served the
// model, so every field here is the contributor's own statement and is
// recorded as one.
const ACCELERATOR_ROLES = ["inference", "present-not-used", "unknown"];

function parseAccelerators(value) {
  return value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).map((line) => {
    const parts = line.split(",").map((part) => part.trim());
    if (parts.length < 2) throw new Error(`Each accelerator needs at least a vendor and a model: "${line}"`);
    const [vendor, model, memory, count, role] = parts;
    const entry = {vendor: vendor.toLowerCase(), model, count: count ? Number(count) : 1,
                   role: role || "unknown"};
    entry.memory_gb = memory ? Number(memory) : null;
    if (entry.memory_gb !== null && !(entry.memory_gb > 0)) throw new Error(`Accelerator memory must be a number of gigabytes: "${line}"`);
    if (!Number.isInteger(entry.count) || entry.count < 1) throw new Error(`Accelerator count must be a whole number: "${line}"`);
    if (!ACCELERATOR_ROLES.includes(entry.role)) throw new Error(`An accelerator's role is one of ${ACCELERATOR_ROLES.join(", ")}: "${line}"`);
    return entry;
  });
}

function parseSettings(value) {
  return value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).map((line) => {
    const index = line.indexOf("=");
    if (index < 1) throw new Error(`Each serving setting is "name = value": "${line}"`);
    return {name: line.slice(0, index).trim(), value: line.slice(index + 1).trim()};
  });
}

function servingDeclaration(data) {
  const host = {};
  if ($("serving-os").value.trim()) host.os = $("serving-os").value.trim();
  if ($("serving-cpu").value.trim()) host.cpu = $("serving-cpu").value.trim();
  if ($("serving-ram-gb").value) host.ram_gb = Number($("serving-ram-gb").value);
  // The engine is what executed the weights. The framework above is how
  // OmoBench classifies the server, and for an application that embeds an
  // engine the two are different things -- LM Studio is not llama.cpp.
  const engine = {name: $("serving-engine").value.trim() || data.framework,
                  version: data.framework_version};
  const serving = {host, accelerators: parseAccelerators($("serving-accelerators").value),
                   runtime: {engine, settings: parseSettings($("serving-settings").value)}};
  const placement = $("serving-placement").value;
  if (placement !== "unknown") {
    serving.placement = {mode: placement};
    const detail = $("serving-placement-detail").value.trim();
    if (detail) serving.placement.detail = detail;
  }
  if ($("serving-application").value.trim()) {
    serving.runtime.application = {name: $("serving-application").value.trim(),
                                   version: $("serving-application-version").value.trim()};
  }
  return serving;
}

function weightsDeclaration() {
  const raw = $("weights-manifest").value.trim();
  if (!raw) return null;
  let manifest;
  try { manifest = JSON.parse(raw); } catch { throw new Error("The weight manifest is not valid JSON. Paste what `omobench weights` printed, unchanged."); }
  return {kind: "local-files", manifest};
}

function proposalDeclaration() {
  if ($("model-proposal-section").hidden) return null;
  return {
    family: $("proposal-family").value.trim(),
    parameter_class: $("proposal-parameter-class").value.trim(),
    source: $("proposal-source").value.trim(),
    licence: $("proposal-licence").value.trim(),
    rationale: $("proposal-rationale").value.trim(),
    weights_publicly_downloadable: $("proposal-public").checked,
  };
}

// Everything the packager needs beyond the schema 1.0 fields, collected in one
// place so the two forms cannot disagree about what was declared.
function withProvenance(data) {
  const enriched = {...data};
  enriched.served_model_id = $("served-model-id").value.trim();
  enriched.serving = servingDeclaration(data);
  const weights = weightsDeclaration();
  if (weights) enriched.weights = weights;
  const proposal = proposalDeclaration();
  if (proposal) { enriched.proposal = proposal; enriched.catalog_status = "proposed"; }
  // A request for the probe the panel read itself, not a document from here.
  if (servingProbeRead) enriched.probe = true;
  delete enriched.serving_os; delete enriched.serving_cpu; delete enriched.serving_ram_gb;
  delete enriched.serving_placement; delete enriched.serving_placement_detail;
  delete enriched.serving_accelerators; delete enriched.serving_application;
  delete enriched.serving_engine;
  delete enriched.serving_application_version; delete enriched.serving_settings;
  delete enriched.weights_manifest; delete enriched.proposal_family;
  delete enriched.proposal_parameter_class; delete enriched.proposal_source;
  delete enriched.proposal_licence; delete enriched.proposal_rationale;
  return enriched;
}

// Asks the companion to read the Windows machine's hardware, where it lives.
// What comes back fills the fields; what it cannot know stays empty rather
// than being guessed at, and the page never sends a probe document of its own.
let servingProbeRead = false;
$("read-serving-hardware").addEventListener("click", () => action(async () => {
  $("serving-probe-result").textContent = "Asking the companion to read this machine's hardware…";
  const probe = await api("/api/serving-probe", {windows_bridge: $("windows-bridge").checked});
  servingProbeRead = true;
  if (probe.os) $("serving-os").value = probe.os;
  if (probe.cpu) $("serving-cpu").value = probe.cpu;
  if (probe.ram_gb) $("serving-ram-gb").value = String(probe.ram_gb);
  const lines = (probe.accelerators || []).map((device) =>
    // Memory is left out on purpose: see the note the probe returns.
    [device.vendor, device.model, "", String(device.count ?? 1), "unknown"].join(", "));
  if (lines.length) $("serving-accelerators").value = lines.join("\n");
  $("serving-probe-result").textContent = probe.note;
}));

$("suggest-model").addEventListener("click", () => action(async () => {
  const alias = $("served-model-id").value.trim() || $("model").value.trim();
  const report = await api("/api/model-suggestions", {served_model_id: alias});
  $("model-suggestions").hidden = false;
  $("model-suggestion-message").textContent = report.message;
  // Offered, never applied: a serving alias is not evidence of which weights
  // were loaded, so the choice stays the contributor's.
  $("model-suggestion-list").replaceChildren(...report.candidates.map((candidate) => {
    const li = document.createElement("li"), reason = document.createElement("span");
    const use = document.createElement("button");
    use.type = "button"; use.className = "secondary"; use.textContent = `Use ${candidate.id}`;
    use.addEventListener("click", () => {
      $("package-model").value = candidate.id;
      $("model-proposal-section").hidden = true;
    });
    li.textContent = candidate.id; reason.className = "sub-check";
    reason.textContent = candidate.reason; li.append(reason, use); return li;
  }));
  $("model-proposal-section").hidden = report.candidates.length > 0
    || report.catalog.includes($("package-model").value.trim());
}));

const PREFLIGHT_LABELS = {
  syntax: "Usable identifier",
  files: "Files where the loader looks",
  loaded: "Loaded by the runner's own interpreter",
  compatibility: "Matches the served weights",
};

function renderPreflight(report) {
  $("tokenizer-preflight").hidden = false;
  $("tokenizer-preflight").classList.toggle("error", !report.ok);
  $("tokenizer-preflight-message").textContent = report.message;
  $("tokenizer-preflight-checks").replaceChildren(...(report.checks || []).map((check) => {
    const li = document.createElement("li"), detail = document.createElement("span");
    li.textContent = `${PREFLIGHT_LABELS[check.id] || check.id}: ${check.state}`;
    detail.className = "sub-check"; detail.textContent = check.detail;
    li.append(detail); return li;
  }));
  $("tokenizer-suggestion").hidden = !report.suggestion;
  if (report.suggestion) {
    $("tokenizer-suggestion-reason").textContent = report.suggestion.reason;
    $("tokenizer-use-suggestion").onclick = () => { $("tokenizer").value = report.suggestion.source; };
  }
  // A download is a separate, named act with a visible source and destination.
  $("tokenizer-download").hidden = report.ok || report.kind === "invalid";
}

$("load-tokenizer").addEventListener("click", () => action(async () => {
  $("tokenizer-preflight").hidden = false;
  $("tokenizer-preflight-message").textContent = "Loading the tokenizer in the runner's interpreter. Closing the panel stops it.";
  $("tokenizer-preflight-checks").replaceChildren();
  renderPreflight(await api("/api/tokenizer-preflight", {
    tokenizer: $("tokenizer").value, model: $("model").value, network: "metadata",
  }));
  await refreshWorkflow();
}));

$("tokenizer-download-confirm").addEventListener("click", () => action(async () => {
  const source = $("tokenizer").value.trim() || $("model").value.trim();
  const plan = await api("/api/tokenizer-download-plan", {tokenizer: source});
  $("tokenizer-download-plan").textContent =
    `From ${plan.source} into ${plan.destination}. ${plan.downloads} ${plan.never_downloads}`;
  renderPreflight(await api("/api/tokenizer-preflight", {
    tokenizer: source, model: $("model").value, network: "download",
  }));
}));

$("package-form").addEventListener("submit", (event) => {
  event.preventDefault(); action(async () => {
    const data = withProvenance(Object.fromEntries(new FormData(event.target)));
    data.runtime_args = data.runtime_args.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    const currentJob = jobs.find((job) => job.id === selected);
    data.gpu_memory_gb = data.gpu_memory_gb && currentJob?.execution_device !== "cpu" ? Number(data.gpu_memory_gb) : null;
    if (data.framework !== "openai-compatible-other") data.server_description = "";
    preview = null; $("submission-preview").hidden = true; $("submit-confirmed").checked = false;
    renderRequirements(await api(`/api/runs/${selected}/requirements`, data));
    // Packaging is gated on what packaging needs, and nothing else. A model
    // awaiting catalog review, or a submitter nobody has typed yet, belongs to
    // the registry's gate: the bundle built from this evidence is just as
    // valid without them, and refusing to build it would throw the run away
    // over a decision somebody else has to make.
    if (requirements.blocks_packaging.length) {
      throw new Error("This evidence cannot be packaged yet: "
        + requirements.blocks_packaging.join("; ")
        + ". The checklist above says what each one needs. Your measurements are kept either way.");
    }
    renderRun(await api(`/api/runs/${selected}/package`, data));
    await refreshWorkflow();
    await refreshEvidence(selected);
  });
});
const community = new CommunitySubmission(api, () => {
  preview = null;
  $("submission-preview").hidden = true;
  $("submit-confirmed").checked = false;
  $("submit-button").disabled = true;
});

$("prepare-button").addEventListener("click", () => action(async () => {
  const reviewingJob = selected, ticket = community.ticket();
  const prepared = await api(`/api/runs/${reviewingJob}/prepare`, { mode: ticket.mode });
  if (selected !== reviewingJob || !community.current(ticket)) return;
  preview = prepared;
  $("repository").textContent = community.destination(preview);
  $("submission-files").replaceChildren(...preview.files.map((file) => {
    const li = document.createElement("li"), detail = document.createElement("span");
    li.textContent = `${file.path} · ${(file.bytes / 1024).toFixed(1)} KiB`; detail.textContent = `SHA-256 ${file.sha256}`; li.append(detail); return li;
  }));
  $("result-json").textContent = JSON.stringify(preview.result, null, 2);
  $("pr-description").textContent = preview.title + "\n\n" + preview.body;
  $("github-message").textContent = preview.message || "Creates a new branch and PR. Your local branch and files stay as they are. CI and review follow on GitHub.";
  $("submit-confirmed").checked = false; $("submission-preview").hidden = false;
}));
$("download-bundle").addEventListener("click", () => action(async () => download(await api(`/api/runs/${selected}/bundle`, undefined, true), `${selected}-submission.zip`)));
$("submit-confirmed").addEventListener("change", updateButtons);
$("submit-button").addEventListener("click", () => action(async () => {
  notice("Uploading the reviewed bundle and creating a pull request. This may take a minute…");
  renderRun(await api(`/api/runs/${selected}/submit`, community.payload(preview, $("submit-confirmed").checked)));
  notice("Submission available on GitHub. Follow its checks and maintainer review there."); $("submit-confirmed").checked = false;
}));

// Exactly one polling loop for the whole page: selecting another run changes
// what it asks for, it never starts a second loop.
async function poll() {
  try {
    if (!pending) {
      jobs = await api("/api/runs"); renderJobs();
      if (selected) {
        const id = selected;
        const job = await api(`/api/runs/${id}`);
        const telemetry = await api(`/api/runs/${id}/telemetry`).catch(() => null);
        if (id === selected) renderRun(job, telemetry);
      }
    }
  } catch (error) { notice(`Local panel connection: ${error.message} Keep omobench ui running.`, true); }
  finally { setTimeout(poll, 1500); }
}

async function init() {
  try {
    if (!token) throw new Error("Open the full connection link printed by omobench ui, including its #token part.");
    community.refresh();
    await refreshSetup(); jobs = await api("/api/runs"); renderJobs();
    if (jobs.length) await selectRun(jobs[0].id);
    else await refreshWorkflow();
    poll();
  } catch (error) { notice(error.message, true); $("runner-status").hidden = true; }
}
updateButtons(); init();
