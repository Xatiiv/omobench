# Guided launcher: milestone 1

Status: **development experiment, not a released installer**.

The goal is an installed OmoBench application that prepares and launches a test,
with the same capabilities available from the CLI. The benchmark methodology,
runner, profiles, normalization, public result schema, eligibility rules, and PR
review process remain the existing ones.

The working product assumptions are Windows first, a desktop window, guided
model/runtime preparation, and an advanced connection mode for existing servers.
Automatic Windows installation and GPU backend qualification must be demonstrated
before those capabilities are advertised as ready.

## Review the interface

Open [the standalone interactive preview](../design/launcher-preview.html) in a
browser (download/open the HTML file if viewing it on GitHub). It needs no Node,
Python, accounts, or network requests. It previews:

1. Test, model, and CPU/GPU selection.
2. Storage selection, including a separate drive for larger downloads.
3. A review step before future downloads.
4. Loading, benchmark activity, cancellation, and result presentation.
5. CLI access, an advanced server connection, and publication eligibility.

The banner marks this as a design preview. Run activity is simulated. Results
contain placeholders, not measurements. Buttons never download models, contact
a model server, execute a benchmark, authenticate, or create a PR. GPU setup is
visibly unavailable until backend qualification. The display is a desktop-style
design; choosing a desktop wrapper is a later decision.

The model choices come from `catalog/models/*.yaml`; profile values come from
`cli.PROFILE_SPECS`. Catalog membership does **not** establish a working automatic
download/runtime configuration. The HTML export is a snapshot for easy review.

To preview the current source and catalog from an activated OmoBench environment:

```bash
python tools/preview_launcher.py
```

This read-only preview is at `http://127.0.0.1:8877/`. On WSL use `--no-browser`
and open that URL in the Windows browser. It exposes no execution APIs.

To regenerate the standalone file after a design change:

```bash
python tools/preview_launcher.py --export design/launcher-preview.html
```

## Exercise the actual launch experiment

This command is for developers testing the launch boundary. It still requires an
OmoBench checkout, an installed Linux/macOS llama-server, an existing GGUF file,
and the pinned GuideLLM runner for benchmark execution. It does not yet implement
the installation experience shown in the design. Use the existing
[setup instructions](quickstart.md) for prerequisites.

Run in an activated Linux/macOS/WSL Python environment, from this checkout:

```bash
omobench launch --check
```

The JSON describes the runner in the **current environment**. On native Windows,
it can list WSL distributions using a bounded diagnostic call, but always reports
`can_execute_here: false`. Listing a distribution does not verify its WSL version,
packages, drivers, or runtime. This check neither installs nor changes WSL.

Use your existing local executable and model paths (the paths below are examples
to replace). Omitting `--execute` and `--smoke` previews commands without starting
programs, contacting endpoints, or creating run directories:

```bash
omobench launch \
  --server /path/to/llama-server \
  --model-file /path/to/model.gguf \
  --model organization/model
```

Add `--smoke` to start the server, check `/health` and the configured model alias,
then stop it. This checks readiness; it is **not an inference or performance test**.

Add `--execute` instead to start the server and invoke the existing command:

```bash
omobench run --endpoint http://127.0.0.1:8092 \
  --model organization/model --profile chat-small-v1 \
  --output-dir .omobench-runs/launcher/job-<id> --execute
```

The launcher chooses the unique output directory; the example shows its shape.
Use `--port` if the default is occupied. A bounded startup timeout defaults to
120 seconds and can be set from 1 to 600 seconds. It only limits model loading;
it does not change the benchmark profile's duration.

The executable must implement the documented llama.cpp server arguments. A
standalone `llama-server` is required; a `llama serve` wrapper is not accepted as
a shell command. In WSL, Windows `.exe` servers are rejected because bridging a
Windows inference host and Linux runner needs separate metadata/transport testing.

This first experiment deliberately supports **CPU + chat-small-v1 only**, with a
2,048-token server context, one server slot, reasoning off, GPU layers set to zero,
and `--device none`. These are explicit server settings, not changes to the
benchmark profile. Models that need other settings remain usable through the
existing advanced CLI/UI workflow. The command does not claim to qualify every
GGUF model or to provide a general CPU score.

The launcher clears inherited `LLAMA_ARG_*` settings in its own server process so
hidden environment defaults cannot override the selected managed configuration.
All executable/argument invocation uses argument arrays, never a shell. Only the
launcher-owned server and CLI process groups are stopped on completion, failure,
Ctrl+C, or SIGTERM. Existing servers and the WSL VM are not terminated.

Readiness HTTP requests stop before the benchmark begins. While measuring, the
launcher checks process liveness only; it does not inject monitoring inference
requests. A periodic message says the runner **process is active**, which is not
a claim of completed requests or a calculated percentage.

## Files and provenance

Launcher records are saved in `.omobench-runs/launcher/job-<id>/`:

- `launcher.json`: lifecycle state, actual server version, model/executable
  SHA-256 hashes, exact launch arguments, and the existing normalizer's local summary.
- `server.stdout.log` and `cli.stdout.log`: local diagnostic output.
- `run-<id>/`: the four normal raw CLI artifacts, produced by the unchanged CLI.

The extra launcher diagnostics are local and can contain local paths. They are
not public submission files. A model-file checksum is recorded, but it does not
replace the canonical immutable revision required by public packaging. No
`result.json` or public CPU submission is created. Existing UI history does not
yet import launcher runs. A nonzero runner exit or incomplete bundle remains a
failed launch, with partial files preserved.

## Verification and remaining gates

Milestone checks on 2026-09-14:

| Check | Result |
| --- | --- |
| CLI/UI tests | 74 passed, including 16 launcher tests |
| Tool/validator tests | 27 passed, including two prototype data-source tests |
| Registry validator | Passed; no submitted bundles in the checkout |
| Website lint/build | Passed; one existing adapter lint warning |
| Browser walkthrough | Selection, storage, setup, stage progression, cancellation, result layout, CLI dialog, and mobile width passed; no JS errors or external requests |
| Benchmark code comparison | Existing command builder, runner execution, normalizer, packager, and evidence helpers unchanged |
| Real Windows installation / AMD / NVIDIA launch | Not verified in this Linux development environment |

Automated lifecycle tests use isolated fixture server/runner processes. They
exercise actual process startup, localhost readiness, alias checking, an occupied
port, timeout, server/runner failure, cancellation, and cleanup. They also assert
that the delegated GuideLLM argv exactly matches `build_guidellm_command()` and
that the summary comes from `normalize_guidellm_report()`. Fixture data is never
placed in `results/` or presented as real hardware measurements.

Windows detection tests mock WSL responses and launch errors. They are not a
successful Windows installation test. The prototype is checked in a browser for
its selection/setup/cancellation/results flows and mobile layout.

Before an installable release:

1. Run the launcher against a real, version-pinned Linux/WSL CPU server and GGUF
   model; compare its command/configuration and evidence with a direct CLI run.
2. Prove Windows installation, restart recovery, dependency isolation, storage
   selection, and bounded WSL recovery on a fresh Windows machine. Microsoft
   documents distribution installation/location and status commands in its
   [WSL command reference](https://learn.microsoft.com/en-us/windows/wsl/basic-commands).
3. Qualify NVIDIA and AMD runtime/driver combinations with real execution evidence.
   Hardware detection alone is not a pass. The previously observed AMD model-load
   crashes are unresolved; do not silently change device or runtime on failure.
4. Introduce reviewed download recipes containing exact upstream URLs, revisions,
   file hashes, runtime versions, licence requirements, and storage sizes. Add
   resumable downloads and verification before execution; do not change workloads.
5. Connect the guided screens to the shared launcher and existing local controller,
   preserving authentication, cancellation, evidence, and submission protections.
6. Package the desktop application and CLI, then test unaided installation through
   first result and optional PR submission. Keep GitHub login optional for local runs.

The [llama.cpp server reference](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
documents the health endpoint, alias, and CPU offload flags used by the experiment.
Runtime compatibility still has to be checked against the exact build distributed.
