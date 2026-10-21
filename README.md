# OmoBench

> Reproducible benchmarks for language models you run yourself, with evidence reviewed through GitHub.

OmoBench measures how quickly a self-hosted, open-weight language model responds
to a fixed workload. It records latency, token throughput, request counts, and
errors alongside the model, runtime, and hardware configuration. Contributors
can submit complete evidence bundles through pull requests for comparison on
the website.

You can now control a benchmark through a **local browser panel**. Its buttons
run the existing CLI and pinned GuideLLM runner. CPU and GPU executions are both
first-class, publishable OmoBench results, provided the model is in the
[eligible-model catalog](catalog/models/seed-v1.yaml).

## Start here

- **Install or update OmoBench:** [Setup guide](docs/quickstart.md).
- **First time? Just run `omobench ui`:** it opens a guided benchmark by default —
  download and verify one reviewed CPU model, run it, understand the result, and
  prepare a submission, with no server address, model revision, runtime argument,
  or shell command to type. See [Guided CPU setup](docs/guided-launcher.md).
- **Already run your own model server?** Open `/advanced` in the same panel, or
  see the [Browser control guide](docs/browser-control.md) and
  [CPU demo walkthrough](docs/local-demo.md) for that workflow.
- **On Windows, prefer not to use a terminal?** Build the
  [Windows companion](docs/windows-companion.md) once and let it find your setup:
  it looks for your checkout and your WSL Python environment itself, so there are
  no paths to type, and afterwards the panel opens from its own window or from an
  `omobench://open` link.
- **Use terminal commands directly:** [CLI workflow](docs/quickstart.md#run-directly-from-the-cli).
- **Develop the comparison website:** [Website development](#develop-the-comparison-website).

## What runs where

| Component | What it does | How it starts |
| --- | --- | --- |
| A reviewed managed CPU model | Downloads, verifies, and serves one pinned model + runtime for you | Automatic, from the panel's default guided page |
| Your own model server (optional) | Loads weights and performs inference on CPU or GPU | Start llama.cpp, LM Studio, vLLM, or another compatible server separately, then connect from `/advanced` |
| Local OmoBench panel | Runs the CLI, shows logs/results, and prepares submissions | `omobench ui`; normally `127.0.0.1:8765` |
| Comparison website | Displays illustrative demo rows and a separate registry of committed results | Visit the hosted site, or use `npm run dev` locally |

The guided page downloads and runs one reviewed CPU model for you; the advanced
panel needs a model server you already run yourself and does not download models,
start that server, or select its hardware for you. The website's **Run a
benchmark** page explains how to launch the local panel; visiting the hosted
site does not start programs on your computer.

## Run through the browser

`omobench ui` opens a **guided benchmark by default** — see
[Guided CPU setup](docs/guided-launcher.md) for that walkthrough. The steps
below describe the **advanced panel** at `/advanced`: connecting to a model
server you already run yourself. Both panels run the same CLI, profiles, and
GuideLLM version; only how you get to a running server differs.

### 1. Prepare OmoBench

Use Linux, macOS, or **Ubuntu inside WSL2** for benchmark execution. Python 3.12
is used in CI and is the recommended version for the setup walkthrough.

On Windows, keep your existing VS Code window and checkout. In its terminal,
enter WSL before using Linux commands:

```bash
wsl.exe -d Ubuntu
```

The prompt should be a Linux prompt such as `user@computer:...$`. If it still
says `MINGW64`, you are in Windows Git Bash. See the
[Windows/WSL setup instructions](docs/quickstart.md#windows-enter-wsl-first).

From your checkout, in Linux/macOS/WSL, activate your Linux Python environment.
If you already have the runner environment used by this walkthrough:

```bash
source "$HOME/.venvs/omobench-runner/bin/activate"
python -m pip install -e './cli[ui,runner]'
omobench --help
omobench doctor
```

If that environment does not exist, [create it first](docs/quickstart.md#create-or-activate-the-python-environment).
Help must include `ui`. If it does not, follow
[update an existing checkout](docs/quickstart.md#update-an-existing-checkout).

The current runner dependency set includes `uvloop`, which does not install
under native Windows Python. A Windows `.venv/Scripts/activate` environment
cannot be used as a WSL environment. Node.js and npm are **not needed** for
benchmarking through the local panel.

On Windows, the [companion](docs/windows-companion.md) performs this step's
*selection* for you once the environment exists: it finds the checkout and the
Linux interpreter that already has OmoBench installed, checks the pair, and
opens the panel without a terminal. It does not create the environment or
install anything — the commands above still do that.

### 2. Start the model server

Use a second terminal, in the same Linux/WSL environment, to start your model.
Follow the [model-server guide](docs/model-server.md#cpu-example-with-llamacpp)
for a complete small-model CPU example.

Wait until the model has loaded and the server is listening. Note its address
and the exact model identifier it accepts. In our example those are
`http://127.0.0.1:8092` and `Qwen/Qwen3-0.6B`.

### 3. Open the panel and run

Back in the terminal with the OmoBench environment activated:

```bash
omobench ui
```

Keep both terminals open. If a browser does not open automatically, copy the
full URL printed by the panel, including `#token=...`, into your browser.
On WSL, use your normal Windows browser. Open **`/advanced`** for the workflow
below; the default page is the guided benchmark instead.

1. Enter the **Server address** and **Server model identifier** from your running
   server. Its address is different from the panel's port `8765`.
2. Click **Test connection** and wait for the streaming connection confirmation.
3. Choose `chat-small-v1` for a first run. Select **CPU** only after configuring
   CPU execution in the server, or select **GPU** after confirming GPU inference
   in its logs or telemetry. Tick the confirmation.
4. Click **Run benchmark**, watch the logs, and inspect the final measurements.
   The fixed workload can take several minutes, particularly on CPU.
5. Use **Download local summary** to save measurements. A completed run also
   offers **Review & submit**: packaging, validation, ZIP download, and PR
   creation, for CPU and GPU alike.

Selecting CPU or GPU in OmoBench records your declaration; it does not change
the model server. A CPU run measures language-model inference on your CPU, not
a general-purpose CPU score. CPU and GPU results are both first-class,
publishable OmoBench results.

See [troubleshooting](docs/troubleshooting.md) for missing commands, WSL paths,
incorrect identifiers, streaming errors, and interrupted runs.

## What is supported today

- Locally served models compatible with streaming `/v1/chat/completions` can be
  tried; public submissions must pass catalog eligibility checks.
- The panel accepts localhost model servers without authentication headers or
  redirects. Run it in the same OS/WSL environment as the server.
- Runtime records distinguish `vllm`, `sglang`, `llama.cpp`, `lmstudio`, and
  `openai-compatible-other` with a meaningful server description.
- NVIDIA, AMD, Intel, and other GPU metadata are supported through best-effort
  detection. This does not guarantee runtime/driver compatibility with every GPU.
- Live logs, cancellation, saved UI-run history, and local summary downloads work
  without a GitHub account. Authentication is needed for direct PR upload.
- Quality/accuracy evaluation, model installation, cloud hardware provisioning,
  and native Windows benchmark execution are outside the current workflow.
- A guided, reviewed CPU setup path (`omobench prepare`/`launch`, and the
  panel's default page) can download, verify, run, and — unlike a plain smoke
  test — package and prepare a submission for one pinned model + runtime
  recipe, without leaving the guided page. It is a development milestone, not
  a general installer; see [Guided CPU setup](docs/guided-launcher.md).

## Fixed workloads and measurements

Profiles live under `benchmarks/profiles/`. Compare results within the same
profile, accounting for model revision, quantization, and runtime differences.

| Profile | Purpose | Target input / requested output | Streams | Request limit | Duration limit |
| --- | --- | ---: | ---: | ---: | ---: |
| `chat-small-v1` | Interactive chat responsiveness | 1,024 / 256 tokens | 1 | 50 | 900 s |
| `chat-long-v1` | Long-context chat latency | 8,192 / 512 tokens | 1 | 30 | 1,800 s |
| `throughput-v1` | Sustained serving throughput | 1,024 / 256 tokens | 16 | 160 | 900 s |

The CLI pins GuideLLM to `0.7.3`, sets a fixed synthetic-prompt seed, includes
warm-up/cool-down phases, and passes request, duration, and 1% error-rate
constraints. Limits are not promises of an exact wall-clock duration or a full
request count. Requested output length is not guaranteed: a model can stop early.
The context must fit the prompt, requested output, and chat-template overhead;
the small demo's 2,048-token context is unsuitable for `chat-long-v1`.

| Metric | Meaning |
| --- | --- |
| TTFT p50 / p95 | Median / 95th-percentile time until the first streamed token; lower is better |
| Inter-token latency p50 / p95 | Delays between output tokens, as reported by GuideLLM; lower is better |
| Output tokens/sec | GuideLLM's reported output-token throughput; higher is better |
| Total tokens/sec | GuideLLM's reported input-plus-output token throughput |
| Requests/sec | GuideLLM's reported request rate for the workload |
| Error rate | Percentage of attempted requests that failed or remained incomplete |

Raw reports remain available for interpretation. The normalized summary leaves
peak memory as `null`; it is not a verified comparable score.

## Evidence and publication

Run locally, inspect the evidence, package a GPU result, validate it, and submit
a PR. Creating a branch or PR uploads evidence to GitHub; acceptance into the
website registry happens after review, merge, and a website build containing
that commit. Running or packaging alone does not upload anything.

UI jobs live under `.omobench-runs/ui/job-<id>/run-<id>/`; direct CLI runs default
to `.omobench-runs/run-<id>/`. The standard submission bundle is:

```text
result.json
guidellm-benchmarks.json.gz
guidellm.stdout.log
environment.json
reproduce.md
```

The packager records model/runtime/hardware metadata, an immutable model revision,
profile hash, and artifact hashes. Validation checks the schema, catalog,
obviously mutable revision labels, artifact paths/hashes, and duplicates. These
checks do not independently prove GPU usage or every workload acceptance rule.

The panel can upload a reviewed bundle using your existing GitHub CLI (`gh`)
authentication and write access to the checkout's `origin` repository.
Otherwise, download the ZIP and use a fork and manual PR. The panel does not
merge PRs. Read the [submission guide](docs/submission-bundle.md) before publishing.

## Develop the comparison website

Only website development needs Node.js (22.13 or later, as declared in
`package.json`). From the checkout, with Node and npm available in that terminal:

```bash
npm ci
npm run dev
```

Open the URL printed by the development server. This displays the comparison
website, not the benchmark controller. Illustrative rows are labelled and kept
separate from real `results/` bundles. The website imports normalized
`result.json` files at build time; raw evidence remains in GitHub for review.

To check website changes:

```bash
npm run lint
npm run build
```

The build uses vinext/Vite. The repository includes a Vercel adapter and the
existing Cloudflare build path; use the checked-in build/configuration rather
than substituting `next build`.

## Repository and documentation map

| Path | Purpose |
| --- | --- |
| `cli/` | CLI, local browser controller/assets, and tests |
| `benchmarks/profiles/` | Fixed workload definitions |
| `catalog/models/` | Eligible public models and licence metadata |
| `schema/` and `tools/` | Result contract and registry validation |
| `results/` | Submitted public evidence bundles |
| `app/` | Comparison website |
| [docs/quickstart.md](docs/quickstart.md) | Installation, updates, and direct CLI commands |
| [docs/model-server.md](docs/model-server.md) | Starting a server, model identifiers, CPU/GPU settings |
| [docs/browser-control.md](docs/browser-control.md) | Panel controls, recovery, packaging, and submission |
| [docs/local-demo.md](docs/local-demo.md) | CPU demonstration and website-only demo |
| [docs/guided-launcher.md](docs/guided-launcher.md) | Reviewed, managed CPU setup, run, and submission review — the panel's default page |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Common setup and runtime failures |
| [docs/submission-bundle.md](docs/submission-bundle.md) | Evidence requirements and manual PR workflow |
| [docs/local-results.md](docs/local-results.md) | Preview a packaged GPU result on the website |
| [AGENTS.md](AGENTS.md) | Shared contributor and coding-agent context |
