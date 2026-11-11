// Loads the real cli/src/omobench/web/app.js in a VM context with the shared
// DOM stand-in and drives its serving-provenance form directly, so the
// declaration the panel builds is checked where CI can see it.
//
// Run by cli/tests/test_provenance_form.py, which asserts on the JSON printed.
import { readFileSync } from "node:fs";
import { createContext, runInContext } from "node:vm";
import { makeDocument } from "./dom_stub.mjs";

const appPath = process.argv[2];

const document = makeDocument();
const get = (id) => document.getElementById(id);

get("package-form").formValues = {};
class HarnessFormData {
  constructor(form) { this.entries = Object.entries(form.formValues || {}); }
  [Symbol.iterator]() { return this.entries[Symbol.iterator](); }
}

// Synthetic throughout: an LM Studio alias, an AMD desktop that does not exist.
const CATALOG = ["Qwen/Qwen3-4B", "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B"];
const requests = [];
const responses = {
  "/api/setup": {
    environment: { cpu: "Fixture CPU", ram_gb: 32, gpus: [], operating_system: "Fixture Linux" },
    profiles: { "chat-small-v1": { profile: "synchronous", input_tokens: 1024, output_tokens: 256, max_requests: 50, max_duration_seconds: 900 } },
    frameworks: ["lmstudio", "llama.cpp", "vllm", "openai-compatible-other"],
    catalog: CATALOG, can_execute: true, can_validate: true, runner_message: "ready",
  },
  "/api/runs": [],
  "/api/github/session": { available: false, signed_in: false },
};

const context = createContext({
  document, console, URLSearchParams, Date, Math, JSON, Number, Set, Map, Object,
  Array, String, Boolean, Promise, Error, isNaN, parseInt, parseFloat,
  FormData: HarnessFormData, Blob: class {},
  URL: { createObjectURL: () => "blob:fixture", revokeObjectURL: () => {} },
  setTimeout: () => 0,
  clearTimeout: () => {},
  location: { hash: "", pathname: "/advanced", href: "http://127.0.0.1:8799/advanced" },
  history: { replaceState: () => {} },
  sessionStorage: { getItem: () => "test-token", setItem: () => {}, removeItem: () => {} },
  fetch: async (path, options) => {
    const body = options?.body ? JSON.parse(options.body) : null;
    requests.push({ path, body });
    const match = Object.keys(responses).find((key) => path.startsWith(key));
    return {
      ok: true, status: 200,
      json: async () => (typeof responses[match] === "function"
        ? responses[match](body) : responses[match] ?? {}),
      blob: async () => ({}),
    };
  },
});
context.globalThis = context;
context.self = context;

const source = readFileSync(appPath, "utf8")
  // The page imports its shared dashboard component; the stand-in supplies a
  // no-op so this harness exercises the provenance form and nothing else.
  .replace(/^import .*$/gm, "")
  .replace(/\bBenchmarkDashboard\b/g, "HarnessDashboard");
context.HarnessDashboard = class {
  constructor() {}
  mount() {} update() {} reset() {} appendLog() {} setStages() {}
};
// The shared workflow view is the real one: this harness is about the form
// the page builds, and the view only renders what the panel is told.
context.WorkflowView = class {
  constructor() {}
  render() {} renderGates() {} renderStages() {} renderCompletion() {} renderEvidence() {}
};
context.CommunitySubmission = class {
  constructor() {} refresh() {} ticket() { return { mode: "community" }; }
  current() { return true; } destination() { return ""; } payload() { return {}; }
};
// The managed-setup component is the real one, loaded the way the page loads
// it: before app.js, which constructs it at module scope. Defining the class
// costs nothing; it talks to no API until the panel selects a workload.
runInContext(readFileSync(appPath.replace(/app\.js$/, "managed-setup.js"), "utf8"), context);
runInContext(source, context, { filename: appPath });

function fill(values) {
  for (const [id, value] of Object.entries(values)) {
    const element = get(id);
    if (typeof value === "boolean") element.checked = value;
    else element.value = value;
  }
}

const states = {};

// A bridged AMD desktop, declared in full.
fill({
  "served-model-id": "deepseek-r1-distill-qwen-14b",
  "serving-os": "Windows 11 Pro 10.0.26200",
  "serving-cpu": "AMD Ryzen 7 9800X3D",
  "serving-ram-gb": "64",
  "serving-placement": "gpu-only",
  "serving-placement-detail": "",
  "serving-accelerators": "amd, AMD Radeon RX 9070, 16, 1, inference",
  "serving-engine": "llama.cpp",
  "serving-application": "LM Studio",
  "serving-application-version": "0.3.31",
  "serving-settings": "context_length = 8192\ngpu_layers = 49",
  "weights-manifest": "",
});
get("model-proposal-section").hidden = true;
get("package-form").formValues = {
  model: "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B", model_revision: "a".repeat(40),
  framework: "lmstudio", framework_version: "b10621", submitter: "fixture-user",
  runtime_args: "",
};
states.bridged = context.withProvenance(
  Object.fromEntries(new HarnessFormData(get("package-form"))));

// A card that was installed but idle, and a split placement.
fill({
  "serving-accelerators":
    "amd, AMD Radeon RX 9070, 16, 1, inference\nintel, Intel Arc A770, 16, 1, present-not-used",
  "serving-placement": "cpu-gpu-offload",
  "serving-placement-detail": "32 of 49 layers on GPU",
});
states.mixed = context.withProvenance(
  Object.fromEntries(new HarnessFormData(get("package-form"))));

// Nothing declared about the serving side at all.
fill({
  "served-model-id": "", "serving-os": "", "serving-cpu": "", "serving-ram-gb": "",
  "serving-placement": "unknown", "serving-placement-detail": "",
  "serving-accelerators": "", "serving-engine": "", "serving-application": "",
  "serving-application-version": "", "serving-settings": "",
});
states.empty = context.withProvenance(
  Object.fromEntries(new HarnessFormData(get("package-form"))));

// A proposal, once the section is open.
get("model-proposal-section").hidden = false;
fill({
  "proposal-family": "DeepSeek", "proposal-parameter-class": "14B",
  "proposal-source": "https://huggingface.co/deepseek-ai/DeepSeek-R1-Distill-Qwen-99B",
  "proposal-licence": "MIT", "proposal-rationale": "Measured on an AMD desktop.",
  "proposal-public": true,
});
states.proposed = context.withProvenance(
  Object.fromEntries(new HarnessFormData(get("package-form"))));
get("model-proposal-section").hidden = true;

// Malformed input is refused in the page rather than sent on.
states.errors = {};
for (const [name, value] of Object.entries({
  tooFewFields: "amd",
  badRole: "amd, AMD Radeon RX 9070, 16, 1, definitely-inference",
  badMemory: "amd, AMD Radeon RX 9070, lots, 1, inference",
  badCount: "amd, AMD Radeon RX 9070, 16, 1.5, inference",
})) {
  fill({ "serving-accelerators": value });
  try {
    context.withProvenance(Object.fromEntries(new HarnessFormData(get("package-form"))));
    states.errors[name] = "accepted";
  } catch (error) { states.errors[name] = error.message; }
}
fill({ "serving-accelerators": "", "serving-settings": "context_length" });
try {
  context.withProvenance(Object.fromEntries(new HarnessFormData(get("package-form"))));
  states.errors.badSetting = "accepted";
} catch (error) { states.errors.badSetting = error.message; }
fill({ "serving-settings": "", "weights-manifest": "{not json" });
try {
  context.withProvenance(Object.fromEntries(new HarnessFormData(get("package-form"))));
  states.errors.badManifest = "accepted";
} catch (error) { states.errors.badManifest = error.message; }

fill({ "weights-manifest": "" });

// A model nobody curated, submitted on what makes it publishable rather than
// on membership of a list it is not asking to join. The panel shows this
// section only when Custom model is the chosen workload, which is what makes
// these fields a declaration about this run.
get("custom-workload").hidden = false;
fill({
  "custom-model-id": "someone/Model-7B",
  "custom-licence": "apache-2.0",
  "custom-licence-url": "https://huggingface.co/someone/Model-7B/blob/main/LICENSE",
  "custom-publisher": "someone",
  "custom-source": "https://huggingface.co/someone/Model-7B",
  "custom-downloadable": true,
});
states.uncatalogued = context.withProvenance(
  Object.fromEntries(new HarnessFormData(get("package-form"))));

// Weights nobody else can obtain. Still declared, still refused publicly by
// the validator; the page does not quietly drop the fact.
fill({ "custom-downloadable": false });
states.privateWeights = context.withProvenance(
  Object.fromEntries(new HarnessFormData(get("package-form"))));

// An open proposal section nobody has typed into declares nothing. The panel
// opens it by itself for an uncatalogued model, so treating that as a started
// proposal would let it beat a complete declaration without anybody asking.
get("model-proposal-section").hidden = false;
fill({
  "proposal-family": "", "proposal-parameter-class": "", "proposal-source": "",
  "proposal-licence": "", "proposal-rationale": "", "proposal-public": false,
  "custom-downloadable": true,
});
states.emptyProposalSection = context.withProvenance(
  Object.fromEntries(new HarnessFormData(get("package-form"))));

// A proposal somebody actually wrote is an explicit request for review and
// keeps precedence over the eligibility route.
fill({
  "proposal-family": "Model", "proposal-parameter-class": "7B",
  "proposal-source": "https://huggingface.co/someone/Model-7B",
  "proposal-licence": "apache-2.0", "proposal-rationale": "Measured here.",
  "proposal-public": true,
});
states.proposalWins = context.withProvenance(
  Object.fromEntries(new HarnessFormData(get("package-form"))));
get("model-proposal-section").hidden = true;

// Nothing typed in the custom panel declares nothing. A blank form is not a
// claim that a model has no licence.
fill({
  "custom-model-id": "", "custom-licence": "", "custom-licence-url": "",
  "custom-publisher": "", "custom-source": "", "custom-downloadable": false,
});
states.noCustomModel = context.withProvenance(
  Object.fromEntries(new HarnessFormData(get("package-form"))));

// Switching back to a curated workload stops the typed fields describing this
// run, without discarding what was typed.
fill({
  "custom-model-id": "someone/Model-7B", "custom-licence": "apache-2.0",
  "custom-licence-url": "https://huggingface.co/someone/Model-7B/blob/main/LICENSE",
  "custom-downloadable": true,
});
get("custom-workload").hidden = true;
states.curatedAgain = context.withProvenance(
  Object.fromEntries(new HarnessFormData(get("package-form"))));

// The notice beside the custom-model fields. A complete declaration is one
// requirement met; it is not a submission cleared, and the wording has to keep
// those apart.
{
  const CHECKS = [
    { id: "catalog", label: "This model may be published here" },
    { id: "eligibility", label: "What makes this model publishable" },
    { id: "submitter", label: "Submitter identity" },
    { id: "acceptance", label: "Profile acceptance" },
  ];
  const render = (state, observed, blockingSubmit) => {
    get("custom-workload").hidden = false;
    context.renderCustomEligibility({
      checks: CHECKS.map((check) => check.id === "eligibility"
        ? { ...check, state, observed } : check),
      blocking: { run: [], package: [], submit: blockingSubmit },
    });
    return { text: get("custom-eligibility").textContent, hidden: get("custom-eligibility").hidden };
  };
  states.notice = {
    nothing: render("unknown", null, ["catalog", "eligibility", "submitter", "acceptance"]),
    partial: render("blocked", "licence_url, weights_publicly_downloadable",
                    ["catalog", "eligibility", "submitter", "acceptance"]),
    complete: render("ok", "declared", ["submitter", "acceptance"]),
    completeAndNothingElse: render("ok", "declared", []),
  };
  // Choosing a curated workload again hides the section, and the notice with
  // it: it is about fields that are no longer on screen.
  get("custom-workload").hidden = true;
  context.renderCustomEligibility({ checks: CHECKS, blocking: { submit: [] } });
  states.notice.hiddenWithTheSection = get("custom-eligibility").hidden;
}

// Detect details, for a run that finished minutes ago in this same panel
// session. What the probe and the server say *now* is not what was captured
// while the run was served, so none of it may be attached automatically, and
// none of it may be sent as detected evidence of the run.
{
  responses["/api/serving-probe"] = {
    kind: "omobench-serving-hardware-probe-v1", os: "Windows 11 Pro 10.0.26200",
    cpu: "AMD Ryzen 7 9800X3D", ram_gb: 64,
    accelerators: [{ vendor: "amd", model: "AMD Radeon RX 9070", count: 1 }],
  };
  responses["/api/github/auth"] = { status: "signed_out" };
  responses["/api/model-suggestions"] = { candidates: [] };
  fill({ "serving-os": "", "serving-cpu": "", "serving-ram-gb": "", "serving-accelerators": "",
         "custom-model-id": "", "custom-licence": "", "custom-licence-url": "" });
  get("custom-workload").hidden = true;
  get("windows-bridge").checked = true;
  runInContext(`
    selectedJob = { id: "job-0000000000000001", model: "deepseek-r1-distill-qwen-14b",
      execution_device: "gpu", historical: false, captured: null,
      created_at: new Date().toISOString(), finished_at: new Date().toISOString() };
    lastServerCheck = { server_metadata: { source: "LM Studio's model API",
      quantization: "Q4_K_M", compatibility_type: "gguf", engine: "llama.cpp" } };
  `, context);
  await runInContext("detectDetails()", context);
  const afterDetect = Object.fromEntries(new HarnessFormData(get("package-form")));
  states.sameSession = {
    servingOsAfterDetect: get("serving-os").value,
    engineAfterDetect: get("serving-engine").value,
    heldForConfirmation: !get("review-now").hidden,
    probeSentAfterDetect: "probe" in context.withProvenance(afterDetect),
  };
  // Confirmed by the person: filled, and recorded as their declaration.
  get("review-now-confirmed").checked = true;
  runInContext("detectedNow.map((item) => item.apply())", context);
  const afterConfirm = context.withProvenance(Object.fromEntries(new HarnessFormData(get("package-form"))));
  states.sameSession.servingOsAfterConfirm = get("serving-os").value;
  states.sameSession.probeSentAfterConfirm = "probe" in afterConfirm;
  states.sameSession.declaredHostCpu = afterConfirm.serving.host.cpu || null;
}

console.log(JSON.stringify(states, null, 2));
