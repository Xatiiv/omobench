# Guided CPU setup (development milestone)

The guided path (`omobench prepare`, `omobench launch`, and `omobench ui`'s
**default page**) is a **development milestone**, not an installer. It adds a
reviewed, verified way to get one small CPU model running, benchmarked, and
ready to submit — without hand-installing a model server, and without typing
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
  on the longer-duration `chat-small-extended-v1` profile — see
  [the proposal](proposals/chat-small-extended-v1.md) — for hardware that
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
exists, (2) `/mnt/d/OmoBench` — a common WSL2 convention — but only when that
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

The CLI keeps these as explicit, separately runnable commands — useful for
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
It stops only the server process it started — never WSL itself, and never a
model server you started separately — on success, on failure, on Ctrl+C, and
on `SIGTERM`, and it keeps a local diagnostic log of that server's output next
to the run directory.

The **guided page** (`omobench ui`'s default page; still reachable at
`/guided`) automates the first four of those steps behind one action instead
of five separate ones:

1. **Choose your test.** Recipe cards, populated from the reviewed download
   catalog and the profile each one runs, including a
   "Recommended for slower hardware" recipe with a longer duration ceiling —
   explained honestly as a maximum that a run may finish well before, never a
   shorter or altered workload.
2. **Prepare and run.** Shows the selected model, execution device, download
   size, storage destination, licences, and duration ceiling up front, with
   exact hashes and revisions available under "Technical details". One
   **Start** (or **Download and start**) action authorizes preparation and
   execution together: review, platform/storage/space checks, download (only
   when needed — already-verified files are reused, never re-downloaded),
   re-verification, server startup, and the readiness check GuideLLM needs
   all happen automatically, with no separate smoke-test button. Progress is
   shown as real stages (checking system, downloading, verifying, loading the
   model, benchmarking, saving results) with genuine download byte counts,
   elapsed time, and the live runner log under "Technical details" — never a
   manufactured request-count or completion estimate GuideLLM itself does not
   report. Refreshing the page, or reopening the panel while a run is still
   active, reconnects to it instead of starting a duplicate; if the panel
   itself restarted mid-run, that is reported honestly as an interruption,
   never as a resumed benchmark.
3. **Your results.** Real throughput, time-to-first-token, latency,
   successful/attempted requests, and error rate, each with a short
   plain-language explanation — the same measurements described in the
   [metrics table](../README.md#fixed-workloads-and-measurements).
4. **Share results.** Packaging and submission review live in the guided page
   itself now — see [packaging a managed CPU run](#packaging-a-managed-cpu-run)
   below.

It requires the same panel token as the advanced page, stays loopback-only,
and makes no external browser requests of its own.

## Packaging a managed CPU run

This recipe's `execution_device` is `cpu`. Exactly like a CPU run through the
advanced panel or `omobench run --execute`, its results are real evidence: a
normal `.omobench-runs/` staging directory that can be packaged, reviewed, and
submitted, the same way a GPU run can. The guided page's **Share results**
screen fills in the model identity, weight source, revision, quantization,
runtime version, startup arguments, profile, and execution device from the
verified recipe automatically — the same package/validate/prepare/submit
Controller endpoints the [advanced panel](browser-control.md) uses, not a
second implementation — so the only thing it still asks for is your
submitter identity. It distinguishes **execution completed**, **profile
acceptance passed/failed**, **bundle validation passed/failed**, and
**submission created** as separate outcomes: a finished run is not
automatically a publishable one, and an unsuccessful or below-threshold run
stays viewable and downloadable locally with its actual rejection reason
shown, never silently discarded. Direct pull-request creation still needs
GitHub CLI authentication and origin write access (see
[Packaging and pull requests](browser-control.md#packaging-and-pull-requests));
without it, downloading the validated bundle for a manual PR still works.
Publication always requires an explicit **Create pull request** click after
reviewing the destination repository and evidence — nothing is uploaded
automatically.

## Adding models later

Additional reviewed models are meant to be added as more entries in
[`catalog/downloads/cpu-v1.json`](../catalog/downloads/cpu-v1.json) (or a
later-versioned sibling file), each with its own pinned URL, revision, size,
hash, and licence — reviewed the same way this first recipe was — rather than
as new per-model branches in the loader or launcher code. The loader already
supports multiple recipes; this milestone intentionally ships only one.
