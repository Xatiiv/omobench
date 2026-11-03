# Installing OmoBench on Windows

This is the download-and-run path: you get an installer, open it, answer a
couple of questions, and end up at the OmoBench benchmark panel, with no
repository to clone and no Python paths to find.

Two prerequisites can still need a command from you, and the installer says so
rather than pretending otherwise:

- **WSL2 is not installed.** Installing it needs an administrator command and a
  restart, see [below](#if-wsl-is-missing).
- **Your Linux distribution is missing `python3` or `python3-venv`.** Installing
  those needs your Linux password, so the installer gives you the exact command
  instead of asking for it.

Everything else, finding or fetching OmoBench, creating an isolated
environment, installing the pinned runner, checking it works, it does itself.

If you already work in a checkout and prefer the terminal, nothing here
replaces that, see [the setup guide](quickstart.md). The installer only
automates *finding and preparing* an environment; the benchmark engine,
profiles, CLI and submission flow are the same either way.

## What you need

- 64-bit Windows 10 version 2004 (build 19041) or later, or Windows 11.
- WSL2, with a Linux distribution. The installer checks for this and explains
  how to get it if it is missing, see [below](#if-wsl-is-missing).
- An internet connection, if you do not already have OmoBench installed.

The installer refuses early and explains why on a machine that cannot work:
32-bit Windows, or a Windows build older than 2004, cannot run the benchmark
runner at all. That is not something OmoBench can work around, the runner's
dependencies include `uvloop`, which has no native Windows build, so the
benchmark has to run inside WSL2.

## Getting the installer

Download `OmoBenchSetup.exe` from the project's releases page, or build it
yourself (below). The website's **Download OmoBench for Windows** button links
to the published release; while no release has been published it says so
rather than offering a link that goes nowhere.

**The installer is not code-signed.** Windows SmartScreen will say the
publisher is unknown, and you will have to choose *More info → Run anyway*.
That warning is accurate, and this project will not pretend otherwise: there is
no code-signing certificate behind these builds yet. What you *can* check is
the SHA-256 checksum published with the release:

```powershell
Get-FileHash .\OmoBenchSetup.exe -Algorithm SHA256
```

Compare it with the value on the release page. The installer also shows its own
version, source revision and signing status on its first screen.

### Building it yourself

The installer is ordinary C# in this repository and needs no developer tools
installed, it compiles with the C# compiler that ships inside Windows:

```powershell
# Windows PowerShell, from the checkout root
powershell -ExecutionPolicy Bypass -File windows\installer\build.ps1 -Test
```

That produces, under `windows\installer\build\`:

```
OmoBenchSetup.exe   the installer, with the companion embedded inside it
version.json        version, source revision, checksums, signing status
SHA256SUMS          the installer's checksum
```

Nothing built is committed; the build directory is ignored by Git.

## First installation

1. Run `OmoBenchSetup.exe`. The first screen shows the version, the source
   revision it was built from, its signing status, and exactly what it will do.
2. Press **Install**. This installs for your user account only, no
   administrator prompt. It places the companion in
   `%LOCALAPPDATA%\Programs\OmoBench`, adds a Start menu entry and an entry in
   Apps & features, and registers `omobench://` links against the installed
   program.
3. The installer then looks at your machine:
   - **If you already have OmoBench**, an existing checkout and a Linux Python
     environment with it installed, it finds them, checks them, and uses them.
     Nothing is downloaded and nothing of yours is changed.
   - **If you do not**, it offers to prepare one. See
     [below](#when-it-prepares-an-environment-for-you).
   - **If WSL is missing**, it explains the Microsoft steps and waits.
4. When the environment check passes, the installer says OmoBench is ready and
   offers to open it.

The setup is never reported complete before that check actually passes. The
stages shown while preparing describe what was *attempted*; only the final
check decides.

## When it prepares an environment for you

If there is nothing to reuse, **Prepare it for me** will, inside your WSL2
distribution:

1. unpack the OmoBench source it carries, the exact revision the installer was
   built from, into `%LOCALAPPDATA%\OmoBench\source\<revision>`;
2. create a Python environment used only by OmoBench, at
   `~/.local/share/omobench/managed-env` inside Linux;
3. install OmoBench and the pinned benchmark runner version into it;
4. check the result before calling it finished.

It shows each stage as it happens, reports the real error if one fails, and can
be stopped at any point, stopping leaves the rest untouched.

**It does not touch anything you already have.** A checkout you work in, a
virtual environment you maintain, your system Python: none of them are written
to, upgraded, or repointed. Everything the installer creates is in the two
managed locations above, and deleting those removes it.

It does not download any model or run any benchmark. Those remain explicit
actions inside the OmoBench panel, exactly as before.

### Where the source comes from

The OmoBench source travels **inside the installer**. It is not fetched from
GitHub at install time, so there is nothing to sign in to, no account needed,
and no dependency on the repository being readable by a stranger. Before it is
unpacked, the archive is checked against the SHA-256 recorded when the
installer was built; a truncated or altered payload is refused rather than
installed.

The archive is an explicit list of what the CLI, guided panel, packaging and
validation actually read, the `cli` package, benchmark profiles, the model
catalogue and download recipes, the result schema and the validator. Git
history, the website, tests, caches, evidence and model files are not in it.

**This is not an offline installer.** Python dependencies, including the pinned
benchmark runner, are installed from PyPI, so preparing an environment needs a
working internet connection.

### Licence and public downloads

OmoBench is licensed under [Apache License 2.0](../LICENSE). The installer
embeds `LICENSE`, `NOTICE` and `THIRD_PARTY_NOTICES.md`, installs them beside the
Windows companion, and includes them in the verified runtime source archive.
The Python package includes its own identical copy of `LICENSE`. Third-party
packages, downloaded runtimes and model weights retain their separate terms.

Public installers are distributed through the repository's
[GitHub Releases](https://github.com/Xatiiv/omobench/releases). The website only
enables its download button once the release asset is publicly available and
its source revision and checksum have been verified. `version.json` records
`license: "Apache-2.0"` alongside the build identity.

### If something is missing inside Linux

Two cases need your Linux password, so the installer explains them instead of
trying to do them for you:

- **No `python3`**, `sudo apt update && sudo apt install -y python3 python3-venv`
- **`python3 -m venv` fails**, on Ubuntu this usually means
  `sudo apt install -y python3-venv`

Run either from inside your distribution, then press **Try again**.

## Supported, tested and recommended are three different things

Package metadata allows a range. It does not promise that every point in that
range works on a fresh machine, and this project does not claim otherwise.

| | What it means | Today |
| --- | --- | --- |
| **Supported by metadata** | What `pyproject.toml` and the pinned runner permit | Python ≥ 3.10, < 4.0 (`omobench` requires ≥ 3.10; `guidellm[recommended]==0.7.3` requires `<4.0,>=3.10.0`) |
| **Tested** | What this project has actually run a benchmark or a full check on | Python 3.12.3 on Ubuntu 24.04.2 LTS under WSL2, x86-64 |
| **Recommended** | What a new contributor should choose | The distribution's own `python3` on a current Ubuntu LTS under WSL2 |

A version inside the supported range but outside the tested row is **untested**,
not blessed. The installer uses whatever `python3` the chosen distribution
provides and creates a virtual environment from it; it does not select a version
for you and does not install one.

### Why readiness now means "it imported"

Preparation used to finish by confirming that the pinned runner's *metadata* was
present in the new environment. Installed is not usable: a contributor's
prepared environment reported the pinned version and then died with a SIGBUS
while importing `torch`. Replacing `torch` with a CPU build let it import again,
but that is **not** a demonstration that the Python version or the GPU vendor
caused it — the same installation had also hit storage problems, which is a
known cause of exactly this failure, and no controlled comparison was made.
OmoBench does not record a root cause it cannot show.

What changed is the check, not the diagnosis. Preparation now imports the
runner's own entry point in a **separate, bounded child process** before
reporting readiness:

- it imports libraries only — no model, no tokenizer, no download, no inference,
  and no remote code (the child runs with `HF_HUB_OFFLINE`,
  `TRANSFORMERS_OFFLINE` and telemetry disabled);
- a crash is reported as a crash, naming the signal, and says plainly that
  OmoBench cannot tell a damaged install from unusable storage from here;
- a timeout, a non-zero exit and a missing dependency are three different,
  actionable messages;
- cancellation is answered promptly, and nothing is launched as a fallback;
- diagnostics are redacted and length-bounded before they are shown or logged.

Because the import is a real one, preparation and the Setup window now take a few
seconds longer than before. That is the point.

## If WSL is missing

Installing WSL is a Windows operation that needs administrator rights and a
restart, so it is done once, by you, outside this installer. The installer does
not ask for elevation, nothing else it does needs it, and the install would
not survive the reboot anyway.

1. Open Terminal, Command Prompt or PowerShell **as administrator**.
2. Run `wsl --install`.
3. **Restart Windows** when asked. WSL is not usable until this finishes.
4. After the restart, Ubuntu opens and asks you to create a Linux username and
   password. These are new credentials for the Linux environment, not your
   Windows account, and they can be different.
5. Return to the installer and press **Check again**.

The installer re-checks the real state of the machine; it does not assume the
steps worked. Microsoft's own instructions:
<https://learn.microsoft.com/windows/wsl/install>

If WSL is present but has no distribution, `wsl --install -d Ubuntu` is usually
enough and usually needs no administrator rights.

## Afterwards

**Opening OmoBench.** Use the Start menu entry, the companion's own **Open
panel** button, or any `omobench://open` link. The companion starts the panel in
your WSL environment and opens it in your browser. See
[the companion guide](windows-companion.md) for what that window does.

**Updating.** Run a newer `OmoBenchSetup.exe`. It updates the installed program
in place and leaves your settings, benchmark evidence and model files alone. If
the companion is open, the installer offers to close it first, its program file
cannot be replaced while it is running.

**Uninstalling.** Apps & features → OmoBench → Uninstall, or run
`OmoBenchUninstall.exe /uninstall` from the install folder.

Uninstalling removes four things: the program folder, the Start menu entry, the
Apps & features entry, and the `omobench://` registration, and only if that
registration still points at this installation.

It deliberately keeps:

- your benchmark evidence and saved settings;
- any model files you downloaded;
- your WSL installation and every Linux distribution on it;
- any checkout and Python environment you had before.

There is no option to delete your evidence. It is the output of work you did,
and removing a Windows launcher is not a reason to destroy it. An
OmoBench-managed environment, if one was prepared for you, stays too; delete
`%LOCALAPPDATA%\OmoBench\source` and `~/.local/share/omobench` by hand if you
want it gone.

## Where things live

| What | Where |
| --- | --- |
| The program | `%LOCALAPPDATA%\Programs\OmoBench` |
| Start menu entry | `…\Start Menu\Programs\OmoBench.lnk` |
| Apps & features entry | `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\OmoBench` |
| Protocol registration | `HKCU\Software\Classes\omobench` |
| Companion settings | `%APPDATA%\OmoBench\companion.config` |
| Managed source (if prepared) | `%LOCALAPPDATA%\OmoBench\source\<revision>` |
| Managed Linux environment (if prepared) | `~/.local/share/omobench/managed-env` inside WSL |
| Your benchmark evidence | in your checkout's `.omobench-runs/`, untouched |

Everything is per-user. Nothing is written to `Program Files`, to `HKEY_LOCAL_MACHINE`,
or to any location shared with other accounts.

## How builds are produced

Every pull request that touches `windows/` builds the installer on a Windows
runner, runs the companion's test harness against the production sources and
the installer's own tests, and attaches the result as a build artifact, so a
reviewer can download and run exactly what the branch produces.

Publishing a release is a separate, deliberate action: a tag or an explicit
manual run on `main`. An ordinary pull request build can never publish.

The installer and the companion inside it are always built from one revision,
and CI fails the build if the stamped revision does not match the commit being
built. That revision is what the managed environment fetches, so the installer,
the companion and the source all agree.

## Submitting from the installed panel

The community submission route is independent of a `.git` directory and GitHub
CLI. A configured build lets the contributor sign in with a one-time GitHub code,
review five evidence files, and confirm upload to their public fork and a PR to
OmoBench. The OAuth client ID is public build configuration. The OmoBench
Community app is registered and its public ID is included in the source. A build
without a configured ID reports sign-in unavailable and offers the complete ZIP
instead. The currently released installer
does not gain this feature until a new release is built and published.
See [community submissions](community-submissions.md).

## Managed runner dependency policy

New managed environments install `torch==2.15.0+cpu` from PyTorch's CPU wheel
index before installing the application. The application installation uses the
shipped `runner-constraints.txt` to retain that CPU build and GuideLLM 0.7.3.
An incompatible Python/platform combination fails with a named prerequisite;
setup does not silently compile Torch or substitute a CUDA build. `pip check`
must pass before the environment can be marked ready.

This changes the HTTP benchmark client's dependencies, not the execution device
of your model server. GPU inference in LM Studio or another server remains
possible. Existing discovered environments are reused without modification;
setup does not remove old NVIDIA packages or repair someone else's environment.
The two pins are not a complete transitive dependency lock. Dependencies still
require an internet connection, and package availability is platform-specific.
