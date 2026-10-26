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
const { incompatibilities, metricDifference, rankRuns, eligibleLeaderboard, barPercent, comparisonHref, formatMetric } = await loadModule("../app/lib/comparison.ts");

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
