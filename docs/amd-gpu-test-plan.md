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

## 0. Which code is actually running

Worth doing first and again at the end, because the most likely way this test
goes wrong is quietly measuring the old code.

The installer carries the OmoBench source inside it and unpacks it to
`%LOCALAPPDATA%\OmoBench\source\<revision>` — **but only when it has nothing
to reuse.** If this machine already has an OmoBench checkout and a Linux
environment with OmoBench installed, the installer finds them and uses them,
which is the behaviour step 2b asks you to confirm. In that case the panel
runs *your checkout*, and installing a newer installer over the top changes
nothing about which code serves the page.

So check all three:

```powershell
# What the installer says it is
Get-Content "$env:LOCALAPPDATA\Programs\OmoBench\version.json"

# What it unpacked, if it unpacked anything
Get-ChildItem "$env:LOCALAPPDATA\OmoBench\source"
```

```bash
# And, if the panel runs from a checkout of your own, what that checkout is
git -C /path/to/your/omobench rev-parse HEAD
git -C /path/to/your/omobench status --porcelain
```

Pass: the revision in `version.json`, the directory name under `source\`, and
the checkout's `HEAD` (where one is in play) are the revision named at the top
of this round of testing. If the checkout is behind, `git pull` it before going
further — and if it has local changes, say so rather than discarding them.

The page itself is the fourth check, and the quickest. This revision opens on
**Your hardware** and **Choose a workload for this hardware**, with one
selector. The previous one opened on a list of model cards headed *Choose your
test* and kept a separate `/advanced` page. If you see model cards first, the
panel is running the old code, whatever the version files say.

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

Two notes on that command. Run it in a PowerShell window rather than piping its
output through a redirect: Windows PowerShell turns a native program's stderr
into a terminating error, and git prints ordinary line-ending warnings there,
so a redirect makes a passing run look like a failure. And the build refuses to
produce a payload from a dirty working tree -- the payload is the committed
revision, byte for byte -- so commit first.

To watch the real installer window without installing anything, build the
sandbox fixture as well:

```powershell
powershell -ExecutionPolicy Bypass -File windows\installer\build.ps1 -WizardFixture
```

`build\OmoBenchWizardFixture.exe` runs the actual wizard against its own install
location, registry entries and fake distribution. Nothing runs in WSL and
nothing of yours is touched, so Stop, retry and closing mid-work can be tried
on a real desktop. It is never shipped.

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

1. In **LM Studio**, load the model onto the AMD GPU and start its server.
   Note the address it listens on (its default is `http://127.0.0.1:1234`, a
   different port from the panel's own `8765`) and the model identifier it
   answers to -- that identifier is usually an alias, not the model's canonical
   name, and the panel records the two separately on purpose.
   In LM Studio's own hardware panel, note the GPU it reports, its memory, and
   how many layers it placed on the card. Those are the numbers to compare the
   panel's claims against; the panel has no way to see inside LM Studio.
2. In the panel's **Connect & run** section, enter that address and the served
   identifier. The panel runs inside WSL and LM Studio runs on Windows, so tick
   **Windows bridge**: without it the panel is on the wrong side of the
   network boundary and the address may not resolve at all.
3. Press **Test connection**. Pass: it confirms the address, this environment,
   the server, its model list and one streaming request, and says plainly that
   the test request is not benchmark evidence.
4. Press **Read the serving hardware**. The companion asks Windows what is in
   the machine, because WSL cannot see it. Pass: the vendor reads `amd`, the
   model and memory match LM Studio's own hardware panel, and nothing is filled
   in that the companion could not actually read.
   Then set the placement yourself: **GPU only** if LM Studio put every layer
   on the card, **split CPU/GPU** otherwise -- and in the split case say how it
   was split, for example `32 of 49 layers on GPU`. The panel will not guess
   this and should refuse a split with no detail.
5. Fill in the canonical model ID, an immutable weights revision, the
   quantisation, and the serving runtime: LM Studio's release is **not** the
   llama.cpp build it embeds, and the two are recorded separately.
6. Run the shortest profile available.

Pass: the run completes, the dashboard shows real progress, and **Review &
submit** reports each measurement with where it came from -- detected, read
from the server, or declared by you.

Record, whatever happens:

- the accelerator the panel detected, with its vendor and memory, beside what
  LM Studio reported;
- the placement the result records, and whether it matches what LM Studio
  actually did;
- that the bridge's latency is inside every number and has not been subtracted
  -- the panel should say so itself, in the evidence summary and in the
  comparison caveats;
- whether `execution_device` reads `gpu`, and on what basis.

**Finding a card is not evidence that inference used it.** If the panel claims
GPU execution while LM Studio was serving on CPU, that is a bug worth reporting
with the exact screens.

### Then look at what submission would need

Open **Review & submit** without submitting anything. The checklist is the
point of this step: it should name every outstanding requirement at once
rather than one at a time. Expect the model's catalog status to be settled one
of three ways -- catalogued, a declared eligibility, or a proposal -- and
expect *acceptance* to stay outstanding until a run has actually met the
profile's thresholds for successful requests and error rate.

If you use a custom model, the notice beside those fields should say that a
complete eligibility declaration is this model's eligibility and **not**
permission to submit the result, that a reviewer reads the licence itself, and
what is still outstanding. If it ever reads as though filling in four fields
cleared the submission, that is the defect this round of testing was meant to
catch.


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
- The downloader's resume, quarantine and cancellation are tested over
  loopback against a 4 MiB fixture (`cli/tests/test_managed_downloads.py`).
  Step 5 is the first time that code meets a real 2.5 GB fetch over a real
  network, which is why step 5 asks you to interrupt it on purpose.
- Suspension detection cannot see a Hyper-V pause from inside WSL2; a laptop
  lid or a sleeping host is reported as an unconfirmed clock jump, not as a
  confirmed suspension. See [bounded-execution.md](bounded-execution.md).
