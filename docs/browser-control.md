# Advanced panel: browser control

`omobench ui` opens a local control panel around the existing OmoBench CLI.
Its **default page is a guided benchmark** — see
[Guided CPU setup](guided-launcher.md) — that downloads, verifies, runs, and
helps submit one reviewed small CPU model for you, with nothing to type but a
submitter identity. This page describes the **advanced panel**, at
`/advanced` in the same running panel: connecting to a model server you
already run yourself. You use browser forms and buttons; benchmark execution
still runs through `omobench run --execute`, with the same GuideLLM version,
profiles, and evidence. Packaging still runs through `omobench package`.
Submission remains a GitHub PR.

The advanced panel connects to an already-running model server on the same
machine. It does not install models, start the model server, or switch its
execution device. The hosted website links to these launch instructions; it
does not remotely execute programs on visitors' computers.

## First-time setup

1. Open a terminal in your existing OmoBench checkout. Use Linux, macOS, or WSL2
   for execution. On Windows, run `wsl.exe -d Ubuntu` and wait for a Linux prompt;
   a prompt containing `MINGW64` is still Windows Git Bash. See
   [setup and updates](quickstart.md) if `omobench --help` does not list `ui`.
2. Activate your Linux environment, for example:

   ```bash
   source "$HOME/.venvs/omobench-runner/bin/activate"
   python -m pip install -e './cli[ui,runner]'
   ```

   If it does not exist, [create the environment first](quickstart.md#create-or-activate-the-python-environment).
   A Windows `.venv` cannot be reused as a WSL environment.
3. Start your chosen model server and load the model. Keep it running in its own
   terminal or app. Note its localhost address and API model identifier. Follow
   [model-server setup](model-server.md) for a complete CPU example. Every new
   Windows terminal must enter WSL too if the server is installed there.
4. In the activated OmoBench environment, run:

   ```bash
   omobench ui
   ```

5. Keep this terminal open. The browser opens automatically when supported.
   Otherwise, copy the **full URL**, including `#token=…`, from the terminal into
   your browser. The default panel port is `8765`; your model server uses a
   different port, for example `8092`. The page that opens is the guided
   benchmark; click through to **`/advanced`** (same token, same origin) for
   the connect-your-own-server workflow this page describes.

No `npm install`, Node.js, or website development server is needed for this panel.
On native Windows you can install `./cli[ui]` and open the panel for setup and
review, but execution is blocked with an explanation. The current GuideLLM runner
extra includes `uvloop`, which does not support native Windows.

## Run and review

1. **Check the environment.** The sidebar shows detected CPU, RAM, GPUs, and runner.
   GPU detection alone does not prove GPU inference.
2. **Enter the connection.** Use an address such as `http://127.0.0.1:8092` or
   `http://127.0.0.1:8092/v1`. Enter the model identifier accepted by the server.
   Use the exact API name, including case and slash. For the documented llama.cpp
   example it is `Qwen/Qwen3-0.6B`, not the shortened `Qwen3-0.6B` display name.
   [List your server's identifiers](model-server.md#address-and-model-identifier)
   if unsure. Click **Test connection**. This sends one tiny streaming request; it is not
   included in benchmark evidence.
3. **Choose a fixed profile and declare the device.** The panel displays the
   profile's token counts, load, and limits. Configure CPU/GPU use in the model
   server, then confirm it in the panel. Selecting a device does not change server
   settings. Disable GPU offload in the server before declaring a CPU run.
4. Click **Run benchmark**. The panel launches the existing CLI as a subprocess.
   Only one benchmark or packaging/submission operation runs at a time in a panel.
   Logs update automatically; output can arrive in bursts when the runner buffers it.
5. Review the latency, throughput, success counts, and errors. Use **Download local
   summary** to save the displayed measurements. This summary is clearly identified
   as local data and is not a public `result.json`.

For a CPU demonstration, load a small model with CPU execution explicitly
enabled in your server, choose **chat-small-v1**, select **CPU**, and run. This is a
real inference workload on your CPU, not a general CPU hardware test. Slower CPUs
may reach the profile's time limit; the UI does not shorten or alter the workload.
CPU and GPU results are both eligible for packaging and submission through the panel.
The [CPU demo walkthrough](local-demo.md) gives the two-terminal launch sequence
and exact form values. [Troubleshooting](troubleshooting.md) covers connection
failures, missing commands, and slow runs.

Use **Cancel benchmark** to stop the CLI and its GuideLLM child process. The model
server stays running. Cancelled, failed, and interrupted runs are labelled accordingly;
partial output is kept locally and is not eligible for packaging through the panel.
The panel allows only one instance per checkout, including across different ports.

## Packaging and pull requests

After a completed CPU or GPU run, **Review & submit** becomes available:

1. Enter the canonical model ID, immutable model revision, framework and exact
   version, quantization, submitter, and the server's actual startup arguments.
   The API alias used to make requests can differ from the canonical model ID.
   Generic servers require a meaningful server description. Do not enter secrets.
   Leave **Model weights source** blank when the weights came from the model
   ID's own repository; set it only when they came from elsewhere (for example
   a quantized re-export) — the panel prefills it automatically for a run
   started from a reviewed managed recipe.
2. Use the optional hardware fields only if detection was incomplete. The GPU
   memory field only applies to a GPU run and is hidden for a CPU run — no GPU
   performed inference, so there is nothing to fill in. A GPU memory value can
   fill unknown VRAM for one detected vendor/model class; it cannot invent a
   GPU or replace already-detected VRAM. The CLI enforces these rules.
3. Click **Package & validate**. The panel invokes the CLI packager, then the
   repository's validator for schema, model catalog, revisions, artifact paths/hashes,
   and duplicates against the local registry. Failures remain local and are displayed.
4. Click **Review submission files**. Inspect the normalized result and PR description.
   Download the complete ZIP to inspect all five files, including logs and raw output.
5. For direct PR creation, install GitHub CLI and authenticate in the same environment
   as `omobench ui` using `gh auth login`. Your account needs write access to the
   checkout's `origin` repository on github.com. The panel shows that exact destination
   and base branch before you confirm. Authentication credentials stay with GitHub CLI.
   If `origin` is your fork, the panel opens the PR there; it does not open a
   cross-fork PR against the upstream project. Use the manual workflow for upstream.
6. After reviewing the bundle and confirming inference on the declared device
   and absence of secrets, click **Create pull request**. This uploads only the
   five standard evidence files
   into a new `submit/<run-id>` branch and opens a PR against the repository's default
   branch. It does not stage local changes, switch branches, or push to `main`.
7. Review the PR's CI checks and follow the normal maintainer review process. The
   panel never merges a PR. Evidence is uploaded at submission time and is visible
   according to the repository's visibility, even before merge. The registry site
   includes it after merge and a successful build containing that commit.

The complete bundle is `result.json`, `guidellm-benchmarks.json.gz`,
`guidellm.stdout.log`, `environment.json`, and `reproduce.md`. File hashes shown in
the preview describe the bytes that will be uploaded. If the files change after
review, submission is rejected until they are reviewed again. Existing remote
branches or bundle directories are never overwritten.

Without GitHub CLI or write access, **Download complete bundle** still works. Unzip
it into your fork/check-out and follow [the manual submission workflow](submission-bundle.md).
The panel does not automatically create forks or upload repository source changes.
Uploads larger than 50 MiB per file must use the manual workflow.
If a model needs a catalog entry, add it through a separate source-change PR or
include the entry in a manually prepared submission PR. The panel uploads only
the five evidence files. Downloaded ZIPs already contain the `results/...` path;
extract at the checkout root rather than adding a second `results/` level.

## Local storage and recovery

- UI runs live under `.omobench-runs/ui/job-<id>/run-<id>/`. The CLI creates the
  evidence inside that run directory. The parent holds local job status and UI logs;
  those extra files are excluded from submission ZIPs and PRs.
- Reloading the page or restarting the panel restores its previous UI-created runs.
  Existing runs created directly by the CLI are not imported automatically.
- Ctrl+C stops the panel and cancels its active benchmark. Abrupt interruptions are
  marked on the next launch; they are not silently treated as completed benchmarks.
- If GitHub fails during submission, evidence remains on disk. Retry **Review
  submission files** and **Create pull request**. A saved branch/commit is reused;
  an existing PR is linked instead of duplicated. Once an upload has created a
  commit, repackaging that job is blocked to preserve the evidence being submitted.
- The panel binds only to `127.0.0.1`. Each launch creates a new connection token,
  checked on API requests along with the request host and origin. The token is
  placed in the URL fragment and browser session storage, not submission evidence.
  Do not expose the panel through a public tunnel or share its connection link.

Useful launch options:

```bash
omobench ui --port 8766       # If the default port is occupied
omobench ui --no-browser     # Print the connection URL without opening a browser
```

This first version accepts only localhost model endpoints without authentication
headers or redirects. Run the panel and model server in the same OS/WSL environment;
WSL-to-Windows localhost forwarding depends on your WSL configuration. Remote server
hardware collection and model-server lifecycle management are separate future work.
