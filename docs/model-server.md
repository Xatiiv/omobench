# Start a model server for OmoBench

OmoBench sends benchmark requests to a server you run separately. Installing
`./cli[ui,runner]` installs the benchmark tools, not a model runtime or weights.
Use a runtime that provides streaming OpenAI-compatible `/v1/chat/completions`.

For the browser panel, start the server in the same Linux/macOS/WSL environment
as OmoBench. Both must be able to reach the same localhost address. Windows
LM Studio and WSL are different environments; their localhost connectivity
depends on WSL networking and should not be assumed.

## CPU example with llama.cpp

This example uses Qwen3 0.6B Q4_0 to keep a local demo small. OmoBench is not tied
to this model. It is not in the current public catalog, and this CPU example
is not a public submission.

Install a Linux/WSL build of llama.cpp using its
[official installation guide](https://github.com/ggml-org/llama.cpp/blob/master/docs/install.md)
or [release downloads](https://github.com/ggml-org/llama.cpp/releases).
For macOS, use a macOS build. Do not run Windows installation commands in WSL
and expect them to create a Linux executable.

If you already have the llama app used in the local walkthrough, open a
second WSL terminal and check it:

```bash
~/.llama-app/llama --version
```

With that app installed at that path, start the server:

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

If your installation instead provides a standalone `llama-server`, use this
command **instead**, not as a second server:

```bash
llama-server \
  -hf ggml-org/Qwen3-0.6B-GGUF:Q4_0 \
  --alias Qwen/Qwen3-0.6B \
  --reasoning off \
  --parallel 1 \
  --ctx-size 2048 \
  -ngl 0 \
  --host 127.0.0.1 \
  --port 8092
```

If the executable is not on PATH, use its actual installed path. The standalone
server does not take the app's `serve` subcommand. Check your installed version's
`--help` if it rejects a flag.

The runtime downloads the GGUF on first use and normally reuses its cached copy.
An existing GGUF can be used by replacing the `-hf ...` argument with
`-m "/absolute/path/to/model.gguf"`. Keep weights outside the repository.
The download shortcut is for local testing; record the actual immutable weights
revision and runtime version for any later reproducible submission.

In this example, `-ngl 0` requests no GPU layer offload, `--alias` supplies the API
identifier, `--parallel 1` uses one server slot, and `--reasoning off` disables
thinking for the demo. Verify CPU execution in the runtime's logs. For runtimes
with other offload settings, disable those too; llama.cpp also documents
`--device none` to disable device offload. The
[server option reference](https://github.com/ggml-org/llama.cpp/tree/master/tools/server)
describes these settings.

Wait until the model is loaded and the server reports it is listening on
`http://127.0.0.1:8092`. A download completing, or a model appearing in a library,
does not mean the server is ready. Keep this terminal open. It does not require
activation of the OmoBench Python environment.

## Address and model identifier

For the example above, enter these exact values in OmoBench:

| Field | Value |
| --- | --- |
| Server address | `http://127.0.0.1:8092` |
| Server model identifier | `Qwen/Qwen3-0.6B` |
| Benchmark profile | `chat-small-v1` |
| Server execution device | CPU · local measurements |

The model server's address is not the panel's address (`127.0.0.1:8765`).
The panel accepts a server base URL with or without a final `/v1`; do not enter
the full `/v1/chat/completions` path in that field.

The identifier is the exact name accepted by the server API, including case
and any slash. Do not guess it from the model's display title. In llama.cpp
the example's `--alias` sets it. LM Studio may show a different identifier such
as `qwen3-0.6b`; copy the actual value from your running server.

When supported, list API identifiers from the **same environment as OmoBench**:

```bash
curl --noproxy '*' --fail-with-body --max-time 15 http://127.0.0.1:8092/v1/models
```

Read the `id` value in `data`. Adjust the port for your server. Some runtimes can
list models that are not loaded, so also load the model and test a streaming
completion. See [LM Studio's model-list documentation](https://lmstudio.ai/docs/developer/openai-compat/models).

## Verify streaming before benchmarking

Use **Test connection** in the panel. It sends a tiny request and does not
create benchmark evidence. To diagnose it from the OmoBench terminal instead:

```bash
curl --noproxy '*' --fail-with-body --max-time 60 -sS -N \
  http://127.0.0.1:8092/v1/chat/completions \
  -H 'Content-Type: application/json' \
  --data '{"model":"Qwen/Qwen3-0.6B","messages":[{"role":"user","content":"Reply with exactly OK"}],"temperature":0,"max_tokens":16,"stream":true}'
```

Expect streamed `data:` chunks rather than a connection error or HTML page.
The model need not answer exactly `OK` for transport to work. Do not send
additional chat/test requests during the benchmark; they can affect measurements.
See [connection troubleshooting](troubleshooting.md#streaming-connection-fails)
if this request fails.

## Use a different model or GPU

Load your chosen model in the server, test its actual API identifier, and enter
that identifier in the panel. Set a context size large enough for the profile's
input, requested output, and template overhead. The demo's context/slot settings
are for `chat-small-v1`; long-context and 16-stream workloads need appropriate
server capacity.

For GPU measurements, configure offload/acceleration in the runtime first.
Confirm actual GPU inference in logs or telemetry, then select GPU in OmoBench.
Do not reuse the CPU command above unchanged and label its output GPU-backed.
Vendor-neutral metadata does not repair a crashing runtime or install its drivers.

The API alias is separate from the canonical model identity used for packaging.
For public results, supply a catalogued ID, immutable revision, quantization, and
the actual framework/version/settings. LM Studio should be recorded as
`lmstudio`, not relabelled as a standalone llama.cpp server. Follow the
[submission contract](submission-bundle.md).
