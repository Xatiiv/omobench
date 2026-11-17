// Loads the real cli/src/omobench/web/app.js in a VM context with the shared
// DOM stand-in and drives its tokenizer check, so what the panel offers for
// each kind of tokenizer failure is checked where CI can see it.
//
// Run by cli/tests/test_tokenizer_panel.py, which asserts on the JSON printed.
// Every backend answer here is a fixture shaped like tokenizer_preflight's
// as_dict(); nothing is downloaded and no model is involved.
import { readFileSync } from "node:fs";
import { createContext, runInContext } from "node:vm";
import { makeDocument } from "./dom_stub.mjs";

const appPath = process.argv[2];
const document = makeDocument();
const get = (id) => document.getElementById(id);
const settle = async () => { for (let i = 0; i < 20; i += 1) await new Promise((r) => setImmediate(r)); };

let preflightAnswers = [];
const requests = [];
const responses = {
  "/api/setup": {
    environment: { cpu: "Fixture CPU", ram_gb: 32, gpus: [], operating_system: "Fixture Linux" },
    profiles: {}, frameworks: ["lmstudio"], catalog: [], can_execute: true, can_validate: true,
    runner_message: "ready",
  },
  "/api/workloads": () => null,
  "/api/runs": [],
  "/api/github/session": { available: false, signed_in: false },
  "/api/tokenizer-download-plan": { source: "https://huggingface.co/x", destination: "/cache" },
  "/api/tokenizer-preflight": () => preflightAnswers.shift(),
};

const context = createContext({
  document, console, URLSearchParams, Date, Math, JSON, Number, Set, Map, Object,
  Array, String, Boolean, Promise, Error, isNaN, parseInt, parseFloat,
  FormData: class { [Symbol.iterator]() { return [][Symbol.iterator](); } }, Blob: class {},
  URL: { createObjectURL: () => "blob:fixture", revokeObjectURL: () => {} },
  setTimeout: () => 0, clearTimeout: () => {},
  location: { hash: "", pathname: "/", href: "http://127.0.0.1:8799/" },
  history: { replaceState: () => {} },
  sessionStorage: { getItem: () => "test-token", setItem: () => {}, removeItem: () => {} },
  fetch: async (path, options) => {
    const body = options?.body ? JSON.parse(options.body) : null;
    requests.push({ path, body });
    const match = Object.keys(responses).find((key) => path.startsWith(key));
    const answer = typeof responses[match] === "function" ? responses[match](body) : responses[match];
    return { ok: true, status: 200, json: async () => answer ?? {}, blob: async () => ({}) };
  },
});
context.globalThis = context;
context.self = context;
context.HarnessDashboard = class { constructor() {} mount() {} update() {} reset() {} appendLog() {} setStages() {} };
context.WorkflowView = class {
  constructor() {} render() {} renderGates() {} renderStages() {} renderCompletion() {} renderEvidence() {}
};
context.CommunitySubmission = class {
  constructor() {} refresh() {} ticket() { return { mode: "community" }; }
  current() { return true; } destination() { return ""; } payload() { return {}; }
};
const source = readFileSync(appPath, "utf8")
  .replace(/^import .*$/gm, "")
  .replace(/\bBenchmarkDashboard\b/g, "HarnessDashboard");
runInContext(readFileSync(appPath.replace(/app\.js$/, "managed-setup.js"), "utf8"), context);
runInContext(source, context, { filename: appPath });
await settle();

const MODEL = "qwen2.5-coder-14b-instruct";
const TOKENIZER = "Qwen/Qwen2.5-Coder-14B-Instruct";

function failure(kind, recovery, message, network = "none") {
  return { ok: false, kind, recovery, message, network, checks: [], source: TOKENIZER };
}
const LOADED = { ok: true, kind: "loaded", recovery: null, message: "Loaded", network: "download",
                 revision: "abc123", encoded_tokens: 15, checks: [], source: TOKENIZER };

function snapshot() {
  return {
    status: get("tokenizer-status").textContent,
    downloadOffered: !get("tokenizer-fetch").hidden,
    downloadLabel: get("tokenizer-fetch").textContent,
    advancedOpen: !!get("tokenizer-details").open,
    runEnabled: !get("start-run").disabled,
  };
}

async function check(answers) {
  get("model").value = MODEL;
  get("tokenizer").value = TOKENIZER;
  get("tokenizer-details").open = false;
  get("tokenizer-fetch").hidden = true;
  preflightAnswers = [...answers];
  context.tokenizerVerified = null;
  await runInContext(`verifyTokenizer(${JSON.stringify(TOKENIZER)}, "metadata")`, context);
  await settle();
  return snapshot();
}

async function pressDownload(answer) {
  preflightAnswers = [answer];
  await get("tokenizer-fetch").onclick();
  await settle();
  return snapshot();
}

const states = {};

states.notCached = await check([failure("not-cached", "download", "not in this machine's cache")]);
states.afterNetworkFailure = await pressDownload(
  failure("network", "fix-network", "Hugging Face could not be reached.", "download"));
states.afterRetrySucceeded = await pressDownload(LOADED);

get("model").value = "a-different-model";
runInContext("invalidateTokenizer()", context);
states.afterModelChange = { runEnabled: !get("start-run").disabled };

states.ggufOnly = await check([failure("gguf-only", "choose-source", "contains GGUF weights and no Transformers tokenizer")]);
states.remoteCode = await check([failure("needs-remote-code", "choose-source", "ships its own tokenizer code")]);
states.cachedUnloadable = await check([failure("cached-unloadable", "choose-source", "could not be determined")]);
states.notFound = await check([failure("not-found", "choose-source", "no public repository", "download")]);
states.gated = await check([failure("gated", "fix-access", "accepted its terms", "download")]);
states.encodeFailed = await check([failure("encode-failed", "choose-source", "could not encode a sample sentence")]);
states.runnerUnusable = await check([failure("runner-unusable", "fix-runner", "could not load Transformers")]);
states.loadedOffline = await check([{ ...LOADED, network: "none" }]);

states.downloadRequests = requests.filter((r) => r.path === "/api/tokenizer-preflight").map((r) => r.body.network);
process.stdout.write(JSON.stringify(states));
