# Demonstrate OmoBench locally

There are two demos: a **real CPU inference benchmark** using the local control
panel, and a **website-only comparison preview** using illustrative data. The
first produces measurements on your machine; the second only demonstrates
the presentation and filters.

## Real CPU demo

Prefer not to hand-install a model server for this? [Guided CPU setup](guided-launcher.md)
downloads, verifies, and starts one reviewed small CPU model (`qwen3-4b-cpu-v1`)
through `omobench prepare`/`omobench launch` or the panel's `/guided` page,
then runs this same unchanged benchmark. It is a development milestone, not a
general installer. The rest of this walkthrough uses a model you install and
start yourself, which stays available either way.

### 1. Prepare once, before the demo

Complete [installation](quickstart.md) and confirm `omobench --help` lists
`ui`. Use Python/GuideLLM in Linux, macOS, or WSL2. Also install the model runtime
as described in [model-server setup](model-server.md).

This walkthrough uses an existing llama app at `~/.llama-app/llama` and Qwen3
0.6B Q4_0. If your installation provides `llama-server` instead, use the
[standalone command](model-server.md#cpu-example-with-llamacpp). The model is a
small local example, not a required OmoBench model or a catalogued public result.

Use one VS Code window with two terminal tabs. On Windows, enter this command
in **each** tab before running any Linux commands:

```bash
wsl.exe -d Ubuntu
```

Both prompts should become Linux prompts, without `MINGW64`. Keep the existing
checkout; a second terminal does not require a second clone or worktree.

### 2. Terminal A: start the CPU model server

In WSL/Linux, run:

```bash
~/.llama-app/llama serve \
  -hf ggml-org/Qwen3-0.6B-GGUF:Q4_0 \
  --alias Qwen/Qwen3-0.6B \
  --reasoning off \
  --parallel 1 \
  --ctx-size 2048 \
  -ngl 0 \
  --host 127.0.0.1 \
  --port 8092
```

The runtime may download the weights on first use. Start it before the
presentation, wait for the model to load, and verify CPU execution in its logs.
`-ngl 0` requests no GPU layer offload. Wait for it to report that it is
listening on port `8092`, then leave this terminal open.

If this command fails, follow [server troubleshooting](troubleshooting.md#streaming-connection-fails).
Do not proceed on the assumption that OmoBench will start the server later.

### 3. Terminal B: start the OmoBench panel

In your existing checkout, with the same Linux/WSL environment:

```bash
source "$HOME/.venvs/omobench-runner/bin/activate"
omobench ui
```

If you need to locate the checkout from WSL, see
[Windows paths](quickstart.md#windows-enter-wsl-first). The model-server terminal
does not need this Python activation.

Leave Terminal B open too. Copy its full printed connection URL, including
`#token=...`, into your normal browser if it does not open automatically.
The panel normally uses port `8765`; the model server uses `8092`.

### 4. Browser: connect and start

Enter:

| Field | Value |
| --- | --- |
| Server address | `http://127.0.0.1:8092` |
| Server model identifier | `Qwen/Qwen3-0.6B` |
| Benchmark profile | `chat-small-v1` |
| Server execution device | CPU · local measurements |

Click **Test connection**. After it confirms streaming works, tick the device
confirmation and click **Run benchmark**.

The test sends a tiny request; the benchmark runs the full fixed profile.
GuideLLM requests 1,024 input tokens and up to 256 output tokens, one stream at
a time, with a 50-request and 900-second duration constraint plus its configured
warm-up/cool-down behavior. It can take several minutes; neither a full request
count nor an exact completion time is guaranteed. See
[profile interpretation](../README.md#fixed-workloads-and-measurements).

While it runs, show the detected machine, profile settings, status, and live log.
Avoid chatting with the server or running other tests during measurement.

### 5. Inspect and save the measurements

When the status becomes **completed**, show the successful/failed request counts,
TTFT p50/p95, output throughput, and errors. Use **Download local summary** to
save the actual measured values. The complete local runner evidence stays in
the path shown below the log.

This is a real CPU language-model inference benchmark. CPU results remain local
under the current public v1 rules; the GPU packaging/submission controls are
unavailable. It is not a general CPU score, model-quality test, or fabricated
comparison-table row.

For a short presentation, run it beforehand and select the genuine completed
entry under **Recent runs**. Say it was measured earlier. You can show the
start flow separately without claiming an unfinished run has completed.
Restarting the panel from the same checkout restores UI-created run history;
direct CLI runs are not imported automatically.

### 6. Stop cleanly and run again later

If a benchmark is active, click **Cancel benchmark** first. It stops the
runner, not the model server. Cancelled or interrupted output is kept locally
and cannot be packaged through the panel.

Press Ctrl+C in Terminal B to stop the panel, then Ctrl+C in Terminal A to stop
the model server. Closing only the browser does not stop either service.

Next time, repeat the two launch steps and open the newly printed panel URL.
No reinstall or Git pull is needed on every launch; only update when you want
new code, and do that between runs.

## Website-only comparison preview

From the checkout, in a terminal with Node.js and npm installed:

```bash
npm ci
npm run dev
```

Open the development URL it prints. This starts the comparison website, not
`omobench ui`, and needs neither a model server nor a GPU.

`app/data/demo-results.ts` supplies labelled illustrative rows for exercising
the filters and preview details. They are separate from `results/`, contain
no real benchmark evidence, and must not be submitted as measurements.

A validated, packaged GPU bundle can be previewed in the website's registry
using [local results](local-results.md). CPU summary downloads remain in the
panel/local files; they are not the website's public result format.

For a project presentation, explain that a real public result requires GPU
evidence, packaging/validation, a reviewed GitHub PR, and a website build
containing the merged result. A successful deployment alone is not a benchmark.
