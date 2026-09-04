# Result submission bundle

## v1 scope

OmoBench v1 covers open-weight text-generation models served through an
OpenAI-compatible chat-completions endpoint.

## Model eligibility

`catalog/models/*.yaml` is the source of truth for eligible submitted models.
`tools/validate_results.py` rejects a submitted result whose `model.id` is
absent from the catalog, or whose catalog `modality` is not
`text-generation`. This gate lives only in registry validation and CI; the
`omobench` CLI package itself stays permissive about model IDs so it can be
used for local staging before a model is added to the catalog.

A catalog entry needs at least `id` and `modality`; a missing or malformed
`catalog/models/*.yaml` file fails validation with a clear error instead of
silently accepting or rejecting every submission.

## Immutable model revisions

`model.revision` must identify an immutable model revision or commit SHA.
Registry validation rejects an empty revision and clearly mutable labels —
`main`, `master`, and `latest` (case-insensitive) — because they can point to
different weights over time and break reproducibility. Normal SHA-based (or
otherwise content-addressed) revisions are accepted.

Each benchmark submission occupies one directory:

```text
results/<model-slug>/<YYYY-MM-DD>-<run-id>/
  result.json
  guidellm-benchmarks.json.gz
  guidellm.stdout.log
  environment.json
  reproduce.md
```

`result.json` is the normalized summary. It must validate against
`schema/benchmark-run.schema.json` and record the exact model revision, runtime
version, normalized startup arguments, driver, hardware, runner version, and
profile hash.

The remaining files are the evidence for the summary:

- `guidellm-benchmarks.json.gz` — unmodified raw GuideLLM output, compressed.
- `guidellm.stdout.log` — the runner's standard output after redaction.
- `environment.json` — detected operating system, CPU, RAM, GPU, driver, and
  package-version information.
- `reproduce.md` — exact non-secret commands needed to recreate the run.

Never commit API keys, access tokens, hostnames, serial numbers, user prompts,
model weights, or credentials. A submitter must redact those values before
creating a pull request.

## Create the local normalized summary

After `omobench run --execute` finishes successfully, package its staging
directory into a local `result.json`:

```bash
omobench package \
  --run-dir .omobench-runs/run-EXAMPLE \
  --model Qwen/Qwen3-4B \
  --model-revision YOUR_IMMUTABLE_MODEL_REVISION \
  --quantization BF16 \
  --framework vllm \
  --framework-version YOUR_VLLM_VERSION \
  --execution-device gpu \
  --runtime-arg "--served-model-name=Qwen/Qwen3-4B" \
  --profile chat-small-v1 \
  --submitter YOUR_GITHUB_USERNAME
```

The command reads the raw compressed GuideLLM report, local environment
snapshot, runner log, and reproduction file. It writes `result.json` beside
them, including hashes for every evidence file. It accepts only the pinned
GuideLLM v0.7.3 report format and does not contact GitHub.

The model revision, quantization, framework version, and startup arguments are
deliberately supplied by the operator. An OpenAI-compatible endpoint cannot
reliably report those reproducibility-critical facts itself.

OmoBench v1 packages GPU-backed results only. The operator must pass
`--execution-device gpu` after confirming that the model server used the GPU.
CPU-only runs may be useful for local testing, but they are not publishable
records yet.

## Runtime provenance

`--framework` records which server produced the endpoint and accepts:

- `vllm`
- `sglang`
- `llama.cpp`
- `lmstudio`
- `openai-compatible-other` — any other OpenAI-compatible server

Every `runtime` still requires an `endpoint_type` of `openai-compatible`;
these framework choices only record *what implements* that endpoint.

When `--framework openai-compatible-other` is used, `--server-description`
is required — a free-text description of the server (for example, "internal
gateway in front of a fine-tuned model"). `omobench package` fails clearly
if that framework is selected without a description:

```bash
omobench package --framework openai-compatible-other ...
# --server-description is required when --framework is openai-compatible-other.
```

`--server-description` is optional for the other frameworks. When supplied,
it is recorded as `runtime.server_description` in `result.json` and included
in the `configuration_fingerprint`, so changing only the description produces
a new configuration. Existing `result.json` files without
`runtime.server_description` remain valid; the field is optional in
`schema/benchmark-run.schema.json` except when `runtime.framework` is
`openai-compatible-other`.

## Repeats and duplicates

A `configuration_fingerprint` is a SHA-256 digest of the normalized model
revision, quantization, framework and version, startup arguments, hardware
class, driver, and profile. A `run_id` identifies one execution session.

- Same fingerprint and same raw-runner hash: accidental duplicate; rejected.
- Same fingerprint but a different raw-runner hash: valid repeat.
- Different submitter or host with the same configuration: valid replication.
- Changed model revision, quantization, runtime, driver, hardware, arguments,
  or benchmark profile: a new configuration.

## Continuous integration checks every submission

Opening a pull request against `results/` runs `tools/validate_results.py` in
GitHub Actions. It re-validates every `result.json` in the registry (not just
the one you added) against `schema/benchmark-run.schema.json`, then confirms
that each artifact path in `artifacts` is relative, stays inside its own
result directory, exists, and matches the recorded SHA-256 hash. The same
workflow runs the CLI's unit tests and the website's lint and build. CI
reports an invalid or tampered bundle on the pull request, and this check
should pass before the pull request is merged.

You can run the same check locally before opening a pull request:

```bash
python tools/validate_results.py
```
