# OmoBench user guide

This guide takes you from installing OmoBench to your first benchmark result,
and then to comparing and, if you choose, publishing it. It describes the
current OmoBench panel and uses its exact button labels.

The main example is **Windows with LM Studio**, because that is the most common
first setup. Linux, macOS and other model servers are covered where they differ.

**Contents**

- [What OmoBench does](#what-omobench-does)
- [A. Install and update](#a-install-and-update)
- [B. Prepare LM Studio and connect](#b-prepare-lm-studio-and-connect)
- [C. Model and tokenizer](#c-model-and-tokenizer)
- [D. Hardware](#d-hardware)
- [E. Run a benchmark](#e-run-a-benchmark)
- [F. Read and compare results](#f-read-and-compare-results)
- [G. Model file identity](#g-model-file-identity)
- [H. Review, package and publish](#h-review-package-and-publish)
- [I. Troubleshooting](#i-troubleshooting)
- [J. Model eligibility and licences](#j-model-eligibility-and-licences)
- [Words used in this guide](#words-used-in-this-guide)

## What OmoBench does

OmoBench measures how fast a language model runs on your own hardware. You run
the model yourself in a **model server** such as LM Studio, llama.cpp, vLLM or
SGLang. OmoBench sends that server a fixed, repeatable set of chat requests,
called a **benchmark profile**, and records how quickly the answers come back.

It measures **inference performance**: how many tokens per second the model
produces, how long you wait for the first token, the gaps between tokens, and
whether any requests failed. It does **not** measure how good, correct or safe
the answers are, and it is not a general CPU or GPU benchmark. A fast result
says nothing about answer quality.

Every run is saved on your computer with its full evidence. You can keep it to
yourself, or package it as an **evidence bundle** and submit it to the public
registry at <https://www.omobench.org> through a GitHub pull request.

OmoBench does not download or start models for you in the panel. It does not
change your model server's settings, and it cannot prove on its own which
device did the work. You tell it, and it records what you told it as your
declaration.

## A. Install and update

### What runs where

On Windows, OmoBench is split across three places. Knowing which is which
helps when something goes wrong.

| Part | Where it runs | What it does |
| --- | --- | --- |
| LM Studio (your model server) | Windows | Loads the model and answers requests |
| OmoBench companion | Windows | Starts and stops the panel, and relays requests from WSL to LM Studio (the "Windows bridge") |
| OmoBench panel and benchmark runner | WSL2 (Linux inside Windows) | Checks the server, runs the benchmark, packages evidence |
| The panel page | Your web browser | The buttons and forms you use; it talks only to the panel on `127.0.0.1` |

**WSL2** (Windows Subsystem for Linux) runs a Linux system inside Windows. The
benchmark runner needs Linux because one of its dependencies (`uvloop`) does not
work on native Windows.

### Windows: what you need

- 64-bit Windows 10 version 2004 (build 19041) or later, or Windows 11.
- WSL2 with a Linux distribution such as Ubuntu. The installer checks and tells
  you what to do if it is missing.
- `python3` and `python3-venv` inside that distribution. The installer checks
  these too and gives you the exact command when they are missing, because
  installing them needs your Linux password.
- An internet connection the first time, to install Python packages.

You do **not** need Node.js, Git, or a copy of the OmoBench source code.

### Windows: install

1. Open <https://www.omobench.org/run> and choose **Download for Windows**. The
   page shows the current version, the source revision and the SHA-256
   checksum of the file.
2. Optionally check the download. In PowerShell, in your Downloads folder, run
   `Get-FileHash .\OmoBenchSetup.exe -Algorithm SHA256` and compare the result
   with the checksum on that page or on the
   [release page](https://github.com/Xatiiv/omobench/releases).
3. Run `OmoBenchSetup.exe`. The current builds are **not code-signed**, so
   Windows SmartScreen reports an unknown publisher. Choose **More info** and
   then **Run anyway** only if the checksum matched. The installer's first
   screen shows its version, source revision and signing status.
4. Press **Install**. It installs for your Windows account only and needs no
   administrator rights.
5. The installer then looks for an existing OmoBench environment:
   - If none is found, choose **Prepare it for me**. It unpacks the OmoBench
     source it carries and builds a private Python environment inside WSL. This
     downloads Python packages, so it can take a few minutes.
   - If WSL is missing, follow its instructions (an administrator
     `wsl --install`, then a restart), then press **Check again**.
   - If `python3` or `python3-venv` is missing, run the command it shows inside
     Ubuntu, then press **Try again**.
6. When the check passes, choose **Open OmoBench**.

More detail, including where files are stored and how to uninstall, is in
[Installing OmoBench on Windows](windows-installer.md).

### Opening the panel later

Use any of these. They all start the same panel:

- the **OmoBench** entry in the Start menu, then **Open panel** in the
  companion window;
- **Open OmoBench** on <https://www.omobench.org/run>, which opens an
  `omobench://open` link on your computer (your browser may ask first);
- any `omobench://open` shortcut.

The companion window shows whether the panel is starting, ready or failed, and
has **Open panel**, **Stop** and **Setup…** buttons. The website cannot see
whether the panel started; check the companion window.

### Updating

Download and run the newer `OmoBenchSetup.exe` from the same page. If the
companion is open, the installer offers to close it first.

If the installer prepared your environment, it offers **Upgrade to
\<revision\>** or **Keep current**. Upgrading builds the new version beside the
old one and switches over only when the new one passes its check. Your earlier
runs stay in the old folder. When the upgrade finishes, choose **Copy *N*
previous run(s)** to see them in the new panel. Nothing is copied unless you
ask, and nothing is deleted.

If you pointed the companion at your own checkout, the installer leaves it
alone; update that checkout yourself.

### Linux, macOS, or Windows without the installer

You can run the same panel from a source checkout on Linux, macOS or WSL2,
with Python 3.10 or newer (3.12 is what the project tests with):

```bash
git clone https://github.com/Xatiiv/omobench.git
cd omobench
python3 -m venv ~/.venvs/omobench-runner
source ~/.venvs/omobench-runner/bin/activate
python -m pip install -e './cli[ui,runner]'
omobench ui
```

Open the full address `omobench ui` prints, including the `#token=…` part.
Keep that terminal open while you use the panel. Full instructions, including
updates, are in [Install, update, and run OmoBench](quickstart.md).

On a Mac, the model server and the panel run on the same machine, so you do not
need the Windows bridge.

## B. Prepare LM Studio and connect

### Download and load a model in LM Studio

These steps follow LM Studio's own documentation
([downloading models](https://lmstudio.ai/docs/app/basics/download-model),
[running the server](https://lmstudio.ai/docs/developer/core/server)). LM
Studio's interface changes between versions, so check its documentation if a
label differs.

1. Open the **Discover** tab in LM Studio and download a model. For a first
   run, pick a model that fits comfortably in your graphics card's memory or,
   for CPU only, in your system memory.
2. Load the model. In the **Chat** tab, open the model loader and choose the
   model. Choose its load settings here, such as how much of the model goes on
   the GPU and the context length, because these affect speed. Note them down;
   you will record them later.
3. Start the server. In the **Developer** tab, turn on the **Start server**
   switch. You can also start it from a terminal with `lms server start`, and
   load a model with `lms load <model>`.

LM Studio's examples use port `1234`, which gives the address
`http://127.0.0.1:1234`. Use whatever port your server shows. Leave **Serve on
Local Network** off; OmoBench only connects to the same computer.

LM Studio can also load a model automatically when the first request asks for
it ("Just in Time" loading). Load the model yourself before benchmarking, so
you know which settings it ran with.

### Two different addresses

Two programs listen on your computer, and they have different addresses:

- **The panel**: something like `http://127.0.0.1:8765/#token=…`. This is the
  page in your browser. The companion opens it for you.
- **The model server**: something like `http://127.0.0.1:1234` for LM Studio.
  This is what you type into **Server address** in the panel.

Do not type the panel's address into **Server address**.

### When you need the Windows bridge

The panel runs inside WSL. A model server running on Windows, such as LM
Studio, listens on Windows' own `127.0.0.1`, which WSL usually cannot reach
directly. The **Windows bridge** carries the requests across, through the
companion.

Tick **The server runs on Windows and this panel runs in WSL. Connect through
the Windows bridge.** when:

- you installed OmoBench with the Windows installer, and
- your model server runs on Windows (LM Studio for Windows, for example).

Leave it unticked when the model server runs in the same place as the panel:
both in WSL, both on Linux, or both on a Mac.

The bridge adds a small delay to every request. That delay is included in every
measured number and is never subtracted. Bridged results say so, and they are
only compared with other bridged results.

### Connect

1. In the panel, under **01 Connect your model server**, enter the **Server
   address**, for example `http://127.0.0.1:1234`.
2. Tick the bridge option if it applies (see above).
3. Press **Connect**. OmoBench asks the server which models it offers.
   - If it offers one, that model is selected.
   - If it offers several, choose the one you loaded under **Which model?**
   - If the server does not list its models, press **Enter a model ID
     instead** and type the **Server model identifier** exactly as the server
     expects it.

The **served model identifier** is the name your server answers to, for
example `qwen2.5-coder-14b-instruct` in LM Studio. It is not necessarily the
model's official name.

**Check this server** sends one tiny streaming request to confirm the server
answers properly. The request is not part of the benchmark results.

## C. Model and tokenizer

### Three names for one model

A model can appear under several names, and OmoBench keeps them apart:

| Name | Example | Used for |
| --- | --- | --- |
| Served model identifier | `qwen2.5-coder-14b-instruct` | What every benchmark request sends to your server |
| Canonical model name | `Qwen/Qwen2.5-Coder-14B-Instruct` | The model's real identity, recorded when you publish |
| Tokenizer source | `Qwen/Qwen2.5-Coder-14B-Instruct` | Where the tokenizer comes from (see below) |

These examples show how the names can differ. They are not measurements.

### Why a tokenizer is needed

A **tokenizer** splits text into the tokens a model works with. OmoBench needs
the model's tokenizer to build prompts of exactly the right length and to count
the tokens in the answers. The tokenizer usually comes from the model's
repository on Hugging Face.

The served identifier often is not a Hugging Face repository. For example,
LM Studio serves `qwen2.5-coder-14b-instruct`, but there is no Hugging Face
repository with that name. A run that tried to use it as a tokenizer source
would fail before sending a single request. OmoBench therefore finds the
tokenizer separately, and the served identifier stays exactly what requests
send.

### How OmoBench finds the tokenizer

After **Connect**, OmoBench looks for the tokenizer in this order:

1. **A choice you already confirmed** for the same model, name and
   quantization on this computer. It is reused.
2. **The model file itself.** GGUF model files usually record which repository
   they were converted from. If you set the **Model folder** (see
   [section G](#g-model-file-identity)), OmoBench reads that record. When it
   names exactly one repository, it is used automatically.
3. **The served identifier**, if it already is a Hugging Face repository name.
4. **The name alone.** OmoBench may suggest repositories with a matching name,
   from its curated list or from a Hugging Face name search. It never uses one
   of these without asking. You see **Which repository is this model from?**;
   pick the right one and press **Use this tokenizer**.

When several repositories are possible, OmoBench asks rather than guessing. When
it finds nothing, it says so and opens **Advanced: tokenizer** for you.

### Checking and downloading the tokenizer

Before the run is allowed, OmoBench loads the tokenizer inside the benchmark
runner and encodes a sample sentence. It tries the copy already on your
computer first, so a tokenizer you used before works offline.

- **Tokenizer ready: …** means it loaded. **Run standard test** can now be
  used.
- **… is not on this machine yet** usually means it has not been downloaded.
  Press **Download the tokenizer files**. This fetches only the small tokenizer
  files from Hugging Face, never the model weights. Afterwards it works offline.
  In the current version the panel shows this same message for some other
  loading failures too, such as a repository that publishes only GGUF weight
  files and no tokenizer, a tokenizer that would need to run code from its
  repository (which OmoBench refuses), or a cached tokenizer that fails to
  load. Downloading cannot fix those. If the download completes and the
  tokenizer still does not load, the message then shows the actual error; use
  **Advanced: tokenizer** to choose a different source.

**Run standard test** stays disabled until the tokenizer is ready for the model
currently selected. If you choose a different model, the check runs again.

### Advanced: tokenizer

Open **Advanced: tokenizer** only to override what was found, for example when
nothing was found, or when you know the model comes from a different
repository. Enter a Hugging Face repository such as
`Qwen/Qwen2.5-Coder-14B-Instruct`, or a local folder containing tokenizer
files, and press **Check and load this tokenizer**.

Choose the repository the model was actually built from. A different tokenizer
counts tokens differently, so it changes the measurements. OmoBench never runs
code shipped inside a tokenizer repository; a tokenizer that needs it is
refused.

### What the tokenizer does not prove

Finding a tokenizer does not show which weights your server has loaded. Two
different models can share a tokenizer. Model identity is recorded separately;
see [section G](#g-model-file-identity).

## D. Hardware

### Words used

- **CPU**: the processor. OmoBench records its name, physical cores and threads.
- **RAM**: system memory. *Installed* is what is fitted in the computer;
  *usable* is what the operating system reports.
- **GPU**: graphics card. **VRAM** is the memory on the card. OmoBench records
  it per card, so two 8 GB cards are recorded as two 8 GB cards, not 16 GB.
- **Execution**: where the model runs. **CPU only**, **GPU only**, or **split**
  between the card and the CPU (some layers on each).

### Serving hardware and the benchmark runner

Two machines can be involved, and OmoBench keeps them apart:

- The **serving hardware** is the computer that runs the model. With LM Studio
  on Windows, that is your Windows machine.
- The **benchmark runner** is where OmoBench sends requests from. With the
  installer, that is WSL.

The **This machine** list in the panel's sidebar describes the runner's
environment. Inside WSL, its memory figure is what Windows gave WSL, not the
memory in your computer. It is never used as the serving hardware.

### Confirm the serving hardware

Under **02 Confirm the serving hardware**:

1. Press **Read the serving hardware**. With the Windows bridge, the companion
   reads the Windows machine. Otherwise the panel reads the machine it runs
   on. You see the CPU, cores and threads, installed and usable memory, and
   each graphics card with its dedicated memory.
2. Under **Where does the server run the model?**, choose **On the graphics
   card (GPU)**, **Split between the card and the CPU**, or **On the CPU
   only**.
3. If you chose a card, tick every card under **Which cards run the model?**.
   A card that is installed but not ticked is recorded as present, not as
   running the model.
4. For a split, describe it in **How the weights were split** as your server
   reports it, for example "32 of 49 layers on GPU".
5. Tick **I have checked in my server (its logs or hardware panel) that it
   really runs the model where I said.**

### Detected is not the same as used

Finding a graphics card shows that the card exists, not that it ran the model.
Choosing **On the graphics card (GPU)** in OmoBench **does not change your model
server's settings**. Set GPU offload in LM Studio, then tell OmoBench what you
set. For a CPU-only run, turn GPU offload off in your server first.

### Missing or unknown information

Some facts cannot always be read. For example, some drivers do not report a
card's memory, or a card is not visible from WSL. OmoBench then shows **Not
recorded** or "VRAM not reported". It never treats unknown as zero. During
Review, **Hardware details, if detection is incomplete** lets you enter a
card's memory or the CPU description, and the serving machine's details can be
edited under **All details**. What you enter is recorded as your declaration,
not as detected.

## E. Run a benchmark

### The standard test

Under **03 Run a standard test**, the default is **Standard chat**, which is
the `chat-small-v2` profile. Use it for your first run: it is the workload most
published results use, so your result can be compared with them.

What `chat-small-v2` does, in plain terms:

| | |
| --- | --- |
| Each request | A synthetic prompt of about 1,024 tokens, asking for 256 tokens back, with the answer streamed |
| At the same time | 1 request (concurrency 1: one user chatting) |
| Warm-up | 5 requests first, not measured, so loading and caches do not distort the results |
| Measured | The next 50 requests |
| Cool-down | 5 final requests, not measured |
| Time limit | 15 minutes for the whole run; if it is reached, the run stops there |
| To be publishable | At least 40 successful requests, and an error rate of at most 1% |

With 50 measured requests, one failed request is already a 2% error rate, so a
publishable run needs every measured request to succeed. A model can stop
before 256 tokens; requested output length is not guaranteed.

Other profiles are under **More tests**, with their details under **Benchmark
profile details**:

- `chat-small-extended-v1`: the same workload with a longer time limit, for
  slower hardware.
- `chat-long-v1`: about 8,192 input tokens and 512 output tokens. Your server's
  context length must fit both, plus some overhead.
- `throughput-v1`: 16 requests at the same time, to measure how much the server
  delivers under load.
- `chat-small-v1`: kept so earlier results can be reproduced. Its results are
  not comparable with `chat-small-v2`.

How long a run takes depends entirely on your hardware and model. OmoBench does
not predict it.

### Before you start

**Before you start** lists every check, grouped by what it would stop: running
the benchmark, packaging the evidence, or submitting to the public registry.
Only problems that would actually stop the run disable it. A model that is not
on the curated list, for example, never stops a run.

### During the run

Press **Run standard test**. The dashboard shows progress, live throughput,
first-token latency and the log.

- Keep the computer awake and plugged in. If it sleeps, the run is marked as
  interrupted and cannot be published, because suspended time distorts the
  measurements.
- Avoid other heavy work on the computer, such as games, video exports or
  another model, while the benchmark runs.
- **Cancel benchmark** stops the benchmark. Your model server keeps running.

### Saved runs

Every run is saved locally and listed under **Recent runs** in the sidebar,
including cancelled and failed ones. Press **+ New** to start another.
**Save local report** downloads a summary of the measurements for your own
records; it needs no account.

With the Windows installer, runs are stored inside the OmoBench source folder,
under `%LOCALAPPDATA%\OmoBench\source\<revision>\.omobench-runs`. With a
checkout, they are under `.omobench-runs/` in the checkout. Nothing is
uploaded.

## F. Read and compare results

### The measurements

| Measurement | Meaning | Better |
| --- | --- | --- |
| Output tokens/sec | Tokens of answer produced per second | Higher |
| Total tokens/sec | Prompt and answer tokens processed per second | Higher |
| Time to first token (TTFT) | How long you wait before the answer starts | Lower |
| Inter-token latency (ITL) | The gap between one answer token and the next | Lower |
| Requests/sec | Completed requests per second | Higher |
| Error rate | Measured requests that failed or did not finish | Lower |

**p50** is the median: half of the requests were faster. **p95** is the slow
end: 95% of requests were at least this fast, so it shows the occasional slow
response.

With one request at a time (`chat-small-v2`), output tokens per second is close
to the speed one user sees. With several at once (`throughput-v1`), it is the
server's combined output across all requests; each individual request may be
slower.

### What makes results comparable

Speed depends on much more than hardware. Results are only directly comparable
when these match, or when the difference is the thing you are asking about:

- the **model** and its exact weights;
- the **quantization** (for example `Q4_K_M`): a smaller format uses less
  memory and is often faster, but not always; how much it helps, or whether it
  helps at all, depends on the hardware, the runtime and its settings. It is
  also a different set of weights;
- the **runtime** (for example llama.cpp inside LM Studio) and its version;
- the **benchmark profile**;
- the **context length** and other server settings;
- **concurrency**, which is fixed by the profile;
- **CPU/GPU placement**: where the weights run changes speed a great deal. A
  split run is often slower than the same model fully on a card, but the size of
  the difference, and whether a split is the only way the model fits at all,
  depends on the hardware and configuration;
- the **transport**: bridged results include the bridge's delay.

### Comparing on the website

On <https://www.omobench.org>, the **Benchmark registry** has filters:

- **Serving hardware**: CPU, Inference GPU, VRAM per inference GPU, System
  RAM, and Execution.
- **Model and workload**: Model, Quantization, Runtime, and Profile.

To see **one model on different hardware**, choose the Model, Quantization and
Profile, and leave the hardware filters open. To see **different models on one
setup**, choose the CPU, Inference GPU and Profile, and leave the model open.

Filters select results; they do not make every selected result comparable.
When the rows you see differ in something like quantization or context length,
the page names it. For a careful side-by-side view, open **Compare**. Choose a
reference run and answer **What are you comparing?**, for example
"Different hardware, same everything else". The page then shows only the runs
that differ in the way that question allows, and lists the others under runs
excluded from direct comparison with the reason.

You can also open your own `result.json` on the Compare page with **Open your
result.json**. It stays in your browser and is not uploaded.

### Repeat runs

One run is one sample. Running the same setup two or three times shows how much
the numbers vary from run to run. A difference smaller than that variation
says little.

## G. Model file identity

### Why it matters

A published result says which exact weights were measured. A model name is not
enough: the same name can point to different files over time, and different
quantizations are different files.

There are two kinds of identity, and they are not interchangeable:

- A **repository revision** is a commit in the model's repository on Hugging
  Face, a long hexadecimal ID. It identifies what the repository contained at
  that moment.
- A **file hash** (SHA-256) is a fingerprint calculated from the file itself.
  It identifies the exact bytes on your disk.

Do not record the repository's current latest revision ("HEAD", `main`) as the
revision of a file you downloaded earlier. The repository may have changed
since, so that would be a claim nobody checked. When you are unsure, use the
file fingerprint.

### The model folder

Under **Model folder** in the Connect section, enter the folder your server
loads model files from and press **Save folder**. A Windows path such as
`D:\OmoBench\lmstudio-models` works. For LM Studio, this is the models directory
shown in its **My Models** tab.

OmoBench reads model file headers there to help find the tokenizer, and records
which file a run used when the run starts. It does not change, move or upload
anything in that folder.

### Identify model file

After the run, in **Review & share**, press **Identify model file**. OmoBench
calculates a fingerprint of the model file. For a model split across several
files ("sharded" GGUF files, named like `…-00001-of-00002.gguf`), it
fingerprints every part and combines them into one `sha256-manifest:`
identity. Hashing a large file takes a while; it happens only after the run,
never during it.

- **Model file verified** means the file recorded when the run started is still
  the same file (same size and modification time), and its fingerprint is now
  recorded.
- If the file was not recorded at the start, or has changed since, OmoBench
  asks you to choose the file. Tick **This is the file this run used. It was
  found today, so it is recorded as chosen by you, not as verified.** and press
  **Choose model file**. This happens, for example, when you reopen an older
  run. The result then says the file was chosen by you, which is honest and
  still publishable.

The fingerprint and the file names are shown under **Details**. Neither the
tokenizer revision nor the repository's current revision is ever used as the
weights' identity.

## H. Review, package and publish

### Local results and evidence bundles

A finished run is already saved on your computer, and you can stop there.

To publish it, OmoBench packages an **evidence bundle**: five files that let
anyone check the result.

| File | Contents |
| --- | --- |
| `result.json` | The normalized result: model, runtime, hardware, profile and measurements |
| `guidellm-benchmarks.json.gz` | The benchmark runner's complete raw report |
| `guidellm.stdout.log` | The runner's log |
| `environment.json` | The environment the benchmark ran in |
| `reproduce.md` | How to reproduce the run |

A sixth file, `model-proposal.json`, is added only if you ask for the model to
be added to the curated list.

### Review & share

After a completed run, **05 Review & share** shows the result at a glance.

1. Press **Detect details**. OmoBench fills in what it can read, such as the
   runtime and quantization reported by LM Studio, and names the source of
   each fact. It keeps anything you already typed.
2. Open **Model identity and licence** and enter the **Canonical model ID**,
   for example `Qwen/Qwen2.5-Coder-14B-Instruct`. **Suggest a catalog model**
   offers matches from the curated list. If the model is not on that list, a
   **Your own model** section asks for its licence, licence URL, weights
   source and weights revision. See [section J](#j-model-eligibility-and-licences).
3. Fill in anything listed under **Still needed**. Record the server settings
   you used, such as the context length and how many layers were on the GPU,
   in the fields provided. Do not include passwords or API keys.
4. Press **Identify model file** (see [section G](#g-model-file-identity)).
5. **Check publication requirements** lists anything still missing.
6. Press **Package & validate**. OmoBench builds the bundle and checks it with
   the same validator the public registry uses.

A run that did not meet the profile's thresholds, for example because a request
failed, can be packaged and kept, but the registry will not accept it.

### Submitting through GitHub

Under **Sharing needs GitHub sign-in**:

1. Leave **Submission destination** at **OmoBench community registry**.
2. Press **Sign in with GitHub**. The panel shows a one-time code. Open
   **Enter this code on GitHub** (`https://github.com/login/device`) and enter
   it. Only enter a code your own panel showed you. Signing in uploads nothing.
3. Press **Review submission files**. You see the GitHub account, where the
   files will go, each file's size and SHA-256, the normalized result and the
   pull request text.
4. Read it. **Download complete bundle** saves a ZIP of all files so you can
   inspect the raw evidence.
5. Tick the confirmation and press **Create pull request**. OmoBench creates
   or reuses your public fork of OmoBench on GitHub, uploads the five files to
   a new branch there, and opens a pull request.

The permission requested is GitHub's `public_repo`, which allows writing to
public repositories on your account. The sign-in is kept only in the panel's
memory, for at most eight hours, and is forgotten when you press **Sign out**
or stop the panel. **Permissions and privacy** in the panel links to where you
can revoke it on GitHub.

### Without GitHub sign-in

- **Download complete bundle** works without signing in. You can submit the
  ZIP's files yourself with an ordinary pull request; see
  [the manual pull request workflow](submission-bundle.md#manual-pull-request-workflow).
- **Checkout origin, existing GitHub CLI authentication** is for people who
  work in their own Git checkout and already use the GitHub CLI (`gh`).

### When data leaves your computer

Nothing is uploaded by running, saving, packaging or validating. Data leaves
your computer only in these cases:

| When | What is sent | To |
| --- | --- | --- |
| Connect, when the model is identified only by name | The model's name, as a search | Hugging Face |
| **Download the tokenizer files** | A request for that repository's tokenizer files | Hugging Face |
| **Sign in with GitHub** and **Review submission files** | Your sign-in, and reads of your account and forks | GitHub |
| **Create pull request** | The five (or six) evidence files and the pull request text | GitHub, publicly |
| Installing or upgrading with the Windows installer | Requests for Python packages | PyPI and the PyTorch package index |

The benchmark itself talks only to your model server.

### What becomes public

Everything in the evidence bundle becomes public as soon as the pull request is
created, before any review: the measurements, the model and runtime details,
your hardware description, the operating system, your GitHub account name,
and the submitter name you entered.

For runs made with the current version, OmoBench redacts some things as the
evidence is written: the server address it measured and other loopback
addresses become `<LOCAL_ENDPOINT>`, fields such as `api_key` or `access_token`
and `Bearer` headers become `<REDACTED_CREDENTIAL>`, and the run folder,
checkout and home directory become `<RUN_DIRECTORY>`, `<CHECKOUT>` and `<HOME>`.
Validation also scans for these. It does not understand everything a file may
contain: anything else you typed into a field, a model's output, or a server
setting that includes a secret in an unusual form is not recognised. Evidence
from older versions may not be redacted at all (see
[evidence that does not name you](submission-bundle.md#evidence-that-does-not-name-you)).
Open and read every file in the bundle (**Download complete bundle**) before
you confirm.

### After the pull request

Opening a pull request does **not** publish the result. Next:

1. Automatic checks run on GitHub and validate the bundle again.
2. A maintainer reviews it and may ask questions in the pull request.
3. If accepted, it is merged.
4. The website is rebuilt. Only then does the result appear on
   <https://www.omobench.org>.

The panel cannot see these steps; follow them on GitHub.

## I. Troubleshooting

Each entry is symptom → likely cause → what to do. More technical cases are in
[troubleshooting.md](troubleshooting.md) and the
[companion guide](windows-companion.md#troubleshooting).

**The installer says WSL is missing**
→ WSL2 is not installed. → Open PowerShell as administrator, run
`wsl --install`, restart Windows, create your Linux user when Ubuntu opens, then
press **Check again**.

**The installer says `python3` or `python3 -m venv` is missing**
→ Ubuntu lacks Python's virtual-environment package. → In Ubuntu, run
`sudo apt update && sudo apt install -y python3 python3-venv`, then press
**Try again**.

**Open OmoBench on the website does nothing**
→ The `omobench://` link is not registered, or the browser blocked it. → Open
OmoBench from the Start menu and use **Open panel**. In the companion,
**Setup…** can register the link again.

**Connect fails, or no models are listed**
→ The server is not running, the port is wrong, no model is loaded, or the
bridge option does not match where the server runs. → Check that LM Studio's
**Start server** switch is on and note its port. Use `http://127.0.0.1:<port>`,
not the panel's address. Tick the Windows bridge option when LM Studio runs on
Windows and OmoBench runs in WSL. If no list appears, use **Enter a model ID
instead**.

**The bridge reports an error**
→ The panel was not started by the companion, the companion is out of date, or
WSL cannot run Windows programs. → Open the panel from the Start menu entry or
**Open OmoBench**, not from a terminal. Reinstall with the current installer
if needed. OmoBench never falls back to a different server when the bridge
fails.

**The tokenizer is "not on this machine yet", or a run fails while loading the tokenizer**
→ Usually the tokenizer has not been downloaded, or the served identifier (such
as `qwen2.5-coder-14b-instruct`) was used where a Hugging Face repository is
needed. The same message currently also appears for some other loading
failures, such as a repository with no tokenizer files or one that requires
running its own code. → Press **Download the tokenizer files**. If it still
fails, read the error it then shows, enter the right repository or a local
tokenizer folder under **Advanced: tokenizer**, and press **Check and load this
tokenizer**. Do not rename the model in your server.

**"Model folder not set", or no model file is found**
→ OmoBench does not know where your server keeps its files, or the path is
wrong. → Under **Model folder**, enter the folder LM Studio uses (see its **My
Models** tab) and press **Save folder**. A Windows path such as
`D:\OmoBench\lmstudio-models` is accepted and translated for WSL. A folder
outside the Windows drives WSL can see cannot be read.

**Hardware shows "Not recorded" or "VRAM not reported"**
→ The driver did not report it, or the run was not bridged so Windows could not
be read. → During Review, enter a card's memory under **Hardware details, if
detection is incomplete**, or describe the serving machine under **All
details**. It is recorded as your declaration.

**Requests fail, or the server runs out of memory**
→ The model and its context do not fit in memory, or the context length is
shorter than the prompt plus answer. → Load a smaller model or quantization,
offload fewer layers, or raise the context length in your server. `chat-long-v1`
needs a context of well over 8,192 tokens.

**The run was interrupted or the computer slept**
→ Sleep or hibernation paused the benchmark. → Run it again with the computer
plugged in and sleep disabled. An interrupted run is kept locally but cannot be
published.

**Package & validate fails**
→ A required detail is missing, or the run did not meet the profile's
thresholds. → Read the message beside the button and **Still needed**.
Descriptive details can be added afterwards without re-running: the model's
identity and licence, runtime version, server settings, and the model file's
fingerprint while the same file is still on disk (recorded as chosen by you if
it was not recorded at run start). Evidence that was not captured while the run
happened cannot be verified afterwards: hardware read at the time, a model file
that has since changed or been deleted, or measurements from an interrupted or
failed run. Facts like these can only be added as your declaration, if at all,
and some problems need a new run. A run with failed requests cannot be made
publishable.

**Create pull request fails**
→ The GitHub sign-in expired, the fork is still being created, or the network
dropped. → Sign in again if asked, wait a minute and press **Review
submission files** again. Your evidence is never deleted, and a retry does not
create a duplicate pull request. **Download complete bundle** is always
available as a fallback.

## J. Model eligibility and licences

### Three different licences

- **OmoBench's licence** (Apache License 2.0) covers the OmoBench software only.
- **The model's licence** is set by its publisher. It governs how you may use
  the weights and whether results may be published.
- **The runtime's licence** (LM Studio, llama.cpp, and so on) is set by its
  makers.

Being able to download weights does not make a model open source or
unrestricted. Read the licence.

### Finding a model's licence

Look at the model's page on Hugging Face or its publisher's site: the model
card usually names the licence, and the repository often has a `LICENSE` file.
Some models require you to accept terms before downloading. Fine-tuned models
can have different terms from the model they were built on.

### Which models can be published

Any model can be benchmarked locally, provided your server can serve it
through a streaming, OpenAI-compatible `/v1/chat/completions` endpoint on this
computer, and OmoBench can load a matching tokenizer (from Hugging Face or a
local folder) without running code from it. To be **published**, a result must meet
the requirements in [Which models may be submitted](model-eligibility.md). In
short:

- the model has a published licence that permits use and publication of
  benchmark results;
- anybody can download the weights, accepting the publisher's terms if there
  are any;
- it is a text model, or a vision-language model running a text-only workload;
- the weights' identity is a fixed revision or a file fingerprint;
- the run used an unmodified profile, met its thresholds and was not
  interrupted.

The **curated list** (`catalog/models/`) is a recommendation of models whose
licences someone has already checked. **A model does not need to be on it.** For
a model that is not on the list, you declare its licence, licence URL and
weights source in **Your own model** during Review. You can also send a
proposal to add it to the list through **Propose this model for the catalog**,
but that is optional.

### Declared and verified

OmoBench records how each fact was established. What it read from your machine
is "detected"; what your server reported is "server-reported"; what you entered
is your declaration. The panel and the website keep these labels. A
declaration is not independently verified: OmoBench does not open licence
links or check that the weights are downloadable. Reviewers check them.

## Words used in this guide

| Term | Meaning |
| --- | --- |
| Model server | The program that runs the model and answers requests, such as LM Studio |
| Served model identifier | The name the model server answers to |
| Serving hardware | The computer the model runs on |
| Benchmark runner | The part of OmoBench that sends requests and measures them |
| Benchmark profile | A fixed, versioned workload definition, such as `chat-small-v2` |
| Tokenizer | Splits text into tokens; used to build prompts and count tokens |
| Quantization | A smaller numeric format of the weights, such as `Q4_K_M` |
| Model file identity | A fixed revision or file fingerprint identifying the exact weights |
| Evidence bundle | The five files that document one run |
| Published result | A bundle that was reviewed, merged and appears on the website |

Developer and contributor documentation, including the result schema and the
rules behind each check, is linked from the [README](../README.md).
