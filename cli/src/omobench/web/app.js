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
  $("profile").replaceChildren(...Object.keys(setup.profiles).map((id) => option(id)));
  if (currentProfile) $("profile").value = currentProfile;
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

$("test-connection").addEventListener("click", () => action(async () => {
  $("connection-result").textContent = "Checking the address, this environment, the server, its model list and one streaming request…";
  renderServerCheck(await api("/api/server-check", {
    endpoint: $("endpoint").value, model: $("model").value, tokenizer: $("tokenizer").value,
  }));
}));
$("run-form").addEventListener("submit", (event) => {
  event.preventDefault(); action(async () => {
    const data = Object.fromEntries(new FormData(event.target)); data.device_confirmed = $("device-confirmed").checked;
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
  const data = Object.fromEntries(new FormData($("package-form")));
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
$("package-form").addEventListener("submit", (event) => {
  event.preventDefault(); action(async () => {
    const data = Object.fromEntries(new FormData(event.target));
    data.runtime_args = data.runtime_args.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    const currentJob = jobs.find((job) => job.id === selected);
    data.gpu_memory_gb = data.gpu_memory_gb && currentJob?.execution_device !== "cpu" ? Number(data.gpu_memory_gb) : null;
    if (data.framework !== "openai-compatible-other") data.server_description = "";
    preview = null; $("submission-preview").hidden = true; $("submit-confirmed").checked = false;
    renderRequirements(await api(`/api/runs/${selected}/requirements`, data));
    if (!requirements.ready) {
      throw new Error("This run is not ready for a community submission. The checklist above lists every missing requirement. Your measurements are kept either way.");
    }
    renderRun(await api(`/api/runs/${selected}/package`, data));
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
    poll();
  } catch (error) { notice(error.message, true); $("runner-status").hidden = true; }
}
updateButtons(); init();
