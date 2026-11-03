"""Build the schema 1.1 `environment` object: who ran the benchmark, what
served the model, and how requests travelled between them.

Three rules shape everything here.

A fact carries how it was established. `detected` means a probe on the machine
that owns the fact read it; `server-reported` means the inference server said
so over its own API; `contributor-declared` means a person typed it;
`unknown` means nobody established it, and then the value is null. A fact is
never upgraded: a value a person typed stays contributor-declared however
plausible it looks.

What WSL can see is not evidence about Windows. On a bridged run the runner is
a WSL2 distribution; its CPU, RAM and devices describe the distribution, not
the Windows host that served the model. So the serving side is built only from
the contributor's declaration and from a companion probe that actually ran on
Windows -- never from `doctor_report()`.

Presence is not use. Detecting an accelerator establishes the device, not that
inference touched it; `role` is a separate claim and defaults to unknown.
"""
from __future__ import annotations

import math
from typing import Any

METADATA_VERSION = "serving-environment-v1"
HARDWARE_PROBE_KIND = "omobench-serving-hardware-probe-v1"
PROVENANCE = ("detected", "server-reported", "contributor-declared", "unknown")
RELATIONSHIPS = ("same-host", "windows-host-wsl-runner")
PLACEMENT_MODES = ("cpu-only", "gpu-only", "cpu-gpu-offload", "unknown")
ACCELERATOR_VENDORS = ("nvidia", "amd", "intel", "apple", "other")
ACCELERATOR_ROLES = ("inference", "present-not-used", "unknown")
TRANSPORT_KINDS = ("direct", "windows-wsl-stdio-bridge")

MAX_ACCELERATORS = 16
MAX_SETTINGS = 32
MAX_TEXT = 200


class ServingMetadataError(ValueError):
    """A declaration that cannot be turned into honest provenance."""


def _text(value: Any, label: str, *, limit: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ServingMetadataError(f"{label} must be a non-empty string.")
    cleaned = value.strip()
    if len(cleaned) > limit or any(ord(character) < 32 for character in cleaned):
        raise ServingMetadataError(f"{label} is too long or contains control characters.")
    return cleaned


def _optional_text(value: Any, label: str, *, limit: int = MAX_TEXT) -> str | None:
    return None if value is None else _text(value, label, limit=limit)


def _positive_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ServingMetadataError(f"{label} must be a number.")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ServingMetadataError(f"{label} must be a finite positive number.")
    return number


def fact(value: Any, provenance: str) -> dict[str, Any]:
    """One fact with how it was established.

    A null value and `unknown` provenance always travel together: a value with
    nothing behind it would be an invented fact, and an `unknown` fact that
    still carries a value would be one laundered of its origin.
    """
    if provenance not in PROVENANCE:
        raise ServingMetadataError(f"Unknown provenance: {provenance!r}.")
    if (value is None) != (provenance == "unknown"):
        raise ServingMetadataError(
            "A fact is unknown exactly when it has no value: "
            f"value={value!r} cannot be {provenance!r}."
        )
    return {"value": value, "provenance": provenance}


def unknown_fact() -> dict[str, Any]:
    return fact(None, "unknown")


def _declared_fact(declared: Any, probed: Any, label: str, *, number: bool = False) -> dict[str, Any]:
    """Prefer what a probe read; fall back to what a person typed.

    The probe wins only because it is the stronger evidence, never because the
    values agree: when both exist the recorded provenance is still `detected`,
    and when only the person's value exists it stays `contributor-declared`.
    """
    if probed is not None:
        return fact(_positive_number(probed, label) if number else _text(probed, label), "detected")
    if declared is not None:
        return fact(_positive_number(declared, label) if number else _text(declared, label),
                    "contributor-declared")
    return unknown_fact()


def accelerator(entry: Any, *, provenance: str) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise ServingMetadataError("Each accelerator must be an object.")
    unexpected = set(entry) - {"vendor", "model", "memory_gb", "count", "role"}
    if unexpected:
        raise ServingMetadataError(f"Unsupported accelerator field(s): {', '.join(sorted(unexpected))}.")
    vendor = _text(entry.get("vendor"), "Accelerator vendor")
    if vendor not in ACCELERATOR_VENDORS:
        raise ServingMetadataError(
            f"Accelerator vendor must be one of {', '.join(ACCELERATOR_VENDORS)}.")
    count = entry.get("count", 1)
    if isinstance(count, bool) or not isinstance(count, int) or count < 1 or count > 64:
        raise ServingMetadataError("Accelerator count must be a whole number of at least 1.")
    memory = entry.get("memory_gb")
    # The role is always the contributor's or the server's claim about
    # execution. A probe that found the card did not watch it run.
    role = entry.get("role", "unknown")
    if role not in ACCELERATOR_ROLES:
        raise ServingMetadataError(f"Accelerator role must be one of {', '.join(ACCELERATOR_ROLES)}.")
    return {
        "vendor": vendor,
        "model": _text(entry.get("model"), "Accelerator model"),
        "memory_gb": None if memory is None else _positive_number(memory, "Accelerator memory_gb"),
        "count": count,
        "role": role,
        "provenance": provenance,
    }


def _accelerators(declaration: dict[str, Any], probe: dict[str, Any] | None) -> list[dict[str, Any]]:
    declared = declaration.get("accelerators") or []
    if not isinstance(declared, list) or len(declared) > MAX_ACCELERATORS:
        raise ServingMetadataError(
            f"accelerators must be a list of at most {MAX_ACCELERATORS} entries.")
    entries = [accelerator(entry, provenance="contributor-declared") for entry in declared]
    probed = (probe or {}).get("accelerators") or []
    if not isinstance(probed, list) or len(probed) > MAX_ACCELERATORS:
        raise ServingMetadataError("The hardware probe reported an unusable accelerator list.")
    # A probe runs on the serving machine, so its device facts are detected --
    # but it says nothing about which device served, so anything it adds that
    # the contributor did not claim keeps role "unknown".
    for entry in probed:
        candidate = accelerator({**entry, "role": "unknown"} if isinstance(entry, dict) else entry,
                                provenance="detected")
        match = next((existing for existing in entries
                      if existing["vendor"] == candidate["vendor"]
                      and existing["model"] == candidate["model"]), None)
        if match is None:
            entries.append(candidate)
            continue
        match["provenance"] = "detected"
        match["count"] = candidate["count"]
        if candidate["memory_gb"] is not None:
            match["memory_gb"] = candidate["memory_gb"]
    if len(entries) > MAX_ACCELERATORS:
        raise ServingMetadataError(f"At most {MAX_ACCELERATORS} accelerators can be recorded.")
    return entries


def _placement(declaration: dict[str, Any]) -> dict[str, Any]:
    placement = declaration.get("placement") or {}
    if not isinstance(placement, dict):
        raise ServingMetadataError("placement must be an object.")
    unexpected = set(placement) - {"mode", "detail"}
    if unexpected:
        raise ServingMetadataError(f"Unsupported placement field(s): {', '.join(sorted(unexpected))}.")
    mode = placement.get("mode", "unknown")
    if mode not in PLACEMENT_MODES:
        raise ServingMetadataError(f"placement.mode must be one of {', '.join(PLACEMENT_MODES)}.")
    detail = _optional_text(placement.get("detail"), "placement.detail", limit=500)
    if mode == "cpu-gpu-offload" and not detail:
        raise ServingMetadataError(
            "A cpu-gpu-offload placement needs a detail saying how the weights were split, "
            "as the serving runtime reported it (for example '32 of 49 layers on GPU').")
    return {"mode": mode, "detail": detail,
            "provenance": "unknown" if mode == "unknown" else "contributor-declared"}


def _runtime(declaration: dict[str, Any]) -> dict[str, Any]:
    runtime = declaration.get("runtime") or {}
    if not isinstance(runtime, dict):
        raise ServingMetadataError("runtime must be an object.")
    unexpected = set(runtime) - {"application", "engine", "settings"}
    if unexpected:
        raise ServingMetadataError(f"Unsupported runtime field(s): {', '.join(sorted(unexpected))}.")

    application = runtime.get("application")
    if application is not None:
        if not isinstance(application, dict):
            raise ServingMetadataError("runtime.application must be an object or null.")
        application = {
            "name": _text(application.get("name"), "runtime.application.name"),
            # An application release and the engine build inside it are two
            # different versions; recording one as the other loses the engine.
            "version": _text(application.get("version"), "runtime.application.version"),
            "provenance": "contributor-declared",
        }

    engine = runtime.get("engine") or {}
    if not isinstance(engine, dict):
        raise ServingMetadataError("runtime.engine must be an object.")
    unexpected = set(engine) - {"name", "version"}
    if unexpected:
        raise ServingMetadataError(f"Unsupported runtime.engine field(s): {', '.join(sorted(unexpected))}.")
    engine_version = _optional_text(engine.get("version"), "runtime.engine.version")
    engine = {
        "name": _text(engine.get("name"), "runtime.engine.name"),
        "version": engine_version,
        "provenance": "contributor-declared" if engine_version else "unknown",
    }

    declared_settings = runtime.get("settings") or []
    if not isinstance(declared_settings, list) or len(declared_settings) > MAX_SETTINGS:
        raise ServingMetadataError(f"runtime.settings must be a list of at most {MAX_SETTINGS} entries.")
    settings = []
    for entry in declared_settings:
        if not isinstance(entry, dict) or set(entry) - {"name", "value"}:
            raise ServingMetadataError("Each runtime setting must be {name, value}.")
        value = entry.get("value")
        if not isinstance(value, str) or len(value) > MAX_TEXT or any(ord(c) < 32 for c in value):
            raise ServingMetadataError("A runtime setting value must be a short plain string.")
        settings.append({"name": _text(entry.get("name"), "Setting name", limit=60),
                         "value": value, "provenance": "contributor-declared"})
    return {"application": application, "engine": engine, "settings": settings}


def runner_environment(report: dict[str, Any], *, relationship: str) -> dict[str, Any]:
    """Describe the machine that ran GuideLLM, from its own environment snapshot.

    This is the one place `doctor_report()` output belongs, because it was
    collected where the runner ran. On a bridged run it is explicitly the WSL2
    distribution and nothing is carried across to the serving side.
    """
    operating_system = report.get("operating_system")
    return {
        "os": _text(operating_system, "Runner operating system", limit=500)
             if operating_system else "unknown",
        "kind": "wsl2" if relationship == "windows-host-wsl-runner" else "native",
        "python_version": _optional_text(report.get("python"), "Runner Python version", limit=60),
        "cpu": _optional_text(report.get("cpu"), "Runner CPU", limit=500),
        "ram_gb": None if report.get("ram_gb") is None
                  else _positive_number(report.get("ram_gb"), "Runner RAM"),
    }


def transport_metadata(provenance_record: dict[str, Any] | None) -> dict[str, Any]:
    """The transport this run's requests actually travelled over.

    `provenance_record` is the bridge's own description, written into
    environment.json when the run executed. Absent means the requests went
    straight to the server, which is the only other arrangement v1 supports.
    """
    if provenance_record is None:
        return {"kind": "direct", "protocol": "HTTP/1.1", "bridge_version": None,
                "overhead": "none-direct"}
    if not isinstance(provenance_record, dict):
        raise ServingMetadataError("The recorded transport provenance is not an object.")
    kind = provenance_record.get("kind")
    if not isinstance(kind, str) or not kind.startswith("windows-stdio-"):
        raise ServingMetadataError(
            "This run recorded a transport OmoBench cannot describe. It can only be exported "
            "as a local report.")
    return {
        "kind": "windows-wsl-stdio-bridge",
        "protocol": _text(provenance_record.get("protocol") or "HTTP/1.1", "Transport protocol"),
        "bridge_version": _text(kind, "Bridge version"),
        # Never "corrected for": the relay sits inside every measured request,
        # and OmoBench has not measured how much of each number it owns.
        "overhead": "included-not-measured",
    }


def build_environment(
    declaration: dict[str, Any] | None,
    *,
    runner_report: dict[str, Any],
    transport_provenance: dict[str, Any] | None,
    allow_probe: bool = True,
) -> dict[str, Any]:
    """Assemble the schema 1.1 environment object.

    `declaration` is what the contributor supplied, optionally carrying a
    `probe` document that a companion ran on the serving machine. A bridged run
    must supply one: without it there is no evidence at all about the Windows
    host, and the WSL runner's view is not a substitute.
    """
    declaration = declaration or {}
    if not isinstance(declaration, dict):
        raise ServingMetadataError("The serving declaration must be a JSON object.")
    unexpected = set(declaration) - {"relationship", "host", "accelerators", "placement",
                                     "runtime", "probe"}
    if unexpected:
        raise ServingMetadataError(f"Unsupported serving field(s): {', '.join(sorted(unexpected))}.")

    transport = transport_metadata(transport_provenance)
    bridged = transport["kind"] == "windows-wsl-stdio-bridge"
    relationship = declaration.get("relationship",
                                   "windows-host-wsl-runner" if bridged else "same-host")
    if relationship not in RELATIONSHIPS:
        raise ServingMetadataError(f"relationship must be one of {', '.join(RELATIONSHIPS)}.")
    if bridged and relationship != "windows-host-wsl-runner":
        raise ServingMetadataError(
            "This run went through the Windows bridge, so its serving environment is the Windows "
            "host and its runner is WSL2. It cannot be declared as a single host.")
    if not bridged and relationship != "same-host":
        raise ServingMetadataError(
            "This run addressed its server directly, so the runner and the server shared a host. "
            "Declaring a Windows host with a WSL runner would claim a transport this run did not use.")

    probe = declaration.get("probe")
    if probe is not None and not allow_probe:
        raise ServingMetadataError(
            "This run finished before these facts were collected, so a hardware probe cannot "
            "speak for it: it would describe the machine as it is now, not as it was while the "
            "benchmark ran. Declare the serving hardware instead; it is recorded as declared.")
    if probe is not None and not isinstance(probe, dict):
        raise ServingMetadataError("probe must be an object.")
    if probe is not None and probe.get("kind") != HARDWARE_PROBE_KIND:
        raise ServingMetadataError(
            f"A serving hardware probe must be a {HARDWARE_PROBE_KIND} document.")

    host = declaration.get("host") or {}
    if not isinstance(host, dict):
        raise ServingMetadataError("host must be an object.")
    unexpected = set(host) - {"os", "cpu", "ram_gb"}
    if unexpected:
        raise ServingMetadataError(f"Unsupported host field(s): {', '.join(sorted(unexpected))}.")

    if relationship == "same-host" and not probe:
        # The runner's own snapshot was taken on the serving machine while the
        # benchmark ran, so here -- and only here -- it is a detection.
        # One host served and measured, so the runner's own snapshot is a
        # reading of the serving machine -- the one case where it is evidence.
        probe = {"kind": HARDWARE_PROBE_KIND,
                 "os": runner_report.get("operating_system"),
                 "cpu": runner_report.get("cpu"),
                 "ram_gb": runner_report.get("ram_gb"),
                 "accelerators": [
                     {"vendor": gpu.get("vendor") or "other", "model": gpu.get("model"),
                      "memory_gb": gpu.get("memory_gb"), "count": 1}
                     for gpu in (runner_report.get("gpus") or []) if isinstance(gpu, dict)
                 ]}

    serving = {
        "host": {
            "os": _declared_fact(host.get("os"), (probe or {}).get("os"), "Serving OS"),
            "cpu": _declared_fact(host.get("cpu"), (probe or {}).get("cpu"), "Serving CPU"),
            "ram_gb": _declared_fact(host.get("ram_gb"), (probe or {}).get("ram_gb"),
                                     "Serving RAM", number=True),
        },
        "accelerators": _accelerators(declaration, probe),
        "placement": _placement(declaration),
        "runtime": _runtime(declaration),
    }
    return {
        "metadata_version": METADATA_VERSION,
        "relationship": relationship,
        "runner": runner_environment(runner_report, relationship=relationship),
        "serving": serving,
        "transport": transport,
    }


def inference_accelerators(environment: dict[str, Any]) -> list[dict[str, Any]]:
    """The devices this result claims performed inference, grouped for `hardware.gpus`.

    Grouping is by the identity a reader compares on, so two identical cards
    become one entry with count 2 rather than two entries that look like
    different hardware.
    """
    grouped: dict[tuple[str, str, float | None], int] = {}
    for device in environment["serving"]["accelerators"]:
        if device["role"] != "inference":
            continue
        key = (device["vendor"], device["model"], device["memory_gb"])
        grouped[key] = grouped.get(key, 0) + device["count"]
    return [{"vendor": vendor, "model": model, "memory_gb": memory, "count": count}
            for (vendor, model, memory), count in sorted(
                grouped.items(), key=lambda item: (item[0][0], item[0][1], item[0][2] or 0))]


def hardware_from_serving(environment: dict[str, Any], *, execution_device: str) -> dict[str, Any]:
    """Build the 1.0-shaped `hardware` object from a 1.1 serving environment.

    `hardware` has always described what performed inference, so on a bridged
    run it must come from the serving side. Building it here, rather than from
    the runner's snapshot, is what stops a WSL2 distribution being published as
    though it were the Windows machine that served the model.
    """
    serving = environment["serving"]
    host = serving["host"]
    missing = [name for name in ("os", "cpu", "ram_gb") if host[name]["value"] is None]
    if missing:
        raise ServingMetadataError(
            "The serving machine's " + ", ".join(missing) + " is unknown. A public result names "
            "the hardware that served the model; supply it, or export this run as a local report.")
    devices = inference_accelerators(environment)
    if execution_device == "cpu":
        if devices:
            raise ServingMetadataError(
                "This run is declared as CPU execution, but an accelerator is marked as having "
                "performed inference. Mark it present-not-used, or declare GPU execution.")
    elif not devices:
        raise ServingMetadataError(
            "This run is declared as GPU execution, but no accelerator is marked as having "
            "performed inference.")
    unknown_memory = [device for device in devices if device["memory_gb"] is None]
    if unknown_memory:
        raise ServingMetadataError(
            "Memory is unknown for: "
            + ", ".join(f"{device['vendor']}:{device['model']}" for device in unknown_memory)
            + ". Record each serving accelerator's memory; confirm it in the serving runtime's "
              "own hardware panel rather than guessing.")
    detected = all(
        device["provenance"] == "detected"
        for device in serving["accelerators"] if device["role"] == "inference"
    ) and all(host[name]["provenance"] == "detected" for name in ("os", "cpu", "ram_gb"))
    return {
        "os": host["os"]["value"],
        "cpu": host["cpu"]["value"],
        "ram_gb": float(host["ram_gb"]["value"]),
        "gpus": devices,
        "driver_version": None,
        "detection": {
            # "auto" would claim OmoBench read this machine itself. It only
            # ever reads the serving machine through a probe that ran there.
            "hardware": "auto" if detected else "user-confirmed",
            "model": "manual",
            "runtime": "manual",
        },
    }
