# Guided CPU setup (development milestone)

The guided path (`omobench prepare`, `omobench launch`, and `omobench ui`'s
**default page**) is a **development milestone**, not an installer. It adds a
reviewed, verified way to get one small CPU model running, benchmarked, and
ready to submit, without hand-installing a model server, and without typing
a server address, model revision, runtime argument, or shell command anywhere
in the guided page. It does not change the benchmark engine: profiles,
GuideLLM command construction, normalization, packaging, and the result
schema are exactly the same ones the [advanced panel](browser-control.md) and
[CLI workflow](quickstart.md#run-directly-from-the-cli) already use. Use this
page alongside [setup](quickstart.md), [the CPU demo](local-demo.md), and
[browser control](browser-control.md); it does not replace them, and the
existing "connect to a model server you already run yourself" workflow stays
fully available at `/advanced` in the same panel, and from the CLI.

## What it adds, and what it does not

- **Reviewed recipes**, not a general downloader. A recipe in
  [`catalog/downloads/cpu-v1.json`](../catalog/downloads/cpu-v1.json) pins an
  exact HTTPS URL, an immutable revision, a file size, a SHA-256 hash, and a
  licence for one model file and one runtime archive. This milestone ships
  two recipes, both Qwen3 4B (Q4_K_M GGUF) with the same matching llama.cpp
  CPU build and the same pinned artifacts: `qwen3-4b-cpu-v1` (the existing
  `chat-small-v1` profile) and `qwen3-4b-cpu-extended-v1` (the same workload
  on the longer-duration `chat-small-extended-v1` profile, see
  [the proposal](proposals/chat-small-extended-v1.md), for hardware that
  needs more than 15 minutes to collect enough measured samples). Selecting
  the extended recipe never re-downloads anything already installed for the
  classic one: both recipes pin identical model/runtime artifacts.
- It never runs an installer or touches system packages. It downloads two
  verified files and extracts one archive under a storage folder you choose,
  below `omobench-managed-v1/`.
- It never starts a model server for you outside of `omobench launch`/the
  guided page's own benchmark run, and it never uploads anything to GitHub
  without an explicit, reviewed **Create pull request** action; see
  [packaging a managed CPU run](#packaging-a-managed-cpu-run) below.
- More models arrive later as additional reviewed entries in that same JSON
  catalog, not as hard-coded logic per model; see
  [adding models later](#adding-models-later).
- It reuses your previous storage-folder choice for this checkout, and skips
  re-downloading files it has already verified. It never assumes a `D:` drive
  or either developer's own path exists; if nothing has been chosen yet and
  no established WSL convention folder is found, it suggests a per-user
  folder under your home directory, editable before you confirm.

## Returning to saved results

The guided page lists saved runs under **Choose your test**, including after
the panel restarts. Select a completed benchmark to reopen its measurements,
then **Prepare submission** to review its recorded metadata, validation and
submission status. Reopening never starts inference, downloads a model,
packages evidence or uploads it. Completed runs that fail acceptance remain
rejected; the existing packaging and validation rules still apply.

New managed runs save versioned UI history in
`.omobench-runs/ui/managed/mrun-<id>/job.json`, separate from benchmark evidence.
Records link to the underlying benchmark job by ID. A run recorded as active
when the panel stopped is shown as interrupted, without inventing a completion
time, claiming ownership of an old process, or restarting it. Missing benchmark
records are reported as unavailable. Invalid history records are ignored.

Older panel builds saved benchmark jobs but not managed-run records. Those
jobs now appear as **Saved local run**, using their original model, profile,
device and measurements. Log-only `mrun-*` directories cannot establish which
benchmark they belong to and are not matched by timestamps or guesses. Standalone
CLI run directories without a panel job record are not imported automatically.
The guided packaging form remains for recorded llama.cpp CPU runs; other saved
runs use the advanced metadata form for packaging. Already validated bundles
can be reviewed in either panel. A server readiness check is not a benchmark
and offers no measurements or sharing action.

History recovery reads existing records without rewriting them. New history
write failures after a run starts do not change benchmark execution or evidence;
the run response carries a history warning. Packaging and submission remain
explicit actions and require a fresh submission review after restart.

## Why WSL/Linux, not native Windows

Like the rest of OmoBench, benchmark execution needs Linux, macOS, or WSL2,
because the pinned GuideLLM runner extra depends on `uvloop`, which does not
support native Windows. The managed CPU recipe adds one more requirement of
its own: **Linux x86_64 with glibc 2.34 or newer** (glibc 2.34 itself is
accepted), because the pinned llama.cpp release build links against it. On
native Windows, `omobench prepare` and `omobench launch` print a clear message
telling you to start WSL2 (`wsl.exe -d Ubuntu`) instead of silently failing or
guessing your intent. See [Windows: enter WSL first](quickstart.md#windows-enter-wsl-first)
if you have not done this before.

## Storage folder: no assumed drive or path

The model file alone is about 2.3 GiB. `--storage-dir`/the guided page's
storage field needs a folder with room to spare. Nothing here assumes a `D:`
drive exists, or either developer's own path: the guided page suggests, in
order, (1) whatever folder this checkout's panel last used, if it still
exists, (2) `/mnt/d/OmoBench`, a common WSL2 convention, but only when that
exact path already exists, and otherwise (3) a per-user folder under your own
home directory, offered only when it is actually writable. Every suggestion
stays editable before you confirm, and manual entry always works. OmoBench
does **not** convert one path style into the other for you: pass
`/mnt/d/OmoBench` when running under WSL2/Linux, and `D:\OmoBench` only for
the native-Windows review/setup checks described above (which cannot
download or launch anything anyway). Passing the wrong style for your current
environment is rejected with a specific error rather than silently writing to
the wrong filesystem. The storage folder must be an absolute path.

## What each step does

The CLI keeps these as explicit, separately runnable commands, useful for
scripting, CI, or inspecting one step in isolation:

| Step | Command | What happens |
| --- | --- | --- |
| Review | `omobench prepare --recipe qwen3-4b-cpu-v1 --storage-dir /mnt/d/OmoBench` | Read-only. Reports the exact files, sizes, licences, hashes, platform check, and free space. Makes no network requests and writes nothing. |
| Download | `omobench prepare --recipe qwen3-4b-cpu-v1 --storage-dir /mnt/d/OmoBench --download` | Downloads (resumably) and verifies both files by size and SHA-256, then atomically installs them under the storage folder. Corrupted downloads are quarantined, not used. |
| Smoke test | `omobench launch --recipe qwen3-4b-cpu-v1 --storage-dir /mnt/d/OmoBench --smoke` | Re-verifies the installed files, starts the verified server with fixed CPU-only settings, confirms it reports the expected model alias, then stops it. **No inference. No performance result.** |
| Execute | `omobench launch --recipe qwen3-4b-cpu-v1 --storage-dir /mnt/d/OmoBench --execute` | Re-verifies the installed files, starts the same server, then runs the existing, unchanged benchmark profile against it with the existing CLI/GuideLLM runner and normalizer, and stops the server afterward. |
| Package | `omobench package --execution-device cpu ...` (see [submission-bundle.md](submission-bundle.md)) | The run directory `omobench launch --execute` produces is a normal `.omobench-runs/` directory: package it from the CLI, the advanced panel, or the guided page's own "Share results" screen, the same as any CPU or GPU run. |

`omobench launch` binds the managed server to `127.0.0.1` only, uses the
canonical model ID as its API alias, and applies fixed CPU-only settings
(`--device none --n-gpu-layers 0 --ctx-size 2048 --parallel 1 --reasoning off`).
It stops only the server process it started, never WSL itself, and never a
model server you started separately, on success, on failure, on Ctrl+C, and
on `SIGTERM`, and it keeps a local diagnostic log of that server's output next
to the run directory.

The **guided page** (`omobench ui`'s default page; still reachable at
`/guided`) automates the first four of those steps behind one action instead
of five separate ones:

1. **Choose your test.** Recipe cards, populated from the reviewed download
   catalog and the profile each one runs, including a
   "Recommended for slower hardware" recipe with a longer duration ceiling, explained honestly as a maximum that a run may finish well before, never a
   shorter or altered workload.
2. **Prepare and run.** Shows the selected model, execution device, download
   size, storage destination, licences, and duration ceiling up front, with
   exact hashes and revisions available under "Technical details". One
   **Start** (or **Download and start**) action authorizes preparation and
   execution together: review, platform/storage/space checks, download (only
   when needed, already-verified files are reused, never re-downloaded),
   re-verification, server startup, and the readiness check GuideLLM needs
   all happen automatically, with no separate smoke-test button. A live
   dashboard then shows a progress bar and percentage, the current stage
   (checking system, downloading, verifying, loading the model, warmup,
   benchmarking, cooldown, or saving results, whichever GuideLLM's own
   execution actually supports at that moment), elapsed time, an estimated
   time remaining, completed requests against the run's total budget, an
   animated output-tokens/second chart, and a live log, see
   [Live progress, ETA, and the chart](#live-progress-eta-and-the-chart)
   below for exactly what each of these means and where the numbers come
   from. Refreshing the page, or reopening the panel while a run is still
   active, reconnects to it and restores its progress and chart instead of
   starting a duplicate; if the panel itself restarted mid-run, that is
   reported honestly as an interruption, never as a resumed benchmark.
3. **Your results.** Real throughput, time-to-first-token, latency,
   successful/attempted requests, and error rate, each with a short
   plain-language explanation, the same measurements described in the
   [metrics table](../README.md#fixed-workloads-and-measurements).
4. **Share results.** Packaging and submission review live in the guided page
   itself now, see [packaging a managed CPU run](#packaging-a-managed-cpu-run)
   below.

It requires the same panel token as the advanced page, stays loopback-only,
and makes no external browser requests of its own.

## Live progress, ETA, and the chart

The "Prepare and run" dashboard's numbers come from a small, additive
telemetry adapter (`cli/src/omobench/telemetry.py`), not from a second
benchmark implementation. When it can attach (the pinned GuideLLM 0.7.3
package resolved from the exact same environment as the panel itself, not a
stand-in), it runs GuideLLM's own official `benchmark_generative_text()`
entrypoint in-process, with the *identical* resolved configuration
`build_guidellm_command()` already describes, and reads a public,
documented GuideLLM interface (`BenchmarkerProgress`) to observe already-
computed running statistics. It never drives the scheduler, never changes a
constraint, seed, or workload, and, because it calls GuideLLM's own output
formatter with the same `--output` argument, writes the exact same
`guidellm-benchmarks.json.gz` evidence the plain command line always has. If
attaching telemetry fails for any reason, `omobench run --execute` falls
back to the exact plain command it has always run; a telemetry problem can
disable the live dashboard, but never the benchmark itself, and the CLI's
own direct use is completely unaffected either way.

- **Stage.** Read from GuideLLM's own accumulator state
  (`accumulator.timings.status`: pending/warmup/active/cooldown), not
  guessed from elapsed time. Preparation/download/verification/model-startup
  come from the existing managed-setup and launcher state machine described
  above, unchanged.
- **Progress bar and percentage.** GuideLLM's own
  `SchedulerState.progress.remaining_fraction`, the fraction of whichever
  stopping condition (request count or duration) is closer to firing,
  combined the same way GuideLLM itself combines them (the smaller of the
  two). When this is not yet known (for example, still starting up, or
  telemetry is unavailable for this run), the bar shows an indeterminate
  animation rather than a fabricated number. Once the time budget is spent
  while a request is still in flight, that fraction legitimately reads zero
  remaining, so instead of a finished-looking 100% bar over a still-running
  benchmark, the bar holds just short of complete and says the runner is
  finishing its current request and stopping.

  100% is reserved for a run that genuinely finished: the benchmark executed
  *and* its evidence files (compressed runner output, `environment.json`,
  `reproduce.md`) were written successfully. While those files are still
  being written the bar says so and stays below 100%, so a failure during
  that step is never left displaying a completed run. An in-flight
  percentage is rounded down for the same reason: 99.6% reads as 99%, not as
  a finished 100%.
- **Completed requests and total budget.** Two different numbers, kept
  visibly separate: the runner's total request budget (`total_budget`,
  including warmup and cooldown, 60 for `chat-small-extended-v1`) and the
  profile's own declared measured-request target (`measured_requests_target`, 50 for both `chat-small-v1` and `chat-small-extended-v1`). Neither
  changes how many requests actually run; see
  [what each step does](#what-each-step-does) above for why the two numbers
  differ for the extended profile specifically.
- **Estimated time remaining.** Computed from the *observed* completion
  rate (requests completed so far ÷ elapsed time), projected across the
  remaining requests, capped by GuideLLM's own remaining-duration estimate
  for the time limit. Shows "Estimating…" until there have been enough real
  observations (never extrapolated from a single sample), and never shows
  the duration ceiling itself as if it were the expected running time. Once
  GuideLLM's own execution finishes and OmoBench is writing the final
  evidence files, this becomes "Saving results…" rather than a countdown
  that would otherwise read as stuck at zero.

  A run can also pass its time budget *before* finishing: `max_duration`
  stops the scheduler from starting new work, but cannot interrupt a request
  already streaming. Rather than a countdown frozen at "0s remaining", the
  dashboard then states that the time limit has been reached and the runner
  is finishing its current request. The displayed ceiling covers benchmark
  execution only, model startup and cleanup are separate stages and were
  never inside it.
- **The chart.** Output tokens per second, plotted against elapsed
  benchmark time, from GuideLLM's own running statistic
  (`completed_metrics.output_tokens.rate_per_second`), a **cumulative
  average since the first request**, labelled as such; it is not an
  instantaneous or per-request measurement. It shows "Waiting for the first
  measurement…" until a real sample exists (never a placeholder waveform),
  animates only when a genuinely new sample arrives, and keeps a separate,
  bounded history per run so reopening the current run restores its own
  chart. A long CPU prefill before the first token can mean this stays
  empty for a while; that alone does not mean the benchmark is stuck, the
  live log's own status line distinguishes "no recent runner update" (which
  can be entirely normal on slow hardware) from "runner process exited" and
  from "panel connection lost."
- **100% and completion.** The bar only reaches 100% once GuideLLM's own
  execution *and* OmoBench's local evidence finalization have both
  finished. Cancelling a run, or a run that fails, keeps whatever progress,
  stage, and chart were last genuinely observed, never reset to zero and
  never advanced to 100%. A run that finishes normally can still fail its
  profile's acceptance thresholds; "the benchmark completed" and "this
  result is publishable" stay two separate, separately reported outcomes
  (see [Packaging a managed CPU run](#packaging-a-managed-cpu-run) below).
  Final displayed measurements always come from the existing,
  unchanged `normalize_guidellm_report()`, the live dashboard's own numbers
  are for following the run, not the report of record.

## Packaging a managed CPU run

This recipe's `execution_device` is `cpu`. Exactly like a CPU run through the
advanced panel or `omobench run --execute`, its results are real evidence: a
normal `.omobench-runs/` staging directory that can be packaged, reviewed, and
submitted, the same way a GPU run can. The guided page's **Share results**
screen fills in the model identity, weight source, revision, quantization,
runtime version, startup arguments, profile, and execution device from the
verified recipe automatically, the same package/validate/prepare/submit
Controller endpoints the [advanced panel](browser-control.md) uses, not a
second implementation, so the only thing it still asks for is your
submitter identity. It distinguishes **execution completed**, **profile
acceptance passed/failed**, **bundle validation passed/failed**, and
**submission created** as separate outcomes: a finished run is not
automatically a publishable one, and an unsuccessful or below-threshold run
stays viewable and downloadable locally with its actual rejection reason
shown, never silently discarded. The community submission route uses GitHub
sign-in in the browser, then a reviewed public fork and upstream PR. It works
without a Git checkout or GitHub CLI when the build has the project's OAuth
client ID configured. The legacy `gh`/origin route and complete-bundle download
remain available. See [community submissions](community-submissions.md).
Publication always requires an explicit **Create pull request** click after
reviewing the destination repository and evidence, nothing is uploaded
automatically.

## Adding models later

Additional reviewed models are meant to be added as more entries in
[`catalog/downloads/cpu-v1.json`](../catalog/downloads/cpu-v1.json) (or a
later-versioned sibling file), each with its own pinned URL, revision, size,
hash, and licence, reviewed the same way this first recipe was, rather than
as new per-model branches in the loader or launcher code. The loader already
supports multiple recipes; this milestone intentionally ships only one.
