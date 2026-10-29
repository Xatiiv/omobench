# OmoBench agent guide

This is the shared project context for people and coding agents. Read it before changing the repository.

The [README](README.md) is the user-facing contributor guide. This file explains how to make safe, consistent changes. If guidance conflicts, follow the user's explicit request first, then the current schema, tests, and documentation.

## What OmoBench is

OmoBench is a GitHub-backed evidence registry and test harness for self-hosted, open-weight language-model inference. A contributor runs a fixed profile against their own OpenAI-compatible server, preserves raw evidence, packages a normalized result, and submits the complete bundle through a GitHub pull request.

`omobench ui` is a local browser controller around the same CLI. It can run CPU
or GPU workloads; both are eligible for public v1 packaging and submission.
The comparison website and its `/run` launch guide are separate from this panel.

It is **not** a model host, model downloader, cloud GPU provider, or automatic proof that a model server used the declared execution device.

`omobench prepare`/`omobench launch` and the panel's default page (also
reachable at `/guided`; the "connect a server you already run" panel moved to
`/advanced`) add a reviewed, managed CPU setup path (a development milestone,
not an installer): they download and verify one pinned model + runtime recipe
from `catalog/downloads/`, then run the same unchanged benchmark engine
described below, and can package/prepare its submission from the same page.
See [guided-launcher.md](docs/guided-launcher.md).

## V1 scope and invariants

- Public results are for catalogued open-weight, text-generation models.
- Requests use streaming OpenAI-compatible `/v1/chat/completions`.
- Public v1 results must be genuinely executed on their declared device. CPU and GPU are both first-class, publishable execution devices; `execution_device` describes what actually performed inference, not merely what hardware is installed.
- Benchmark profiles in `benchmarks/profiles/` are versioned workload definitions. Do not silently change their load, token counts, or metrics.
- A model revision must be immutable; do not accept `main`, `master`, or `latest`.
- The model catalog in `catalog/models/` is the source of truth for eligible public models. Do not hard-code a competing model list.
- Runtime provenance matters. Preserve the exact framework, version, startup arguments, and a meaningful description for `openai-compatible-other`.
- GPU metadata is vendor-neutral. Do not make NVIDIA-only assumptions or infer VRAM from an unreliable source.
- `--execution-device` records a contributor's declaration; it does not create or independently prove execution on that device. A CPU result must not represent an installed-but-unused GPU as having performed inference, its `hardware.gpus` stays empty even when the machine has an idle GPU.

## Evidence and result integrity

Treat `results/` as append-only public evidence.

- Do not fabricate, edit by hand, redact incompletely, or regenerate benchmark evidence to make a result look better.
- Preserve the complete bundle: `result.json`, raw runner output, runner log, environment snapshot, and reproduction instructions.
- Keep artifact paths and SHA-256 hashes valid.
- Never place API keys, tokens, private hostnames, private prompts, model weights, or other secrets in a bundle, log, or documentation.
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
| `schema/` | Public `result.json` contract |
| `tools/` | Result validator and validator tests |
| `results/` | Submitted evidence bundles |
| `app/` | Results website |
| `windows/companion/` | Windows companion that opens the existing panel from the website's `omobench://` link; see [windows-companion.md](docs/windows-companion.md) |
| `windows/installer/` | Downloadable Windows installer that installs the companion and prepares an environment; see [windows-installer.md](docs/windows-installer.md) |
| `docs/` | Contributor and submission documentation |

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
