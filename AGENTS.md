# OmoBench agent guide

This is the shared project context for people and coding agents. Read it before changing the repository.

The [README](README.md) is the user-facing contributor guide. This file explains how to make safe, consistent changes. If guidance conflicts, follow the user's explicit request first, then the current schema, tests, and documentation.

## What OmoBench is

OmoBench is a GitHub-backed evidence registry and test harness for self-hosted, open-weight language-model inference. A contributor runs a fixed profile against their own OpenAI-compatible server, preserves raw evidence, packages a normalized result, and submits the complete bundle through a GitHub pull request.

It is **not** a model host, model downloader, cloud GPU provider, or automatic proof that a model server used a GPU.

## V1 scope and invariants

- Public results are for catalogued open-weight, text-generation models.
- Requests use streaming OpenAI-compatible `/v1/chat/completions`.
- Public v1 results must be genuinely GPU-backed.
- Benchmark profiles in `benchmarks/profiles/` are versioned workload definitions. Do not silently change their load, token counts, or metrics.
- A model revision must be immutable; do not accept `main`, `master`, or `latest`.
- The model catalog in `catalog/models/` is the source of truth for eligible public models. Do not hard-code a competing model list.
- Runtime provenance matters. Preserve the exact framework, version, startup arguments, and a meaningful description for `openai-compatible-other`.
- GPU metadata is vendor-neutral. Do not make NVIDIA-only assumptions or infer VRAM from an unreliable source.
- `--execution-device gpu` records a contributor's declaration; it does not create or independently prove GPU execution.

## Evidence and result integrity

Treat `results/` as append-only public evidence.

- Do not fabricate, edit by hand, redact incompletely, or regenerate benchmark evidence to make a result look better.
- Preserve the complete bundle: `result.json`, raw runner output, runner log, environment snapshot, and reproduction instructions.
- Keep artifact paths and SHA-256 hashes valid.
- Never place API keys, tokens, private hostnames, private prompts, model weights, or other secrets in a bundle, log, or documentation.
- Do not claim that a run is publishable unless the server's own logs or hardware telemetry confirm real GPU inference.

Local staging data belongs in `.omobench-runs/` and is not public until a complete bundle is intentionally copied into `results/` and merged.

## Repository map

| Path | Responsibility |
| --- | --- |
| `benchmarks/profiles/` | Fixed, versioned benchmark workloads |
| `catalog/models/` | Eligible-model registry |
| `cli/` | `omobench` CLI and CLI tests |
| `schema/` | Public `result.json` contract |
| `tools/` | Result validator and validator tests |
| `results/` | Submitted evidence bundles |
| `app/` | Results website |
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

## Keep this file useful

Update this guide only when a durable project rule, scope boundary, validation requirement, or contributor workflow changes. Keep it concise; detailed usage belongs in the README and `docs/`.
