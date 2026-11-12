import type { ComparisonEntry } from "@/app/lib/comparison";
import type { ResultRecord, ServingEnvironment } from "@/app/lib/result-record";

// Fictional values for the opt-in interface preview. Never registry evidence.
//
// The set is chosen to show what the comparison explanations actually say, so
// the preview demonstrates the reasoning rather than a row of identical cards:
// one pair that matches on everything that is held fixed, one that differs
// where only some questions allow it, one whose evidence is simply absent, one
// measured through the bridge, and one of a different model entirely.
type Scenario = {
  id: string;
  name: string;
  device: "cpu" | "gpu";
  output: number;
  ttft: [number, number];
  itl: [number, number];
  ram: number;
  // The machine, where it is not simply this scenario's own name. Two
  // scenarios sharing one lets the preview show the question OmoBench exists
  // to ask: what does this computer do across the workloads it could run?
  machine?: string;
  note?: string;
  overrides?: Partial<ResultRecord>;
};

const DECLARED = "contributor-declared" as const;
const SPLIT_SERVING: ServingEnvironment = {
  relationship: "same-host",
  host: { os: { value: "Example OS", provenance: DECLARED },
          cpu: { value: "Example host CPU", provenance: DECLARED },
          ramGb: { value: 64, provenance: DECLARED },
          cpuCores: null, cpuThreads: null, ramInstalledGb: null },
  accelerators: [{ vendor: "other", model: "Example GPU", memoryGb: 16, count: 1,
                   role: "inference", provenance: DECLARED }],
  placement: { mode: "cpu-gpu-offload", detail: "32 of 49 layers on GPU" },
  placementProvenance: DECLARED,
  engine: { name: "Other example runtime", version: "2.0", provenance: DECLARED },
  application: null,
  settings: [],
  runner: { cpu: "Example host CPU", kind: "native", os: "Example OS", ramGb: 64 },
};

// The same machine serving and measuring, with the weights undivided. Used by
// the schema 1.2 example so that what distinguishes it is the model and its
// eligibility, not a split placement nobody asked about.
const PLAIN_SERVING: ServingEnvironment = {
  ...SPLIT_SERVING,
  accelerators: [],
  placement: { mode: "cpu-only", detail: null },
  settings: [{ name: "context_length", value: "8192", provenance: DECLARED }],
};

const scenarios: Scenario[] = [
  { id: "example-reference", name: "Example CPU setup A", device: "cpu", output: 12, ttft: [850, 1400], itl: [65, 98], ram: 32 },
  // Everything the questions hold fixed is identical; only the machine differs.
  { id: "example-cpu-b", name: "Example CPU setup B", device: "cpu", output: 18, ttft: [620, 1050], itl: [48, 72], ram: 64 },
  { id: "example-gpu-a", name: "Example GPU setup A", device: "gpu", output: 48, ttft: [240, 430], itl: [17, 25], ram: 32 },
  // A confounded pair: a different engine *and* the weights split across CPU
  // and GPU. A question about the runtime expects both; a question about
  // hardware alone does not.
  {
    id: "example-runtime-b", name: "Example split-placement setup", device: "gpu",
    output: 31, ttft: [380, 700], itl: [28, 41], ram: 64,
    overrides: {
      framework: "Other example runtime", frameworkVersion: "2.0",
      schemaVersion: "1.1", serving: SPLIT_SERVING,
    },
  },
  // Evidence that was never recorded, which is not the same as evidence that
  // differs: nobody wrote down the quantization, the weights source or the
  // tokenizer.
  {
    id: "example-missing", name: "Example setup with gaps", device: "cpu",
    output: 14, ttft: [900, 1500], itl: [70, 110], ram: 16,
    overrides: { quantization: null, modelSource: null, tokenizer: null },
  },
  // A relay sits inside every request this one made, and its cost was never
  // subtracted. No question makes that comparable with a direct run.
  {
    id: "example-bridged", name: "Example bridged setup", device: "gpu",
    output: 22, ttft: [700, 1200], itl: [40, 60], ram: 32,
    overrides: {
      schemaVersion: "1.1", transportRecorded: true,
      transport: { bridgeVersion: "example-bridge-v1", kind: "windows-wsl-stdio-bridge",
                   overheadIncluded: true, protocol: "HTTP/1.1" },
    },
  },
  // A different model. Token rates across models do not measure the same work,
  // which the explanation says rather than leaving the reader to assume.
  {
    id: "example-model-b", name: "Example other-model setup", device: "gpu",
    output: 36, ttft: [300, 520], itl: [22, 33], ram: 32,
    overrides: {
      model: "Illustrative larger model", modelRevision: "example-revision-2",
      modelSource: "https://example.invalid/illustrative-larger-weights.gguf",
      quantization: "Example Q8",
    },
  },
  // A model nobody curated, published on the eligibility it carries rather
  // than on membership of a list. Schema 1.2, so the page has something to
  // render those fields from; still fictional, still never registry evidence.
  {
    id: "example-uncatalogued", name: "Example uncatalogued model", device: "cpu",
    output: 15, ttft: [800, 1350], itl: [60, 95], ram: 32,
    // Deliberately the same machine as the reference: a second workload on
    // one computer, which is the comparison this project is for.
    machine: "Example CPU setup A",
    overrides: {
      schemaVersion: "1.2", catalogStatus: "uncatalogued", catalogStatusRecorded: true,
      model: "example-org/Illustrative-Uncurated-7B",
      modelRevision: "example-revision-3",
      modelSource: "https://example.invalid/illustrative-uncurated-weights.gguf",
      serving: PLAIN_SERVING,
      eligibility: {
        baseModel: null,
        family: "Illustrative",
        licence: "apache-2.0",
        licenceUrl: "https://example.invalid/illustrative-licence",
        modality: "text-generation",
        parameterClass: "7B",
        publisher: "example-org",
        source: "https://example.invalid/illustrative-uncurated",
        weightsPubliclyDownloadable: true,
      },
    },
  },
];

export const comparisonExamples: ComparisonEntry[] = scenarios.map((s, index) => ({
  key: s.id,
  origin: "example",
  result: {
    id: s.id,
    model: "Illustrative model",
    modelRevision: "example-revision",
    modelSource: "https://example.invalid/illustrative-weights.gguf",
    quantization: "Example Q4",
    profile: "example-chat-v1",
    profileSha256: `sha256:${"0".repeat(64)}`,
    runnerVersion: "Example runner",
    framework: "Example runtime",
    frameworkVersion: "Illustrative",
    runtimeArguments: [],
    configurationFingerprint: `sha256:${String(index).repeat(64)}`,
    executionDevice: s.device as "cpu" | "gpu",
    hardware: { cpu: s.device === "cpu" ? (s.machine ?? s.name) : "Example host CPU", display: s.machine ?? s.name, ramGb: s.ram, os: "Example OS", driverVersion: null,
      gpus: s.device === "gpu" ? [{ count: 1, memoryGb: 16, model: "Example GPU", vendor: "other" }] : [] },
    outputTokensPerSecond: s.output,
    ttftP50: s.ttft[0], ttftP95: s.ttft[1], itlP50: s.itl[0], itlP95: s.itl[1],
    requests: { attempted: 50, succeeded: 50, failed: 0 }, errorRatePercent: 0,
    requestsPerSecond: s.output / 256, totalTokensPerSecond: s.output * 5,
    runKind: "initial", startedAt: "2026-01-01T00:00:00Z", submitter: "Illustrative setup", sourceCommit: null, artifacts: [],
    // These examples describe nothing real, so most of them describe no serving
    // environment either: they are shaped like a schema 1.0 record, where none
    // of this was collected, rather than inventing a machine. The two that do
    // carry one are the ones whose point is what it records.
    schemaVersion: "1.0", catalogStatus: "catalogued", catalogStatusRecorded: false,
    metadataDeclaredAfterExecution: false,
    hardwareDetection: null,
    serving: null, servedModelId: null, tokenizer: null, weights: null, execution: null,
    // Schema 1.0 collected no eligibility facts. These examples say so rather
    // than implying somebody declared them.
    eligibility: null,
    transportRecorded: false,
    transport: { bridgeVersion: null, kind: "direct", overheadIncluded: false, protocol: "Not recorded" },
    ...s.overrides,
  },
}));
