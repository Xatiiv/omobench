"""Curated workloads, read against the hardware in front of you.

OmoBench measures hardware. A model is the load you put on a machine to find
out what it does, so the question here is not which model is best -- nothing
in this module ranks anything -- but what this machine can be asked to run,
and how much of that is actually known.

Identity lives elsewhere. `catalog/models/` is the authoritative record of
what a model is: one id, one publisher, one licence, one modality. This module
reads the recipes in `catalog/workloads/`, each of which names one of those
models and describes one way of running it. A licence is never copied into a
recipe, because a licence copied six times is a licence wrong five times.

Three distinctions the module exists to keep.

**Loading is not completing, and failing is not a requirement.** A server that
starts in 4.3 GB can be holding 7 GB by the time a profile is half done. The
only large figure this project has measured is the one a process had reached
when the kernel killed it, which is a lower bound on a failure and tells
nobody what success would have cost. So `completion_requirement_gb` is null
everywhere, and fit verdicts say "unknown" rather than inventing one.

**One observation is not a model of anything.** The growth ratio measured here
came from one engine, one model and one run that failed. It is offered as a
hint so a reader knows growth happens at all, and it never produces a verdict.

**Environments are not machines.** A WSL distribution has an allocation, and
within it an amount currently available -- different numbers, and neither is
the physical computer's. On a bridged run the Windows server and the WSL
runner are two environments on *one* physical host, sharing its RAM: the
runner's footprint does not stop mattering just because it is on the other
side of a bridge.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

# Offered as filter rows in the panel, and the sizes people actually have.
CPU_RAM_TIERS_GB = (8, 16, 32, 64)
GPU_VRAM_TIERS_GB = (4, 8, 12, 16, 20)

CATALOG = Path("catalog") / "workloads" / "text-generation-v1.yaml"
MODELS = Path("catalog") / "models"

# What a fit answer can say. "unknown" is the honest answer far more often
# than a recommendation list would like.
FITS, MARGINAL, TOO_LARGE, UNKNOWN = "fits", "marginal", "too-large", "unknown"


def _as_text(value: Any) -> str | None:
    """A date, or nothing. Never a Python object the panel cannot send."""
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _read_yaml(path: Path) -> Any:
    try:
        import yaml

        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, ImportError):
        return None


def models(root: Path) -> dict[str, dict[str, Any]]:
    """The authoritative identity record, by model id.

    Read from the same files the registry validator reads, so the panel and
    the registry cannot disagree about what a model is.
    """
    found: dict[str, dict[str, Any]] = {}
    directory = root / MODELS
    if not directory.is_dir():
        return found
    for path in sorted(directory.glob("*.yaml")):
        document = _read_yaml(path)
        if not isinstance(document, dict):
            continue
        for entry in document.get("models") or []:
            if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                found[entry["id"]] = entry
    return found


def load(root: Path) -> dict[str, Any]:
    """The workload recipes, or an empty set when they cannot be read.

    A missing catalog is not an error that should stop a benchmark: a
    contributor connecting their own server needs none of it.
    """
    document = _read_yaml(root / CATALOG)
    if not isinstance(document, dict):
        return {"recipes": [], "excluded": [], "estimate_basis": {}}
    document.setdefault("recipes", [])
    document.setdefault("excluded", [])
    document.setdefault("estimate_basis", {})
    return document


def tier_for(gigabytes: float | None, tiers: tuple[int, ...]) -> int | None:
    """The named tier a machine belongs to: the largest one it meets.

    A machine with 12 GB is an 8 GB machine for the purpose of a tier list,
    because that is the largest tier it certainly satisfies. Rounding it up
    would be flattery.
    """
    if gigabytes is None:
        return None
    met = [tier for tier in tiers if gigabytes >= tier]
    return met[-1] if met else None


def assess(
    *,
    load_memory_gb: float | None,
    completion_requirement_gb: float | None,
    available_gb: float | None,
    weights_gb: float | None = None,
) -> dict[str, Any]:
    """Two separate questions, because they have separate answers.

    Will it load, and will it finish. The first is often knowable; the second
    is knowable only from a completed benchmark, and this project does not yet
    have one. A recommendation list that answered the second from arithmetic
    would be making it up, so when it does not know it says so.
    """
    loads = UNKNOWN
    floor = load_memory_gb if load_memory_gb is not None else weights_gb
    if floor is not None and available_gb is not None:
        loads = FITS if available_gb >= floor else TOO_LARGE

    completes = UNKNOWN
    if completion_requirement_gb is not None and available_gb is not None:
        completes = FITS if available_gb >= completion_requirement_gb else TOO_LARGE

    # The headline. It is deliberately conservative about claiming success and
    # deliberately unwilling to claim failure from a number nobody measured.
    if loads == TOO_LARGE:
        verdict = TOO_LARGE
    elif loads == FITS and completes == FITS:
        verdict = FITS
    elif loads == FITS:
        # It will start. Whether it survives a profile is the open question,
        # and on this project's own hardware the answer was no.
        verdict = MARGINAL
    else:
        verdict = UNKNOWN
    return {"verdict": verdict, "loads": loads, "completes": completes}


def describe(
    root: Path,
    *,
    serving_available_gb: float | None = None,
    serving_allocation_gb: float | None = None,
    serving_vram_gb: float | None = None,
    physical_ram_gb: float | None = None,
    placement: str = "cpu-only",
    bridged: bool = False,
) -> dict[str, Any]:
    """Every curated recipe, annotated against the serving environment given.

    `serving_available_gb` is what is free in the environment that will serve
    the model *now*; `serving_allocation_gb` is the ceiling that environment
    has. They are different numbers and the panel shows both. `physical_ram_gb`
    is the computer's own, which on a bridged run both environments are
    sharing.
    """
    document = load(root)
    identities = models(root)
    device = "gpu" if placement != "cpu-only" and serving_vram_gb is not None else "cpu"
    available = serving_vram_gb if device == "gpu" else serving_available_gb
    tiers = GPU_VRAM_TIERS_GB if device == "gpu" else CPU_RAM_TIERS_GB
    basis = document.get("estimate_basis") or {}
    hint = basis.get("growth_hint") or {}

    by_family: dict[str, dict[str, Any]] = {}
    for recipe in document["recipes"]:
        if not isinstance(recipe, dict):
            continue
        identity = identities.get(recipe.get("model")) or {}
        family = identity.get("family") or "Uncatalogued"
        group = by_family.setdefault(family, {
            "family": family,
            "publisher": identity.get("publisher"),
            "licence": identity.get("licence"),
            "licence_url": identity.get("licence_url"),
            "gated": bool(identity.get("gated")),
            "gate_note": identity.get("gate_note"),
            "verified": _as_text(identity.get("verified")),
            "models": {},
        })
        model_id = recipe.get("model")
        entry = group["models"].setdefault(model_id, {
            "id": model_id,
            "parameter_class": identity.get("parameter_class"),
            "source": identity.get("source"),
            "context_tokens": identity.get("context_tokens"),
            "known_identity": bool(identity),
            "options": [],
        })
        verdict = assess(
            load_memory_gb=recipe.get("load_memory_gb"),
            completion_requirement_gb=recipe.get("completion_requirement_gb"),
            available_gb=available,
            weights_gb=recipe.get("weights_gb"),
        )
        entry["options"].append({
            "quantisation": recipe.get("quantisation"),
            "runtime": recipe.get("runtime"),
            "artefact": recipe.get("artefact"),
            "managed_recipe": recipe.get("managed_recipe"),
            "prepared": recipe.get("managed_recipe") is not None,
            # Four quantities, each saying whether anybody measured it.
            "weights_gb": recipe.get("weights_gb"),
            "weights_measured": bool(recipe.get("weights_measured")),
            "load_memory_gb": recipe.get("load_memory_gb"),
            "load_measured": bool(recipe.get("load_measured")),
            "observed_before_failure_gb": recipe.get("observed_before_failure_gb"),
            "observation_note": recipe.get("observation_note"),
            # Null everywhere, and that is the point: no profile has completed
            # on measured hardware, so nothing here knows what completing costs.
            "completion_requirement_gb": recipe.get("completion_requirement_gb"),
            "growth_hint_applies": recipe.get("growth_hint_applies", True)
                                   and recipe.get("runtime") == hint.get("applies_to"),
            "benchmark": recipe.get("benchmark") or "never-run",
            "benchmark_note": recipe.get("benchmark_note"),
            **verdict,
        })

    families = []
    for group in by_family.values():
        group = dict(group)
        group["models"] = list(group["models"].values())
        families.append(group)

    return {
        "families": families,
        "excluded": [{**entry, "checked": _as_text(entry.get("checked"))}
                     for entry in document["excluded"] if isinstance(entry, dict)],
        "hardware": describe_hardware(
            device=device, placement=placement, bridged=bridged, tiers=tiers,
            available=available, serving_available_gb=serving_available_gb,
            serving_allocation_gb=serving_allocation_gb, serving_vram_gb=serving_vram_gb,
            physical_ram_gb=physical_ram_gb),
        "estimate_basis": basis,
        "growth_hint": hint,
        "qualifications": [
            "Fit is arithmetic about memory, not a benchmark result: it predicts nothing about "
            "speed, about meeting a profile's acceptance thresholds, or about the quality of "
            "what a model produces.",
            "Loading is not completing. 'Marginal' means it should start and nothing here knows "
            "whether it will finish -- which on this project's own hardware it did not.",
            "No curated workload has a measured completion requirement. The one large figure "
            "measured here is what a server had reached when the kernel killed it, which is a "
            "lower bound on a failure and not a specification for success.",
            "The growth figure in estimate_basis is one observation of one engine running one "
            "model, from a run that failed. It is a hint that growth happens, not a model of "
            "how much.",
            "An environment's allocation is not the memory currently free in it, and neither is "
            "the physical computer's total. On a bridged run the serving environment and the "
            "runner are two environments on one computer, sharing its RAM.",
            "The curated list is a recommendation. Any compatible model can be benchmarked, and "
            "submitted on the requirements in docs/model-eligibility.md.",
        ],
    }


def describe_hardware(**facts: Any) -> dict[str, Any]:
    """The machine, said in the terms that are actually true of it."""
    available = facts["available"]
    bridged, device = facts["bridged"], facts["device"]
    note = (
        "The model server and this runner are separate environments on the same physical "
        "computer, so they draw on the same RAM. Memory the runner holds is not available to "
        "the server, whichever side of the bridge it is on."
        if bridged else
        "This environment both serves the model and runs the benchmark, so the two share "
        "whatever it has."
    )
    return {
        "device": device,
        "placement": facts["placement"],
        "bridged": bridged,
        # Kept apart deliberately: a WSL allocation is a ceiling, not free
        # memory, and neither is the computer's own total.
        "serving_available_gb": facts["serving_available_gb"],
        "serving_allocation_gb": facts["serving_allocation_gb"],
        "serving_vram_gb": facts["serving_vram_gb"],
        "physical_ram_gb": facts["physical_ram_gb"],
        "decides_on_gb": available,
        "tier_gb": tier_for(available, facts["tiers"]),
        "tiers": list(facts["tiers"]),
        "known": available is not None,
        "shared_host_note": note,
    }
