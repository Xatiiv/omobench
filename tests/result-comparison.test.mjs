import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import ts from "typescript";

// These production modules have type-only imports. Transpile with the project's
// compiler so the tests also run on the pinned Node 22.13 CI environment.
async function loadModule(path) {
  const { outputText } = ts.transpileModule(readFileSync(new URL(path, import.meta.url), "utf8"), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } });
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
}
const { readResultRecord } = await loadModule("../app/lib/result-record.ts");
const { comparisonExamples } = await loadModule("../app/data/comparison-examples.ts");
const { assess, comparisonQuestions, suitabilityLabel, incompatibilities, metricDifference, rankRuns, eligibleLeaderboard, barPercent, comparisonHref, formatMetric, transportLabel, servingLabel, placementLabel, tokenizerLabel } = await loadModule("../app/lib/comparison.ts");

function fixture() {
  const checksum = `sha256:${"a".repeat(64)}`;
  return {
    schema_version: "1.0", run_id: "run-fixture-1234", started_at: "2026-01-01T00:00:00Z", configuration_fingerprint: checksum, execution_device: "cpu",
    model: { id: "Fixture/Model", revision: "immutable-fixture-revision", source: "https://example.invalid/fixture.gguf", quantization: "Q4_K_M" },
    runtime: { framework: "llama.cpp", version: "fixture-version", arguments: ["--threads", "4"], endpoint_type: "openai-compatible" },
    hardware: { cpu: "Fixture CPU", os: "Fixture Linux", ram_gb: 16, driver_version: null, gpus: [] },
    benchmark: { profile_id: "fixture-chat-v1", profile_sha256: checksum, runner: "guidellm", runner_version: "0.7.3" },
    results: { requests: { attempted: 50, succeeded: 50, failed: 0 }, latency_ms: { ttft: { p50: 100, p95: 200 }, inter_token: { p50: 20, p95: 40 } }, output_tokens_per_second: 12, total_tokens_per_second: 60, requests_per_second: 0.05, error_rate_percent: 0 },
    submission: { submitter: "fixture", run_kind: "initial" },
    artifacts: Object.fromEntries(["raw_runner_output", "runner_stdout_log", "environment_snapshot", "reproduce_markdown"].map(key => [key, { path: `fixture-${key}`, sha256: checksum }])),
  };
}
function record() { return readResultRecord("test fixture", fixture()); }
function entry(key, value, origin = "registry") { return { key, origin, result: { ...record(), outputTokensPerSecond: value, ttftP50: value } }; }

test("reads final measured latency, profile identity and reliability without recomputing throughput", () => {
  const result = record();
  assert.equal(result.itlP50, 20); assert.equal(result.itlP95, 40);
  assert.equal(result.outputTokensPerSecond, 12); assert.equal(result.errorRatePercent, 0);
  assert.equal(result.profileSha256, fixture().benchmark.profile_sha256);
});

test("different hardware, runtime and fingerprints remain comparable", () => {
  const base = record();
  const other = { ...base, executionDevice: "gpu", hardware: { ...base.hardware, cpu: "Another CPU", gpus: [{ model: "Fixture GPU", count: 1, memoryGb: 16 }] }, framework: "vllm", frameworkVersion: "other-version", configurationFingerprint: `sha256:${"b".repeat(64)}`, runtimeArguments: ["--other-setting"] };
  assert.deepEqual(incompatibilities(base, other), []);
});

test("every workload identity difference excludes direct comparison", () => {
  for (const [field, value, expected] of [
    ["model", "Other/Model", "model"], ["modelRevision", "another-revision", "revision"],
    ["modelSource", "https://example.invalid/other.gguf", "source"], ["modelSource", null, "source"],
    ["quantization", "Q8", "quantization"], ["profile", "fixture-extended-v1", "profile"],
    ["profileSha256", `sha256:${"b".repeat(64)}`, "checksum"], ["runnerVersion", "0.8.0", "runner"],
  ]) assert.match(incompatibilities(record(), { ...record(), [field]: value }).join(" "), new RegExp(expected, "i"));
});

test("unknown quantization cannot form a ranking even against itself", () => {
  const unknown = { ...record(), quantization: null };
  assert.deepEqual(incompatibilities(unknown, unknown), ["Quantization not recorded"]);
  assert.deepEqual(eligibleLeaderboard([{ key: "unknown", origin: "registry", result: unknown }], unknown, "registry"), []);
});

test("legacy canonical source records match each other but not a sourced re-export", () => {
  const legacy = { ...record(), modelSource: null };
  assert.deepEqual(incompatibilities(legacy, legacy), []);
  assert.notDeepEqual(incompatibilities(legacy, record()), []);
});

test("percentages describe values accurately and correctly favor lower latency", () => {
  assert.deepEqual(metricDifference(150, 100, true), { text: "50% higher than reference", favorable: true });
  assert.deepEqual(metricDifference(75, 100, false), { text: "25% lower than reference", favorable: true });
  assert.equal(metricDifference(150, 100, false).favorable, false);
  assert.equal(metricDifference(75, 100, true).favorable, false);
  assert.equal(metricDifference(100.001, 100, true).text, "<0.1% higher than reference");
});

test("zero and invalid references never produce infinite or fabricated percentages", () => {
  assert.equal(metricDifference(0, 0, true).text, "Same as reference");
  assert.equal(metricDifference(2, 0, true).text, "Reference is zero, no percentage");
  assert.equal(metricDifference(0, 100, false).text, "100% lower than reference");
  for (const value of [NaN, Infinity, -1]) assert.equal(metricDifference(value, 10, true).text, "Unavailable");
  assert.equal(metricDifference(Number.MAX_VALUE, Number.MIN_VALUE, true).favorable, null);
});

test("bar lengths are proportional with a true zero and no minimum invented fill", () => {
  assert.equal(barPercent(0, 100), 0); assert.equal(barPercent(25, 100), 25);
  assert.equal(barPercent(0, 0), 0); assert.equal(barPercent(120, 100), 100);
  assert.equal(barPercent(NaN, 100), 0);
});

test("very large or small finite metrics remain readable without rounding a positive value to zero", () => {
  assert.equal(formatMetric(0), "0");
  assert.equal(formatMetric(0.00001), "1E-5");
  assert.equal(formatMetric(1000000), "1E6");
  assert.ok(formatMetric(Number.MAX_VALUE).length < 12);
  assert.ok(metricDifference(1e10, 1, true).text.length < 40);
});

test("ranking reverses for latency, keeps ties, and does not mutate records", () => {
  const rows = [entry("b", 10), entry("a", 10), entry("c", 20)];
  const before = JSON.stringify(rows);
  assert.deepEqual(rankRuns(rows, "outputTokensPerSecond").map(r => [r.entry.key, r.rank]), [["c", 1], ["a", 2], ["b", 2]]);
  assert.deepEqual(rankRuns(rows, "ttftP50").map(r => [r.entry.key, r.rank]), [["a", 1], ["b", 1], ["c", 3]]);
  assert.equal(JSON.stringify(rows), before);
});

test("private, example and incompatible records never enter community rankings", () => {
  const rows = [entry("real", 10), entry("private", 999, "local"), entry("demo", 9999, "example"), { ...entry("other", 100), result: { ...record(), profile: "different-v1" } }];
  assert.deepEqual(eligibleLeaderboard(rows, record(), "registry").map(r => r.key), ["real"]);
  assert.deepEqual(eligibleLeaderboard(rows, record(), "example").map(r => r.key), ["demo"]);
  assert.deepEqual(eligibleLeaderboard([], record(), "registry"), []);
});

test("summary import rejects malformed counts, latencies and identity", () => {
  const mutations = [
    f => { f.schema_version = "2.0"; }, f => { f.run_id = "../../run"; },
    f => { f.benchmark.profile_sha256 = "unknown"; }, f => { f.model.revision = "latest"; },
    f => { f.results.output_tokens_per_second = NaN; }, f => { f.results.output_tokens_per_second = "12"; },
    f => { f.results.requests.failed = 1; }, f => { f.results.requests = { attempted: 0, succeeded: 0, failed: 0 }; },
    f => { f.results.requests = { attempted: 50.5, succeeded: 50.5, failed: 0 }; },
    f => { f.results.error_rate_percent = 10; }, f => { f.results.latency_ms.inter_token.p95 = 1; },
    f => { f.benchmark.runner = "other"; }, f => { f.hardware.ram_gb = 0; },
  ];
  for (const mutate of mutations) { const input = fixture(); mutate(input); assert.throws(() => readResultRecord("fixture", input)); }
  assert.throws(() => readResultRecord("fixture", { kind: "omobench-local-summary" }));
});

test("import checks do not masquerade as acceptance or evidence validation", () => {
  const input = fixture();
  input.results.requests = { attempted: 10, succeeded: 9, failed: 1 }; input.results.error_rate_percent = 10;
  assert.equal(readResultRecord("private preview", input).requests.succeeded, 9);
});

test("legacy GPU execution and missing quantization retain their interpretation", () => {
  const input = fixture(); delete input.execution_device;
  input.hardware.gpus = [{ model: "Fixture GPU", count: 1, memory_gb: 16 }]; input.model.quantization = null;
  assert.equal(readResultRecord("legacy", input).executionDevice, "gpu");
  assert.equal(readResultRecord("legacy", input).quantization, null);
});

test("compare links carry only an encoded public run ID", () => {
  assert.equal(comparisonHref("run-fixture-1234"), "/compare?run=run-fixture-1234");
  assert.equal(comparisonHref("a&token=secret"), "/compare?run=a%26token%3Dsecret");
});


// --- Schema 1.1: what served the result, beside what measured it -----------

function fact(value, provenance = "contributor-declared") {
  return { value, provenance: value === null ? "unknown" : provenance };
}

// A bridged AMD desktop. Entirely synthetic; no machine like this was measured.
function bridgedFixture(overrides = {}) {
  const base = fixture();
  const checksum = `sha256:${"a".repeat(64)}`;
  return {
    ...base,
    schema_version: "1.1",
    execution_device: "gpu",
    runtime: { ...base.runtime, framework: "lmstudio", version: "b10621" },
    hardware: {
      cpu: "AMD Ryzen 7 9800X3D", os: "Windows 11 Pro", ram_gb: 64, driver_version: null,
      gpus: [{ vendor: "amd", model: "AMD Radeon RX 9070", memory_gb: 16, count: 1 }],
    },
    model: {
      ...base.model,
      served_model_id: "deepseek-r1-distill-qwen-14b",
      catalog_status: "catalogued",
      tokenizer: { source: "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B", kind: "huggingface",
                   loaded: "loaded-before-run", matches_served_weights: "not-verifiable-here",
                   identity: { class: "LlamaTokenizerFast", vocabulary_size: 152064 } },
      weights: { kind: "repository", repository: "https://example.invalid/weights",
                 repository_revision: "immutable-fixture-revision",
                 identity: "repository-revision", content: null },
    },
    environment: {
      metadata_version: "serving-environment-v1",
      relationship: "windows-host-wsl-runner",
      runner: { os: "Linux-6.6-WSL2", kind: "wsl2", python_version: "3.12.3", cpu: "x86_64", ram_gb: 31 },
      serving: {
        host: { os: fact("Windows 11 Pro"), cpu: fact("AMD Ryzen 7 9800X3D"), ram_gb: fact(64) },
        accelerators: [{ vendor: "amd", model: "AMD Radeon RX 9070", memory_gb: 16, count: 1,
                         role: "inference", provenance: "contributor-declared" }],
        placement: { mode: "gpu-only", detail: null, provenance: "contributor-declared" },
        runtime: { application: { name: "LM Studio", version: "0.3.31", provenance: "contributor-declared" },
                   engine: { name: "llama.cpp", version: "b10621", provenance: "contributor-declared" },
                   settings: [{ name: "context_length", value: "8192", provenance: "contributor-declared" }] },
      },
      transport: { kind: "windows-wsl-stdio-bridge", protocol: "HTTP/1.1",
                   bridge_version: "windows-stdio-http1-v1", overhead: "included-not-measured" },
    },
    configuration_fingerprint: checksum,
    ...overrides,
  };
}

function bridged(overrides) { return readResultRecord("bridged fixture", bridgedFixture(overrides)); }

test("a legacy 1.0 result still reads, and gains no facts it never recorded", () => {
  const result = record();
  assert.equal(result.schemaVersion, "1.0");
  assert.equal(result.serving, null);
  assert.equal(result.tokenizer, null);
  assert.equal(result.weights, null);
  assert.equal(result.servedModelId, null);
  assert.equal(result.transportRecorded, false);
  assert.equal(result.metadataDeclaredAfterExecution, false);
});

test("a 1.0 result cannot carry serving metadata in place", () => {
  assert.throws(() => readResultRecord("mixed", { ...fixture(), environment: bridgedFixture().environment }), /schema 1.0/);
});

test("a bridged result reads its two environments separately", () => {
  const result = bridged();
  assert.equal(result.serving.relationship, "windows-host-wsl-runner");
  assert.equal(result.serving.runner.kind, "wsl2");
  assert.equal(result.hardware.cpu, "AMD Ryzen 7 9800X3D");
  assert.notEqual(result.hardware.cpu, result.serving.runner.cpu);
});

test("bridge latency is reported as included, never as corrected for", () => {
  const result = bridged();
  assert.equal(result.transport.overheadIncluded, true);
  assert.match(transportLabel(result), /inside every number/);
});

test("a legacy result says its transport was not separately recorded", () => {
  assert.match(transportLabel(record()), /not separately recorded/);
});

test("a fact and its origin have to agree", () => {
  for (const host of [
    { os: { value: null, provenance: "detected" }, cpu: fact("x"), ram_gb: fact(1) },
    { os: { value: "Windows", provenance: "unknown" }, cpu: fact("x"), ram_gb: fact(1) },
  ]) {
    const document = bridgedFixture();
    document.environment.serving.host = host;
    assert.throws(() => readResultRecord("bad fact", document));
  }
});

test("a result cannot describe its serving hardware two different ways", () => {
  const document = bridgedFixture();
  document.hardware.gpus = [{ vendor: "nvidia", model: "NVIDIA RTX 4090", memory_gb: 24, count: 1 }];
  assert.throws(() => readResultRecord("mismatched", document), /do not match/);
});

test("a card that was present but idle stays out of the result's hardware", () => {
  const document = bridgedFixture();
  document.environment.serving.accelerators.push({
    vendor: "intel", model: "Intel Arc A770", memory_gb: 16, count: 1,
    role: "present-not-used", provenance: "detected" });
  const result = readResultRecord("idle card", document);
  assert.equal(result.hardware.gpus.length, 1);
  assert.equal(result.serving.accelerators.length, 2);
});

test("a split placement has to say how the weights were split", () => {
  const document = bridgedFixture();
  document.environment.serving.placement = { mode: "cpu-gpu-offload", detail: null, provenance: "contributor-declared" };
  assert.throws(() => readResultRecord("split", document), /how the weights were split/);
  document.environment.serving.placement.detail = "32 of 49 layers on GPU";
  assert.match(placementLabel(readResultRecord("split", document)), /32 of 49 layers/);
});

test("a transport and a serving arrangement that describe different setups are refused", () => {
  const document = bridgedFixture();
  document.environment.relationship = "same-host";
  document.environment.runner.kind = "native";
  assert.throws(() => readResultRecord("mismatched setup", document), /different setups/);
});

test("a tokenizer that loaded is never read as a tokenizer that matches", () => {
  const result = bridged();
  assert.equal(result.tokenizer.loaded, "loaded-before-run");
  assert.match(tokenizerLabel(result), /loaded before the run/);
  const document = bridgedFixture();
  document.model.tokenizer.matches_served_weights = "yes";
  assert.throws(() => readResultRecord("claimed match", document), /nothing can establish/);
});

test("a served alias is kept apart from the canonical model", () => {
  const result = bridged();
  assert.equal(result.servedModelId, "deepseek-r1-distill-qwen-14b");
  assert.equal(result.model, "Fixture/Model");
});

test("content-hashed weights report the files they were hashed from", () => {
  const document = bridgedFixture();
  document.model.weights = { kind: "local-files", repository: null, repository_revision: null,
    identity: "content-hash",
    content: { manifest_sha256: `sha256:${"c".repeat(64)}`,
               files: [{ name: "a.gguf", bytes: 1, sha256: `sha256:${"d".repeat(64)}` },
                       { name: "b.gguf", bytes: 2, sha256: `sha256:${"e".repeat(64)}` }] } };
  const result = readResultRecord("hashed weights", document);
  assert.equal(result.weights.identity, "content-hash");
  assert.equal(result.weights.fileCount, 2);
  assert.equal(result.weights.repositoryRevision, null);
});

test("a proposed model is readable and labelled, not silently catalogued", () => {
  const document = bridgedFixture();
  document.model.catalog_status = "proposed";
  const result = readResultRecord("proposed", document);
  assert.equal(result.catalogStatus, "proposed");
  assert.throws(() => readResultRecord("proposed on 1.0", { ...fixture(), model: { ...fixture().model, catalog_status: "proposed" } }), /schema 1.1/);
});

test("metadata declared after the run is reported as such", () => {
  const document = bridgedFixture();
  document.submission = { ...document.submission, packaging: {
    metadata_declared_after_execution: true, derived_from_run_id: "run-original-1234",
    evidence_sha256: { "reproduce.md": `sha256:${"f".repeat(64)}` } } };
  assert.equal(readResultRecord("derived", document).metadataDeclaredAfterExecution, true);
});

// --- Comparison -------------------------------------------------------------

test("a bridged run is not mixed into a direct run's comparison", () => {
  const direct = record();
  const relayed = { ...direct, transport: bridged().transport, serving: bridged().serving,
                    tokenizer: bridged().tokenizer };
  assert.match(incompatibilities(direct, relayed).join(" "), /Different transport/);
});

test("two bridged runs stay comparable with each other", () => {
  assert.deepEqual(incompatibilities(bridged(), bridged()), []);
});

// --- A run that was not running ---------------------------------------------

function suspendedFixture(state = "suspended", seconds = 4296) {
  const document = bridgedFixture();
  document.execution = {
    contract: "bounded-execution-v1",
    limits: { inactivity_timeout_seconds: 300, request_deadline_seconds: 5400,
              measurement_ceiling_seconds: 5400, shutdown_grace_seconds: 60 },
    interruption: { state, observed_seconds: seconds, longest_seconds: seconds, events: [] },
  };
  return document;
}

test("a run suspended while measuring never ranks, whatever is asked of it", () => {
  const suspended = readResultRecord("suspended", suspendedFixture());
  for (const question of Object.keys(comparisonQuestions)) {
    const assessment = assess(bridged(), suspended, question);
    assert.equal(assessment.suitability, "unsuitable", question);
    assert.match(assessment.reasons.join(" "), /suspended for 4296 seconds/);
    assert.ok(assessment.blockers.includes("Suspended while measuring"), question);
  }
});

test("it does not matter which side of the comparison was suspended", () => {
  const suspended = readResultRecord("suspended", suspendedFixture());
  assert.match(assess(suspended, bridged()).reasons.join(" "), /This run was suspended/);
  assert.match(assess(bridged(), suspended).reasons.join(" "), /The other run was suspended/);
});

test("an unexplained gap is not treated as a suspension", () => {
  const gapped = readResultRecord("gap", suspendedFixture("gap", 119));
  assert.equal(assess(bridged(), gapped).suitability, "ranking");
});

test("an ambiguous clock jump does not rank, and is not called a suspension", () => {
  const jumped = readResultRecord("jump", suspendedFixture("clock-jump", 4296));
  const assessment = assess(bridged(), jumped);
  assert.equal(assessment.suitability, "unsuitable");
  assert.ok(assessment.blockers.includes("Clocks disagreed while measuring"));
  assert.match(assessment.reasons.join(" "), /no suspend recorded by the kernel/);
  assert.doesNotMatch(assessment.reasons.join(" "), /was suspended for/);
});

test("the website refuses an interruption state it does not understand", () => {
  const document = suspendedFixture();
  document.execution.interruption.state = "probably-fine";
  assert.throws(() => readResultRecord("unknown", document), /unknown interruption state/);
});

test("a run that recorded no execution block is unaffected", () => {
  assert.equal(assess(bridged(), bridged()).suitability, "ranking");
  assert.equal(record().execution, null);
});

test("a legacy result reads as not recorded rather than as not interrupted", () => {
  // The distinction the whole module turns on: nobody looked, which is not
  // the same claim as nothing happened.
  const legacy = record();
  assert.equal(legacy.schemaVersion, "1.0");
  assert.equal(legacy.execution, null);
  const modern = readResultRecord("modern", bridgedFixture());
  assert.equal(modern.execution, null, "1.1 without the block is also 'not recorded'");
  assert.equal(assess(legacy, legacy).suitability, "ranking");
});

test("the fields a recorded execution block exposes survive ingestion", () => {
  const ingested = readResultRecord("recorded", suspendedFixture("none", 0));
  assert.equal(ingested.execution.contract, "bounded-execution-v1");
  assert.equal(ingested.execution.limits.measurementCeilingSeconds, 5400);
  assert.equal(ingested.execution.limits.inactivityTimeoutSeconds, 300);
  assert.equal(ingested.execution.interruption.state, "none");
});

test("a schema 1.0 result may not carry an execution record", () => {
  const document = fixture();
  document.execution = suspendedFixture().execution;
  assert.throws(() => readResultRecord("legacy", document), /cannot carry an execution record/);
});

test("a split placement is not compared against an undivided one", () => {
  const document = bridgedFixture();
  document.environment.serving.placement = { mode: "cpu-gpu-offload", detail: "32 of 49 layers on GPU", provenance: "contributor-declared" };
  assert.match(incompatibilities(bridged(), readResultRecord("split", document)).join(" "), /weight placement/i);
});

test("different tokenizers are not compared as if they counted the same work", () => {
  const document = bridgedFixture();
  document.model.tokenizer.source = "Qwen/Qwen3-4B";
  assert.match(incompatibilities(bridged(), readResultRecord("other tokenizer", document)).join(" "), /tokenizer/i);
});

test("a proposed model does not join a catalogued leaderboard", () => {
  const document = bridgedFixture();
  document.model.catalog_status = "proposed";
  const proposed = readResultRecord("proposed", document);
  assert.match(incompatibilities(bridged(), proposed).join(" "), /proposed/i);
  assert.deepEqual(eligibleLeaderboard([{ key: "p", origin: "registry", result: proposed }], bridged(), "registry"), []);
});

test("legacy results stay comparable with each other", () => {
  assert.deepEqual(incompatibilities(record(), record()), []);
});

test("serving labels say which arrangement produced the numbers", () => {
  assert.match(servingLabel(bridged()), /Windows host served/);
  assert.match(servingLabel(record()), /not recorded/i);
  assert.match(placementLabel(record()), /Not recorded/);
  assert.match(tokenizerLabel(record()), /Not recorded/);
});


// --- Comparable for which question? ----------------------------------------
//
// "Comparable" is a property of two runs and a question. Asking which of two
// machines is faster requires the hardware to differ; refusing that pair
// because their hardware differs answers a question nobody asked.

function withServing(overrides = {}) {
  const base = bridgedFixture();
  base.environment.relationship = "same-host";
  base.environment.runner.kind = "native";
  base.environment.transport = { kind: "direct", protocol: "HTTP/1.1", bridge_version: null,
                                 overhead: "none-direct" };
  base.hardware.gpus = [{ vendor: "amd", model: "AMD Radeon RX 9070", memory_gb: 16, count: 1 }];
  return readResultRecord("question fixture", { ...base, ...overrides });
}

test("a repeat of the same setup ranks, and says the workload is what matched", () => {
  const assessment = assess(withServing(), withServing(), "exact");
  assert.equal(assessment.suitability, "ranking");
  assert.deepEqual(assessment.blockers, []);
  assert.match(assessment.reasons.join(" "), /declared workload matches/);
  assert.equal(suitabilityLabel(assessment), "Directly comparable");
});

test("hardware and engine build differing never stops a comparison", () => {
  const other = { ...withServing(), hardware: { ...withServing().hardware, display: "Another box" },
                  framework: "vllm", frameworkVersion: "0.11.0", executionDevice: "cpu" };
  for (const question of ["exact", "hardware", "runtime", "model"]) {
    const assessment = assess(withServing(), other, question);
    assert.equal(assessment.suitability, "ranking", question);
    assert.match(assessment.differences.join(" "), /Serving hardware|Runtime build|Execution device/);
  }
});

test("a different model is unsuitable unless that is the question", () => {
  const other = { ...withServing(), model: "Qwen/Qwen3-4B" };
  assert.equal(assess(withServing(), other, "exact").suitability, "unsuitable");
  assert.equal(assess(withServing(), other, "hardware").suitability, "unsuitable");
  const asked = assess(withServing(), other, "model");
  assert.equal(asked.suitability, "qualified");
  assert.match(asked.differences.join(" "), /^Model: /);
  assert.match(asked.reasons.join(" "), /not comparable across models as a measure of work done/);
});

test("a split placement is a difference when the question is about hardware", () => {
  // The base fixture runs gpu-only; this one splits the weights across both.
  const document = bridgedFixture();
  document.environment.relationship = "same-host";
  document.environment.runner.kind = "native";
  document.environment.transport = { kind: "direct", protocol: "HTTP/1.1", bridge_version: null, overhead: "none-direct" };
  document.hardware.gpus = [{ vendor: "amd", model: "AMD Radeon RX 9070", memory_gb: 16, count: 1 }];
  document.environment.serving.placement = { mode: "cpu-gpu-offload", detail: "32 of 49 layers on GPU", provenance: "contributor-declared" };
  const split = readResultRecord("split", document);
  assert.equal(assess(withServing(), split, "exact").suitability, "unsuitable");
  assert.ok(assess(withServing(), split, "exact").blockers.includes("Different weight placement"));
  // Two machines place the weights differently; that is the point of asking.
  assert.equal(assess(withServing(), split, "hardware").suitability, "qualified");
  assert.equal(assess(withServing(), split, "runtime").suitability, "qualified");
  assert.equal(assess(withServing(), split, "model").suitability, "unsuitable");
});

test("a bridged run is never comparable with a direct one, whatever is asked", () => {
  const relayed = readResultRecord("bridged", bridgedFixture());
  for (const question of ["exact", "hardware", "runtime", "model"]) {
    const assessment = assess(withServing(), relayed, question);
    assert.equal(assessment.suitability, "unsuitable", question);
    assert.match(assessment.reasons.join(" "), /never subtracted/);
    assert.ok(assessment.blockers.includes("Different transport"), question);
  }
});

test("serving on one machine and measuring on another is never set aside either", () => {
  const relayed = bridgedFixture();
  relayed.environment.transport = { kind: "direct", protocol: "HTTP/1.1", bridge_version: null, overhead: "none-direct" };
  relayed.environment.runner.kind = "native";
  // relationship stays windows-host-wsl-runner, which the reader is told about
  assert.throws(() => readResultRecord("mismatch", relayed), /different setups/);
});

test("information nobody recorded is stated rather than assumed away", () => {
  const legacy = record();
  const full = withServing();
  const assessment = assess(legacy, full, "hardware");
  assert.notEqual(assessment.suitability, "ranking");
  assert.match(assessment.missing.join(" "), /not recorded on/);
});

test("two runs that both omit the same thing agree rather than differ", () => {
  assert.deepEqual(assess(record(), record(), "exact").blockers, []);
  assert.deepEqual(assess(record(), record(), "exact").missing, []);
});

test("quantization is the exception: both omitting it still blocks a ranking", () => {
  const unknown = { ...record(), quantization: null };
  const assessment = assess(unknown, unknown, "exact");
  assert.equal(assessment.suitability, "qualified");
  assert.deepEqual(assessment.blockers, ["Quantization not recorded"]);
  assert.match(assessment.missing.join(" "), /not recorded on either run/);
});

test("a proposed model can be looked at but never ranked", () => {
  const proposed = withServing({ model: { ...bridgedFixture().model, catalog_status: "proposed" } });
  const assessment = assess(withServing(), proposed, "exact");
  assert.equal(assessment.suitability, "unsuitable");
  assert.match(assessment.reasons.join(" "), /measurements are real; its identity is still a request/);
  assert.equal(assess(withServing(), proposed, "model").suitability, "qualified");
});

test("ranking eligibility is exactly what it was, expressed through one rule", () => {
  assert.deepEqual(incompatibilities(withServing(), withServing()), []);
  const other = { ...withServing(), model: "Other/Model" };
  assert.deepEqual(incompatibilities(withServing(), other), assess(withServing(), other, "exact").blockers);
  assert.deepEqual(eligibleLeaderboard(
    [{ key: "p", origin: "registry", result: other }], withServing(), "registry"), []);
});

test("the leaderboard stays strict however exploratory the question is", () => {
  const other = { ...withServing(), model: "Other/Model" };
  // assess() would allow this pair for a model question; the ranking does not.
  assert.equal(assess(withServing(), other, "model").suitability, "qualified");
  assert.deepEqual(eligibleLeaderboard(
    [{ key: "p", origin: "registry", result: other }], withServing(), "registry"), []);
});

test("every question explains itself before it is chosen", () => {
  for (const [key, value] of Object.entries(comparisonQuestions)) {
    assert.ok(value.title.length > 5, key);
    assert.ok(value.detail.length > 40, key);
  }
  assert.match(comparisonQuestions.model.detail, /do not measure the same semantic work/);
});

test("no composite score and no universal threshold is produced", () => {
  const assessment = assess(withServing(), withServing(), "hardware");
  for (const value of Object.values(assessment)) {
    assert.notEqual(typeof value, "number");
  }
  assert.ok(["ranking", "qualified", "unsuitable"].includes(assessment.suitability));
});

// The illustrative examples the comparison page offers before any real result
// exists. They are fictional and labelled as such on the page; what they have
// to do is demonstrate the explanations honestly, which means each one has to
// land where it claims to land.
const example = (key) => comparisonExamples.find((entry) => entry.key === key).result;
const verdict = (key, question) => assess(example("example-reference"), example(key), question).suitability;

test("the illustrative examples are not kept out of the public registry", () => {
  for (const entry of comparisonExamples) {
    assert.equal(entry.origin, "example");
    assert.match(entry.result.id, /^example-/);
    assert.equal(entry.result.submitter, "Illustrative setup");
  }
});

test("an example pair that differs only in hardware stays directly comparable", () => {
  for (const question of Object.keys(comparisonQuestions)) {
    assert.equal(verdict("example-cpu-b", question), "ranking", question);
  }
  const assessment = assess(example("example-reference"), example("example-cpu-b"), "hardware");
  assert.deepEqual(assessment.blockers, []);
  assert.ok(assessment.differences.some((text) => text.startsWith("Serving hardware:")));
});

test("a confounded example is comparable only where the question allows it", () => {
  // The weights are split across CPU and GPU and the engine is a different
  // one. A question about hardware or runtime expects that; "the same setup
  // again" does not.
  assert.equal(verdict("example-runtime-b", "exact"), "unsuitable");
  assert.equal(verdict("example-runtime-b", "hardware"), "qualified");
  assert.equal(verdict("example-runtime-b", "runtime"), "qualified");
  const strict = assess(example("example-reference"), example("example-runtime-b"), "exact");
  assert.ok(strict.blockers.includes("Different weight placement"));
  assert.ok(strict.reasons.some((text) => text.includes("Weight placement differs")));
});

test("an example with gaps reports what nobody recorded, by name", () => {
  const assessment = assess(example("example-reference"), example("example-missing"), "model");
  assert.ok(assessment.missing.some((text) => text.includes("Quantization is not recorded")));
  assert.ok(assessment.missing.some((text) => text.includes("Weights source is not recorded")));
  // Unrecorded is not the same as different: the gaps are listed separately
  // from the differences, and nothing is invented to fill them.
  assert.ok(!assessment.missing.some((text) => text.includes("against")));
  assert.equal(verdict("example-missing", "exact"), "unsuitable");
});

test("a bridged example is comparable with a direct one under no question", () => {
  for (const question of Object.keys(comparisonQuestions)) {
    assert.equal(verdict("example-bridged", question), "unsuitable", question);
  }
  const assessment = assess(example("example-reference"), example("example-bridged"), "hardware");
  assert.ok(assessment.blockers.includes("Different transport"));
  assert.ok(assessment.reasons.some((text) => text.includes("never subtracted")));
});

test("a different-model example is comparable only when that is the question", () => {
  assert.equal(verdict("example-model-b", "exact"), "unsuitable");
  assert.equal(verdict("example-model-b", "hardware"), "unsuitable");
  assert.equal(verdict("example-model-b", "model"), "qualified");
  const assessment = assess(example("example-reference"), example("example-model-b"), "model");
  assert.ok(assessment.reasons.some((text) => text.includes("token is not a unit of meaning")));
  assert.ok(assessment.differences.some((text) => text.startsWith("Model:")));
});

test("only the directly comparable examples are offered for ranking", () => {
  const ranked = eligibleLeaderboard(comparisonExamples, example("example-reference"), "example");
  assert.deepEqual(ranked.map((entry) => entry.key).sort(),
                   ["example-cpu-b", "example-gpu-a", "example-reference"]);
});
