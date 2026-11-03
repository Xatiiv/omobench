// Turn a result into something a person can read without the schema beside it.
//
// This mirrors cli/src/omobench/evidence.py row for row and label for label:
// the panel shows a contributor what their own evidence says, this shows a
// reader the same thing about a published result, and the two must not drift
// into saying it differently. cli/tests/test_evidence_parity.py holds them to
// the same output for the same result.
//
// Three rules shape it. A fact nobody recorded reads as "Not recorded" rather
// than being omitted, because a missing row is a gap a reader fills with an
// assumption. Nothing is scored: a detected processor and a declared GPU
// memory are not two points on one scale. And detection is not verification --
// reading a machine establishes what it reports about itself and no more.

import type { ResultRecord } from "./result-record";

export const NOT_RECORDED = "Not recorded";

export const PROVENANCE_LABELS: Record<string, string> = {
  detected: "read on that machine",
  "server-reported": "reported by the server",
  "contributor-declared": "stated by the contributor",
  unknown: "not recorded",
};

const DETECTION_PROVENANCE: Record<string, string> = {
  auto: "detected",
  "user-confirmed": "contributor-declared",
  manual: "contributor-declared",
};

const ROLE_LABELS: Record<string, string> = {
  inference: "performed inference",
  "present-not-used": "present, did not serve",
  unknown: "role not recorded",
};

const PLACEMENT_LABELS: Record<string, string> = {
  "cpu-only": "CPU only",
  "gpu-only": "GPU only",
  "cpu-gpu-offload": "Split between CPU and GPU",
  unknown: NOT_RECORDED,
};

const RELATIONSHIP_LABELS: Record<string, string> = {
  "same-host": "One machine served the model and ran the benchmark",
  "windows-host-wsl-runner": "A Windows host served the model; a WSL2 runner measured it",
};

export const STANDING_NOTE =
  "Reading a fact on a machine establishes what that machine reports about itself. "
  + "None of this independently confirms that the hardware named here did the work, or "
  + "that the weights were the ones named.";

export const LEGACY_NOTE =
  "This result was packaged under schema 1.0, which recorded one machine and no serving "
  + "provenance. Everything it did not collect is shown as not recorded, rather than "
  + "filled in from somewhere else.";

export type EvidenceRow = {
  label: string;
  value: string;
  recorded: boolean;
  source: string | null;
  note: string;
};

export type EvidenceSection = { id: string; title: string; rows: EvidenceRow[] };
export type EvidenceSummary = {
  schemaVersion: string;
  sections: EvidenceSection[];
  qualifications: string[];
};

function row(
  label: string,
  value: string | number | null,
  options: { provenance?: string | null; note?: string } = {},
): EvidenceRow {
  const recorded = value !== null && value !== undefined && value !== "";
  const { provenance = null, note = "" } = options;
  return {
    label,
    value: recorded ? String(value) : NOT_RECORDED,
    recorded,
    source: !recorded
      ? PROVENANCE_LABELS.unknown
      : provenance
        ? (PROVENANCE_LABELS[provenance] ?? provenance)
        : null,
    note,
  };
}

function legacyProvenance(result: ResultRecord): string {
  // Schema 1.0 recorded one coarse detection state for the whole hardware
  // block rather than a provenance per fact. These are the same four kinds.
  return DETECTION_PROVENANCE[result.hardwareDetection ?? ""] ?? "unknown";
}

function environments(result: ResultRecord): EvidenceSection {
  if (!result.serving) {
    const detected = legacyProvenance(result);
    return {
      id: "environments",
      title: "Where this ran",
      rows: [
        row("Arrangement", null, {
          note: "Schema 1.0 recorded one machine. Which of the two it describes, when they "
            + "differed, is not in this evidence.",
        }),
        row("Serving operating system", result.hardware.os, { provenance: detected }),
        row("Serving processor", result.hardware.cpu, { provenance: detected }),
        row("Serving memory", result.hardware.ramGb ? `${result.hardware.ramGb} GB` : null,
          { provenance: detected }),
        row("Benchmark runner", null,
          { note: "Not recorded separately from the serving machine." }),
      ],
    };
  }
  const { host, runner } = result.serving;
  return {
    id: "environments",
    title: "Where this ran",
    rows: [
      row("Arrangement", RELATIONSHIP_LABELS[result.serving.relationship] ?? null,
        { provenance: "contributor-declared" }),
      row("Serving operating system", host.os.value, { provenance: host.os.provenance }),
      row("Serving processor", host.cpu.value, { provenance: host.cpu.provenance }),
      row("Serving memory", host.ramGb.value === null ? null : `${host.ramGb.value} GB`,
        { provenance: host.ramGb.provenance }),
      row("Benchmark runner", runner.os ? `${runner.os} (${runner.kind})` : null, {
        provenance: "detected",
        note: "The machine that ran GuideLLM. On a bridged run this is the WSL2 distribution, "
          + "and nothing it can see of its host is evidence about what served the model.",
      }),
    ],
  };
}

function accelerators(result: ResultRecord): EvidenceSection {
  const rows: EvidenceRow[] = [row("Declared execution device", result.executionDevice.toUpperCase(), {
    provenance: "contributor-declared",
    note: "What the contributor says performed inference. OmoBench does not observe this and "
      + "cannot confirm it.",
  })];
  if (!result.serving) {
    const detected = legacyProvenance(result);
    for (const gpu of result.hardware.gpus) {
      rows.push(row("Accelerator",
        `${gpu.count} × ${gpu.model}${gpu.memoryGb ? `, ${gpu.memoryGb} GB each` : ""}`, {
          provenance: detected,
          note: "Schema 1.0 recorded no role for this device. It is listed because the result "
            + "declares GPU execution, not because anything observed it serve.",
        }));
    }
    if (!result.hardware.gpus.length) {
      rows.push(row("Accelerator", null,
        { note: "No accelerator is recorded as having performed inference." }));
    }
    rows.push(row("Weight placement", null,
      { note: "Schema 1.0 did not record how the weights were split." }));
    return { id: "accelerators", title: "What ran the weights", rows };
  }
  const devices = result.serving.accelerators;
  if (!devices.length) {
    rows.push(row("Accelerator", null,
      { note: "No accelerator is recorded for the serving machine." }));
  }
  for (const device of devices) {
    rows.push(row("Accelerator",
      `${device.count} × ${device.vendor} ${device.model}`
      + (device.memoryGb ? `, ${device.memoryGb} GB each` : ", memory not recorded"), {
        provenance: device.provenance,
        note: `${ROLE_LABELS[device.role] ?? ROLE_LABELS.unknown}. Finding a device establishes `
          + "the device, not that inference touched it.",
      }));
  }
  const { mode, detail } = result.serving.placement;
  rows.push(row("Weight placement", mode && mode !== "unknown" ? PLACEMENT_LABELS[mode] : null,
    { provenance: result.serving.placementProvenance, note: detail ?? "" }));
  return { id: "accelerators", title: "What ran the weights", rows };
}

function model(result: ResultRecord): EvidenceSection {
  const rows = [
    row("Canonical model", result.model, {
      provenance: "contributor-declared",
      note: "The catalog identity this result is compared under.",
    }),
    row("Identifier the server answered to", result.servedModelId, {
      provenance: "contributor-declared",
      note: "A serving alias. It is not a claim about which weights were loaded.",
    }),
    row("Catalog status",
      result.schemaVersion === "1.1"
        ? (result.catalogStatus === "proposed" ? "Proposed — awaiting review" : "Catalogued")
        : null,
      {
        provenance: result.schemaVersion === "1.1" ? "contributor-declared" : null,
        note: result.catalogStatus === "proposed"
          ? "This model is not in the catalog. The bundle carries a proposal for review; nothing "
            + "here makes it an eligible model."
          : "",
      }),
    row("Quantization", result.quantization, { provenance: "contributor-declared" }),
  ];
  const weights = result.weights;
  if (!weights) {
    rows.push(row("Weights", result.modelRevision, {
      provenance: "contributor-declared",
      note: result.modelSource
        ? `From ${result.modelSource}.`
        : "Recorded as a revision only. Which repository it is a revision of is not separately "
          + "recorded.",
    }));
  } else if (weights.identity === "content-hash") {
    rows.push(row("Weights", `${weights.fileCount} file(s), identified by their bytes`, {
      provenance: "contributor-declared",
      note: `Manifest ${weights.manifestSha256}. These weights have no public immutable `
        + "revision, so the bytes themselves are the identity.",
    }));
  } else {
    rows.push(row("Weights", `${weights.repository} at ${weights.repositoryRevision}`, {
      provenance: "contributor-declared",
      note: "A repository commit. A file checksum is a different thing and is not this.",
    }));
  }
  return { id: "model", title: "Which model, and which weights", rows };
}

function runtime(result: ResultRecord): EvidenceSection {
  const rows = [row("Framework", result.framework, { provenance: "contributor-declared" })];
  if (!result.serving) {
    rows.push(row("Serving runtime build", result.frameworkVersion, {
      provenance: "contributor-declared",
      note: "Schema 1.0 recorded one version. Whether it is the inference engine's build or a "
        + "hosting application's release is not distinguished here.",
    }));
    rows.push(row("Hosting application", null,
      { note: "Schema 1.0 did not record this separately." }));
  } else {
    const { engine, application, settings } = result.serving;
    rows.push(row("Inference engine",
      engine.name ? `${engine.name} ${engine.version ?? ""}`.trim() : null,
      { provenance: engine.provenance, note: "What executed the weights." }));
    rows.push(row("Hosting application",
      application ? `${application.name} ${application.version}` : null, {
        provenance: application?.provenance ?? null,
        note: application
          ? "The program hosting the engine. Its release version is a different thing from the "
            + "engine build above."
          : "No hosting application is recorded; the engine was run directly.",
      }));
    for (const setting of settings) {
      rows.push(row(`Serving setting · ${setting.name}`, setting.value,
        { provenance: setting.provenance }));
    }
  }
  rows.push(row("Startup arguments", result.runtimeArguments.join(" ") || null,
    { provenance: "contributor-declared" }));
  return { id: "runtime", title: "What served it", rows };
}

function tokenizer(result: ResultRecord): EvidenceSection {
  if (!result.tokenizer) {
    return {
      id: "tokenizer",
      title: "What counted the tokens",
      rows: [row("Tokenizer source", null, {
        note: "Schema 1.0 did not record which tokenizer built and counted the workload. Every "
          + "token figure in this result is some tokenizer's count; which one is not in this "
          + "evidence.",
      })],
    };
  }
  const loaded = result.tokenizer.loaded === "loaded-before-run";
  return {
    id: "tokenizer",
    title: "What counted the tokens",
    rows: [
      row("Tokenizer source", result.tokenizer.source, {
        provenance: "contributor-declared",
        note: result.tokenizer.kind === "served-model-default"
          ? "Used as the served identifier's default rather than chosen." : "",
      }),
      row("Loaded before the run",
        loaded ? "Yes, in the runner's own interpreter" : "Not checked by OmoBench",
        { provenance: loaded ? "detected" : null }),
      row("Tokenizer implementation", result.tokenizer.className, { provenance: "detected" }),
      row("Vocabulary size", result.tokenizer.vocabularySize, { provenance: "detected" }),
      row("Matches the served weights", "Not verifiable from this evidence", {
        note: "A tokenizer that loads is not thereby the one the served weights were trained "
          + "with, and nothing available to OmoBench can establish that it is.",
      }),
    ],
  };
}

function transport(result: ResultRecord): EvidenceSection {
  if (!result.serving) {
    return {
      id: "transport",
      title: "How the requests travelled",
      rows: [row("Transport", "Direct to the server", {
        provenance: "contributor-declared",
        note: "Schema 1.0 could only carry a direct run: the registry refused bridged evidence "
          + "outright. This result records no transport of its own.",
      })],
    };
  }
  const bridged = result.transport.kind === "windows-wsl-stdio-bridge";
  return {
    id: "transport",
    title: "How the requests travelled",
    rows: [
      row("Transport", bridged ? "Through the Windows–WSL bridge" : "Direct to the server", {
        provenance: "detected",
        note: bridged ? `Bridge ${result.transport.bridgeVersion}.` : "",
      }),
      row("Protocol", result.transport.protocol, { provenance: "detected" }),
      row("Transport latency",
        bridged ? "Inside every measurement, never subtracted" : "None to account for", {
          provenance: "detected",
          note: bridged
            ? "A relay sits inside every request this run made and its cost has not been "
              + "separated out, so these numbers are comparable with other bridged runs rather "
              + "than with direct ones."
            : "",
        }),
    ],
  };
}

export function summarize(result: ResultRecord): EvidenceSummary {
  const qualifications = [STANDING_NOTE];
  if (!result.serving) qualifications.unshift(LEGACY_NOTE);
  if (result.transport.kind === "windows-wsl-stdio-bridge" && result.serving) {
    qualifications.push("Measured through the Windows–WSL bridge. Its latency is inside every "
      + "number here and has not been subtracted.");
  }
  if (result.catalogStatus === "proposed") {
    qualifications.push("This model is not catalogued. The bundle proposes it for review, which "
      + "is a separate decision from accepting this result.");
  }
  if (result.metadataDeclaredAfterExecution) {
    qualifications.push("This bundle was packaged later from saved evidence. The measurements "
      + "are the original ones; the serving details were supplied afterwards.");
  }
  return {
    schemaVersion: result.schemaVersion,
    sections: [
      environments(result), accelerators(result), model(result),
      runtime(result), tokenizer(result), transport(result),
    ],
    qualifications,
  };
}
