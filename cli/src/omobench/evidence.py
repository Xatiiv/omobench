"""Turn a result into something a person can read without the schema beside them.

A result.json is a contract, not an explanation. It can say that an accelerator
had role "present-not-used" with provenance "detected", that a transport's
overhead is "included-not-measured", and that a tokenizer's
matches_served_weights is "not-verifiable-here" -- each of which means
something precise and none of which reads as English.

This produces the same facts as labelled rows. Three rules shape it.

Every row says where its value came from, in words rather than schema values,
and a row nobody established says "not recorded" instead of being left out. An
absent fact is information; a missing row is a gap a reader fills in with an
assumption.

Nothing here is scored. There is no number summarising how trustworthy a
result is, because the facts differ in kind rather than in degree: a detected
processor and a declared GPU memory are not two points on one scale, and
averaging them would invent a quantity nobody measured.

And detection is not verification. A probe reading a machine establishes what
that machine reports about itself. Nothing in this evidence is an independent
confirmation that the hardware named did the work, and the summary says so
rather than letting a row full of "detected" imply it.
"""
from __future__ import annotations

from typing import Any

NOT_RECORDED = "Not recorded"

# How each schema provenance value reads to somebody who has not read the
# schema. Deliberately all of the same kind -- a description of who established
# the fact -- so no two of them look like different grades of the same thing.
PROVENANCE_LABELS = {
    "detected": "read on that machine",
    "server-reported": "reported by the server",
    "contributor-declared": "stated by the contributor",
    "unknown": "not recorded",
}

# Schema 1.0 recorded one coarse detection state for the whole hardware block
# rather than a provenance per fact. These are the same four kinds, so a 1.0
# row says where its value came from in the same words as a 1.1 one.
DETECTION_PROVENANCE = {
    "auto": "detected",
    "user-confirmed": "contributor-declared",
    "manual": "contributor-declared",
}

ROLE_LABELS = {
    "inference": "performed inference",
    "present-not-used": "present, did not serve",
    "unknown": "role not recorded",
}

PLACEMENT_LABELS = {
    "cpu-only": "CPU only",
    "gpu-only": "GPU only",
    "cpu-gpu-offload": "Split between CPU and GPU",
    "unknown": NOT_RECORDED,
}

RELATIONSHIP_LABELS = {
    "same-host": "One machine served the model and ran the benchmark",
    "windows-host-wsl-runner": "A Windows host served the model; a WSL2 runner measured it",
}

STANDING_NOTE = (
    "Reading a fact on a machine establishes what that machine reports about itself. "
    "None of this independently confirms that the hardware named here did the work, or "
    "that the weights were the ones named."
)

LEGACY_NOTE = (
    "This result was packaged under schema 1.0, which recorded one machine and no serving "
    "provenance. Everything it did not collect is shown as not recorded, rather than "
    "filled in from somewhere else."
)


def _row(label: str, value: Any, *, provenance: str | None = None, note: str = "") -> dict[str, Any]:
    """One labelled fact. A value of None is shown as not recorded, not hidden."""
    recorded = value is not None and value != ""
    return {
        "label": label,
        "value": str(value) if recorded else NOT_RECORDED,
        "recorded": recorded,
        "source": (PROVENANCE_LABELS.get(provenance, provenance) if recorded and provenance
                   else PROVENANCE_LABELS["unknown"] if not recorded else None),
        "note": note,
    }


def _fact_row(label: str, fact: Any, *, note: str = "") -> dict[str, Any]:
    """A row from a schema 1.1 {value, provenance} pair."""
    if not isinstance(fact, dict):
        return _row(label, None, note=note)
    return _row(label, fact.get("value"), provenance=fact.get("provenance"), note=note)


def _legacy_provenance(hardware: dict[str, Any]) -> str:
    """How a schema 1.0 result says its hardware block was established."""
    state = (hardware.get("detection") or {}).get("hardware")
    return DETECTION_PROVENANCE.get(state, "unknown")


def _environments(result: dict[str, Any], environment: dict[str, Any] | None) -> dict[str, Any]:
    hardware = result.get("hardware") or {}
    if environment is None:
        detected = _legacy_provenance(hardware)
        return {
            "id": "environments",
            "title": "Where this ran",
            "rows": [
                _row("Arrangement", None,
                     note="Schema 1.0 recorded one machine. Which of the two it describes, "
                          "when they differed, is not in this evidence."),
                _row("Serving operating system", hardware.get("os"), provenance=detected),
                _row("Serving processor", hardware.get("cpu"), provenance=detected),
                _row("Serving memory",
                     f"{hardware['ram_gb']} GB" if hardware.get("ram_gb") else None,
                     provenance=detected),
                _row("Benchmark runner", None,
                     note="Not recorded separately from the serving machine."),
            ],
        }
    serving = environment.get("serving") or {}
    host = serving.get("host") or {}
    runner = environment.get("runner") or {}
    ram = host.get("ram_gb") or {}
    return {
        "id": "environments",
        "title": "Where this ran",
        "rows": [
            _row("Arrangement",
                 RELATIONSHIP_LABELS.get(environment.get("relationship")),
                 provenance="contributor-declared"),
            _fact_row("Serving operating system", host.get("os")),
            _fact_row("Serving processor", host.get("cpu")),
            _row("Serving memory",
                 f"{ram['value']} GB" if ram.get("value") is not None else None,
                 provenance=ram.get("provenance")),
            _row("Benchmark runner",
                 f"{runner.get('os')} ({runner.get('kind')})" if runner.get("os") else None,
                 provenance="detected",
                 note="The machine that ran GuideLLM. On a bridged run this is the WSL2 "
                      "distribution, and nothing it can see of its host is evidence about "
                      "what served the model."),
        ],
    }


def _accelerators(result: dict[str, Any], environment: dict[str, Any] | None) -> dict[str, Any]:
    device = result.get("execution_device")
    rows = [_row("Declared execution device",
                 (device or "gpu").upper(),
                 provenance="contributor-declared",
                 note="What the contributor says performed inference. OmoBench does not "
                      "observe this and cannot confirm it.")]
    if environment is None:
        hardware = result.get("hardware") or {}
        detected = _legacy_provenance(hardware)
        for gpu in hardware.get("gpus") or []:
            rows.append(_row(
                "Accelerator",
                f"{gpu.get('count', 1)} × {gpu.get('model')}"
                + (f", {gpu['memory_gb']} GB each" if gpu.get("memory_gb") else ""),
                provenance=detected,
                note="Schema 1.0 recorded no role for this device. It is listed because the "
                     "result declares GPU execution, not because anything observed it serve."))
        if not hardware.get("gpus"):
            rows.append(_row("Accelerator", None, note="No accelerator is recorded as having "
                                                       "performed inference."))
        rows.append(_row("Weight placement", None,
                         note="Schema 1.0 did not record how the weights were split."))
        return {"id": "accelerators", "title": "What ran the weights", "rows": rows}

    serving = environment.get("serving") or {}
    devices = serving.get("accelerators") or []
    if not devices:
        rows.append(_row("Accelerator", None,
                         note="No accelerator is recorded for the serving machine."))
    for entry in devices:
        memory = entry.get("memory_gb")
        rows.append(_row(
            "Accelerator",
            f"{entry.get('count', 1)} × {entry.get('vendor')} {entry.get('model')}"
            + (f", {memory} GB each" if memory else ", memory not recorded"),
            provenance=entry.get("provenance"),
            note=ROLE_LABELS.get(entry.get("role"), ROLE_LABELS["unknown"])
                 + ". Finding a device establishes the device, not that inference touched it.",
        ))
    placement = serving.get("placement") or {}
    mode = placement.get("mode")
    rows.append(_row(
        "Weight placement",
        PLACEMENT_LABELS.get(mode) if mode and mode != "unknown" else None,
        provenance=placement.get("provenance"),
        note=placement.get("detail") or "",
    ))
    return {"id": "accelerators", "title": "What ran the weights", "rows": rows}


def _model(result: dict[str, Any]) -> dict[str, Any]:
    model = result.get("model") or {}
    weights = model.get("weights")
    status = model.get("catalog_status")
    rows = [
        _row("Canonical model", model.get("id"), provenance="contributor-declared",
             note="The catalog identity this result is compared under."),
        _row("Identifier the server answered to", model.get("served_model_id"),
             provenance="contributor-declared",
             note="A serving alias. It is not a claim about which weights were loaded."),
        _row("Catalog status",
             "Proposed — awaiting review" if status == "proposed"
             else "Catalogued" if status else None,
             provenance="contributor-declared" if status else None,
             note="This model is not in the catalog. The bundle carries a proposal for "
                  "review; nothing here makes it an eligible model."
                  if status == "proposed" else ""),
        _row("Quantization", model.get("quantization"), provenance="contributor-declared"),
    ]
    if weights is None:
        rows.append(_row("Weights", model.get("revision"), provenance="contributor-declared",
                         note="Recorded as a revision only. Which repository it is a revision "
                              "of is not separately recorded."
                              if not model.get("source") else
                              f"From {model['source']}."))
    elif weights.get("identity") == "content-hash":
        content = weights.get("content") or {}
        files = content.get("files") or []
        rows.append(_row(
            "Weights", f"{len(files)} file(s), identified by their bytes",
            provenance="contributor-declared",
            note=f"Manifest {content.get('manifest_sha256')}. These weights have no public "
                 "immutable revision, so the bytes themselves are the identity."))
    else:
        rows.append(_row(
            "Weights", f"{weights.get('repository')} at {weights.get('repository_revision')}",
            provenance="contributor-declared",
            note="A repository commit. A file checksum is a different thing and is not this."))
    return {"id": "model", "title": "Which model, and which weights", "rows": rows}


def _runtime(result: dict[str, Any], environment: dict[str, Any] | None) -> dict[str, Any]:
    runtime = result.get("runtime") or {}
    serving_runtime = ((environment or {}).get("serving") or {}).get("runtime") or {}
    engine = serving_runtime.get("engine") or {}
    application = serving_runtime.get("application")
    rows = [_row("Framework", runtime.get("framework"), provenance="contributor-declared")]
    if environment is None:
        rows.append(_row("Serving runtime build", runtime.get("version"),
                         provenance="contributor-declared",
                         note="Schema 1.0 recorded one version. Whether it is the inference "
                              "engine's build or a hosting application's release is not "
                              "distinguished here."))
        rows.append(_row("Hosting application", None,
                         note="Schema 1.0 did not record this separately."))
    else:
        rows.append(_row(
            "Inference engine", f"{engine.get('name')} {engine.get('version') or ''}".strip()
            if engine.get("name") else None,
            provenance=engine.get("provenance"),
            note="What executed the weights."))
        rows.append(_row(
            "Hosting application",
            f"{application['name']} {application['version']}" if application else None,
            provenance=(application or {}).get("provenance"),
            note="The program hosting the engine. Its release version is a different thing "
                 "from the engine build above." if application else
                 "No hosting application is recorded; the engine was run directly."))
        for setting in serving_runtime.get("settings") or []:
            rows.append(_row(f"Serving setting · {setting.get('name')}", setting.get("value"),
                             provenance=setting.get("provenance")))
    rows.append(_row("Startup arguments",
                     " ".join(runtime.get("arguments") or []) or None,
                     provenance="contributor-declared"))
    return {"id": "runtime", "title": "What served it", "rows": rows}


def _tokenizer(result: dict[str, Any]) -> dict[str, Any]:
    tokenizer = (result.get("model") or {}).get("tokenizer")
    if tokenizer is None:
        return {"id": "tokenizer", "title": "What counted the tokens", "rows": [
            _row("Tokenizer source", None,
                 note="Schema 1.0 did not record which tokenizer built and counted the "
                      "workload. Every token figure in this result is some tokenizer's "
                      "count; which one is not in this evidence."),
        ]}
    identity = tokenizer.get("identity") or {}
    return {"id": "tokenizer", "title": "What counted the tokens", "rows": [
        _row("Tokenizer source", tokenizer.get("source"), provenance="contributor-declared",
             note="Used as the served identifier's default rather than chosen."
                  if tokenizer.get("kind") == "served-model-default" else ""),
        _row("Loaded before the run",
             "Yes, in the runner's own interpreter"
             if tokenizer.get("loaded") == "loaded-before-run" else "Not checked by OmoBench",
             provenance="detected" if tokenizer.get("loaded") == "loaded-before-run" else None),
        _row("Tokenizer implementation", identity.get("class"), provenance="detected"),
        _row("Vocabulary size", identity.get("vocabulary_size"), provenance="detected"),
        _row("Matches the served weights", "Not verifiable from this evidence",
             note="A tokenizer that loads is not thereby the one the served weights were "
                  "trained with, and nothing available to OmoBench can establish that it is."),
    ]}


def _transport(result: dict[str, Any], environment: dict[str, Any] | None) -> dict[str, Any]:
    if environment is None:
        return {"id": "transport", "title": "How the requests travelled", "rows": [
            _row("Transport", "Direct to the server", provenance="contributor-declared",
                 note="Schema 1.0 could only carry a direct run: the registry refused bridged "
                      "evidence outright. This result records no transport of its own."),
        ]}
    transport = environment.get("transport") or {}
    bridged = transport.get("kind") == "windows-wsl-stdio-bridge"
    return {"id": "transport", "title": "How the requests travelled", "rows": [
        _row("Transport",
             "Through the Windows–WSL bridge" if bridged else "Direct to the server",
             provenance="detected",
             note=f"Bridge {transport.get('bridge_version')}." if bridged else ""),
        _row("Protocol", transport.get("protocol"), provenance="detected"),
        _row("Transport latency",
             "Inside every measurement, never subtracted" if bridged else "None to account for",
             provenance="detected",
             note="A relay sits inside every request this run made and its cost has not been "
                  "separated out, so these numbers are comparable with other bridged runs "
                  "rather than with direct ones." if bridged else ""),
    ]}


def summarize(result: dict[str, Any]) -> dict[str, Any]:
    """Every recorded fact about how this result came about, as labelled rows."""
    if not isinstance(result, dict):
        raise ValueError("A result summary needs the result object.")
    version = result.get("schema_version")
    environment = result.get("environment") if version == "1.1" else None
    if environment is not None and not isinstance(environment, dict):
        raise ValueError("This result's environment is not an object.")
    qualifications = [STANDING_NOTE]
    if environment is None:
        qualifications.insert(0, LEGACY_NOTE)
    if ((environment or {}).get("transport") or {}).get("kind") == "windows-wsl-stdio-bridge":
        qualifications.append(
            "Measured through the Windows–WSL bridge. Its latency is inside every number "
            "here and has not been subtracted.")
    if (result.get("model") or {}).get("catalog_status") == "proposed":
        qualifications.append(
            "This model is not catalogued. The bundle proposes it for review, which is a "
            "separate decision from accepting this result.")
    if (result.get("submission") or {}).get("packaging", {}).get(
            "metadata_declared_after_execution"):
        qualifications.append(
            "This bundle was packaged later from saved evidence. The measurements are the "
            "original ones; the serving details were supplied afterwards.")
    return {
        "schema_version": version,
        "sections": [
            _environments(result, environment),
            _accelerators(result, environment),
            _model(result),
            _runtime(result, environment),
            _tokenizer(result),
            _transport(result, environment),
        ],
        "qualifications": qualifications,
    }
