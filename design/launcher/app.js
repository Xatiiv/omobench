"use strict";

const data = JSON.parse(document.getElementById("preview-data").textContent);
const $ = (id) => document.getElementById(id);
const labels = {"chat-small-v1": ["Chat responsiveness", "A short conversation. Time to first token and generation speed.", "◷"],
  "chat-long-v1": ["Long-context chat", "A longer prompt. Explore how context affects response time.", "☷"],
  "throughput-v1": ["Serving throughput", "Concurrent conversations. Measure sustained serving capacity.", "▥"]};
let profile = "chat-small-v1";
let device = "cpu";
let folder = "D:\\OmoBench";
let timer = null;
let started = 0;
let stage = 0;

function show(view) {
  if (view !== "running") { clearInterval(timer); timer = null; }
  document.querySelectorAll(".view").forEach((section) => { section.hidden = section.id !== view; });
  document.querySelectorAll("[data-view]").forEach((button) => {
    const active = button.dataset.view === view;
    button.classList.toggle("selected", active);
    if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
  });
  $("breadcrumb").textContent = "Workspace / " + ({benchmarks: "Benchmarks", setup: "Prepare", running: "Run preview", results: "Results", settings: "Storage & setup"}[view]);
  window.scrollTo(0, 0);
}

function syncFolder(value) {
  folder = value.trim() || "D:\\OmoBench";
  ["folder-inline", "folder-setup", "folder-settings"].forEach((id) => { $(id).value = folder; });
}

function syncSelection() {
  const spec = data.profiles[profile];
  const minutes = spec.max_duration_seconds / 60;
  $("selected-test").textContent = labels[profile][0];
  $("duration").textContent = `${minutes} minutes`;
  $("running-limit").textContent = `${minutes} minutes`;
  $("setup-model").textContent = $("model").value;
  $("result-model").textContent = $("model").value;
  $("result-profile").textContent = profile;
  $("server-model").placeholder = $("model").value;
  const model = data.models.find((item) => item.id === $("model").value);
  $("model-note").textContent = model ? `${model.parameter_class ? model.parameter_class + " parameters" : "Parameter class not listed"} · ${model.licence}` : "";
  document.querySelectorAll(".profile-card").forEach((card) => {
    const active = card.dataset.profile === profile;
    card.classList.toggle("active", active);
    card.setAttribute("aria-pressed", String(active));
  });
  $("cli-command").textContent = `omobench run \\\n  --endpoint http://127.0.0.1:8092 \\\n  --model ${$("model").value} \\\n  --profile ${profile} \\\n  --execute`;
}

Object.entries(data.profiles).forEach(([id, spec]) => {
  const card = document.createElement("button");
  card.className = "profile-card";
  card.dataset.profile = id;
  const icon = document.createElement("span"); icon.className = "profile-icon"; icon.textContent = labels[id][2]; icon.setAttribute("aria-hidden", "true");
  const copy = document.createElement("span"); copy.className = "profile-copy";
  const title = document.createElement("strong"); title.textContent = labels[id][0];
  const description = document.createElement("p"); description.textContent = labels[id][1];
  const detail = document.createElement("small");
  const streams = spec.profile.includes("streams=16") ? 16 : 1;
  detail.textContent = `${spec.input_tokens.toLocaleString()} input / ${spec.output_tokens} requested output tokens · ${streams} stream${streams === 1 ? "" : "s"} · ${id}`;
  const radio = document.createElement("span"); radio.className = "radio-mark"; radio.setAttribute("aria-hidden", "true");
  copy.append(title, description, detail); card.append(icon, copy, radio);
  card.addEventListener("click", () => { profile = id; syncSelection(); });
  $("profile-list").append(card);
});

data.models.forEach((model) => {
  const option = document.createElement("option"); option.value = model.id; option.textContent = model.id;
  $("model").append(option);
});
$("model").addEventListener("change", syncSelection);
document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => show(button.dataset.view)));
document.querySelectorAll("[data-back]").forEach((button) => button.addEventListener("click", () => show(button.dataset.back)));
document.querySelector(".brand").addEventListener("click", (event) => { event.preventDefault(); show("benchmarks"); });
document.querySelectorAll("[data-device]").forEach((button) => button.addEventListener("click", () => {
  device = button.dataset.device;
  document.querySelectorAll("[data-device]").forEach((item) => {
    item.classList.toggle("active", item.dataset.device === device);
    item.setAttribute("aria-pressed", String(item.dataset.device === device));
  });
  $("hardware-name").textContent = device === "cpu" ? "Your processor" : "GPU compatibility";
  $("hardware-detail").textContent = device === "cpu" ? "Detected during application setup" : "Requires backend qualification";
  $("device-note").textContent = device === "cpu" ? "CPU inference measurements can be saved locally." : "Managed GPU execution is not qualified yet. Select CPU to explore the setup preview.";
  $("prepare-button").disabled = device !== "cpu";
}));

$("prepare-button").addEventListener("click", () => { syncFolder($("folder-inline").value); syncSelection(); show("setup"); });
$("save-settings").addEventListener("click", () => {
  syncFolder($("folder-settings").value);
  $("settings-feedback").textContent = "Storage choice updated in this preview. No directory was created.";
  $("settings-feedback").hidden = false;
});
$("advanced-button").addEventListener("click", () => {
  $("advanced-note").textContent = "Connection-check layout preview. No request was sent to this server.";
});
$("cli-button").addEventListener("click", () => { syncSelection(); $("cli-dialog").showModal(); });

const stages = [
  ["Checking local files", "Reusing verified downloads where possible."],
  ["Loading your model", "The selected runtime prepares the model for CPU inference."],
  ["Running the fixed workload", "A real run will use the existing profile, token counts, and stopping rules."],
  ["Saving measurements", "The original runner output and reproduction details stay together."]
];
function renderStage() {
  $("stage-title").textContent = stages[stage][0];
  $("stage-description").textContent = stages[stage][1];
  document.querySelectorAll("#stage-list li").forEach((item, index) => {
    item.classList.toggle("current", index === stage); item.classList.toggle("done", index < stage);
  });
  $("example-log").textContent = stages.slice(0, stage + 1).map((item) => `[preview] ${item[0]}`).join("\n");
}
$("start-preview").addEventListener("click", () => {
  syncFolder($("folder-setup").value);
  show("running"); stage = 0; started = Date.now(); $("elapsed").textContent = "00:00";
  $("running-title").textContent = "A run you can follow.";
  $("running-model").textContent = `${$("model").value} · CPU · ${profile}`;
  renderStage();
  timer = setInterval(() => {
    const elapsed = Math.floor((Date.now() - started) / 1000);
    $("elapsed").textContent = `${Math.floor(elapsed / 60).toString().padStart(2, "0")}:${(elapsed % 60).toString().padStart(2, "0")}`;
    stage = Math.min(3, Math.floor(elapsed / 3)); renderStage();
  }, 1000);
});
$("show-results").addEventListener("click", () => show("results"));
$("cancel-preview").addEventListener("click", () => show("benchmarks"));
$("another-preview").addEventListener("click", () => show("benchmarks"));
syncSelection();
