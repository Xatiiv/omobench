type JsonObject = Record<string, unknown>;

export type RegistryArtifact = {
  label: string;
  path: string;
  sha256: string;
};

export type RegistryResult = {
  artifacts: RegistryArtifact[];
  configurationFingerprint: string;
  framework: string;
  frameworkVersion: string;
  hardware: {
    cpu: string;
    driverVersion: string | null;
    display: string;
    gpus: Array<{ count: number; memoryGb: number; model: string }>;
    os: string;
    ramGb: number;
  };
  id: string;
  model: string;
  modelRevision: string;
  outputTokensPerSecond: number;
  profile: string;
  quantization: string | null;
  requests: { attempted: number; failed: number; succeeded: number };
  requestsPerSecond: number;
  runKind: string;
  runtimeArguments: string[];
  runnerVersion: string;
  sourceCommit: string | null;
  startedAt: string;
  submitter: string;
  totalTokensPerSecond: number;
  ttftP50: number;
  ttftP95: number;
};

function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function objectField(value: JsonObject, key: string, sourcePath: string): JsonObject {
  const field = value[key];
  if (!isObject(field)) {
    throw new Error(`Local result ${sourcePath} is missing the ${key} object.`);
  }
  return field;
}

function textField(value: JsonObject, key: string, sourcePath: string): string {
  const field = value[key];
  if (typeof field !== "string" || !field.trim()) {
    throw new Error(`Local result ${sourcePath} is missing a usable ${key} value.`);
  }
  return field;
}

function optionalTextField(value: JsonObject, key: string, sourcePath: string): string | null {
  const field = value[key];
  if (field === null || field === undefined) {
    return null;
  }
  if (typeof field !== "string") {
    throw new Error(`Local result ${sourcePath} has an invalid ${key} value.`);
  }
  return field;
}

function numberField(value: JsonObject, key: string, sourcePath: string): number {
  const field = value[key];
  if (typeof field !== "number" || !Number.isFinite(field) || field < 0) {
    throw new Error(`Local result ${sourcePath} has an invalid ${key} value.`);
  }
  return field;
}

function stringArrayField(value: JsonObject, key: string, sourcePath: string): string[] {
  const field = value[key];
  if (!Array.isArray(field) || field.some((item) => typeof item !== "string")) {
    throw new Error(`Local result ${sourcePath} has an invalid ${key} list.`);
  }
  return field;
}

function readArtifact(
  artifacts: JsonObject,
  key: string,
  label: string,
  sourcePath: string,
): RegistryArtifact {
  const artifact = objectField(artifacts, key, sourcePath);
  return {
    label,
    path: textField(artifact, "path", sourcePath),
    sha256: textField(artifact, "sha256", sourcePath),
  };
}

function readRegistryResult(sourcePath: string, value: unknown): RegistryResult {
  if (!isObject(value)) {
    throw new Error(`Local result ${sourcePath} must contain a JSON object.`);
  }
  if (value.schema_version !== "1.0") {
    throw new Error(`Local result ${sourcePath} must use OmoBench schema version 1.0.`);
  }

  const model = objectField(value, "model", sourcePath);
  const runtime = objectField(value, "runtime", sourcePath);
  const hardware = objectField(value, "hardware", sourcePath);
  const benchmark = objectField(value, "benchmark", sourcePath);
  const results = objectField(value, "results", sourcePath);
  const requests = objectField(results, "requests", sourcePath);
  const latency = objectField(results, "latency_ms", sourcePath);
  const ttft = objectField(latency, "ttft", sourcePath);
  const submission = objectField(value, "submission", sourcePath);
  const artifacts = objectField(value, "artifacts", sourcePath);
  const gpuValues = hardware.gpus;

  if (!Array.isArray(gpuValues) || gpuValues.length === 0) {
    throw new Error(`Local result ${sourcePath} must contain at least one GPU.`);
  }

  const gpus = gpuValues.map((gpu, index) => {
    if (!isObject(gpu)) {
      throw new Error(`Local result ${sourcePath} has an invalid GPU at position ${index + 1}.`);
    }
    return {
      count: numberField(gpu, "count", sourcePath),
      memoryGb: numberField(gpu, "memory_gb", sourcePath),
      model: textField(gpu, "model", sourcePath),
    };
  });

  const gpuDisplay = gpus
    .map((gpu) => `${gpu.count}× ${gpu.model} · ${gpu.memoryGb} GB each`)
    .join(" + ");

  return {
    artifacts: [
      readArtifact(artifacts, "raw_runner_output", "Raw GuideLLM output", sourcePath),
      readArtifact(artifacts, "runner_stdout_log", "GuideLLM log", sourcePath),
      readArtifact(artifacts, "environment_snapshot", "Environment snapshot", sourcePath),
      readArtifact(artifacts, "reproduce_markdown", "Reproduction notes", sourcePath),
    ],
    configurationFingerprint: textField(value, "configuration_fingerprint", sourcePath),
    framework: textField(runtime, "framework", sourcePath),
    frameworkVersion: textField(runtime, "version", sourcePath),
    hardware: {
      cpu: textField(hardware, "cpu", sourcePath),
      driverVersion: optionalTextField(hardware, "driver_version", sourcePath),
      display: gpuDisplay,
      gpus,
      os: textField(hardware, "os", sourcePath),
      ramGb: numberField(hardware, "ram_gb", sourcePath),
    },
    id: textField(value, "run_id", sourcePath),
    model: textField(model, "id", sourcePath),
    modelRevision: textField(model, "revision", sourcePath),
    outputTokensPerSecond: numberField(results, "output_tokens_per_second", sourcePath),
    profile: textField(benchmark, "profile_id", sourcePath),
    quantization: optionalTextField(model, "quantization", sourcePath),
    requests: {
      attempted: numberField(requests, "attempted", sourcePath),
      failed: numberField(requests, "failed", sourcePath),
      succeeded: numberField(requests, "succeeded", sourcePath),
    },
    requestsPerSecond: numberField(results, "requests_per_second", sourcePath),
    runKind: textField(submission, "run_kind", sourcePath),
    runtimeArguments: stringArrayField(runtime, "arguments", sourcePath),
    runnerVersion: textField(benchmark, "runner_version", sourcePath),
    sourceCommit: optionalTextField(submission, "source_commit", sourcePath),
    startedAt: textField(value, "started_at", sourcePath),
    submitter: textField(submission, "submitter", sourcePath),
    totalTokensPerSecond: numberField(results, "total_tokens_per_second", sourcePath),
    ttftP50: numberField(ttft, "p50", sourcePath),
    ttftP95: numberField(ttft, "p95", sourcePath),
  };
}

// Vite expands this at build time. Only normalized result.json files under
// results/ are included; raw logs and other evidence remain out of the browser bundle.
const localResultModules = import.meta.glob("../../results/**/result.json", {
  eager: true,
  import: "default",
}) as Record<string, unknown>;

const loadedResults = Object.entries(localResultModules).map(([sourcePath, value]) =>
  readRegistryResult(sourcePath, value),
);

const uniqueRunIds = new Set<string>();
for (const result of loadedResults) {
  if (uniqueRunIds.has(result.id)) {
    throw new Error(`Local results contain duplicate run_id: ${result.id}.`);
  }
  uniqueRunIds.add(result.id);
}

export const registryResults = loadedResults.sort((left, right) =>
  right.startedAt.localeCompare(left.startedAt),
);

export function getRegistryResult(runId: string): RegistryResult | undefined {
  return registryResults.find((result) => result.id === runId);
}
