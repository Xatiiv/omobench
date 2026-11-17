# Panel reference

`omobench ui` opens a local control panel around the existing OmoBench CLI. New
to OmoBench? Start with the [user guide](user-guide.md); this page is the
detailed reference for the same panel.

The panel is one page. It starts at **01 Connect your model server**, then
**02 Confirm the serving hardware** and **03 Run a standard test**, followed by
the run's dashboard and **Review & share**. (`/advanced` and `/guided` are kept
as bookmarks and serve the same page.) You use browser forms and buttons;
benchmark execution still runs through `omobench run --execute`, with the same
GuideLLM version, profiles, and evidence. Packaging still runs through
`omobench package`. Submission remains a GitHub PR.

The panel connects to a model server you already run. It does not download
models, start the model server, or switch its execution device. The hosted
website links to these launch instructions; it does not remotely execute
programs on visitors' computers. Earlier versions of the panel opened on a
curated workload picker with a managed CPU download; that is no longer part of
the panel, and the equivalent CLI commands are described in
[Managed CPU setup](guided-launcher.md).

On Windows, the [companion](windows-companion.md) starts this same panel from
the Start menu or the website's **Open OmoBench** button, and provides the
Windows bridge to a model server running on Windows. Everything below works the
same whether the companion or `omobench ui` started the panel.

## What this panel can benchmark

The panel will benchmark any local model server that meets the requirements
below, whatever serves it and whatever model it holds.

**Server API.** Streaming OpenAI-compatible `POST /v1/chat/completions` that
returns `text/event-stream` chunks. `GET /v1/models` is optional: when a server
offers it, the panel lists its identifiers for you to choose from; when it does
not, you type the identifier the server documents and the streaming check is
what proves it works. A server that answers `/v1/models` but cannot stream chat
completions cannot run this benchmark.

**Server location.** A loopback address on the machine the benchmark runner
runs on — `http://127.0.0.1:<port>`, `http://localhost:<port>` or
`http://[::1]:<port>`, with an optional `/v1` suffix. Any port. This is a
deliberate boundary, not a missing feature: the result records *this machine's*
hardware as the hardware that served the run, and a local panel must not be
usable to reach arbitrary hosts. Nothing is discovered, scanned or guessed; the
panel contacts only the address you type. The one exception to "this machine"
is the Windows bridge, below.

**Model.** Any text-generation model your server can serve, catalogued or not.
Local measurement and local export never require catalog membership. Only
public submission does. A vision-capable model can be benchmarked, but this
workload sends text prompts only: it measures text generation on that model and
says nothing about its image handling.

**Runner.** The pinned GuideLLM version, in a Python environment on Linux,
macOS or WSL2. The runner's own dependencies are irrelevant to your server's
hardware: OmoBench never infers that a GPU is required, or that it must be any
particular vendor, from the packages its runner installs.

**Server-side hardware.** Any. **Where does the server run the model?** records
*your declaration* about where your server performed inference: on the GPU,
split between GPU and CPU, or on the CPU only; GPU covers any vendor's
accelerator, and the result's GPU metadata is vendor-neutral. **Read the serving
hardware** reads the serving machine's CPU, memory and cards, but OmoBench does
not configure or verify which device your server used.

### Where this leaves a Windows-hosted server

A benchmark runner in WSL2 and a model server running on the Windows side are
two different loopback namespaces. The panel will not dial the Windows host's
WSL-facing address, because allowing non-loopback targets would turn a local
panel into a general-purpose client for arbitrary hosts.

Instead, tick **The server runs on Windows and this panel runs in WSL. Connect
through the Windows bridge.** The companion then carries each request to the
Windows server's own loopback address. This needs a panel started by the
companion, and WSL interop enabled; a missing or old companion is an error, never
a fallback to a different server. The bridge's latency is included in every
measured number and never subtracted, so bridged results are compared only with
bridged results. The serving hardware is read on Windows through the companion
and recorded separately from the WSL runner. See
[the bridge notes](bridge-frontend-status.md) and [provenance](provenance.md).

### Checking a server before you run

**Check this server** runs these checks from the environment the runner is
launched from and reports each separately, because they establish different
things:

| Check | What a pass means |
| --- | --- |
| Server address accepted | The address is loopback on this machine. |
| Benchmark runner available | This environment can execute a benchmark at all. |
| Server reachable | Something answered at that address. |
| Served model identifier | The server lists this identifier, or does not publish a list. |
| Streaming chat completions | One small streaming completion actually arrived. |
| Tokenizer source | The identifier is usable and whether its files are already local. |

It ends by naming what it did *not* establish: that the tokenizer matches the
served weights, which device performed inference, what a full run will measure,
and whether the result qualifies for the registry. A green row is a statement
about one check, never about the whole benchmark.

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
   different port, for example `8092`. The page opens at **Connect your model
   server**.

No `npm install`, Node.js, or website development server is needed for this panel.
On native Windows you can install `./cli[ui]` and open the panel for setup and
review, but execution is blocked with an explanation. The current GuideLLM runner
extra includes `uvloop`, which does not support native Windows.

## Run and review

1. **Check the environment.** The sidebar's **This machine** list shows the
   runner environment's CPU, memory, GPUs, and runner. In WSL this is the
   runner, not the serving machine, and GPU detection alone does not prove GPU
   inference.
2. **Connect.** Enter the **Server address**, such as `http://127.0.0.1:8092`
   or LM Studio's `http://127.0.0.1:1234`. Tick **The server runs on Windows and
   this panel runs in WSL. Connect through the Windows bridge.** when that is
   the case. Press **Connect**: the panel lists the models the server reports
   through `/v1/models` (and LM Studio's own description of each, where it
   offers one). Choose one under **Which model?**, or use **Enter a model ID
   instead** and type the exact **Server model identifier**, including case and
   slash. For the documented llama.cpp example it is `Qwen/Qwen3-0.6B`.
   [List your server's identifiers](model-server.md#address-and-model-identifier)
   if unsure. **Check this server** sends one tiny streaming request; it is not
   included in benchmark evidence.
3. **Let the panel find the tokenizer.** The workload's prompts are built and
   counted with a tokenizer. A served identifier is often a serving alias, such
   as LM Studio's `qwen2.5-coder-14b-instruct`, not a Hugging Face repository.
   After **Connect**, the panel resolves the tokenizer separately: a choice
   already verified for the same model, then the GGUF file's own metadata (when
   the **Model folder** is set), then the served identifier when it already is
   a repository id, and finally a name match that it offers once for
   confirmation (**Use this tokenizer**). It then loads the tokenizer in the
   runner's own interpreter and encodes a sample, offline first. When the
   files are not cached, **Download the tokenizer files** fetches them (never
   weights). **Run standard test** stays disabled until the tokenizer for the
   currently selected model is ready. **Advanced: tokenizer** holds the
   override: enter a repository or local folder and use **Check tokenizer** or
   **Check and load this tokenizer**. Do not rename your served model. Remote
   code execution stays disabled, and the tokenizer is never a claim about
   which weights are loaded.
4. **Confirm the serving hardware.** Press **Read the serving hardware**
   (through the companion when bridged), choose where the server runs the
   model, tick each card that runs it, describe a split when there is one, and
   tick the confirmation. Selecting a placement does not change server
   settings. Disable GPU offload in the server before declaring a CPU run.
5. **Choose a profile and run.** **Standard chat** (`chat-small-v2`) is the
   default; other profiles are under **More tests**, with token counts, load
   and limits under **Benchmark profile details**. Press **Run standard test**.
   The panel launches the existing CLI as a subprocess. Only one benchmark or
   packaging/submission operation runs at a time in a panel. Logs update
   automatically; output can arrive in bursts when the runner buffers it.
6. Watch the run on the dashboard, then review the results. See
   [the live dashboard](#the-live-dashboard) and
   [saving a local report](#saving-a-local-report-is-not-submitting).

For a CPU demonstration, load a small model with CPU execution explicitly
enabled in your server, keep **Standard chat** (`chat-small-v2`), choose **On the CPU only**, and run. This is a
real inference workload on your CPU, not a general CPU hardware test. Slower CPUs
may reach the profile's time limit; the UI does not shorten or alter the workload.
CPU and GPU results are both eligible for packaging and submission through the
panel, and so is a run measured through the Windows–WSL bridge, provided its
serving machine is described. See [provenance.md](provenance.md).

Running and publishing are two different things with two different requirement
lists, and the second one used to appear only once the benchmark had finished.
**Before you start**, beside the server check, groups every check by what it
would stop: running this benchmark, packaging the evidence, or submitting to
the public registry. Each says what was looked at, what was seen, where that
came from and one thing to do about it. Only an actual runner constraint stops
a run, and a local report needs none of it.

The panel's stages — Connect, Identify, Check, Run, Review, Submit — are
derived from what is true rather than from what was clicked, so going back to
fix something clears nothing. See [guided-workflow.md](guided-workflow.md).
The [CPU demo walkthrough](local-demo.md) gives the two-terminal launch sequence
and exact form values. [Troubleshooting](troubleshooting.md) covers connection
failures, missing commands, and slow runs.

Use **Cancel benchmark** to stop the CLI and its GuideLLM child process. The model
server stays running. Cancelled, failed, and interrupted runs are labelled accordingly;
partial output is kept locally and is not eligible for packaging through the panel.
The panel allows only one instance per checkout, including across different ports.

## The live dashboard

A run against your own server shows the same dashboard as the guided recipes:
the live throughput chart with a sample inspector, time to first token and
request latency with their units, successful/errored/cancelled counts against
the total request budget, progress, elapsed time and estimated remaining time,
the run's stages, and collapsible runner log and raw telemetry.

It is literally the same component, not a copy
(`cli/src/omobench/web/dashboard.js` and `dashboard.css`, mounted by both
pages), so the two routes cannot drift apart.

What differs is only what OmoBench can honestly observe. For a server you run
yourself it never downloaded weights, never started the server and never loaded
a model, so those stages do not exist here: the stages are **starting the
runner, warming up, running the benchmark, cooling down, saving results**, and
warmup and cooldown appear only when GuideLLM itself reports them. Everything
else follows the same rules everywhere on the page: a missing value reads "Pending"
or "unavailable" rather than zero, progress comes from the runner's own
remaining fraction rather than from elapsed time, a spent time budget is
labelled instead of shown as a finished run, and 100% appears only once the
evidence files are written.

The profile's declared measured-request count is a *declaration*, not a
guarantee. Three of the four shipped profiles resolve warmup and cooldown
against the clock rather than against request counts, so their measured window
is a fixed slice of the duration limit and how many requests land inside it
depends on how long each request takes. The dashboard says so where it applies,
and `docs/proposals/profile-phase-mapping-v2.md` records the confirmed
declared-versus-enforced mapping for every profile along with a proposed
correction that is not yet active.

The request counts are deliberately two different sets. The live counters —
**Requests finished** and the Successful/Errored/Cancelled row — come from the
runner's scheduler, which has no phase concept: they count every request that
finished, warmup and cooldown included, against the profile's request *cap*,
which is an upper bound that a run bounded by its time limit never reaches. The
published figures in **Successful / attempted** come from the measured window
alone, after warmup and before cooldown, which is why they are smaller. A
completed benchmark is not thereby publication-eligible.

**Cancel benchmark** stops OmoBench's own runner process group. Your model
server is started and owned by you; the panel has no handle on it and never
signals it.

## Saving a local report is not submitting

**Save local report** downloads what this machine measured: the metrics, the
profile, the server model identifier, the tokenizer source, the declared
execution device, and whatever provenance OmoBench already recorded, with
anything unknown written as `unknown` rather than guessed. It needs no
publication metadata, no catalogued model and no GitHub sign-in, it works for a
run reopened after the panel restarts, and it rereads the saved record rather
than rerunning or rewriting any evidence. It deliberately excludes the runner
log, the environment snapshot and the endpoint, so it cannot carry a token that
appeared in a log.

A local report is **not** a public result. It uses its own
`omobench-local-report` format and makes no claim about publication
eligibility; the registry's contract remains `result.json`, produced by
packaging.

### Comparing results

The report records comparison context in three groups, because they answer
different questions:

| Group | Contents | Role in a comparison |
| --- | --- | --- |
| Workload definition | profile id and `profile_sha256`, request format, concurrency, input and requested output tokens, the profile's real phase contract | Must match for a comparison to be **controlled** |
| Model configuration | server identifier, canonical id, revision, quantization, tokenizer source | Must match for a comparison to be about one model |
| Execution configuration | hardware, the CPU/GPU declaration, serving runtime and version, runtime arguments | Usually the **subject** of the comparison |

Comparing CPU against GPU, or one machine against another, on an otherwise
identical workload is a controlled comparison and exactly what the execution
group exists for. A difference there is a dimension, not a disqualification.
A difference in the model or the workload makes the comparison *exploratory*:
still informative, but a comparison between different things rather than a
ranking of one thing.

Two cautions the report states explicitly. Token counts come from the tokenizer
named in the report, so the same token count under two different tokenizers has
not necessarily processed the same text or done the same work. And unknown
provenance means comparability is **not fully established** for those fields --
not that a comparison is certified, and not that it is forbidden; the report
lists exactly which fields are unknown.

None of this is publication eligibility. A result can be entirely comparable and
still ineligible for the registry, whose schema, catalog, provenance and
acceptance checks are unchanged.

## Packaging and pull requests

After a completed CPU, GPU or split run, **Review & share** becomes available. A
community submission needs more than a finished benchmark, and **Check
publication requirements** lists everything that is still missing at once,
before packaging is attempted, rather than stopping at the first empty field.
Six identities are easy to confuse, and only the first four are interchangeable
in nobody's reading:

| | What it is |
| --- | --- |
| Canonical model ID | The catalogued identity, for example `Qwen/Qwen3-4B`. |
| Server model alias | The string your server's API accepts. May differ, and often does. |
| Tokenizer source | The repository or folder whose tokenizer built the workload. |
| Model revision | The immutable revision of the repository the weights really came from. |
| File SHA-256 | A checksum of one downloaded file. Not a revision. |
| OmoBench source commit | The revision of this tool. Recorded automatically; not a model revision. |

A model revision must identify the weights source's own revision. `main`,
`master` and `latest` move, so they identify nothing, and the validator rejects
them. A file checksum or the OmoBench commit is not a substitute. When the
weights came from a quantization repository, record that repository as the
weights source and pin its own revision, consistently with the artifact URL.
The exact serving runtime version is the runtime's own build, such as
`llama.cpp b10621`; an application version, for example the LM Studio release,
is a different thing, and OmoBench will not guess a build for you.

If a model is not in `catalog/models/`, it is not catalogued, and the panel
never relabels the run as a different, catalogued model. Two routes remain
open. **Suggest a catalog model** offers entries the served alias may mean and
applies none of them: one match is a suggestion to confirm, several make the
mapping ambiguous and you choose. A model that is not on the curated list is
still publishable: when the canonical model ID is not catalogued, **Your own
model** asks for its licence, licence URL, weights source, weights revision and
downloadability, recorded as your declaration (see
[model eligibility](model-eligibility.md)). Optionally, **Propose this model for
the catalog** adds a model proposal, which travels with the result so a
reviewer sees the request and the evidence together. A proposal does not add
the model to the catalog and merging the result would not either; accepting
one is a separate, reviewed change. Local viewing and export need none of this.

1. Enter the canonical model ID, immutable model revision, framework and exact
   version, quantization, submitter, and the server's actual startup arguments.
   The API alias used to make requests can differ from the canonical model ID.
   Generic servers require a meaningful server description. Do not enter secrets.
   Leave **Model weights source** blank when the weights came from the model
   ID's own repository; set it only when they came from elsewhere (for example
   a quantized re-export), the panel prefills it automatically for a run
   started from a reviewed managed recipe.
2. Use the optional hardware fields only if detection was incomplete. The GPU
   memory field only applies to a GPU run and is hidden for a CPU run, no GPU
   performed inference, so there is nothing to fill in. A GPU memory value can
   fill unknown VRAM for one detected vendor/model class; it cannot invent a
   GPU or replace already-detected VRAM. The CLI enforces these rules.
3. Click **Identify model file** to record the identity of the weights the run
   used: a `sha256-manifest:` fingerprint of the GGUF file (all shards, for a
   split file), verified against the file recorded at run start, or chosen and
   confirmed by you for a reopened run. See
   [model file identity](user-guide.md#g-model-file-identity).
4. Click **Package & validate**. The panel invokes the CLI packager, then the
   repository's validator for schema, model catalog, revisions, artifact paths/hashes,
   and duplicates against the local registry. Failures remain local and are displayed.
5. Click **Review submission files**. Inspect the normalized result and PR description.
   Download the complete ZIP to inspect all five files, including logs and raw output.
6. Select **OmoBench community registry**, then **Sign in with GitHub**. Enter
   this panel's one-time code at GitHub. A configured build requests `public_repo`,
   which grants public-repository write access, not private-repository access.
   The credential remains in the panel process's memory. Review again after
   signing in: the preview names your account, public upload fork, and upstream
   `Xatiiv/omobench` PR destination. No Git checkout or `gh` installation is needed.
   See [community submissions](community-submissions.md) for availability,
   permission details and review/retry behavior.
   The **Checkout origin** option preserves the previous `gh auth login` route
   for developers with write access to that remote. It opens the PR in that
   repository, not automatically upstream.
7. After reviewing the bundle and confirming inference on the declared device
   and absence of secrets, click **Create pull request**. This uploads only the
   five standard evidence files
   into a new `submit/<run-id>` branch and opens a PR against the repository's default
   branch. It does not stage local changes, switch branches, or push to `main`.
8. Review the PR's CI checks and follow the normal maintainer review process. The
   panel never merges a PR. Evidence is uploaded at submission time and is visible
   according to the repository's visibility, even before merge. The registry site
   includes it after merge and a successful build containing that commit.

The complete bundle is `result.json`, `guidellm-benchmarks.json.gz`,
`guidellm.stdout.log`, `environment.json`, and `reproduce.md`. File hashes shown in
the preview describe the bytes that will be uploaded. If the files change after
review, submission is rejected until they are reviewed again. Existing remote
branches or bundle directories are never overwritten.

Without sign-in, **Download complete bundle** still works. Unzip
it into your fork/check-out and follow [the manual submission workflow](submission-bundle.md).
The community route creates a public fork only after explicit submission confirmation.
Neither route uploads repository source changes.
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
  commit, or starts a community submission, repackaging that job is blocked to preserve the evidence being submitted.
- The panel binds only to `127.0.0.1`. Each launch creates a new connection token,
  checked on API requests along with the request host and origin. The token is
  placed in the URL fragment and browser session storage, not submission evidence.
  Do not expose the panel through a public tunnel or share its connection link.

Useful launch options:

```bash
omobench ui --port 8766       # If the default port is occupied
omobench ui --no-browser     # Print the connection URL without opening a browser
```

The panel accepts only loopback model endpoints without authentication headers
or redirects. Run the panel and model server in the same OS/WSL environment, or,
for a server on Windows measured from WSL, use the Windows bridge, which needs a
panel started by the companion. Model-server lifecycle management is not part of
the panel.
