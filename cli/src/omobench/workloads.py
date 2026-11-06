"""Curated workloads, read against the hardware in front of you.

OmoBench measures hardware. A model is the load you put on a machine to find
out what it does, so the question this module answers is not "which model is
best" -- it answers nothing of the kind, and nothing here ranks anything -- but
"what can this machine be asked to run, and what would that tell me".

Two distinctions the whole module exists to keep:

**Expected to fit is not benchmarked.** Fit is arithmetic about memory, done
from the weight size, the profile's context and a stated runtime overhead. It
predicts nothing about speed, nothing about whether a run will meet its
profile's acceptance thresholds, and nothing about the quality of what comes
out. A machine that fits a workload can still fail to finish it -- that has
already happened here, twice. Only a result establishes that a machine ran
something.

**Serving hardware is not runner hardware.** What matters for fit is the memory
available where the model is *served*. On a bridged run that is a different
computer from the one this panel is in, and nothing visible from the runner
describes it. The caller says which it is passing; this module never guesses.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

# Offered as filter rows in the panel, and the sizes people actually have.
CPU_RAM_TIERS_GB = (8, 16, 32, 64)
GPU_VRAM_TIERS_GB = (4, 8, 12, 16, 20)

# Anything at or above the largest named tier is reported as that tier "or
# larger" rather than invented as a new one.
LARGEST_GPU_TIER_GB = GPU_VRAM_TIERS_GB[-1]

CATALOG = Path("catalog") / "workloads" / "text-generation-v1.yaml"


def load(root: Path) -> dict[str, Any]:
    """The curated workload catalog, or an empty one when it cannot be read.

    A missing catalog is not an error that should stop a benchmark: a
    contributor connecting their own server needs none of it.
    """
    try:
        import yaml

        document = yaml.safe_load((root / CATALOG).read_text(encoding="utf-8"))
    except (OSError, ValueError, ImportError):
        return {"families": [], "excluded": [], "estimate_basis": {}}
    if not isinstance(document, dict):
        return {"families": [], "excluded": [], "estimate_basis": {}}
    document.setdefault("families", [])
    document.setdefault("excluded", [])
    document.setdefault("estimate_basis", {})
    return document


def _as_text(value: Any) -> str | None:
    """A date, or nothing. Never a Python object the panel cannot send."""
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


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


def fits(requirement_gb: int | None, available_gb: float | None) -> str:
    """Three answers, and "unknown" is one of them.

    A fit nobody could compute is not a fit that failed. Saying so is the
    difference between "this will not run" and "nothing here knows".
    """
    if requirement_gb is None or available_gb is None:
        return "unknown"
    return "fits" if available_gb >= requirement_gb else "too-large"


def describe(
    root: Path,
    *,
    serving_ram_gb: float | None = None,
    serving_vram_gb: float | None = None,
    placement: str = "cpu-only",
) -> dict[str, Any]:
    """Every curated workload, annotated against the serving hardware given.

    `serving_ram_gb` and `serving_vram_gb` describe the machine that will
    *serve* the model, which on a bridged run is not this one. Either may be
    None, and None produces "unknown" rather than a guess.
    """
    document = load(root)
    device = "gpu" if placement != "cpu-only" and serving_vram_gb is not None else "cpu"
    available = serving_vram_gb if device == "gpu" else serving_ram_gb
    tiers = GPU_VRAM_TIERS_GB if device == "gpu" else CPU_RAM_TIERS_GB

    families = []
    for family in document["families"]:
        variants = []
        for variant in family.get("variants") or []:
            options = []
            for option in variant.get("quantisations") or []:
                # A requirement, not a tier. Comparing a machine against the
                # *class* it belongs to said this laptop could not run the
                # model it had been running all day: 7.49 GB is short of the
                # 8 GB tier, and the workload needs 4.
                requirement = option.get("requires_gb")
                options.append({
                    "quantisation": option.get("label"),
                    "runtime": option.get("runtime"),
                    "weights_gb": option.get("weights_gb"),
                    # Says which kind of number the one above is. A reader who
                    # cannot tell a measured file size from arithmetic cannot
                    # judge the fit either.
                    "weights_estimated": bool(option.get("estimated")),
                    "recipe": option.get("recipe"),
                    "prepared": option.get("recipe") is not None,
                    "requires_gb": requirement,
                    # The tier row this belongs under in the panel's filter:
                    # the smallest named size that covers the requirement.
                    # Presentation, never the comparison.
                    "tier_gb": next((tier for tier in tiers if requirement is not None
                                     and tier >= requirement), None),
                    "fit": fits(requirement, available),
                    "offload_required": (
                        device == "gpu" and requirement is not None
                        and available is not None and available < requirement),
                })
            variants.append({
                "id": variant.get("id"),
                "parameter_class": variant.get("parameter_class"),
                "source": variant.get("source"),
                "context_tokens": variant.get("context_tokens"),
                "quantisations": options,
            })
        families.append({
            "family": family.get("family"),
            "publisher": family.get("publisher"),
            "licence": family.get("licence"),
            "licence_url": family.get("licence_url"),
            "gated": bool(family.get("gated")),
            "gate_note": family.get("gate_note"),
            # YAML turns an unquoted 2026-11-06 into a date object, which no
            # JSON encoder will take. The panel wants the day it was read, not
            # a Python type, so it leaves here as the string it looks like.
            "source_verified": _as_text(family.get("source_verified")),
            "source_note": family.get("source_note"),
            "variants": variants,
        })

    return {
        "families": families,
        "excluded": [{**entry, "checked": _as_text(entry.get("checked"))}
                     for entry in document["excluded"] if isinstance(entry, dict)],
        "hardware": {
            "device": device,
            "placement": placement,
            "serving_ram_gb": serving_ram_gb,
            "serving_vram_gb": serving_vram_gb,
            # What the machine actually has, and separately the tier row it
            # sits in. A 7.49 GB machine is short of the 8 GB tier and can
            # still run plenty; only the first number decides anything.
            "available_gb": available,
            "tier_gb": tier_for(available, tiers),
            "tiers": list(tiers),
            "known": available is not None,
        },
        "estimate_basis": document["estimate_basis"],
        # Said here, once, so every surface that renders this can repeat it
        # rather than inventing its own wording for the same caveat.
        "qualifications": [
            "Expected to fit is arithmetic about memory, not a benchmark result: it predicts "
            "nothing about speed, about meeting a profile's acceptance thresholds, or about the "
            "quality of what the model produces.",
            "A machine that fits a workload can still fail to finish one.",
            "These sizes describe the machine that serves the model. On a bridged run that is a "
            "different computer from the one this panel is in.",
            "The curated list is a recommendation. Any compatible model can be benchmarked, and "
            "submitted on the requirements in docs/model-eligibility.md.",
        ],
    }
