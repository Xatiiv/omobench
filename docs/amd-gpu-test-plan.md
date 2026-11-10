# Testing OmoBench on a Windows PC with an AMD GPU

Written for the person sitting at that machine. Every step says what to do,
what a pass looks like, and what to write down if it does not pass.

Nothing in this document has been run on an AMD GPU. The panel, the installer
and the bridge have been exercised on this project's development laptop (Intel
CPU, 4 GB of accelerator memory, WSL2 Ubuntu 24.04) and against fixtures. **The
AMD path is untested**, which is the reason for this plan. Treat a failure here
as new information, not as a regression.

## What you need

- A Windows 10 21H2 / Windows 11 machine with the AMD GPU in it.
- WSL2 installed, with a distribution that has `python3` and `python3-venv`.
  Installing WSL needs administrator rights and a restart; do that first,
  separately. See [If WSL is missing](windows-installer.md#if-wsl-is-missing).
- A serving runtime that can use the AMD card. LM Studio with its Vulkan or
  ROCm runtime, or a llama.cpp build with Vulkan, are the realistic choices on
  Windows today. OmoBench does **not** install one and does not pick one for
  you.
- About 15 GB free on the drive you point the model download at, if you use a
  managed recipe.

Write down, before you start: the exact GPU model, the driver version, the
Windows build, the WSL distribution and its kernel, and how much memory the
card reports. Those are the facts a result is worth nothing without.

## 1. Build and install

The installer carries the OmoBench source inside it, so it must be built from a
**committed** revision. A build from a dirty working tree is refused for the
payload and marked as not matching the revision for the executables.

```powershell
# Windows PowerShell, from the checkout root
powershell -ExecutionPolicy Bypass -File windows\installer\build.ps1 -Test
```

Pass: `windows\installer\build\` contains `OmoBenchSetup.exe`, `version.json`
and `SHA256SUMS`, and the `-Test` run reports the payload provenance tests and
the installer harness as passing.

Then run `OmoBenchSetup.exe`.

- SmartScreen will say the publisher is unknown. That is accurate: these builds
  are unsigned. Choose *More info → Run anyway*.
- Check that the first screen's **source revision** matches the commit you
  built from, and that its **signing status** says unsigned.
- Press **Install**. There should be no administrator prompt.

Pass: the installer finishes by saying OmoBench is ready, and offers to open
it. If it says it is ready before the environment check has passed, that is a
bug — record the exact screen.

## 2. The two source paths, and the upgrade

This is the part most worth testing, because it is where a second machine
differs from the one the code was written on.

**2a. Nothing to reuse.** On a machine with no OmoBench checkout and no Linux
environment containing it, the installer should offer **Prepare it for me**,
unpack its carried source into
`%LOCALAPPDATA%\OmoBench\source\<revision>`, create
`~/.local/share/omobench/managed-env` inside Linux, install OmoBench and the
pinned runner into it, and check the result before reporting success.

Pass: each stage is shown as it happens; the final check passes; nothing
outside those two locations was written.

Also test **Stop** partway through preparation. Pass: it stops, says so, and
leaves the rest untouched.

**2b. Something to reuse.** If you do have a checkout and an environment with
OmoBench installed, the installer should find them, check them, and use them,
downloading nothing and changing nothing of yours.

Pass: it reports what it found, by path, and the paths are the ones you expect.
Fail, and record it, if it rewrites, upgrades or repoints anything you
maintain.

**2c. Upgrade.** Install a build, then build and install a newer one over it.

Pass: the second install reports the new version and new source revision; the
Apps & features entry shows the new version; `omobench://` links still open the
installed program; the previous managed source directory is either reused or
replaced under `%LOCALAPPDATA%\OmoBench\source\<revision>` and nothing you
maintain is touched. Record which of "reused" or "replaced" actually happens —
that is a fact about the build, not a prediction.

## 3. The panel, before any model

Open the panel (Start menu entry, or `omobench ui` inside WSL). It prints a URL
with `#token=...`; the whole URL including the fragment has to go into the
browser.

Check, in order:

1. The page opens on **Your hardware**, not on a model list.
2. The hardware facts name the serving environment's free memory, that
   environment's ceiling, and the accelerator memory. On this machine the
   accelerator line should name your AMD card. **If it does not, stop and
   record what it says** — detection is the thing most likely to differ here.
3. **Choose a workload for this hardware** offers one selector with grouped
   curated workloads and a **Custom model** option. There must be exactly one
   selector on the page.
4. Picking a curated workload shows a fit verdict and, where one exists, a
   managed setup block *inside* the same panel. There is no second page, no
   second stepper and no second model list.
5. Picking **Custom model** hides the managed block and shows the custom
   fields, because the panel has no reviewed recipe for weights it has never
   seen.
6. Open the browser's console. It should be empty.

### What the fit verdicts mean

Read these literally; they are deliberately not a recommendation engine.

| Verdict | What it is saying |
| --- | --- |
| **Fits** | A benchmark of this workload completed on hardware like this. No result in this project currently qualifies, so you should not see it yet. |
| **Completion unverified** | Nobody has measured what completing this workload costs. The load may well work; this says only that the completion requirement is unknown. |
| **Marginal** | There is evidence of limited headroom — a measurement, not an estimate. |
| **Too large** | The requirement exceeds the memory here. |
| **Unknown** | Not enough is recorded to say anything. |

An estimate is advisory and is labelled as one. An estimate never says "will
load".

## 4. A bounded run through your own AMD server

Do this before any managed download. It is the cheaper test and it is the one
that proves the AMD path.

1. Start your serving runtime on Windows with the model on the GPU, and note
   its address and the model identifier it answers to.
2. In the panel's **Connect & run** section, enter that address and model
   identifier. If the panel is running inside WSL and the server is on Windows,
   tick the Windows bridge.
3. Press **Test connection**. Pass: it confirms the address, this environment,
   the server, its model list and one streaming request, and says plainly that
   the test request is not benchmark evidence.
4. Run the shortest profile available.

Pass: the run completes, the dashboard shows real progress, and the review
section reports the measurements with their provenance.

Record, whatever happens:

- whether the serving environment's accelerator was detected, and with what
  vendor and memory;
- whether the placement was reported as GPU-only or split, and whether that
  matches what your runtime actually did;
- if you used the bridge, that its latency is inside every number and has not
  been subtracted — the panel should say so itself.

**Finding a card is not evidence that inference used it.** If the panel claims
GPU execution while your runtime was on CPU, that is a bug worth reporting with
the exact screens.

## 5. A managed download, if you want one

Only after step 4. A curated recipe is roughly 2.5 GB.

Test, in this order:

1. Start a download and **cancel** it partway. Pass: it stops, says so, and
   does not start a benchmark.
2. Start it again. Pass: it resumes rather than restarting from zero.
3. Let it finish. Pass: verification runs, then the server starts, then the
   benchmark starts — in that order, with no separate smoke-test step.
4. Close the panel mid-download and reopen it. Pass: it reconnects to the same
   job and restores its progress, rather than starting a duplicate.

If a checksum is rejected, the download should be quarantined and reported as a
failure. It must never become a benchmark run.

## 6. What to send back

For each step above, one line: what you did, what happened, and the exact
wording of anything that surprised you. Screens are better than paraphrase.

Alongside that, the facts from the top of this document, plus:

- `windows\installer\build\version.json` from the build you installed;
- the panel's hardware facts as rendered;
- for any completed run, the run directory's `result.json`, if one was
  packaged.

Do not submit anything to the public registry from this round of testing. These
are shakedown runs on an untested path, and a measurement nobody can yet
interpret is not evidence.

## Known limits of this plan

- No AMD GPU inference has been run by this project. Everything above is a
  procedure, not a report.
- AMD accelerator detection, placement reporting and the Vulkan/ROCm runtime
  identification have never been exercised against real hardware.
- The managed recipes are CPU recipes. They are useful here for testing
  download, verification, cancellation and recovery, not for measuring the GPU.
- Suspension detection cannot see a Hyper-V pause from inside WSL2; a laptop
  lid or a sleeping host is reported as an unconfirmed clock jump, not as a
  confirmed suspension. See [bounded-execution.md](bounded-execution.md).
