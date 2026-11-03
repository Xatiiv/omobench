# Serving provenance, weight identity and model proposals

This describes schema 1.1, and the one JSON document that produces it.

## Why there is a 1.1 at all

A contributor finished a benchmark of DeepSeek-R1-Distill-Qwen-14B and could
not submit it. Nothing was wrong with the run: LM Studio served the model on
Windows, a GuideLLM runner measured it inside WSL2, and schema 1.0 has exactly
one `hardware` object with no way to say which of the two machines it is.
Publishing the WSL distribution as though it were the serving hardware would
have been a lie, so the registry refused the bundle instead.

Schema 1.1 says it. Schema 1.0 is unchanged, and a stored 1.0 bundle keeps
exactly the meaning it was written with: every 1.1-only property is forbidden
on a 1.0 result, so one can never acquire a fact nobody collected.

## When a result is 1.1

`omobench package` produces 1.0 unless you pass `--provenance`, in which case
it produces 1.1. A run that went through the Windows bridge can only be
packaged with `--provenance`: there is no honest 1.0 form of it.

Nothing about an existing 1.0 packaging changes — same fields, same bytes, same
`configuration_fingerprint`.

## Provenance: how each fact was established

Every serving fact carries one of four origins.

| Provenance | Means |
| --- | --- |
| `detected` | A probe running **on the machine that owns the fact** read it. |
| `server-reported` | The inference server itself reported it over its own API. |
| `contributor-declared` | A person typed it. |
| `unknown` | Nobody established it. The value is then `null`. |

A null value and `unknown` always travel together, in both directions: a value
with nothing behind it would be invented, and an `unknown` that still carried a
value would be laundered of where it came from. Both are rejected.

A fact is never upgraded. A value a person typed stays `contributor-declared`
however plausible it looks, and however well it agrees with something else.

**Detecting a device is not evidence that inference used it.** Each accelerator
carries a separate `role`: `inference`, `present-not-used`, or `unknown`. A
probe that found a card leaves the role unknown, because it watched nothing
run. Only the devices marked `inference` become `hardware.gpus`.

## The declaration document

One JSON file, passed as `--provenance`. The panel writes it for you as
`provenance.json` beside the evidence; by hand it looks like this.

```json
{
  "serving": {
    "relationship": "windows-host-wsl-runner",
    "host": {
      "os": "Windows 11 Pro 10.0.26200",
      "cpu": "AMD Ryzen 7 9800X3D",
      "ram_gb": 64
    },
    "accelerators": [
      {"vendor": "amd", "model": "AMD Radeon RX 9070",
       "memory_gb": 16, "count": 1, "role": "inference"}
    ],
    "placement": {"mode": "gpu-only"},
    "runtime": {
      "application": {"name": "LM Studio", "version": "0.3.31"},
      "engine": {"name": "llama.cpp", "version": "b10621"},
      "settings": [{"name": "context_length", "value": "8192"}]
    }
  },
  "served_model_id": "deepseek-r1-distill-qwen-14b",
  "tokenizer": {
    "source": "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B",
    "kind": "huggingface",
    "loaded": "loaded-before-run"
  },
  "catalog_status": "catalogued"
}
```

### `serving.relationship`

`same-host` (one machine served and measured) or `windows-host-wsl-runner`
(the server on the Windows host, the runner in a WSL2 distribution on it).
Nothing else is representable, and a declaration that disagrees with the
evidence is refused in both directions.

On `same-host`, the runner's own `environment.json` snapshot **is** a reading
of the serving machine, so its facts are recorded as `detected`. On
`windows-host-wsl-runner` nothing from that snapshot crosses over.

### `serving.placement.mode`

`cpu-only`, `gpu-only`, `cpu-gpu-offload` or `unknown`. A split placement must
carry a `detail` saying how the weights were split, as the serving runtime
itself reports it — "32 of 49 layers on GPU". Without it, the result would say
"GPU" and mean something else.

### `serving.runtime`

`engine` is what executed the weights; `application` is the program hosting it,
where there is one. LM Studio's release version is not llama.cpp's build, and
recording one as the other loses the engine. When the engine's version is
known it must equal `runtime.version` in the result, so there is one serving
runtime version rather than two that can disagree.

### `served_model_id`

The string your server answers to. It is a serving alias a serving application
invented; `model.id` stays the canonical catalog identity, and nothing derives
one from the other without a person choosing it.

`omobench ui` offers candidates ("Suggest a catalog model") and applies none of
them. When more than one matches, the mapping is ambiguous and you must pick,
because picking for you would publish a measurement under a model nobody ran.

### `weights`

Where the bytes came from, kept apart from the canonical model. Two routes.

**A repository commit**, when the weights have a public immutable revision:

```json
"weights": {
  "kind": "repository",
  "repository": "https://huggingface.co/unsloth/Qwen3-4B-GGUF",
  "repository_revision": "<commit sha>"
}
```

`repository_revision` must equal `model.revision`. One weight set has one
revision. A file's SHA-256 is a different thing and does not go here.

**The bytes themselves**, when there is no public immutable revision:

```bash
omobench weights --directory ~/models/my-conversion            # lists, reads nothing
omobench weights --directory ~/models/my-conversion \
  --file model-00001-of-00003.safetensors \
  --file model-00002-of-00003.safetensors \
  --file model-00003-of-00003.safetensors --output weights.json
```

Each named file is read and hashed; a filename is never evidence of content.
The manifest hash covers the whole ordered set, so one shard differing is a
different weight set rather than the same one with a footnote. Put the manifest
in the declaration and use the revision it prints:

```json
"weights": {"kind": "local-files", "manifest": { ... }}
```

The revision is `sha256-manifest:<hex>`, in its own namespace so it cannot be
mistaken for a repository commit in either direction. The registry recomputes
the manifest hash from the listed files: a hash nobody can reproduce is not an
identity.

### `tokenizer`

The tokenizer that built and counted the workload, because the token numbers
being compared are its numbers. `loaded` is `loaded-before-run` when OmoBench
loaded it in the runner's own interpreter, or `not-checked`. Neither says
anything about whether it matches the served weights; the result records
`matches_served_weights: "not-verifiable-here"` so a successful load is never
read as a match.

Omit the whole section and the result records that the runner used the served
identifier as a default — as exactly that, not as a verified tokenizer source.

### `catalog_status` and `proposal`

`catalogued` requires `model.id` to be in `catalog/models/`. `proposed` means
it is not, and the bundle carries a proposal for review:

```json
"catalog_status": "proposed",
"proposal": {
  "family": "DeepSeek",
  "parameter_class": "14B",
  "source": "https://huggingface.co/deepseek-ai/DeepSeek-R1-Distill-Qwen-14B",
  "licence": "MIT",
  "weights_publicly_downloadable": true,
  "rationale": "Why this model belongs in the catalog."
}
```

Packaging writes `model-proposal.json` beside the result, bound to that model,
that run and that account — taken from the result, not from what was typed. It
is uploaded as a sixth file in the same bundle directory, so a submission still
writes to exactly one prefix.

**A proposal is a request for review and nothing else.** It cannot describe
itself as accepted. `catalog/models/` stays the only source of eligible models,
and merging a result never adds one: accepting a proposal is a separate,
reviewed change. The website shows such a result as "Proposed — awaiting
review" and keeps it out of catalogued leaderboards.

## Reading the Windows serving hardware

In the panel, **Read the serving hardware** asks the companion to read the
Windows machine where it lives. It runs four CIM queries, prints one document
and exits: no listener, no process, no file, no registry key, no network, and
unreachable from an `omobench://` link.

It deliberately does not report video memory. Windows reports `AdapterRAM`
capped and wrong under modern drivers — 4 GB for a 16 GB RX 9070 — so VRAM is
left empty and must come from the serving runtime's own hardware panel. A
device whose memory you then supply is not a pure detection and is recorded as
declared.

## Packaging a run that already finished

A completed run can be packaged again, without re-running the benchmark:

```bash
omobench repackage --run-dir .omobench-runs/run-abc123 \
  --provenance provenance.json --output-dir .omobench-runs/run-abc123-repackaged
```

The declaration also needs a `"result"` section with the model, revision,
quantization, framework, framework version, execution device, profile and
submitter, because that run never produced a `result.json`.

The original directory is never written to. Its evidence files are copied byte
for byte, hashed on both sides, and the derived result records
`submission.packaging.metadata_declared_after_execution: true` along with the
hashes of the evidence it came from.

A hardware probe is **refused** here. A probe taken today describes today's
machine, not the machine as it was while the benchmark ran, so everything
supplied afterwards is recorded as declared.

If a run is missing an evidence file, repackaging says which one and that
nothing can reconstruct it. That run cannot become a public result.

## What comparison does with all this

Two results are compared only when their transport, serving arrangement, weight
placement and tokenizer source agree, alongside the model, weights, profile and
runner checks that were already there.

A bridged run carries the bridge's own latency inside every number, and that
latency has never been separated out or subtracted — so a bridged run is
comparable with other bridged runs, is labelled wherever it appears, and is not
charted against a direct one.
