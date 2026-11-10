# Testing OmoBench on a Windows PC with an AMD GPU

Written for the person sitting at that machine. Every step says what to do,
what a pass looks like, and what to write down if it does not pass.

An AMD GPU benchmark has been run on this machine before, with an earlier
build. What is untested is **this** build on it: the one panel, the workload
selector, the requirement-based submission checks and the installer upgrade
are all new since that run, and none of them has met an AMD GPU. Everything
this project has verified of them was verified on its development laptop
(Intel CPU, 4 GB of accelerator memory, WSL2 Ubuntu 24.04) and against
fixtures.

So treat a failure here as new information about the new code, not as a
regression in AMD support that was already working.

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

The panel says so itself. Open it and look at **This machine** in the sidebar:
the **Panel source** row gives the first twelve characters of the revision and
where that came from.

| It says | What that means |
| --- | --- |
| `79354f202b73 · installed` | A source the OmoBench installer unpacked, at that revision |
| `79354f202b73 · checkout` | A checkout of your own, on that commit |
| `79354f202b73 · checkout, edited since` | The same, with uncommitted changes, so what is running is not that commit exactly |
| `Not recorded` | Neither — nothing here says which revision it is |

Hover it for the full path. Check this before you start and again at the end;
the most likely way this test goes wrong is quietly measuring the old code.

The second check is the page itself. This revision opens on **Your hardware**
and **Choose a workload for this hardware**, with one selector. The previous
one opened on a list of model cards headed *Choose your test* and kept a
separate `/advanced` page. Model cards first means you are on the old code.

## 1. Get the installer, and install it

### From CI, which is the one to prefer

Every pull request that touches `windows/**` builds the installer on a clean
`windows-latest` runner, from the branch's own head commit rather than a
synthetic merge commit, and refuses to upload anything whose stamped revision,
recorded checksum and actual bytes do not all agree. That artifact is the one
to test with.

On the pull request, open **Checks -> Windows installer -> Build and test**.
The run's summary page lists an artifact named
`OmoBenchSetup-<version>-<revision>`; the job's log carries a notice line
giving the same version, revision and SHA-256. Download the artifact, unzip it,
and check the executable against that line:

```powershell
Get-FileHash .\OmoBenchSetup.exe -Algorithm SHA256
Get-Content .\version.json
```

Pass: the hash matches the notice and `installer_sha256` in `version.json`,
`source_clean` is `true`, and `revision` is the commit you meant to test.

Artifacts expire after 14 days. **A build is not byte-reproducible** — the same
revision built twice gives two different checksums, which `version.json`
records as `"reproducible": false` — so a locally built installer will not
match the CI one. Check each against its own `version.json`, never against the
other's.

### Or build it yourself

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

### Installing

Run `OmoBenchSetup.exe`, from whichever of the two you chose.

- SmartScreen will say the publisher is unknown. That is accurate: these builds
  are unsigned. Choose *More info → Run anyway*.
- Check that the first screen's **source revision** matches the commit you
  meant to test -- the one from `version.json`, not whatever is checked out on
  this machine -- and that its **signing status** says unsigned.
- Press **Install**. There should be no administrator prompt.

Pass: the installer finishes by saying OmoBench is ready, and offers to open
it. If it says it is ready before the environment check has passed, that is a
bug — record the exact screen.

## 2. Upgrading, and what the installer will not touch

Run `OmoBenchSetup.exe`. What it offers depends on what is already here, and
the three cases are genuinely different.

### A source the installer created

You will see **An OmoBench installation is already here**, naming the revision
it is running and the one this installer carries, with **Upgrade to
&lt;revision&gt;** and **Keep current**.

Press **Upgrade**. It unpacks the new source beside the current one and builds
it a Python environment of its own, then checks that environment. Only if the
check passes does anything switch over.

Pass:

- the stages run and the last one, *Check the finished environment*, passes;
- the finish screen says **OmoBench is ready at revision &lt;revision&gt;**;
- the panel's **Panel source** row now shows that revision, marked `installed`;
- the previous source directory under `%LOCALAPPDATA%\OmoBench\source\` is
  still there, and so is the Python environment it used.

Nothing asks you to clone anything, create a virtual environment or edit
`companion.config`. If it does, that is a defect — say so.

**Stopping it.** Press **Stop** partway through. Pass: it says nothing was
switched over, and the panel still runs the revision it ran before. Press
**Upgrade** again to continue; what pip already downloaded is reused.

**When it fails.** If the new environment cannot be built — pull your network
cable out during the install step, if you want to force it — pass is: the
screen says the new revision is not ready and nothing was switched, and the
panel still works exactly as it did.

**Going back.** The settings in use before the upgrade are copied to
`%APPDATA%\OmoBench\companion.config.previous` before anything changes. To
return to the previous revision, re-select that checkout and interpreter in the
companion's **Setup** window — both are still on disk. Nothing is deleted by
upgrading or by rolling back.

**Your previous runs.** The panel lists runs beside the source it is running
from, so the new source starts with none. After a successful upgrade the finish
screen offers **Copy N previous run(s)**. It copies; the originals stay where
they are. A run whose name already exists at the destination is skipped and
named, never overwritten. If you would rather not copy them, they remain
readable in the old source directory.

**Your models and storage folder are not involved.** They live wherever you
pointed the download at, outside any source directory, and are untouched. The
panel's *remembered* storage path is per-source, so type the same folder once
and the already-verified files are reused rather than downloaded again.

### A checkout of your own

You will see **OmoBench is ready**, with your checkout named, and a plain
statement that the installer has not changed it and will not — so installing
this build has **not** moved the panel to the new revision.

Pass: it says that, your checkout is untouched (`git status` unchanged), and
the **Panel source** row still shows your commit.

To test the new revision on such a machine, either update your checkout
yourself, or point the companion's Setup window at a source the installer
manages. Both remain available; neither is removed.

### Nothing to reuse

The installer offers **Prepare it for me**, which is the first-install path:
it unpacks its carried source, creates one Python environment, installs
OmoBench and the pinned runner, and checks the result before reporting ready.
Pressing **Stop** leaves the rest untouched.

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

- AMD GPU inference has been run on this machine with an earlier build. None
  of the code this round of testing is about has been. Everything above is a
  procedure, not a report.
- AMD accelerator detection, placement reporting and the Vulkan/ROCm runtime
  identification have not been exercised against real hardware by this
  project's own checks, which run on an Intel laptop and on fixtures.
- The installer upgrade is tested against fixtures only: 38 checks cover a
  successful upgrade, a failing one, a cancelled one, rollback, the run
  import's refusal to overwrite, and a developer checkout being left alone.
  No upgrade has been performed on a real machine.
- The managed recipes are CPU recipes. They are useful here for testing
  download, verification, cancellation and recovery, not for measuring the GPU.
- The downloader's resume, quarantine and cancellation are tested over
  loopback against a 4 MiB fixture (`cli/tests/test_managed_downloads.py`).
  Step 5 is the first time that code meets a real 2.5 GB fetch over a real
  network, which is why step 5 asks you to interrupt it on purpose.
- Suspension detection cannot see a Hyper-V pause from inside WSL2; a laptop
  lid or a sleeping host is reported as an unconfirmed clock jump, not as a
  confirmed suspension. See [bounded-execution.md](bounded-execution.md).
