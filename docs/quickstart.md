# Install, update, and run OmoBench

The browser panel and direct CLI use the same benchmark runner. Both need an
existing model server. Set up OmoBench here, then follow the
[CPU demo](local-demo.md) or [browser guide](browser-control.md).

## Choose the environment

| Task | Environment | Needed tools |
| --- | --- | --- |
| Execute through the panel or CLI | Linux, macOS, or WSL2 | Git, Python, OmoBench runner, compatible model server |
| Open the panel for setup/review only | Also possible on native Windows | Python and `./cli[ui]`; execution remains blocked |
| CLI checks and dry runs | Also possible on native Windows | Git, Python and `./cli` |
| Develop the comparison website | Windows, Linux, or macOS | Git, Node.js and npm |
| Upload a PR directly from the panel | Same environment as the panel | Authenticated GitHub CLI (`gh`) and write access to `origin` |

Use Python 3.12 for this walkthrough; it is the version used by project CI.
The runner extra installs `guidellm[recommended]==0.7.3`. Its dependency set
includes `uvloop`, which does not support native Windows. This is a limitation
of the current installation/execution path, not a claim about every possible
GuideLLM installation.

## Windows: enter WSL first

Keep your existing VS Code window and checkout. In its terminal, enter:

```bash
wsl.exe -d Ubuntu
```

This launches an installed Ubuntu distribution. If WSL/Ubuntu is not installed,
follow Microsoft's [WSL installation instructions](https://learn.microsoft.com/en-us/windows/wsl/install)
first. Other installed distributions can be selected with `wsl.exe -d NAME`.

The prompt should change from `MINGW64` or `PS ...>` to a Linux prompt such as
`user@computer:...$`. Confirm:

```bash
uname -s
pwd
```

`uname -s` should print `Linux`. The directory must be your OmoBench checkout.
If `pwd` already shows it, no directory change is needed. Otherwise, a Windows
checkout under your user folder is accessible with:

```bash
cd /mnt/c/Users/YOUR_WINDOWS_USERNAME/Code/omobench
```

Replace `YOUR_WINDOWS_USERNAME` with your Windows folder name. Check the
environment in each new terminal: a new Git Bash terminal does not become Linux
just because another terminal is running WSL. See Microsoft's
[WSL command reference](https://learn.microsoft.com/en-us/windows/wsl/basic-commands).

Windows Git Bash paths such as `/c/Users/...` and activation through
`.venv/Scripts/activate` are for Windows tools. WSL uses `/mnt/c/Users/...`
and Linux environments with `bin/activate`.

If Python or venv support is missing on Ubuntu, install these prerequisites in WSL:

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip git curl
```

## Get a checkout

If you already have the repository, use that folder; no new clone or worktree
is needed just to launch the UI.

For a new Linux/macOS/WSL checkout:

```bash
mkdir -p "$HOME/Code"
cd "$HOME/Code"
git clone https://github.com/Xatiiv/omobench.git
cd omobench
```

### Update an existing checkout

Stop active benchmarks and the panel before updating code. In the checkout:

```bash
git branch --show-current
git status --short
git fetch origin
```

If status lists unsaved work, preserve/review it before switching branches.
With a clean checkout, use `main` for merged software:

```bash
git switch main && git pull --ff-only origin main
```

Browser control was introduced in [PR #7](https://github.com/Xatiiv/omobench/pull/7).
If you are testing it **before that PR is merged**, `main` will not yet contain
`omobench ui`. Use its preview branch instead, from a clean checkout:

```bash
git fetch origin && git switch feature/browser-control && git pull --ff-only origin feature/browser-control
```

`Already up to date` applies only to the branch you pulled. It does not bring
unmerged feature work into `main`. After the PR is merged, use `main` again.
Reinstall the extras below after updating, because dependencies or packaged UI
assets may have changed.

If WSL Git authentication fails but Windows Git is already authenticated, you
can use `git.exe` for these Git commands on a Windows-hosted checkout. Keep
Python and the runner in their Linux environment.

## Create or activate the Python environment

All commands in this section are for Linux, macOS, or WSL. Keeping the runner
environment in your Linux home avoids mixing it with a Windows `.venv`.

If you do not already have this environment, create it once:

```bash
python3 --version
python3 -m venv "$HOME/.venvs/omobench-runner"
```

If you already have a working Linux runner environment at that path, skip
creation. Activate it and install from the checkout:

```bash
source "$HOME/.venvs/omobench-runner/bin/activate"
python -m pip install -e './cli[ui,runner]'
```

Activate it in each new terminal that will run OmoBench. An existing Linux
environment elsewhere is also fine: activate that one instead. Do not activate
a Windows environment from WSL or install runner extras using Windows Python.

### Check the installation

```bash
python -c 'import sys, omobench.cli; print(sys.executable); print(omobench.cli.__file__)'
omobench --help
omobench doctor
omobench profiles
guidellm --version
```

The interpreter should belong to the activated environment; the imported
`cli.py` should belong to the intended checkout. Help must include `ui`, and
GuideLLM must report `0.7.3`. CPU runs do not require a detected GPU. GitHub CLI
is optional until you want to upload a PR directly from the panel.

If `ui` is missing or installation mentions an unknown `ui` extra, check the
branch and imported path; reinstalling an older checkout will not add the
command. See [troubleshooting](troubleshooting.md).

## Start the browser workflow

In the activated OmoBench terminal:

```bash
omobench ui
```

Open the full URL it prints, including `#token=...`. Keep the terminal running.
Your Windows browser can access the WSL-hosted panel. The page that opens is a
**guided benchmark by default**: it downloads, verifies, and runs one reviewed
small CPU model for you, then helps prepare a submission, see
[Guided CPU setup](guided-launcher.md). It adds one extra requirement of its
own, Linux x86_64 with glibc 2.34 or newer, because its pinned runtime build
depends on it.

Already loaded a model yourself using the [model-server guide](model-server.md)?
Open **`/advanced`** in the same panel instead to connect to it directly. The
[browser guide](browser-control.md) explains that panel's controls and
submission flow. No website development server or npm installation is
required for either page.

## Run directly from the CLI

Use this as an alternative to clicking **Run benchmark**, not at the same time
as a panel-controlled run. This example assumes you started the small model in
[the model-server guide](model-server.md) on port `8092`.

Preview the runner command without contacting the model:

```bash
omobench run \
  --endpoint http://127.0.0.1:8092 \
  --model Qwen/Qwen3-0.6B \
  --profile chat-small-v1
```

Then execute it:

```bash
omobench run \
  --endpoint http://127.0.0.1:8092 \
  --model Qwen/Qwen3-0.6B \
  --profile chat-small-v1 \
  --execute
```

The server controls CPU/GPU execution; `omobench run` does not switch devices.
Here, `--model` is the identifier accepted by the server API. The CLI writes
evidence under `.omobench-runs/run-<id>/` and prints its path. It does not upload
anything. Direct CLI runs are not automatically imported into **Recent runs**.

For CPU runs, keep the raw report locally; the panel is the easiest way to get
formatted measurements and a downloadable local summary for a new run.
`omobench package` rejects CPU execution. For completed GPU runs, see the
[packaging and submission commands](submission-bundle.md#create-the-local-normalized-summary).

## What hardware detection means

`omobench doctor` reports tools and hardware visible to its own environment.
It does not start a model or prove that the server uses a detected GPU.

- NVIDIA: `nvidia-smi`, when available, reports model, VRAM, and driver.
- AMD on Linux: `rocm-smi`, when available, supplies best-effort metadata.
- Windows fallback: PowerShell/CIM reports GPU model and driver; OmoBench does
  not trust its `AdapterRAM` field for VRAM.
- Linux fallback: `lspci`, when available, supplies GPU names, with memory and
  driver left unknown.

OmoBench does not install vendor utilities. A GPU visible to Windows may not be
visible to WSL. Run the benchmark on the model-serving machine and verify its
environment before describing it as a public GPU result. Packaging accepts
older `nvidia_gpus` snapshots as well as current `gpus` snapshots. The
[GPU memory override](submission-bundle.md#gpu-vendor-and-memory) can fill
unknown VRAM for detected hardware; it cannot invent a GPU.

## Native Windows development only

For CLI checks, dry runs, or panel setup/review in Windows Git Bash, create a
separate Windows environment if needed, then omit the runner extra:

```bash
python -m venv .venv
source .venv/Scripts/activate
python -m pip install -e './cli[ui]'
omobench doctor
```

This does not enable benchmark execution on Windows. Website development uses
Node and npm separately; see [the README](../README.md#develop-the-comparison-website).
