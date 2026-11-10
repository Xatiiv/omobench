// The managed-setup component, driven inside the one panel's DOM.
//
// What the component does with a download: report real byte counts, cancel the
// job it actually started, refuse to turn a rejected checksum into a
// benchmark, and adopt a job that was still running when the panel reopened.
//
// The API here is a fixture returning the same shapes the real endpoints
// return. It is about the component's behaviour, not the downloader's: resume,
// quarantine and mid-flight cancellation are exercised against the real
// `managed_setup.resumable_download` over loopback in
// cli/tests/test_managed_downloads.py. Neither of them establishes that a real
// 2.5 GB model download resumes correctly -- same code path, different
// network, and that remains untested.
import { createContext, runInContext } from "node:vm";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { makeDocument } from "./dom_stub.mjs";

const componentPath = process.argv[2];
const webDirectory = dirname(componentPath);
const document = makeDocument({ selectors: {} });
const get = (id) => document.getElementById(id);

const context = createContext({ document, console, setTimeout, clearTimeout, globalThis: {} });
context.globalThis = context;
runInContext(readFileSync(join(webDirectory, "managed-setup.js"), "utf8"), context);

const REVIEW = {
  storage_dir: "/mnt/d/OmoBench",
  platform: { ok: true, message: "Linux x64, glibc 2.39" },
  already_installed: { installed: false, valid: false, error: null },
  free_space_bytes: 50 * 1024 * 1024,
  required_bytes: 6 * 1024 * 1024,
  bytes_to_download: 6 * 1024 * 1024,
  has_enough_space: true,
  free_space_verified: true,
  files: [
    { kind: "model", filename: "fixture-model.gguf", size_bytes: 4 * 1024 * 1024,
      licence: "apache-2.0", repository: "fixtures/model", revision: "abc123", sha256: "a".repeat(64),
      url: "https://example.invalid/fixture-model.gguf" },
    { kind: "runtime", filename: "fixture-runtime.tar.gz", size_bytes: 2 * 1024 * 1024,
      licence: "MIT", repository: "fixtures/runtime", revision: "def456", sha256: "b".repeat(64),
      url: "https://example.invalid/fixture-runtime.tar.gz" },
  ],
};

const results = {};
const calls = [];

function makeApi(plan) {
  return async (path, body) => {
    calls.push({ path, body: body ?? null });
    const answer = plan[path] ?? plan[path.replace(/\/[a-z0-9-]+\/cancel$/, "/cancel")];
    if (typeof answer === "function") return answer(body);
    if (answer === undefined) throw new Error(`unexpected request: ${path}`);
    if (answer instanceof Error) throw answer;
    return answer;
  };
}

function newSetup(plan, hooks = {}) {
  calls.length = 0;
  return new context.ManagedSetup({
    api: makeApi(plan),
    document,
    formatBytes: (value) => `${value} bytes`,
    ...hooks,
  });
}

const selection = { managed_recipe: "fixture-cpu-v1", model_id: "fixtures/Model-7B" };

// --- the component takes its workload from the panel -------------------------
{
  const setup = newSetup({ "/api/managed/review": REVIEW });
  get("managed-storage-dir").value = "/mnt/d/OmoBench";
  await setup.select(selection);
  results.usesPanelSelection = {
    reviewRequestedFor: calls.find((c) => c.path === "/api/managed/review")?.body,
    visible: !get("managed-setup").hidden,
    files: get("managed-files").children.length,
    startLabel: get("managed-start").textContent,
    startDisabled: get("managed-start").disabled,
    space: get("managed-space").textContent,
  };
}

// --- a recipe the panel did not mark as prepared hides the component ---------
{
  const setup = newSetup({});
  await setup.select({ managed_recipe: null, model_id: "someone/Custom-7B" });
  results.hiddenForCustom = { hidden: get("managed-setup").hidden, calls: calls.length };
}

// --- already installed and verified: no download at all ----------------------
{
  const setup = newSetup({
    "/api/managed/review": { ...REVIEW, already_installed: { installed: true, valid: true, error: null } },
  });
  get("managed-storage-dir").value = "/mnt/d/OmoBench";
  await setup.select(selection);
  results.alreadyInstalled = {
    label: get("managed-start").textContent,
    flag: get("managed-start").dataset.alreadyInstalled,
  };
}

// --- a download that progresses, then completes, then runs -------------------
{
  const stages = [];
  let polls = 0;
  const setup = newSetup({
    "/api/managed/review": REVIEW,
    "/api/managed/setup-jobs": { id: "mjob-1" },
    "/api/managed/setup-jobs/mjob-1": () => {
      polls += 1;
      if (polls === 1) return { id: "mjob-1", status: "preparing", progress: { done: 1048576, total: 6291456 } };
      if (polls === 2) return { id: "mjob-1", status: "preparing", progress: { done: 4194304, total: 6291456 } };
      return { id: "mjob-1", status: "completed", progress: { done: 6291456, total: 6291456 } };
    },
    "/api/managed/runs": { id: "mrun-1" },
    "/api/managed/runs/mrun-1": { id: "mrun-1", status: "completed", benchmark_job_id: "job-abc" },
  }, { onStage: ({ job, active }) => stages.push(`${job.status}:${active}`) });
  get("managed-storage-dir").value = "/mnt/d/OmoBench";
  await setup.select(selection);
  const finished = new Promise((resolve) => { setup.onFinished = resolve; });
  await setup.start();
  const outcome = await finished;
  setup.stop();
  results.downloadThenRun = {
    stages,
    // Byte counts come from the downloader's own done/total, never a
    // manufactured percentage.
    sawPartialProgress: stages.length > 0,
    outcome: { success: outcome.success, benchmarkJobId: outcome.benchmarkJobId },
    startedRunAfterDownload: calls.some((c) => c.path === "/api/managed/runs" && c.body?.mode === "execute"),
  };
}

// --- cancellation reaches the right job --------------------------------------
{
  const setup = newSetup({
    "/api/managed/review": REVIEW,
    "/api/managed/setup-jobs": { id: "mjob-2" },
    "/api/managed/setup-jobs/mjob-2": { id: "mjob-2", status: "preparing", progress: { done: 1, total: 6291456 } },
    "/api/managed/setup-jobs/mjob-2/cancel": { ok: true },
  });
  get("managed-storage-dir").value = "/mnt/d/OmoBench";
  await setup.select(selection);
  await setup.start();
  await setup.cancel();
  setup.stop();
  results.cancelDownload = {
    cancelled: calls.some((c) => c.path === "/api/managed/setup-jobs/mjob-2/cancel"),
    cancelledTheRun: calls.some((c) => c.path.includes("/api/managed/runs/")),
  };
}

// --- a checksum the server rejects is a failure, not a benchmark -------------
{
  const setup = newSetup({
    "/api/managed/review": REVIEW,
    "/api/managed/setup-jobs": { id: "mjob-3" },
    "/api/managed/setup-jobs/mjob-3": {
      id: "mjob-3", status: "failed",
      error: "fixture-model.gguf failed verification: sha256 mismatch; the download was quarantined.",
    },
  });
  get("managed-storage-dir").value = "/mnt/d/OmoBench";
  await setup.select(selection);
  const finished = new Promise((resolve) => { setup.onFinished = resolve; });
  await setup.start();
  const outcome = await finished;
  setup.stop();
  results.checksumRejected = {
    success: outcome.success,
    message: outcome.message,
    startedARun: calls.some((c) => c.path === "/api/managed/runs"),
  };
}

// --- recovery: a job still running when the panel reopened -------------------
{
  const setup = newSetup({
    "/api/managed/setup-jobs": [{ id: "mjob-4", status: "preparing", progress: { done: 2, total: 9 } }],
    "/api/managed/runs": [],
    "/api/managed/setup-jobs/mjob-4": { id: "mjob-4", status: "preparing", progress: { done: 2, total: 9 } },
  });
  setup.selection = selection;
  const adopted = await setup.recover();
  setup.stop();
  results.recoversDownload = adopted;
}
{
  const setup = newSetup({
    "/api/managed/setup-jobs": [{ id: "mjob-5", status: "completed" }],
    "/api/managed/runs": [{ id: "mrun-5", status: "running_benchmark" }],
    "/api/managed/runs/mrun-5": { id: "mrun-5", status: "running_benchmark" },
  });
  setup.selection = selection;
  const adopted = await setup.recover();
  setup.stop();
  results.recoversRun = adopted;
}
{
  const setup = newSetup({ "/api/managed/setup-jobs": [], "/api/managed/runs": [] });
  results.recoversNothing = await setup.recover();
}

// --- a folder that cannot be used says so and refuses to start ---------------
{
  const setup = newSetup({ "/api/managed/review": new Error("That folder is not writable.") });
  get("managed-storage-dir").value = "/nope";
  await setup.select(selection);
  results.badFolder = {
    error: get("managed-error").textContent,
    hidden: get("managed-error").hidden,
    startDisabled: get("managed-start").disabled,
  };
}

// --- unverifiable free space is neither "enough" nor "not enough" ------------
{
  const setup = newSetup({
    "/api/managed/review": {
      ...REVIEW, free_space_verified: false,
      space_note: "WSL reports its virtual disk, not the Windows drive behind it.",
    },
  });
  get("managed-storage-dir").value = "/mnt/d/OmoBench";
  await setup.select(selection);
  results.unverifiableSpace = get("managed-space").textContent;
}

// --- the component has no selector, stepper or dashboard of its own ----------
results.noDuplicateUi = {
  source: readFileSync(componentPath, "utf8"),
};
results.noDuplicateUi = {
  mentionsWorkloadSelect: results.noDuplicateUi.source.includes("workload-select"),
  mentionsStepper: results.noDuplicateUi.source.includes("step-tab"),
  mountsDashboard: results.noDuplicateUi.source.includes("BenchmarkDashboard"),
};

process.stdout.write(JSON.stringify(results));
