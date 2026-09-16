"use strict";

const $ = (id) => document.getElementById(id);
const ACTIVE_SETUP = new Set(["preparing", "cancelling"]);
const ACTIVE_RUN = new Set(["starting_server", "waiting_ready", "running_benchmark", "cancelling"]);

let recipes = [];
let selected = null;
let storageDir = "";
let reviewReport = null;
let setupJobId = null;
let runJobId = null;
let benchmarkJobId = null;
let timers = [];

const incoming = new URLSearchParams(location.hash.slice(1)).get("token");
if (incoming) {
  sessionStorage.setItem("omobench-token", incoming);
  history.replaceState(null, "", location.pathname);
}
const token = sessionStorage.getItem("omobench-token") || "";

function notice(message, error = false) {
  $("notice").textContent = message;
  $("notice").classList.toggle("error", error);
  $("notice").hidden = !message;
}

async function api(path, data, binary = false) {
  const headers = { "X-OmoBench-Token": token };
  const options = { headers };
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

function formatBytes(value) {
  if (value === null || value === undefined) return "unknown";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let size = value, index = 0;
  while (size >= 1024 && index < units.length - 1) { size /= 1024; index += 1; }
  return `${size.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function download(blob, name) {
  const url = URL.createObjectURL(blob), a = document.createElement("a");
  a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function clearTimers() { timers.forEach(clearTimeout); timers = []; }

// -- Stage: platform ---------------------------------------------------------

async function checkPlatform() {
  const setup = await api("/api/setup");
  $("platform-status").textContent = setup.runner_message;
  $("platform-status").classList.toggle("error", !setup.can_execute);
}

// -- Stage 1/2: recipes -------------------------------------------------------

async function loadRecipes() {
  const data = await api("/api/managed/recipes");
  recipes = data.recipes;
  const list = $("recipe-list");
  list.replaceChildren();
  recipes.forEach((recipe) => {
    const card = document.createElement("div");
    card.className = "recipe-card";
    card.tabIndex = 0;
    card.innerHTML = `<h3>${recipe.title}</h3><p class="hint">Model ${recipe.model_id} (${recipe.quantization}) · profile ${recipe.benchmark_profile} · ${recipe.execution_device.toUpperCase()} only</p>`;
    card.addEventListener("click", () => selectRecipe(recipe));
    list.append(card);
  });
  if (data.default_storage_dir) {
    $("storage-dir").value = data.default_storage_dir;
  }
  if (recipes.length === 1) selectRecipe(recipes[0]);
}

function selectRecipe(recipe) {
  selected = recipe;
  document.querySelectorAll(".recipe-card").forEach((card, index) => card.classList.toggle("selected", recipes[index] === recipe));
  $("stage-2").hidden = false;
  const dl = $("recipe-detail");
  dl.replaceChildren();
  const facts = [
    ["Model", recipe.model_id], ["Quantization", recipe.quantization],
    ["Benchmark profile", recipe.benchmark_profile], ["Execution device", recipe.execution_device.toUpperCase() + " only"],
    ["Supported environment", recipe.supported_environment],
  ];
  facts.forEach(([label, value]) => {
    const dt = document.createElement("dt"), dd = document.createElement("dd");
    dt.textContent = label; dd.textContent = value; dl.append(dt, dd);
  });
  $("stage-3").hidden = false;
}

// -- Stage 3/4: storage + review ---------------------------------------------

async function doReview() {
  if (!selected) throw new Error("Select a recipe first.");
  storageDir = $("storage-dir").value.trim();
  if (!storageDir) throw new Error("Enter a storage folder.");
  reviewReport = await api("/api/managed/review", { recipe: selected.id, storage_dir: storageDir });
  renderReview(reviewReport);
  $("stage-4").hidden = false;
  $("stage-5").hidden = false;
}

function renderReview(report) {
  const container = $("review-report");
  container.replaceChildren();

  const platform = document.createElement("p");
  platform.className = report.platform.ok ? "notice" : "notice error";
  platform.textContent = report.platform.message;
  container.append(platform);

  const space = document.createElement("p");
  space.className = "muted";
  space.textContent = `Storage folder: ${report.storage_dir} (${report.storage_dir_exists ? "exists" : "will be created"}). `
    + `Free space: ${formatBytes(report.free_space_bytes)}; required: ${formatBytes(report.required_bytes)}`
    + (report.has_enough_space === false ? " — NOT ENOUGH FREE SPACE." : report.has_enough_space === null ? "." : " — enough space.");
  container.append(space);

  report.files.forEach((file) => {
    const card = document.createElement("div");
    card.className = "file-card";
    card.innerHTML = `<strong>${file.kind.toUpperCase()}</strong> · ${file.filename} · ${formatBytes(file.size_bytes)} · licence ${file.licence}
      <div class="path">Repository: ${file.repository} @ ${file.revision}</div>
      <div class="path">SHA-256: ${file.sha256}</div>
      <div class="path">${file.url}</div>`;
    container.append(card);
  });

  if (report.already_installed.installed) {
    const installed = document.createElement("p");
    installed.className = report.already_installed.valid ? "notice" : "notice error";
    installed.textContent = report.already_installed.valid
      ? "Already installed under this storage folder and re-verified successfully. You can skip preparation and go to Verify readiness."
      : `Already installed, but re-verification failed: ${report.already_installed.error}`;
    container.append(installed);
    if (report.already_installed.valid) { $("stage-7").hidden = false; }
  }
}

// -- Stage 5/6: prepare (download + install) ---------------------------------

async function doPrepare() {
  const job = await api("/api/managed/setup-jobs", { recipe: selected.id, storage_dir: storageDir, confirmed: true });
  setupJobId = job.id;
  $("stage-6").hidden = false;
  renderSetupJob(job);
  pollSetupJob();
}

function renderSetupJob(job) {
  $("setup-status").textContent = job.status;
  $("cancel-setup").hidden = !ACTIVE_SETUP.has(job.status);
  $("setup-error").hidden = !job.error;
  $("setup-error").textContent = job.error || "";
  const container = $("download-progress");
  container.replaceChildren();
  Object.entries(job.progress || {}).forEach(([label, info]) => {
    const row = document.createElement("div");
    row.className = "progress-row";
    const percent = info.total ? Math.min(100, (100 * info.done) / info.total) : 0;
    row.innerHTML = `<span>${label}: ${formatBytes(info.done)} / ${formatBytes(info.total)}</span>
      <div class="progress-bar"><span style="width:${percent}%"></span></div>`;
    container.append(row);
  });
  if (job.status === "completed") { $("stage-7").hidden = false; }
}

function pollSetupJob() {
  clearTimers();
  const tick = async () => {
    if (!setupJobId) return;
    try {
      const job = await api(`/api/managed/setup-jobs/${setupJobId}`);
      renderSetupJob(job);
      if (ACTIVE_SETUP.has(job.status)) timers.push(setTimeout(tick, 1000));
    } catch (error) { notice(error.message, true); }
  };
  tick();
}

// -- Stage 7: smoke -----------------------------------------------------------

async function doSmoke() {
  const job = await api("/api/managed/runs", { recipe: selected.id, storage_dir: storageDir, mode: "smoke" });
  pollSmoke(job.id);
}

function pollSmoke(jobId) {
  const tick = async () => {
    const job = await api(`/api/managed/runs/${jobId}`);
    $("smoke-result").hidden = false;
    if (ACTIVE_RUN.has(job.status)) {
      $("smoke-result").textContent = `Checking readiness… (${job.status})`;
      timers.push(setTimeout(tick, 1000));
      return;
    }
    $("smoke-result").classList.toggle("error", job.status !== "completed");
    $("smoke-result").textContent = job.status === "completed"
      ? job.result.message
      : (job.error || "Readiness check failed.");
    if (job.status === "completed") $("stage-8").hidden = false;
  };
  tick();
}

// -- Stage 8/9: execute --------------------------------------------------------

async function doExecute() {
  $("execute-button").disabled = true;
  const job = await api("/api/managed/runs", { recipe: selected.id, storage_dir: storageDir, mode: "execute" });
  runJobId = job.id;
  benchmarkJobId = null;
  $("run-status").textContent = job.status;
  $("cancel-run").hidden = false;
  pollRun();
}

function pollRun() {
  const tick = async () => {
    if (!runJobId) return;
    const job = await api(`/api/managed/runs/${runJobId}`);
    $("run-status").textContent = job.status;
    $("run-error").hidden = !job.error;
    $("run-error").textContent = job.error || "";
    if (job.server_log) $("server-log").textContent = job.server_log;
    if (job.benchmark_job_id && job.benchmark_job_id !== benchmarkJobId) {
      benchmarkJobId = job.benchmark_job_id;
    }
    if (benchmarkJobId) {
      await pollBenchmarkOnce();
    }
    if (ACTIVE_RUN.has(job.status)) {
      timers.push(setTimeout(tick, 1000));
    } else {
      $("cancel-run").hidden = true;
      $("execute-button").disabled = false;
    }
  };
  tick();
}

async function pollBenchmarkOnce() {
  const detail = await api(`/api/runs/${benchmarkJobId}`);
  $("benchmark-log").textContent = detail.log || "Waiting for the runner…";
  if (detail.summary) {
    renderMetrics(detail.summary);
    $("stage-9").hidden = false;
  }
}

function metric(label, value, unit = "") {
  const box = document.createElement("dl"), dt = document.createElement("dt"), dd = document.createElement("dd"), suffix = document.createElement("span");
  box.className = "metric"; dt.textContent = label;
  dd.textContent = typeof value === "number" ? value.toLocaleString(undefined, { maximumFractionDigits: 2 }) : value;
  suffix.textContent = unit; dd.append(suffix); box.append(dt, dd); return box;
}

function renderMetrics(summary) {
  const r = summary.results;
  $("guided-metrics").hidden = false;
  $("guided-metrics").replaceChildren(
    metric("Time to first token · p50", r.latency_ms.ttft.p50, "ms"),
    metric("Time to first token · p95", r.latency_ms.ttft.p95, "ms"),
    metric("Output tokens / second", r.output_tokens_per_second),
    metric("Total tokens / second", r.total_tokens_per_second),
    metric("Successful / attempted", `${r.requests.succeeded} / ${r.requests.attempted}`),
    metric("Error rate", r.error_rate_percent, "%"),
  );
  $("guided-download-summary").hidden = false;
  $("guided-download-summary").onclick = () => {
    const blob = new Blob(
      [JSON.stringify({ kind: "omobench-local-summary", execution_device: "cpu", model: selected.model_id, profile: selected.benchmark_profile, ...summary }, null, 2) + "\n"],
      { type: "application/json" },
    );
    download(blob, `${runJobId}-local-summary.json`);
  };
}

async function cancelRun() {
  if (benchmarkJobId) {
    await api(`/api/runs/${benchmarkJobId}/cancel`, {});
  } else if (runJobId) {
    await api(`/api/managed/runs/${runJobId}/cancel`, {});
  }
}

// -- Wiring --------------------------------------------------------------------

function action(work) {
  return async () => {
    notice("");
    try { await work(); }
    catch (error) { notice(error.message, true); }
  };
}

$("review-button").addEventListener("click", action(doReview));
$("confirm-download").addEventListener("change", () => { $("prepare-button").disabled = !$("confirm-download").checked; });
$("prepare-button").addEventListener("click", action(doPrepare));
$("cancel-setup").addEventListener("click", action(async () => api(`/api/managed/setup-jobs/${setupJobId}/cancel`, {})));
$("smoke-button").addEventListener("click", action(doSmoke));
$("execute-button").addEventListener("click", action(doExecute));
$("cancel-run").addEventListener("click", action(cancelRun));

async function init() {
  try {
    if (!token) throw new Error("Open the full connection link printed by omobench ui, including its #token part.");
    await checkPlatform();
    await loadRecipes();
  } catch (error) { notice(error.message, true); }
}
init();
