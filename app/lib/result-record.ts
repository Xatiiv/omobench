type JsonObject = Record<string, unknown>;

export type RegistryArtifact = {
  label: string;
  path: string;
  sha256: string;
};

export type ExecutionDevice = "cpu" | "gpu";
export type SchemaVersion = "1.0" | "1.1";
export type TransportKind = "direct" | "windows-wsl-stdio-bridge";
export type ServingRelationship = "same-host" | "windows-host-wsl-runner";
export type PlacementMode = "cpu-only" | "gpu-only" | "cpu-gpu-offload" | "unknown";
export type Provenance = "detected" | "server-reported" | "contributor-declared" | "unknown";

export type ServingAccelerator = {
  count: number;
  memoryGb: number | null;
  model: string;
  provenance: Provenance;
  role: "inference" | "present-not-used" | "unknown";
  vendor: string;
};

export type ServingEnvironment = {
  accelerators: ServingAccelerator[];
  application: { name: string; version: string } | null;
  engine: { name: string; version: string | null };
  host: { cpu: Fact; os: Fact; ramGb: NumericFact };
  placement: { detail: string | null; mode: PlacementMode };
  relationship: ServingRelationship;
  runner: { cpu: string | null; kind: "native" | "wsl2"; os: string; ramGb: number | null };
  settings: Array<{ name: string; value: string }>;
};

export type Fact = { provenance: Provenance; value: string | null };
export type NumericFact = { provenance: Provenance; value: number | null };

export type Transport = {
  bridgeVersion: string | null;
  kind: TransportKind;
  overheadIncluded: boolean;
  protocol: string;
};

export type WeightIdentity = {
  fileCount: number;
  identity: "repository-revision" | "content-hash";
  kind: "repository" | "local-files";
  manifestSha256: string | null;
  repository: string | null;
  repositoryRevision: string | null;
};

export type TokenizerIdentity = {
  className: string | null;
  kind: "huggingface" | "local-directory" | "served-model-default";
  loaded: "loaded-before-run" | "not-checked";
  source: string;
  vocabularySize: number | null;
};

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
    gpus: Array<{ count: number; memoryGb: number; model: string; vendor: string | null }>;
    os: string;
    ramGb: number;
  };
  id: string;
  // A 1.0 result records no catalog status. It could not have been published
  // without being catalogued, because the registry refused anything else, so
  // this states what that gate established rather than inventing a fact.
  catalogStatus: "catalogued" | "proposed";
  metadataDeclaredAfterExecution: boolean;
  model: string;
  modelRevision: string;
  modelSource: string | null;
  schemaVersion: SchemaVersion;
  // Absent on a 1.0 result, where none of it was collected. Nothing here is
  // reconstructed from the runner's own environment: on a bridged run those
  // are different machines.
  serving: ServingEnvironment | null;
  servedModelId: string | null;
  tokenizer: TokenizerIdentity | null;
  // A 1.0 result could only be published as a direct run -- the registry
  // refused bridged evidence outright -- so "direct" is what that gate
  // established. transportRecorded says whether the result itself says so.
  transport: Transport;
  transportRecorded: boolean;
  weights: WeightIdentity | null;
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

const PROVENANCE: Provenance[] = ["detected", "server-reported", "contributor-declared", "unknown"];

function enumField<T extends string>(value: unknown, allowed: readonly T[], label: string, sourcePath: string): T {
  if (typeof value !== "string" || !(allowed as readonly string[]).includes(value)) {
    throw new Error(`Local result ${sourcePath} has an invalid ${label} value.`);
  }
  return value as T;
}

// A fact and its origin always travel together. A value with nothing behind it
// would be invented; an "unknown" that still carried a value would be laundered
// of where it came from. Either way the record is refused rather than shown.
function readFact(value: unknown, label: string, sourcePath: string): Fact {
  if (!isObject(value)) throw new Error(`Local result ${sourcePath} has an invalid ${label} fact.`);
  const provenance = enumField(value.provenance, PROVENANCE, `${label} provenance`, sourcePath);
  const inner = value.value;
  if (inner === null) {
    if (provenance !== "unknown") throw new Error(`Local result ${sourcePath} marks an absent ${label} as ${provenance}.`);
    return { provenance, value: null };
  }
  if (typeof inner !== "string" || !inner.trim()) throw new Error(`Local result ${sourcePath} has an invalid ${label} value.`);
  if (provenance === "unknown") throw new Error(`Local result ${sourcePath} records a known ${label} as unknown.`);
  return { provenance, value: inner };
}

function readNumericFact(value: unknown, label: string, sourcePath: string): NumericFact {
  if (!isObject(value)) throw new Error(`Local result ${sourcePath} has an invalid ${label} fact.`);
  const provenance = enumField(value.provenance, PROVENANCE, `${label} provenance`, sourcePath);
  const inner = value.value;
  if (inner === null) {
    if (provenance !== "unknown") throw new Error(`Local result ${sourcePath} marks an absent ${label} as ${provenance}.`);
    return { provenance, value: null };
  }
  if (typeof inner !== "number" || !Number.isFinite(inner) || inner <= 0) throw new Error(`Local result ${sourcePath} has an invalid ${label} value.`);
  if (provenance === "unknown") throw new Error(`Local result ${sourcePath} records a known ${label} as unknown.`);
  return { provenance, value: inner };
}

function readServing(environment: JsonObject, sourcePath: string): ServingEnvironment {
  const serving = objectField(environment, "serving", sourcePath);
  const host = objectField(serving, "host", sourcePath);
  const runner = objectField(environment, "runner", sourcePath);
  const runtime = objectField(serving, "runtime", sourcePath);
  const engine = objectField(runtime, "engine", sourcePath);
  const placement = objectField(serving, "placement", sourcePath);
  const accelerators = serving.accelerators;
  if (!Array.isArray(accelerators)) throw new Error(`Local result ${sourcePath} has an invalid serving accelerator list.`);
  const application = runtime.application;
  if (application !== null && application !== undefined && !isObject(application)) {
    throw new Error(`Local result ${sourcePath} has an invalid serving application.`);
  }
  const settings = runtime.settings;
  if (settings !== undefined && !Array.isArray(settings)) {
    throw new Error(`Local result ${sourcePath} has an invalid serving settings list.`);
  }
  return {
    accelerators: accelerators.map((device, index) => {
      if (!isObject(device)) throw new Error(`Local result ${sourcePath} has an invalid accelerator at position ${index + 1}.`);
      const memory = device.memory_gb;
      if (memory !== null && (typeof memory !== "number" || !Number.isFinite(memory) || memory <= 0)) {
        throw new Error(`Local result ${sourcePath} has an invalid accelerator memory value.`);
      }
      return {
        count: numberField(device, "count", sourcePath),
        memoryGb: memory,
        model: textField(device, "model", sourcePath),
        provenance: enumField(device.provenance, PROVENANCE, "accelerator provenance", sourcePath),
        role: enumField(device.role, ["inference", "present-not-used", "unknown"] as const, "accelerator role", sourcePath),
        vendor: textField(device, "vendor", sourcePath),
      };
    }),
    application: isObject(application)
      ? { name: textField(application, "name", sourcePath), version: textField(application, "version", sourcePath) }
      : null,
    engine: { name: textField(engine, "name", sourcePath), version: optionalTextField(engine, "version", sourcePath) },
    host: {
      cpu: readFact(host.cpu, "serving cpu", sourcePath),
      os: readFact(host.os, "serving os", sourcePath),
      ramGb: readNumericFact(host.ram_gb, "serving ram_gb", sourcePath),
    },
    placement: {
      detail: optionalTextField(placement, "detail", sourcePath),
      mode: enumField(placement.mode, ["cpu-only", "gpu-only", "cpu-gpu-offload", "unknown"] as const, "placement mode", sourcePath),
    },
    relationship: enumField(environment.relationship, ["same-host", "windows-host-wsl-runner"] as const, "relationship", sourcePath),
    runner: {
      cpu: optionalTextField(runner, "cpu", sourcePath),
      kind: enumField(runner.kind, ["native", "wsl2"] as const, "runner kind", sourcePath),
      os: textField(runner, "os", sourcePath),
      ramGb: runner.ram_gb === null || runner.ram_gb === undefined ? null : numberField(runner, "ram_gb", sourcePath),
    },
    settings: (settings ?? []).map((entry) => {
      if (!isObject(entry)) throw new Error(`Local result ${sourcePath} has an invalid serving setting.`);
      return { name: textField(entry, "name", sourcePath), value: typeof entry.value === "string" ? entry.value : "" };
    }),
  };
}

function readTransport(environment: JsonObject, sourcePath: string): Transport {
  const transport = objectField(environment, "transport", sourcePath);
  const kind = enumField(transport.kind, ["direct", "windows-wsl-stdio-bridge"] as const, "transport kind", sourcePath);
  const overhead = enumField(transport.overhead, ["none-direct", "included-not-measured"] as const, "transport overhead", sourcePath);
  if ((kind === "windows-wsl-stdio-bridge") !== (overhead === "included-not-measured")) {
    throw new Error(`Local result ${sourcePath} describes a transport whose overhead does not match its kind.`);
  }
  return {
    bridgeVersion: optionalTextField(transport, "bridge_version", sourcePath),
    kind,
    // Never "corrected for": a relay sits inside every measured request here.
    overheadIncluded: overhead === "included-not-measured",
    protocol: textField(transport, "protocol", sourcePath),
  };
}

function readWeights(model: JsonObject, sourcePath: string): WeightIdentity | null {
  const weights = model.weights;
  if (weights === undefined || weights === null) return null;
  if (!isObject(weights)) throw new Error(`Local result ${sourcePath} has an invalid weights record.`);
  const content = weights.content;
  if (content !== null && content !== undefined && !isObject(content)) {
    throw new Error(`Local result ${sourcePath} has an invalid weight content record.`);
  }
  const files = isObject(content) ? content.files : null;
  return {
    fileCount: Array.isArray(files) ? files.length : 0,
    identity: enumField(weights.identity, ["repository-revision", "content-hash"] as const, "weights identity", sourcePath),
    kind: enumField(weights.kind, ["repository", "local-files"] as const, "weights kind", sourcePath),
    manifestSha256: isObject(content) ? textField(content, "manifest_sha256", sourcePath) : null,
    repository: optionalTextField(weights, "repository", sourcePath),
    repositoryRevision: optionalTextField(weights, "repository_revision", sourcePath),
  };
}

function readTokenizer(model: JsonObject, sourcePath: string): TokenizerIdentity | null {
  const tokenizer = model.tokenizer;
  if (tokenizer === undefined || tokenizer === null) return null;
  if (!isObject(tokenizer)) throw new Error(`Local result ${sourcePath} has an invalid tokenizer record.`);
  // Kept explicit so no reader infers a match from a successful load.
  if (tokenizer.matches_served_weights !== "not-verifiable-here") {
    throw new Error(`Local result ${sourcePath} claims a tokenizer match nothing can establish.`);
  }
  const identity = tokenizer.identity;
  if (identity !== null && identity !== undefined && !isObject(identity)) {
    throw new Error(`Local result ${sourcePath} has an invalid tokenizer identity.`);
  }
  const vocabulary = isObject(identity) ? identity.vocabulary_size : null;
  return {
    className: isObject(identity) ? optionalTextField(identity, "class", sourcePath) : null,
    kind: enumField(tokenizer.kind, ["huggingface", "local-directory", "served-model-default"] as const, "tokenizer kind", sourcePath),
    loaded: enumField(tokenizer.loaded, ["loaded-before-run", "not-checked"] as const, "tokenizer loaded", sourcePath),
    source: textField(tokenizer, "source", sourcePath),
    vocabularySize: typeof vocabulary === "number" && Number.isInteger(vocabulary) && vocabulary >= 0 ? vocabulary : null,
  };
}

export function readResultRecord(sourcePath: string, value: unknown): ResultRecord {
  if (!isObject(value)) {
    throw new Error(`Local result ${sourcePath} must contain a JSON object.`);
  }
  if (value.schema_version !== "1.0" && value.schema_version !== "1.1") {
    throw new Error(`Local result ${sourcePath} must use OmoBench schema version 1.0 or 1.1.`);
  }
  const schemaVersion = value.schema_version as SchemaVersion;

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
  const environment = value.environment;
  if (schemaVersion === "1.1") {
    if (!isObject(environment)) throw new Error(`Local result ${sourcePath} is schema 1.1 but has no environment.`);
  } else if (environment !== undefined) {
    // A stored 1.0 bundle keeps the meaning it was written with. It cannot
    // acquire serving metadata in place, because nobody collected any.
    throw new Error(`Local result ${sourcePath} is schema 1.0 and cannot carry an environment.`);
  }
  const serving = isObject(environment) ? readServing(environment, sourcePath) : null;
  const transport = isObject(environment)
    ? readTransport(environment, sourcePath)
    : { bridgeVersion: null, kind: "direct" as const, overheadIncluded: false, protocol: "Not recorded" };
  const packaging = submission.packaging;
  if (packaging !== undefined && !isObject(packaging)) {
    throw new Error(`Local result ${sourcePath} has an invalid submission.packaging record.`);
  }
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
      // Absent on results published before vendor-neutral detection; those
      // stay valid and simply have no vendor to show.
      vendor: optionalTextField(gpu, "vendor", sourcePath),
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
    catalogStatus: model.catalog_status === "proposed" ? "proposed" : "catalogued",
    metadataDeclaredAfterExecution:
      isObject(packaging) && packaging.metadata_declared_after_execution === true,
    schemaVersion,
    serving,
    servedModelId: optionalTextField(model, "served_model_id", sourcePath),
    tokenizer: readTokenizer(model, sourcePath),
    transport,
    transportRecorded: serving !== null,
    weights: readWeights(model, sourcePath),
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
  if (record.serving) {
    // The devices that performed inference and hardware.gpus are the same
    // claim. Two places that can disagree are one place that cannot be
    // trusted, so a result that says both is refused rather than displayed.
    const inference = new Map<string, number>();
    for (const device of record.serving.accelerators) {
      if (device.role !== "inference") continue;
      const key = `${device.vendor}|${device.model}|${device.memoryGb}`;
      inference.set(key, (inference.get(key) ?? 0) + device.count);
    }
    const declared = new Map(gpus.map(gpu => [`${gpu.vendor}|${gpu.model}|${gpu.memoryGb}`, gpu.count]));
    if (inference.size !== declared.size || [...inference].some(([key, count]) => declared.get(key) !== count)) {
      throw new Error("The accelerators marked as having performed inference do not match this result's hardware.");
    }
    if ((record.transport.kind === "windows-wsl-stdio-bridge") !== (record.serving.relationship === "windows-host-wsl-runner")) {
      throw new Error("This result's transport and its serving arrangement describe different setups.");
    }
    if (record.serving.placement.mode === "cpu-gpu-offload" && !record.serving.placement.detail) {
      throw new Error("A split CPU/GPU placement has to say how the weights were split.");
    }
  }
  if (record.catalogStatus === "proposed" && record.schemaVersion !== "1.1") {
    throw new Error("Only a schema 1.1 result can propose a model for the catalog.");
  }
  return record;
}

