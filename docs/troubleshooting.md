# OmoBench troubleshooting

Use these checks in the terminal running OmoBench. Keep its Python interpreter,
checkout, and model server in the intended environment.

## Linux paths fail in a MINGW64 terminal

`MINGW64` means Windows Git Bash. A Linux path such as
`/home/your-user/.venvs/omobench-runner/bin/activate` does not exist there.

Enter the installed Ubuntu distribution first:

```bash
wsl.exe -d Ubuntu
```

Wait for a Linux prompt, then follow [setup](quickstart.md#windows-enter-wsl-first).
Check every new terminal; one WSL terminal does not change the others.

## uvloop does not support Windows

The runner was being installed by native Windows Python. Run installation and
benchmark execution in WSL/Linux/macOS. Keep the Windows and Linux Python
environments separate. Do not remove the runner's pinned dependencies or install
it with `--no-deps` as a workaround.

## ui is an invalid command, or the ui extra is missing

Check the source and interpreter you are actually using:

```bash
git branch --show-current
git status --short
python -c 'import sys, omobench.cli; print(sys.executable); print(omobench.cli.__file__)'
```

If the checkout predates browser control, follow
[update an existing checkout](quickstart.md#update-an-existing-checkout).
Pulling `main` does not install a feature whose PR is still unmerged.

If the imported file points to another checkout, activate the intended Linux
environment and reinstall from the current repository:

```bash
python -m pip install -e './cli[ui,runner]'
python -m omobench.cli --help
```

Then start `python -m omobench.cli ui`. If this works but `omobench ui` does not,
check `command -v omobench` for an old executable on PATH. Do not change or
reinstall unrelated worktrees to repair this environment.

## The panel starts but no browser opens

Keep the command running and paste its full printed connection URL, including
`#token=...`, into your browser. On WSL, use the Windows browser. The token may
disappear from the address bar after loading; the page stores it for that tab.

A restart creates a new token, so reopen the newest printed URL. Do not share
that link or expose the panel through a public tunnel.

If the port is occupied, first check whether your own panel is already running.
Only one panel is allowed per checkout. A different service using port 8765 can
be avoided with:

```bash
omobench ui --port 8766
```

Use the new URL it prints. Changing the port does not permit a second controller
for the same checkout.

## Streaming connection fails

`GuideLLM 0.7.3 ready` confirms the benchmark runner is installed. It does not
mean a model is loaded or a model server is listening.

1. Check the model-server terminal/app. Wait for a successful model load and
   its listening address. If it crashed, resolve that before retrying OmoBench.
2. Enter that address, not the OmoBench panel's port. The example uses model
   server `8092` and panel `8765`.
3. Use the exact API identifier, not a shortened display name or file download URL.
4. Run the [model-list and streaming checks](model-server.md#verify-streaming-before-benchmarking)
   from the same WSL/Linux environment as the panel, before starting a benchmark.

A refused connection commonly means no listener or the wrong port. HTTP errors
can indicate the wrong identifier, missing model, or incompatible server
configuration. A timeout can mean slow loading/inference; a model listed by
`/v1/models` is not sufficient proof of readiness. The panel's short probe
timeout can expire while a cold server is still loading; let it become ready,
then retry. Retrying cannot repair a runtime crash.

If a Windows-hosted server responds from Windows but fails from WSL, check WSL
networking or run a server in the same WSL environment. Do not replace localhost
with a LAN address: this version of the panel accepts loopback endpoints only.
Authentication headers and redirects are also unsupported.

## Run is unavailable, slow, or interrupted

- **Run benchmark** is disabled when the pinned runner is missing, native Windows
  is detected, or another operation is active. Check the setup message.
- Start with `chat-small-v1`. Its duration constraint is 900 seconds; setup,
  warm-up/cool-down, and in-flight requests mean this is not an exact stopwatch
  deadline. CPU runs may be slow, and log output may arrive in bursts.
- Do not chat with the model or run another benchmark at the same time.
- **Cancel benchmark** stops OmoBench's CLI/runner processes; the separately
  started model server remains running.
- Ctrl+C in the panel terminal cancels an active benchmark and stops the panel.
  An abrupt interruption is labelled on restart. Partial evidence is kept, but
  failed/cancelled/interrupted runs cannot be packaged through the panel.
- If the whole WSL VM stops responding, use Microsoft's
  [WSL troubleshooting guide](https://learn.microsoft.com/en-us/windows/wsl/troubleshooting).
  Restarting WSL stops its running processes, including unrelated work.

## CPU measurements have no public submission button

That is the current v1 policy. Use **Download local summary** for a CPU run.
The summary is not the public `result.json` format; do not copy it into
`results/` or mark it GPU-backed to bypass the restriction.

## GPU packaging or submission fails

A GPU selection is only a declaration. Packaging also needs detected GPU
metadata; validation requires a catalogued model, immutable revision, valid
runtime details, and an intact evidence bundle. A memory override can only fill
unknown VRAM for already detected hardware.

For direct PR creation, `gh` must be installed and authenticated in the panel's
environment, with write access to the displayed `origin` repository. Windows
Git credentials are not automatically WSL GitHub CLI credentials. Use
`gh auth status` to check.

Without that access, download the bundle and follow the
[manual PR workflow](submission-bundle.md#manual-pull-request-workflow). The
panel does not create forks or automatically add missing catalog entries.
Do not edit measured values or artifact hashes to make an invalid bundle pass.
