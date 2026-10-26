type JsonObject = Record<string, unknown>;

export type RegistryArtifact = {
  label: string;
  path: string;
  sha256: string;
};

export type ExecutionDevice = "cpu" | "gpu";

export type ResultRecord = {
  artifacts: RegistryArtifact[];
  configurationFingerprint: string;
  executionDevice: ExecutionDevice;
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
  modelSource: string | null;
  outputTokensPerSecond: number;
  profile: string;
  profileSha256: string;
  itlP50: number;
  itlP95: number;
  errorRatePercent: number;
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

// Absent on a result.json published before execution-device tracking; those
// predate this field and were, by definition, GPU runs (v1 packaged nothing else).
function executionDeviceField(value: JsonObject, sourcePath: string): ExecutionDevice {
  const field = value.execution_device;
  if (field === undefined) {
    return "gpu";
  }
  if (field !== "cpu" && field !== "gpu") {
    throw new Error(`Local result ${sourcePath} has an invalid execution_device value.`);
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

export function readResultRecord(sourcePath: string, value: unknown): ResultRecord {
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
  const itl = objectField(latency, "inter_token", sourcePath);
  const submission = objectField(value, "submission", sourcePath);
  const artifacts = objectField(value, "artifacts", sourcePath);
  const executionDevice = executionDeviceField(value, sourcePath);
  const gpuValues = hardware.gpus;

  if (!Array.isArray(gpuValues)) {
    throw new Error(`Local result ${sourcePath} has an invalid gpus list.`);
  }
  if (executionDevice === "gpu" && gpuValues.length === 0) {
    throw new Error(`Local result ${sourcePath} declares gpu execution but contains no GPU.`);
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

  const cpu = textField(hardware, "cpu", sourcePath);
  // A cpu run's gpus list is empty even on a GPU-equipped machine: no GPU
  // performed inference, so none is shown as if it had.
  const hardwareDisplay =
    executionDevice === "cpu"
      ? `CPU · ${cpu}`
      : gpus.map((gpu) => `${gpu.count}× ${gpu.model} · ${gpu.memoryGb} GB each`).join(" + ");

  const record: ResultRecord = {
    artifacts: [
      readArtifact(artifacts, "raw_runner_output", "Raw GuideLLM output", sourcePath),
      readArtifact(artifacts, "runner_stdout_log", "GuideLLM log", sourcePath),
      readArtifact(artifacts, "environment_snapshot", "Environment snapshot", sourcePath),
      readArtifact(artifacts, "reproduce_markdown", "Reproduction notes", sourcePath),
    ],
    configurationFingerprint: textField(value, "configuration_fingerprint", sourcePath),
    executionDevice,
    framework: textField(runtime, "framework", sourcePath),
    frameworkVersion: textField(runtime, "version", sourcePath),
    hardware: {
      cpu,
      driverVersion: optionalTextField(hardware, "driver_version", sourcePath),
      display: hardwareDisplay,
      gpus,
      os: textField(hardware, "os", sourcePath),
      ramGb: numberField(hardware, "ram_gb", sourcePath),
    },
    id: textField(value, "run_id", sourcePath),
    model: textField(model, "id", sourcePath),
    modelRevision: textField(model, "revision", sourcePath),
    modelSource: optionalTextField(model, "source", sourcePath),
    outputTokensPerSecond: numberField(results, "output_tokens_per_second", sourcePath),
    profile: textField(benchmark, "profile_id", sourcePath),
    profileSha256: textField(benchmark, "profile_sha256", sourcePath),
    itlP50: numberField(itl, "p50", sourcePath),
    itlP95: numberField(itl, "p95", sourcePath),
    errorRatePercent: numberField(results, "error_rate_percent", sourcePath),
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
  // The browser checks the summary's shape and arithmetic only. It cannot
  // verify evidence files, catalog eligibility, or publication acceptance.
  if (!/^[a-z0-9][a-z0-9-]{7,63}$/.test(record.id)) throw new Error("The run ID is invalid.");
  if (!/^sha256:[a-f0-9]{64}$/.test(record.profileSha256)) throw new Error("The profile checksum is missing or invalid.");
  if (!/^sha256:[a-f0-9]{64}$/.test(record.configurationFingerprint)) throw new Error("The configuration fingerprint is invalid.");
  if (!/^[a-z0-9-]+-v[0-9]+$/.test(record.profile)) throw new Error("The profile ID is invalid.");
  if (["main", "master", "latest"].includes(record.modelRevision.toLowerCase())) throw new Error("An immutable model revision is required.");
  if (!Number.isFinite(Date.parse(record.startedAt))) throw new Error("The run date is invalid.");
  const counts = Object.values(record.requests);
  if (counts.some(n => !Number.isSafeInteger(n)) || record.requests.attempted !== record.requests.succeeded + record.requests.failed) {
    throw new Error("Attempted requests must equal successful plus failed requests, using whole numbers.");
  }
  if (record.requests.attempted === 0 || record.requests.succeeded === 0) throw new Error("This result has no successful measured requests to compare.");
  const errorRate = record.requests.failed / record.requests.attempted * 100;
  if (record.errorRatePercent > 100 || Math.abs(errorRate - record.errorRatePercent) > 0.01) throw new Error("The error rate does not match the request counts.");
  if (record.ttftP95 < record.ttftP50 || record.itlP95 < record.itlP50) throw new Error("A p95 latency cannot be below its p50.");
  if (executionDevice === "cpu" && (gpus.length || record.hardware.driverVersion !== null)) throw new Error("A CPU result must not declare active GPUs or a GPU driver.");
  if (record.hardware.ramGb <= 0 || gpus.some(g => g.memoryGb <= 0 || !Number.isSafeInteger(g.count) || g.count < 1)) throw new Error("Hardware memory and device counts must be positive.");
  if (textField(benchmark, "runner", sourcePath) !== "guidellm" || textField(runtime, "endpoint_type", sourcePath) !== "openai-compatible") throw new Error("Unsupported benchmark runner or endpoint type.");
  if (!["initial", "repeat", "replication"].includes(record.runKind)) throw new Error("The run kind is invalid.");
  if (record.artifacts.some(a => !/^sha256:[a-f0-9]{64}$/.test(a.sha256))) throw new Error("The evidence checksums are missing or invalid.");
  return record;
}

