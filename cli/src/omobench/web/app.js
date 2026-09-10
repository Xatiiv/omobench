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
  $("cancel-run").disabled = pending;
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
  $("profile-detail").textContent = `${spec.input_tokens.toLocaleString()} input tokens · ${spec.output_tokens} requested output tokens · ${load} · up to ${spec.max_requests} measured requests / ${spec.max_duration_seconds / 60} min, plus warm-up and cool-down`;
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

function metric(label, value, unit = "") {
  const box = document.createElement("dl"), dt = document.createElement("dt"), dd = document.createElement("dd"), suffix = document.createElement("span");
  box.className = "metric"; dt.textContent = label;
  dd.textContent = typeof value === "number" ? value.toLocaleString(undefined, {maximumFractionDigits: 2}) : value;
  suffix.textContent = unit; dd.append(suffix); box.append(dt, dd); return box;
}

function renderRun(job) {
  if (job.id !== selected) return;
  $("run-panel").hidden = false;
  $("run-title").textContent = job.model;
  $("run-status").textContent = `${job.execution_device.toUpperCase()} · ${job.status}`;
  const duration = Math.max(0, Math.floor((new Date(job.finished_at || Date.now()) - new Date(job.created_at)) / 1000));
  $("run-meta").textContent = `${job.profile} · ${Math.floor(duration / 60)}m ${duration % 60}s · ${new Date(job.created_at).toLocaleString()}`;
  $("run-error").hidden = !job.error; $("run-error").textContent = job.error || "";
  const log = $("log"), atEnd = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  log.textContent = job.log || "Waiting for the runner…";
  if (atEnd) log.scrollTop = log.scrollHeight;
  $("run-path").textContent = job.run_path ? `Evidence: ${job.run_path}` : "";
  $("cancel-run").hidden = !["running", "cancelling"].includes(job.status);
  $("cancel-run").textContent = job.status === "cancelling" ? "Cancelling…" : "Cancel benchmark";
  $("metrics").hidden = !job.summary;
  $("download-summary").hidden = !job.summary;
  $("local-note").hidden = !job.summary || job.execution_device !== "cpu";
  if (job.summary) {
    const r = job.summary.results;
    $("metrics").replaceChildren(metric("Time to first token · p50", r.latency_ms.ttft.p50, "ms"),
      metric("Time to first token · p95", r.latency_ms.ttft.p95, "ms"), metric("Output tokens / second", r.output_tokens_per_second),
      metric("Total tokens / second", r.total_tokens_per_second), metric("Successful / attempted", `${r.requests.succeeded} / ${r.requests.attempted}`),
      metric("Error rate", r.error_rate_percent, "%"));
  }
  $("package-panel").hidden = !job.summary || job.execution_device !== "gpu";
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

async function selectRun(id) {
  selected = id; preview = null;
  $("submission-preview").hidden = true; $("submit-confirmed").checked = false;
  const job = await api(`/api/runs/${id}`);
  $("package-form").reset(); $("package-model").value = job.model; $("framework").value = "llama.cpp"; frameworkChanged();
  renderRun(job); renderJobs();
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
$("test-connection").addEventListener("click", () => action(async () => {
  $("connection-result").textContent = "Sending a small streaming request…";
  try { const result = await api("/api/connection", {endpoint: $("endpoint").value, model: $("model").value}); $("connection-result").textContent = result.message; }
  catch (error) { $("connection-result").textContent = "Connection test failed."; throw error; }
}));
$("run-form").addEventListener("submit", (event) => {
  event.preventDefault(); action(async () => {
    const data = Object.fromEntries(new FormData(event.target)); data.device_confirmed = $("device-confirmed").checked;
    const job = await api("/api/runs", data); jobs = await api("/api/runs");
    await selectRun(job.id); $("run-panel").scrollIntoView({behavior: "smooth"});
  });
});
$("cancel-run").addEventListener("click", () => action(async () => renderRun(await api(`/api/runs/${selected}/cancel`, {}))));
$("download-summary").addEventListener("click", () => action(async () => {
  const job = await api(`/api/runs/${selected}`);
  const summary = {kind: "omobench-local-summary", execution_device: job.execution_device, model: job.model, profile: job.profile, ...job.summary};
  download(new Blob([JSON.stringify(summary, null, 2) + "\n"], {type: "application/json"}), `${job.id}-local-summary.json`);
}));
$("package-form").addEventListener("submit", (event) => {
  event.preventDefault(); action(async () => {
    const data = Object.fromEntries(new FormData(event.target));
    data.runtime_args = data.runtime_args.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    data.gpu_memory_gb = data.gpu_memory_gb ? Number(data.gpu_memory_gb) : null;
    if (data.framework !== "openai-compatible-other") data.server_description = "";
    preview = null; $("submission-preview").hidden = true; $("submit-confirmed").checked = false;
    renderRun(await api(`/api/runs/${selected}/package`, data));
  });
});
$("prepare-button").addEventListener("click", () => action(async () => {
  preview = await api(`/api/runs/${selected}/prepare`, {});
  $("repository").textContent = preview.repository ? `Destination: ${preview.repository} · base: ${preview.base_branch}` : "Download the validated bundle to submit manually.";
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
  renderRun(await api(`/api/runs/${selected}/submit`, {repository: preview.repository, bundle_digest: preview.bundle_digest, confirmed: $("submit-confirmed").checked}));
  notice("Pull request created. Review its checks on GitHub before merging."); $("submit-confirmed").checked = false;
}));

async function poll() {
  try {
    if (!pending) {
      jobs = await api("/api/runs"); renderJobs();
      if (selected) renderRun(await api(`/api/runs/${selected}`));
    }
  } catch (error) { notice(`Local panel connection: ${error.message} Keep omobench ui running.`, true); }
  finally { setTimeout(poll, 1500); }
}

async function init() {
  try {
    if (!token) throw new Error("Open the full connection link printed by omobench ui, including its #token part.");
    await refreshSetup(); jobs = await api("/api/runs"); renderJobs();
    if (jobs.length) await selectRun(jobs[0].id);
    poll();
  } catch (error) { notice(error.message, true); $("runner-status").hidden = true; }
}
updateButtons(); init();
