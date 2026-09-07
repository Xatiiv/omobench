# OmoBench

> Reproducible, GitHub-backed benchmarks for self-hosted language-model inference.

OmoBench is a public registry and test harness for comparing how open-weight
language models perform when **you run them yourself**. It applies the same
versioned workload to an existing OpenAI-compatible model server, records the
raw evidence, validates the submission in GitHub, and displays the normalized
result on the website.

It is deliberately **not** a model host, model downloader, cloud GPU service,
or automatic GPU verifier. You choose and run the model server; OmoBench makes
the resulting performance claim inspectable and comparable.

## What OmoBench v1 covers

- Open-weight, text-generation models.
- Streaming OpenAI-compatible `/v1/chat/completions` endpoints.
- GPU-backed public results.
- Reproducible server provenance for `vllm`, `sglang`, `llama.cpp`,
  `lmstudio`, and documented generic OpenAI-compatible servers.
- NVIDIA, AMD, Intel, and other GPU metadata.

The public registry accepts only models listed in `catalog/models/*.yaml`
with `modality: text-generation`. You may stage a local test for another
model, but GitHub validation will reject it until a catalog entry is added.

## How a result moves through the system

1. **Run a model server** yourself and expose an OpenAI-compatible chat endpoint.
2. **Run an OmoBench profile** against that endpoint using the `omobench` CLI.
3. **Keep local evidence**: raw GuideLLM output, a redacted runner log,
   environment snapshot, and reproduction command.
4. **Package** that evidence into a normalized `result.json` with model,
   runtime, hardware, and profile metadata.
5. **Submit the complete bundle in a GitHub pull request**.
6. **CI validates the bundle**, then the website reads the committed summary and
   displays it.

Nothing becomes public when you run or package a benchmark locally.

## Fixed benchmark profiles

Profiles are versioned files under `benchmarks/profiles/`. Do not compare
results from different profiles as if they measured the same thing.

| Profile | Purpose | Input → requested output | Concurrent streams | Measured requests |
| --- | --- | ---: | ---: | ---: |
| `chat-small-v1` | Interactive chat responsiveness | 1,024 → 256 tokens | 1 | 50 |
| `chat-long-v1` | Long-context chat latency | 8,192 → 512 tokens | 1 | 30 |
| `throughput-v1` | Sustained serving throughput | 1,024 → 256 tokens | 16 | 160 |

All profiles use deterministic synthetic prompts, streaming responses, warm-up
and cool-down traffic, and a 1% maximum error-rate constraint.

## Run a benchmark

### 1. Prerequisites

For a contributor running a benchmark, you need:

- Git and Python 3.10 or later.
- A reachable model server with streaming `/v1/chat/completions`.
- A real GPU-backed server for a publishable v1 result.
- Linux, macOS, or WSL2 for the current `--execute` flow; native Windows is currently unsupported for execution.

Native Windows is fine for VS Code, website development, CLI checks, and dry
runs. The current `cli[runner]` extra installs
`guidellm[recommended]==0.7.3`, whose dependency set includes `uvloop`;
that does not install under native Windows. Run the actual benchmark command
from WSL2 or another Linux host instead.

Node.js 22.13 or later is needed only when you are developing or building the
website.

### 2. Install the CLI

From an OmoBench checkout:

```bash
# Linux, macOS, or WSL2
python3 -m venv .venv
source .venv/bin/activate

pip install -e ./cli
omobench doctor
omobench profiles
```

For Windows Git Bash development:

```bash
python -m venv .venv
source .venv/Scripts/activate
pip install -e ./cli
```

`omobench doctor` reports the machine and tools visible to the shell. It
does not start a model or prove that your server is using a GPU.

### 3. Start your model server separately

Start a compatible server using the software you chose, such as LM Studio,
llama.cpp, vLLM, or SGLang. Confirm that it can answer a streaming request at
an endpoint like:

```text
http://localhost:8000/v1/chat/completions
```

Before publishing a GPU result, confirm in that server's own logs or hardware
panel that inference actually ran on the GPU. Passing
`--execution-device gpu` records your confirmation; it does not cause or
independently prove GPU execution.

### 4. Inspect the exact command first

Install the pinned runner when you are ready to contact the endpoint:

```bash
pip install -e './cli[runner]'

omobench run \
  --endpoint http://localhost:8000/v1 \
  --model Qwen/Qwen3-4B \
  --profile chat-small-v1
```

Without `--execute`, this is a dry run: it prints the exact GuideLLM command
and does not contact the endpoint.

### 5. Execute the profile

```bash
omobench run \
  --endpoint http://localhost:8000/v1 \
  --model Qwen/Qwen3-4B \
  --profile chat-small-v1 \
  --execute
```

This creates a Git-ignored staging directory such as:

```text
.omobench-runs/run-xxxxxxxxxxxx/
  guidellm-benchmarks.json.gz
  guidellm.stdout.log
  environment.json
  reproduce.md
```

The endpoint is redacted from the saved runner log and reproduction command.
Do not put tokens, API keys, hostnames, model weights, or private prompts in a
submission.

### 6. Package the local evidence

```bash
omobench package \
  --run-dir .omobench-runs/run-EXAMPLE \
  --model Qwen/Qwen3-4B \
  --model-revision YOUR_IMMUTABLE_MODEL_REVISION \
  --quantization Q4_K_M \
  --framework llama.cpp \
  --framework-version "YOUR_EXACT_VERSION" \
  --execution-device gpu \
  --runtime-arg "--ctx-size=4096" \
  --profile chat-small-v1 \
  --submitter YOUR_GITHUB_USERNAME
```

The package command writes `result.json` beside the evidence. It records:

- model ID, immutable revision, and quantization;
- server framework, exact version, and startup arguments;
- OS, CPU, RAM, GPU model/vendor/VRAM, and driver;
- profile ID and profile hash;
- latency, throughput, request, and error metrics;
- SHA-256 hashes for every evidence artifact.

Some Windows GPU detection paths can identify a GPU but cannot safely read its
VRAM. In that case, confirm the real VRAM in the serving runtime or hardware
panel and add `--gpu-memory-gb 16` (using the appropriate value). It only
fills missing memory for a GPU OmoBench already detected; it cannot invent a
GPU or override a known value.

For `--framework openai-compatible-other`, also supply a meaningful
`--server-description`.

## Read the results

| Metric | Meaning | Better direction |
| --- | --- | --- |
| TTFT p50 / p95 | Time until the first streamed token arrives | Lower |
| Inter-token latency p50 / p95 | Delay between generated tokens | Lower |
| Output tokens/sec | Generation speed after output begins | Higher |
| Total tokens/sec | Overall token-processing rate | Higher |
| Requests/sec | Completed request rate for that profile | Higher |
| Error rate | Failed request percentage | Lower |

OmoBench does not currently treat peak memory as a trustworthy comparable v1
metric, so it is not a headline score.

## Submit a public result

When the local result is correct, copy the **complete** run directory into the
repository:

```text
results/<model-slug>/<YYYY-MM-DD>-<run-id>/
  result.json
  guidellm-benchmarks.json.gz
  guidellm.stdout.log
  environment.json
  reproduce.md
```

Validate it locally:

```bash
python tools/validate_results.py
```

Then create a normal Git branch and pull request. CI checks the schema,
artifact hashes and paths, model catalog eligibility, immutable revision,
duplicate runs, CLI tests, validator tests, website lint, and website build.

A `configuration_fingerprint` distinguishes a real repeat from an accidental
duplicate: the same configuration plus the same raw output is rejected; the
same configuration with new raw evidence is a valid repeat.

## Repository map

| Directory | Purpose |
| --- | --- |
| `benchmarks/profiles/` | Immutable workload definitions |
| `catalog/models/` | Eligible public models and licence metadata |
| `cli/` | The `omobench` command-line tool |
| `schema/` | Public `result.json` contract |
| `results/` | Submitted evidence bundles |
| `tools/` | Registry validator and its tests |
| `app/` | Comparison website |

The website imports only normalized `result.json` files at build time. Raw
evidence remains in GitHub for review rather than being shipped to every
visitor's browser.

## Develop the website

Website work is separate from running a benchmark:

```bash
npm ci
npm run dev
npm run lint
npm run build
```

## More documentation

- [Local setup and runner notes](docs/quickstart.md)
- [Submission-bundle contract](docs/submission-bundle.md)
- [Benchmark-result JSON schema](schema/benchmark-run.schema.json)
- [Eligible-model catalog](catalog/models/seed-v1.yaml)
