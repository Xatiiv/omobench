import type { ComparisonEntry } from "@/app/lib/comparison";

// Fictional values for the opt-in interface preview. Never registry evidence.
const scenarios = [
  { id: "example-reference", name: "Example CPU setup A", device: "cpu", output: 12, ttft: [850, 1400], itl: [65, 98], ram: 32 },
  { id: "example-cpu-b", name: "Example CPU setup B", device: "cpu", output: 18, ttft: [620, 1050], itl: [48, 72], ram: 64 },
  { id: "example-gpu-a", name: "Example GPU setup A", device: "gpu", output: 48, ttft: [240, 430], itl: [17, 25], ram: 32 },
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
    hardware: { cpu: s.device === "cpu" ? s.name : "Example host CPU", display: s.name, ramGb: s.ram, os: "Example OS", driverVersion: null,
      gpus: s.device === "gpu" ? [{ count: 1, memoryGb: 16, model: "Example GPU" }] : [] },
    outputTokensPerSecond: s.output,
    ttftP50: s.ttft[0], ttftP95: s.ttft[1], itlP50: s.itl[0], itlP95: s.itl[1],
    requests: { attempted: 50, succeeded: 50, failed: 0 }, errorRatePercent: 0,
    requestsPerSecond: s.output / 256, totalTokensPerSecond: s.output * 5,
    runKind: "initial", startedAt: "2026-01-01T00:00:00Z", submitter: "Illustrative setup", sourceCommit: null, artifacts: [],
  },
}));
