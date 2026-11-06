# OmoBench agent guide

This is the shared project context for people and coding agents. Read it before changing the repository.

The [README](README.md) is the user-facing contributor guide. This file explains how to make safe, consistent changes. If guidance conflicts, follow the user's explicit request first, then the current schema, tests, and documentation.

## What OmoBench is

OmoBench is a GitHub-backed evidence registry and test harness for self-hosted, open-weight language-model inference. A contributor runs a fixed profile against their own OpenAI-compatible server, preserves raw evidence, packages a normalized result, and submits the complete bundle through a GitHub pull request.

`omobench ui` is a local browser controller around the same CLI. It can run CPU
or GPU workloads, and a Windows server measured from WSL through the explicit
bridge; all are eligible for public v1 packaging and submission, the bridged
case once its serving machine is described.
The comparison website and its `/run` launch guide are separate from this panel.
Both panel routes mount one shared live dashboard (`cli/src/omobench/web/dashboard.js`
and `dashboard.css`); keep it shared rather than reimplementing it per page, and
let each route declare only the stages it actually observes. Saving a local
report and preparing a public submission are separate actions with separate
requirements, on both routes.

It is **not** a model host, model downloader, cloud GPU provider, or automatic proof that a model server used the declared execution device.

`omobench prepare`/`omobench launch` and the panel's default page (also
reachable at `/guided`; the "connect a server you already run" panel moved to
`/advanced`) add a reviewed, managed CPU setup path (a development milestone,
not an installer): they download and verify one pinned model + runtime recipe
from `catalog/downloads/`, then run the same unchanged benchmark engine
described below, and can package/prepare its submission from the same page.
See [guided-launcher.md](docs/guided-launcher.md).

## V1 scope and invariants

- Public results are for catalogued open-weight, text-generation models, or for
  a model submitted with a proposal for the catalog. A proposal is a request for
  review: `catalog/models/` stays the only source of eligible models, merging a
  result never adds one, and accepting a proposal is a separate reviewed change.
- Requests use streaming OpenAI-compatible `/v1/chat/completions`.
- Public v1 results must be genuinely executed on their declared device. CPU and GPU are both first-class, publishable execution devices; `execution_device` describes what actually performed inference, not merely what hardware is installed.
- Benchmark profiles in `benchmarks/profiles/` are versioned workload definitions. Do not silently change their load, token counts, or metrics.
- A model revision must be immutable; do not accept `main`, `master`, or `latest`.
- The model catalog in `catalog/models/` is the source of truth for eligible public models. Do not hard-code a competing model list.
- Runtime provenance matters. Preserve the exact framework, version, startup arguments, and a meaningful description for `openai-compatible-other`.
- GPU metadata is vendor-neutral. Do not make NVIDIA-only assumptions or infer VRAM from an unreliable source.
- `--execution-device` records a contributor's declaration; it does not create or independently prove execution on that device. A CPU result must not represent an installed-but-unused GPU as having performed inference, its `hardware.gpus` stays empty even when the machine has an idle GPU.

### Schema versions and serving provenance

- `schema_version` is `1.0` or `1.1`. **1.0 is frozen.** Every 1.1-only property
  is forbidden on a 1.0 result, so a stored bundle keeps the meaning it was
  written with and never acquires a fact nobody collected. Packaging emits 1.0
  unless `--provenance` is supplied, byte for byte as before, with the same
  `configuration_fingerprint`.
- 1.1 separates the environment that ran the benchmark from the environment that
  served the model, and records the transport between them. `hardware` still
  means "what performed inference", so on a bridged run it comes from the
  serving side. **What WSL can see of its Windows host is not evidence about
  what served the model**: never fill the serving side from the runner's own
  snapshot, and never from a probe that did not run on the serving machine.
- Every serving fact carries how it was established: `detected`,
  `server-reported`, `contributor-declared` or `unknown`. A null value and
  `unknown` always travel together. A fact is never upgraded between them.
- Detecting a device is not evidence that inference used it. An accelerator's
  `role` is a separate claim; only `role: inference` devices become
  `hardware.gpus`.
- Transport latency is recorded as included in every measured number and is
  never subtracted. Bridged results compare with bridged results.
- Four identities stay apart: the served alias the API answers to, the canonical
  catalog model, the weight source, and the weight revision. A repository commit,
  a file SHA-256 and a `sha256-manifest:` hash are different things and are never
  interchanged. A serving alias is never mapped to a catalog model without a
  person choosing it, and an ambiguous mapping requires a choice.
- `runtime.version` is the inference engine's build. An application release, such
  as the LM Studio version, belongs in
  `environment.serving.runtime.application.version`.

See [provenance.md](docs/provenance.md).

### Three gates, and what may stop a run

- Every check a surface shows belongs to exactly one gate: `run` (a benchmark
  can start and would measure something), `package` (the evidence can become a
  schema-valid bundle), or `submit` (the registry will take that bundle). One
  definition in `cli/src/omobench/workflow.py`; the panel's checklists are
  views over it, never a second computation.
- **Only an actual runner constraint may stop a run.** A model awaiting catalog
  review blocks submission alone: the measurements are real, the evidence is
  complete and the bundle validates, because none of that depends on a pending
  decision. Never refuse a benchmark for a publication requirement.
- Never describe an ineligible result as ready for the registry. A validated
  bundle is not an accepted one, and an open pull request is not a published
  result -- acceptance follows review, a merge and a site build, none of which
  the panel observes, so it is never reported as done.
- A check nobody has run is not a pass. Distinguish present-and-wrong (a
  failure) from not-supplied-yet (what will be needed) from nobody-checked.
- Stage progress is derived from state, never from navigation, and moving
  between stages never clears what was entered.
- Both panel routes mount one shared workflow view
  (`cli/src/omobench/web/workflow.js`) for the stepper, the gate lists, the
  completion states and the evidence summary, exactly as they already share one
  dashboard. The guided page and the advanced panel are two ways to start the
  same workflow, not two workflows.
- A profile stops on its request budget or its duration ceiling, whichever
  comes first, and a ceiling-driven stop cancels the request in flight -- which
  counts against the error rate the registry checks. Say what a profile
  executes, when it stops and what qualifying takes *before* the run. Never
  estimate how long a particular machine will take.
- Anything after the benchmark must be retryable without re-running it. A
  refused bundle or a failed submission leaves every evidence file untouched.
- A reading of a machine names the environment it describes. Inside WSL,
  `MemTotal` is what Windows allocated to the distribution, not the memory
  installed in the computer, and on a bridged run the serving host is a
  different machine the runner knows nothing about. Never present one as the
  other. Report severity separately from what a check blocks, state the
  choices, and change nothing: no WSL setting is written and no process is
  stopped on a contributor's behalf. Swap in use is not proof of paging.

### Explaining evidence and comparisons

- The evidence summary (`cli/src/omobench/evidence.py` and
  `app/lib/evidence.ts`) shows a fact nobody recorded as "Not recorded" rather
  than omitting it, scores nothing, and never equates detection with
  verification. The two implementations are held to identical output by
  `cli/tests/test_evidence_parity.py`; change them together.
- Comparison suitability depends on the question being asked, and lives in one
  place (`app/lib/comparison.ts`). Do not duplicate slightly different rules in
  filters, tables and detail pages. Strict ranking eligibility is unchanged and
  separate from exploratory side-by-side inspection.
- A bridged run is never comparable with a direct one under any question. Do
  not invent a composite score or a universal threshold.

See [guided-workflow.md](docs/guided-workflow.md).

## Evidence and result integrity

Treat `results/` as append-only public evidence.

- Do not fabricate, edit by hand, redact incompletely, or regenerate benchmark evidence to make a result look better.
- Preserve the complete bundle: `result.json`, raw runner output, runner log, environment snapshot, and reproduction instructions.
- Keep artifact paths and SHA-256 hashes valid.
- Never place API keys, tokens, private hostnames, private prompts, model weights, or other secrets in a bundle, log, or documentation.
- Evidence is redacted as it is written: before the raw report is compressed,
  and therefore before it is hashed. Evidence collected earlier passes through
  the same rules into a copy (`omobench redact`), never by rewriting the
  original and never by re-running the benchmark; `omobench repackage` refuses
  material it would otherwise copy, because it records source hashes as proof
  the derived bundle carries the same measurements. What a redaction removes,
  `scan-evidence` must independently fail to find — a check that only looks
  for the strings it just replaced only agrees with itself. Schema validity,
  privacy and profile acceptance are three separate verdicts; never let one
  stand in for another.
- Do not claim that a run is publishable unless the server's own logs or hardware telemetry confirm real inference on the declared execution device.

Local staging data belongs in `.omobench-runs/`; UI jobs use
`.omobench-runs/ui/job-<id>/run-<id>/`. Running or packaging does not upload data.
Pushing a submission branch/PR exposes its evidence according to repository
visibility, before merge. Registry inclusion follows review, merge, and a site build.

The panel submits only the five standard evidence files after explicit review,
using either session-only GitHub device sign-in to create/reuse the contributor's
public fork and open an upstream PR, or the legacy `gh` route to the displayed
`origin`. Community uploads require a current server-side review bound to the
account, destination and bundle digest. Sign-in and preparation never upload
files; fork creation happens only after submission confirmation. The panel does
not merge PRs or change local Git branches.
Keep localhost/token/origin protections and the one-panel-per-checkout lock.

## Repository map

| Path | Responsibility |
| --- | --- |
| `benchmarks/profiles/` | Fixed, versioned benchmark workloads |
| `catalog/models/` | Eligible-model registry |
| `catalog/downloads/` | Reviewed managed download recipes (model + runtime provenance for `omobench prepare`/`launch`); see [guided-launcher.md](docs/guided-launcher.md) |
| `cli/` | `omobench` CLI, browser controller/assets, and tests |
| `schema/` | Public `result.json` and model-proposal contracts |
| `tools/` | Result validator and validator tests |
| `results/` | Submitted evidence bundles |
| `app/` | Results website |
| `windows/companion/` | Windows companion that opens the existing panel from the website's `omobench://` link; see [windows-companion.md](docs/windows-companion.md) |
| `windows/installer/` | Downloadable Windows installer that installs the companion and prepares an environment; see [windows-installer.md](docs/windows-installer.md) |
| `docs/` | Contributor and submission documentation; see [guided-workflow.md](docs/guided-workflow.md) and [provenance.md](docs/provenance.md) |

## Safe change workflow

1. Inspect the current branch, `git status`, and relevant existing files before editing.
2. Keep changes narrow and explain any behaviour or contract change in the appropriate documentation.
3. Add or update tests whenever changing CLI behaviour, schema validation, evidence handling, profiles, or the website's result interpretation.
4. Use a feature branch and pull request. Do not push directly to `main`.
5. Do not reset, discard, stash, delete branches, or remove worktrees without explicit user approval.
6. Avoid broad reformatting, unrelated refactors, generated-file churn, and unrequested deployment changes.

## Validation

Run the checks relevant to the change; run the full set for CLI, schema, validator, website, or workflow changes:

```bash
python -m unittest discover -s cli/tests -v
python -m unittest discover -s tools/tests -v
python tools/validate_results.py
npm run lint
npm run build
git diff --check
```

A result-bundle change must pass `python tools/validate_results.py`. Keep CI green before merge.

## Platform notes

Native Windows is supported for repository work, website development, CLI checks, and dry runs. The current GuideLLM execution path (`omobench run --execute`) must run on Linux, macOS, or WSL2 because its recommended dependency set includes `uvloop`, which does not support native Windows.

The panel inherits this execution restriction. Label Windows-shell entry commands
and WSL commands explicitly in documentation. Keep Windows and Linux Python
environments separate, and verify editable imports point to the intended checkout.
Use [the setup guide](docs/quickstart.md), [CPU demo](docs/local-demo.md), and
[troubleshooting](docs/troubleshooting.md) for detailed operational instructions.

## Keep this file useful

Update this guide only when a durable project rule, scope boundary, validation requirement, or contributor workflow changes. Keep it concise; detailed usage belongs in the README and `docs/`.
