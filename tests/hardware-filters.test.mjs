import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync, readdirSync } from "node:fs";
import ts from "typescript";

// Hardware-first registry filters. The published results are read as they
// are; every other machine below is a LABELLED FIXTURE built from a real
// record by changing its hardware, and none of it is published anywhere.

async function loadModule(path) {
  const { outputText } = ts.transpileModule(readFileSync(new URL(path, import.meta.url), "utf8"), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } });
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
}
const { readResultRecord } = await loadModule("../app/lib/result-record.ts");
const hf = await loadModule("../app/lib/hardware-filters.ts");
const { summarize } = await loadModule("../app/lib/evidence.ts");

const resultsDirectory = new URL("../results/", import.meta.url);
const publishedFiles = readdirSync(resultsDirectory, { recursive: true }).filter(path => path.endsWith("result.json")).sort();
const publishedRaw = publishedFiles.map(path => JSON.parse(readFileSync(new URL(path.replaceAll("\\", "/"), resultsDirectory), "utf8")));
const published = publishedRaw.map((raw, index) => readResultRecord(publishedFiles[index], raw));

const base = publishedRaw.find(raw => raw.schema_version === "1.2" && raw.model.id === "qwen2.5-coder-14b-instruct");
const fact = (value, provenance = "detected") => ({ value, provenance });

// A labelled fixture: the Qwen record on another machine.
function fixture(label, change) {
  const raw = structuredClone(base);
  raw.run_id = `run-fixture-${label}`;
  change(raw);
  const serving = raw.environment.serving;
  raw.hardware.gpus = serving.accelerators.filter(device => device.role === "inference")
    .map(({ vendor, model, memory_gb, count }) => ({ vendor, model, memory_gb, count }));
  if (raw.execution_device === "cpu") raw.hardware.driver_version = null;
  return readResultRecord(`labelled fixture: ${label}`, raw);
}
const gpu = (model, memory, count = 1, role = "inference") => ({ vendor: "nvidia", model, memory_gb: memory, count, role, provenance: "detected" });

const twoEights = fixture("two-8gb-cards", raw => {
  raw.environment.serving.accelerators = [gpu("NVIDIA GeForce RTX 4060", 8, 2)];
});
const sixteenNvidia = fixture("one-16gb-nvidia", raw => {
  raw.environment.serving.accelerators = [gpu("NVIDIA GeForce RTX 4070 Ti SUPER", 16)];
});
const cpuOnly = fixture("cpu-only", raw => {
  raw.execution_device = "cpu";
  raw.environment.serving.accelerators = [gpu("AMD Radeon RX 9070", 16, 1, "present-not-used")];
  raw.environment.serving.accelerators[0].vendor = "amd";
  raw.environment.serving.placement = { mode: "cpu-only", detail: null, provenance: "contributor-declared" };
});
const split = fixture("split", raw => {
  raw.environment.serving.placement = { mode: "cpu-gpu-offload", detail: "30 of 48 layers on GPU", provenance: "contributor-declared" };
});
const withCores = fixture("with-cores", raw => {
  Object.assign(raw.environment.serving.host, { cpu_cores: fact(8), cpu_threads: fact(16), ram_installed_gb: fact(32) });
  raw.model.tokenizer.revision = "0123456789abcdef0123456789abcdef01234567";
  raw.environment.serving.accelerators[0].memory_gb = 15.92;
});
// A record whose card memory nobody recorded. Published records must state it
// for an inference GPU, so this one is edited after reading, in memory only.
const unknownVramBase = fixture("unknown-vram", () => {});
const unknownVram = { ...unknownVramBase, serving: { ...unknownVramBase.serving,
  accelerators: [{ vendor: "intel", model: "Intel(R) Arc(TM) A770", memoryGb: null, count: 1, role: "inference", provenance: "unknown" }] } };

test("the real RX 9070 runs are found by GPU and 16 GB per GPU", () => {
  const filters = { ...hf.NO_FILTERS, gpu: "AMD Radeon RX 9070", vram: "vram-16" };
  const found = hf.filterResults(published, filters);
  assert.equal(found.length, published.length, "every published run so far is this machine");
  assert.ok(found.length >= 4);
  const options = hf.filterOptions(published);
  assert.deepEqual(options.gpu.map(o => o.value), ["AMD Radeon RX 9070"]);
  assert.deepEqual(options.vram.map(o => o.value), ["vram-16"]);
  assert.deepEqual(options.ram.map(o => o.value), ["ram-32"]);
  assert.deepEqual(options.mode.map(o => o.value), ["gpu-only"]);
});

test("then a model and a profile narrow it to the runs that measured the same thing", () => {
  const all = [...published, twoEights, sixteenNvidia, cpuOnly, split];
  const filters = { ...hf.NO_FILTERS, gpu: "AMD Radeon RX 9070", vram: "vram-16", model: "qwen2.5-coder-14b-instruct", profile: "chat-small-v2" };
  const found = hf.filterResults(all, filters);
  assert.deepEqual(found.map(r => r.id).sort(), ["run-cc80dd597abb", "run-fixture-split"].sort());
  assert.deepEqual(hf.differingSettings(found), ["execution mode"]);
  const gpuOnly = hf.filterResults(all, { ...filters, mode: "gpu-only" });
  assert.deepEqual(gpuOnly.map(r => r.id), ["run-cc80dd597abb"]);
});

test("two 8 GB cards never match a 16 GB-per-GPU filter", () => {
  const vram16 = { ...hf.NO_FILTERS, vram: "vram-16" };
  assert.equal(hf.matchesFilters(twoEights, vram16), false);
  assert.equal(hf.matchesFilters(sixteenNvidia, vram16), true);
  assert.equal(hf.matchesFilters(twoEights, { ...hf.NO_FILTERS, vram: "vram-8" }), true);
  const facets = hf.hardwareFacets(twoEights);
  assert.equal(facets.inferenceGpuCount, 2);
  assert.equal(hf.gpuSummary(facets), "2 × NVIDIA GeForce RTX 4060");
  assert.equal(hf.vramSummary(facets), "2 × 8 GB each");
});

test("a card that was present but not used is not an inference GPU", () => {
  const facets = hf.hardwareFacets(cpuOnly);
  assert.equal(facets.executionMode, "cpu-only");
  assert.deepEqual(facets.inferenceGpus, []);
  assert.equal(hf.matchesFilters(cpuOnly, { ...hf.NO_FILTERS, gpu: "AMD Radeon RX 9070" }), false);
  assert.equal(hf.matchesFilters(cpuOnly, { ...hf.NO_FILTERS, gpu: "No inference GPU" }), true);
  assert.equal(hf.matchesFilters(cpuOnly, { ...hf.NO_FILTERS, mode: "cpu-only" }), true);
});

test("split CPU/GPU execution is its own mode", () => {
  assert.equal(hf.hardwareFacets(split).executionMode, "cpu-gpu-offload");
  assert.equal(hf.EXECUTION_MODE_LABEL["cpu-gpu-offload"], "Split CPU/GPU");
});

test("unknown memory is not zero and matches only 'Not recorded'", () => {
  for (const range of hf.VRAM_RANGES) {
    assert.equal(hf.matchesFilters(unknownVram, { ...hf.NO_FILTERS, vram: range.id }), false, range.id);
  }
  assert.equal(hf.matchesFilters(unknownVram, { ...hf.NO_FILTERS, vram: "Not recorded" }), true);
  assert.equal(hf.vramSummary(hf.hardwareFacets(unknownVram)), "Not recorded");
  assert.ok(hf.filterOptions([unknownVram]).vram.some(o => o.value === "Not recorded"));
});

test("Windows reporting 15.92 GB for a 16 GB card still matches 16 GB; installed RAM is preferred", () => {
  const facets = hf.hardwareFacets(withCores);
  assert.equal(facets.cpuCores, 8);
  assert.equal(facets.cpuThreads, 16);
  assert.equal(facets.ramGb, 32);
  assert.equal(facets.ramIsInstalled, true);
  assert.equal(hf.matchesFilters(withCores, { ...hf.NO_FILTERS, vram: "vram-16", ram: "ram-32" }), true);
});

test("the 1.2 facts appear in the evidence summary only when recorded", () => {
  const rows = summarize(withCores).sections.flatMap(section => section.rows);
  const byLabel = Object.fromEntries(rows.map(row => [row.label, row.value]));
  assert.equal(byLabel["Serving CPU cores"], "8 physical cores · 16 threads");
  assert.equal(byLabel["Installed memory"], "32 GB");
  assert.equal(byLabel["Tokenizer revision"], "0123456789abcdef0123456789abcdef01234567");
  const older = summarize(published[0]).sections.flatMap(section => section.rows).map(row => row.label);
  assert.ok(!older.includes("Serving CPU cores"));
  assert.ok(!older.includes("Installed memory"));
});

test("an older result without serving metadata stays browsable", () => {
  const checksum = `sha256:${"a".repeat(64)}`;
  const legacy = readResultRecord("labelled fixture: schema 1.0", {
    schema_version: "1.0", run_id: "run-fixture-legacy", started_at: "2026-01-01T00:00:00Z", configuration_fingerprint: checksum, execution_device: "gpu",
    model: { id: "Fixture/Model", revision: "immutable-fixture-revision", source: "https://example.invalid/fixture.gguf", quantization: null },
    runtime: { framework: "llama.cpp", version: "fixture", arguments: [], endpoint_type: "openai-compatible" },
    hardware: { cpu: "Fixture CPU", os: "Fixture Linux", ram_gb: 64, driver_version: "1", gpus: [{ vendor: "nvidia", model: "Fixture GPU", memory_gb: 24, count: 1 }] },
    benchmark: { profile_id: "fixture-chat-v1", profile_sha256: checksum, runner: "guidellm", runner_version: "0.7.3" },
    results: { requests: { attempted: 50, succeeded: 50, failed: 0 }, latency_ms: { ttft: { p50: 100, p95: 200 }, inter_token: { p50: 20, p95: 40 } }, output_tokens_per_second: 12, total_tokens_per_second: 60, requests_per_second: 0.05, error_rate_percent: 0 },
    submission: { submitter: "fixture", run_kind: "initial" },
    artifacts: Object.fromEntries(["raw_runner_output", "runner_stdout_log", "environment_snapshot", "reproduce_markdown"].map(key => [key, { path: `fixture-${key}`, sha256: checksum }])),
  });
  const facets = hf.hardwareFacets(legacy);
  assert.equal(facets.executionMode, "unknown");
  assert.equal(facets.cpuCores, null);
  assert.equal(facets.quantization, "Not recorded");
  assert.equal(hf.matchesFilters(legacy, { ...hf.NO_FILTERS, vram: "vram-24", gpu: "Fixture GPU" }), true);
  assert.equal(hf.filterResults([legacy, ...published], hf.NO_FILTERS).length, published.length + 1);
});

test("the runtime is named once even when its version already carries the name", () => {
  const row = published.find(r => r.model === "qwen2.5-coder-14b-instruct");
  assert.equal(hf.runtimeWithVersion(row), "LM Studio 0.4.28 (build 2)");
});
