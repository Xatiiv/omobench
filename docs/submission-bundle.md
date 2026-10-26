# Result submission bundle

## v1 scope

OmoBench v1 covers open-weight text-generation models served through an
OpenAI-compatible chat-completions endpoint.

CPU and GPU runs are both eligible for public v1 records: `--execution-device`
declares what actually performed inference, not merely what hardware is
installed. A CPU run's quick **Download local summary** JSON is still not a
public `result.json` and must not be placed under `results/` by hand, use
packaging (CLI or panel) to produce a schema-ready bundle instead.

There are two submission routes: **Review & submit** in `omobench ui`, or the
[manual PR workflow](#manual-pull-request-workflow). Both preserve the same five
evidence files and go through CI and maintainer review. Packaging stays local;
uploading a branch/PR makes evidence visible according to GitHub repository
permissions. The website registry updates after merge and a build containing it.

## Model eligibility

`catalog/models/*.yaml` is the source of truth for eligible submitted models.
`tools/validate_results.py` rejects a submitted result whose `model.id` is
absent from the catalog, or whose catalog `modality` is not
`text-generation`. This gate lives only in registry validation and CI; the
`omobench` CLI package itself stays permissive about model IDs so it can be
used for local staging before a model is added to the catalog.

A catalog entry requires non-empty `id`, `modality`, `source`, and `licence`
fields; a missing or malformed `catalog/models/*.yaml` file fails validation
with a clear error instead of silently accepting or rejecting every
submission.

The API identifier used during a run may be an alias. Packaging must use the
canonical catalog ID of the model actually served. Adding an alias does not
make a different model eligible. New eligible models can be added through a
catalog YAML change, without changing the runner; the browser uploader does not
automatically make that source change. The catalog's `initial_runtimes` field
is planning information, not an enforced runtime allowlist.

## Immutable model revisions

`model.revision` must identify an immutable model revision or commit SHA.
Registry validation rejects an empty revision and clearly mutable labels, `main`, `master`, and `latest` (case-insensitive), because they can point to
different weights over time and break reproducibility. Normal SHA-based (or
otherwise content-addressed) revisions are accepted.

`model.id` always stays the canonical catalog identity used for eligibility
and comparison, regardless of where the weights actually came from. The
optional `model.source` (set with `--model-source`) records the actual
weights source, ideally the exact download URL pinned to an immutable
revision and filename, for cases like a quantized re-export repository that
differs from `model.id`'s own repository (for example a GGUF conversion
maintained under a different namespace). **When `model.source` is supplied,
`model.revision` refers to that source's own repository, not to `model.id`'s
canonical upstream repository**, do not present it as a verified revision of
the upstream repo. Omitting `--model-source` keeps the historical
interpretation (`model.revision` refers to `model.id`'s own repository) and
existing bundles without `model.source` remain valid exactly as before.

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

- `guidellm-benchmarks.json.gz`, unmodified raw GuideLLM output, compressed.
- `guidellm.stdout.log`, the runner's standard output after redaction.
- `environment.json`, detected operating system, CPU, RAM, GPU, driver, and
  package-version information.
- `reproduce.md`, exact non-secret commands needed to recreate the run.

Never commit API keys, access tokens, private hostnames, serial numbers, user
prompts, model weights, or credentials. Inspect evidence before final packaging
and hashing; do not modify published bundles to conceal a problem. Automatic
endpoint redaction is not a substitute for reviewing all files and startup arguments.

## Create the local normalized summary

After a confirmed CPU or GPU run finishes successfully, package its staging
directory into a local `result.json`. This is a metadata template: replace the
model, revision, quantization, version, paths, submitter, and every runtime
argument with the actual values from that run.

```bash
omobench package \
  --run-dir .omobench-runs/run-EXAMPLE \
  --model Qwen/Qwen3-4B \
  --model-revision YOUR_IMMUTABLE_MODEL_REVISION \
  --quantization BF16 \
  --framework vllm \
  --framework-version YOUR_VLLM_VERSION \
  --execution-device gpu \
  --runtime-arg=--served-model-name=Qwen/Qwen3-4B \
  --profile chat-small-v1 \
  --submitter YOUR_GITHUB_USERNAME
```

Add `--model-source https://...` (an https URL, no embedded credentials) only
when the weights actually came from somewhere other than `--model`'s own
repository, for example a quantized re-export. Omit it entirely when the
weights came directly from `--model`'s own repository at `--model-revision`;
omitting it changes nothing about how the bundle is interpreted. The advanced
panel's package form prefills this field automatically for a run started from
a reviewed managed recipe, using that recipe's own pinned download URL.

The command reads the raw compressed GuideLLM report, local environment
snapshot, runner log, and reproduction file. It writes `result.json` beside
them, including hashes for every evidence file. It accepts only the pinned
GuideLLM v0.7.3 report format and does not contact GitHub.

Repeat `--runtime-arg=VALUE` for every setting; use the equals form when VALUE
starts with a dash, so argparse does not mistake it for another OmoBench option.
For a UI-created run, use the displayed inner
`.omobench-runs/ui/job-<id>/run-<id>/` path, not its parent job directory.

Packaging alone does not check catalog membership or all registry requirements.
Use **Package & validate** in the panel or the manual validator below. A green
validator result checks the implemented schema/catalog/artifact/duplicate rules;
it does not independently establish GPU execution or verify every acceptance
condition in the profile YAML. Inspect successful request counts, errors, and
raw output against the selected profile before submitting.

The model revision, quantization, framework version, and startup arguments are
deliberately supplied by the operator. An OpenAI-compatible endpoint cannot
reliably report those reproducibility-critical facts itself.

CPU and GPU results are both publishable. `--execution-device` describes what
actually performed inference, not merely what hardware is installed: pass
`--execution-device gpu` only after confirming the model server **genuinely
ran on the GPU** (the flag records that confirmation; it does not itself
verify or cause GPU execution), or `--execution-device cpu` for a confirmed
CPU-only run. A CPU package does not need GPU detection, VRAM, or a driver, its `hardware.gpus` is recorded empty even when the machine has an idle,
unused GPU, because that GPU did not perform inference.

## GPU vendor and memory

OmoBench reports NVIDIA, AMD, Intel, and other GPUs through available vendor
utilities and OS fallbacks. It does not install those utilities, and every
detection method is best-effort, see
[docs/quickstart.md](quickstart.md) for exactly how each vendor and OS
combination is detected. `hardware.gpus[].vendor` is optional in
`schema/benchmark-run.schema.json`; a `result.json` published before
vendor-neutral detection existed simply omits it and remains valid.

Some detection paths (notably the Windows CIM fallback) cannot safely
determine VRAM and record it as unknown rather than guessing. When
`omobench doctor` shows a detected GPU with no memory value, package that
run with:

```bash
omobench package --gpu-memory-gb 16 ...
```

Before doing so, confirm the real VRAM figure in the serving runtime's own
startup log or the OS hardware panel, `--gpu-memory-gb` only fills in a
memory value for a GPU `omobench doctor` already detected; it never invents
a GPU model or vendor, and `omobench package` still requires at least one
automatically detected GPU. It is rejected with a clear error if every
detected GPU already has a known memory value (nothing to fill in), and it
is rejected as ambiguous if GPUs with unknown memory span more than one
vendor/model class, since one value cannot be safely applied to more than
one kind of GPU. Applying it marks `hardware.detection.hardware` as
`user-confirmed` instead of `auto`.

## Runtime provenance

`--framework` records which server produced the endpoint and accepts:

- `vllm`
- `sglang`
- `llama.cpp`
- `lmstudio`
- `openai-compatible-other`, any other OpenAI-compatible server

Every `runtime` still requires an `endpoint_type` of `openai-compatible`;
these framework choices only record *what implements* that endpoint.

When `--framework openai-compatible-other` is used, `--server-description`
is required, a free-text description of the server (for example, "internal
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

`--model-source` behaves the same way: when supplied, it is recorded as
`model.source` and included in the `configuration_fingerprint`, so packaging
the same run once with a source and once without produces two different
configurations. Omitting `--model-source` leaves `model` exactly as before, the fingerprint for a run packaged without a source is unaffected by this
option's existence.

## Repeats and duplicates

A `configuration_fingerprint` is a SHA-256 digest of the normalized model
revision, quantization, source (when supplied), framework and version,
startup arguments, hardware class, driver, and profile. A `run_id` identifies
one execution session. `execution_device` is deliberately **not** part of
this digest: `hardware.gpus` (always empty for a `cpu` run) already separates
a `cpu` configuration from a `gpu` one, which keeps a `gpu` result's
fingerprint identical to what pre-`execution_device` code computed for the
same configuration, an old GPU bundle and a freshly repackaged, otherwise
identical GPU run still collide under the duplicate check below.

- Same fingerprint and same raw-runner hash: accidental duplicate; rejected.
- Same fingerprint but a different raw-runner hash: valid repeat.
- Different submitter or host with the same configuration: valid replication.
- Changed model revision, quantization, model source, runtime, driver,
  hardware, arguments, or benchmark profile: a new configuration.

## Manual pull request workflow

Use this if you prefer Git commands, lack `gh` authentication/write access, or
need to add a catalog entry alongside a result. Work in a checkout where you
can push a feature branch; fork the project first if you lack upstream access.
With a fork, open the eventual PR against the upstream repository's `main`.

1. Inspect `git status --short` and preserve unrelated work. From a suitable
   clean base, create a new submission branch, for example
   `git switch -c submit/your-new-run-id`.
2. If using the panel's ZIP, extract it **at the checkout root**. It already
   contains `results/<model-slug>/<date>-<run-id>/`. Do not nest it inside another
   `results/`, and do not replace an existing bundle.
3. For a directly packaged CLI run, copy the standard files instead. In the
   example below, replace the source path, model slug, date, and run ID first:

   ```bash
   RUN_DIR=".omobench-runs/run-YOUR_RUN_ID"
   RESULT_DIR="results/your-model/YYYY-MM-DD-run-YOUR_RUN_ID"
   mkdir -p results/your-model
   mkdir "$RESULT_DIR" &&
   cp "$RUN_DIR/result.json" "$RUN_DIR/guidellm-benchmarks.json.gz" \
     "$RUN_DIR/guidellm.stdout.log" "$RUN_DIR/environment.json" \
     "$RUN_DIR/reproduce.md" "$RESULT_DIR/"
   ```

   The final `mkdir` intentionally fails if the destination already exists.
   For UI evidence, use its inner run directory; never copy `job.json`, UI logs,
   or the whole `.omobench-runs/` tree into the public registry.
4. Install the validator dependency and check all bundles:

   ```bash
   python -m pip install -e './cli[ui]'
   python tools/validate_results.py
   ```

   If a model is absent from the catalog, add a legitimate entry in
   `catalog/models/` and validate again. Preserve the run's actual model identity.
5. Inspect all five files for completeness and private data, then stage only
   that bundle (and an intentional catalog entry, if needed). Replace the
   example paths and branch below before running:

   ```bash
   git add -- results/your-model/YYYY-MM-DD-run-YOUR_RUN_ID
   git diff --cached --check
   git diff --cached --stat
   git commit -m "Add benchmark evidence for your model"
   git push -u origin submit/your-new-run-id
   ```

6. Open a PR using the link Git prints, or GitHub's **Compare & pull request**.
   Describe the model, immutable revision, runtime, hardware, profile, actual GPU
   execution evidence, and local validation result. Review CI before merge.
   The website must subsequently build the merged commit to show the new result.

The browser panel's direct uploader also uses a new branch, but opens its PR
against the default branch of the displayed `origin` repository. If that is a
fork, it does not automatically create an upstream PR. Follow the manual route
above for an upstream submission.

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
python -m pip install -e './cli[ui]'
python tools/validate_results.py
```
