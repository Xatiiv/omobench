# Guided CPU setup (development milestone)

The guided path (`omobench prepare`, `omobench launch`, and the panel's
`/guided` page) is a **development milestone**, not an installer. It adds a
reviewed, verified way to get one small CPU model running so you can try the
benchmark without hand-installing a model server first. It does not change
the benchmark engine: profiles, GuideLLM command construction, normalization,
packaging, and the result schema are exactly the same ones the
[advanced panel](browser-control.md) and
[CLI workflow](quickstart.md#run-directly-from-the-cli) already use. Use this
page alongside [setup](quickstart.md), [the CPU demo](local-demo.md), and
[browser control](browser-control.md); it does not replace them, and the
existing "connect to a model server you already run yourself" workflow stays
available in the advanced panel and from the CLI.

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
  guided page's own smoke test and benchmark run, and it never packages or
  submits a result itself; see
  [packaging a managed CPU run](#packaging-a-managed-cpu-run) below.
- More models arrive later as additional reviewed entries in that same JSON
  catalog, not as hard-coded logic per model; see
  [adding models later](#adding-models-later).

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

## Why /mnt/d/OmoBench

The model file alone is about 2.3 GiB. Point `--storage-dir` at a drive with
room to spare — for example a Windows `D:\OmoBench` folder, which WSL2 sees as
`/mnt/d/OmoBench`. OmoBench does **not** convert one path style into the
other for you: pass `/mnt/d/OmoBench` when running under WSL2/Linux, and
`D:\OmoBench` only for the native-Windows review/setup checks described
above (which cannot download or launch anything anyway). Passing the wrong
style for your current environment is rejected with a specific error rather
than silently writing to the wrong filesystem. The storage folder must be an
absolute path.

## Review, download, smoke test, execute, and package

These are five different steps; the guided page and `omobench prepare`/`omobench launch`
keep them explicit rather than collapsing them into one "just run it" button:

| Step | Command | What happens |
| --- | --- | --- |
| Review | `omobench prepare --recipe qwen3-4b-cpu-v1 --storage-dir /mnt/d/OmoBench` | Read-only. Reports the exact files, sizes, licences, hashes, platform check, and free space. Makes no network requests and writes nothing. |
| Download | `omobench prepare --recipe qwen3-4b-cpu-v1 --storage-dir /mnt/d/OmoBench --download` | Downloads (resumably) and verifies both files by size and SHA-256, then atomically installs them under the storage folder. Corrupted downloads are quarantined, not used. |
| Smoke test | `omobench launch --recipe qwen3-4b-cpu-v1 --storage-dir /mnt/d/OmoBench --smoke` | Re-verifies the installed files, starts the verified server with fixed CPU-only settings, confirms it reports the expected model alias, then stops it. **No inference. No performance result.** |
| Execute | `omobench launch --recipe qwen3-4b-cpu-v1 --storage-dir /mnt/d/OmoBench --execute` | Re-verifies the installed files, starts the same server, then runs the existing, unchanged `chat-small-v1` benchmark against it with the existing CLI/GuideLLM runner and normalizer, and stops the server afterward. |
| Package | `omobench package --execution-device cpu ...` (see [submission-bundle.md](submission-bundle.md)) | Not offered as a guided-page step, but the run it produces is a normal `.omobench-runs/` directory: package it from the CLI or the [advanced panel](browser-control.md), the same as any CPU or GPU run. |

`omobench launch` binds the managed server to `127.0.0.1` only, uses the
canonical model ID as its API alias, and applies fixed CPU-only settings
(`--device none --n-gpu-layers 0 --ctx-size 2048 --parallel 1 --reasoning off`).
It stops only the server process it started — never WSL itself, and never a
model server you started separately — on success, on failure, on Ctrl+C, and
on `SIGTERM`, and it keeps a local diagnostic log of that server's output next
to the run directory.

The guided page (`/guided` in the local panel) walks through the same five
steps as stages: select the test, select the reviewed model, choose a storage
folder, review the downloads, confirm preparation, watch download progress
(with cancellation), verify readiness, run the benchmark, and see the results
and logs. It requires the same panel token as the advanced page, stays
loopback-only, and makes no external browser requests of its own.

## Packaging a managed CPU run

This recipe's `execution_device` is `cpu`. Exactly like a CPU run through the
advanced panel or `omobench run --execute`, its results are real evidence: a
normal `.omobench-runs/` staging directory that can be packaged, reviewed, and
submitted, the same way a GPU run can. The guided page itself never shows a
"package" or "submit" step for a managed CPU run — use the
[advanced panel](browser-control.md) or `omobench package --execution-device cpu`
against the run directory the guided page reports.

## Adding models later

Additional reviewed models are meant to be added as more entries in
[`catalog/downloads/cpu-v1.json`](../catalog/downloads/cpu-v1.json) (or a
later-versioned sibling file), each with its own pinned URL, revision, size,
hash, and licence — reviewed the same way this first recipe was — rather than
as new per-model branches in the loader or launcher code. The loader already
supports multiple recipes; this milestone intentionally ships only one.
