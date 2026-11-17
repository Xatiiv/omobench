# OmoBench

> Measure how fast open-weight language models run on your own hardware, and
> publish the evidence so others can check it.

OmoBench connects to a model server you already run, such as LM Studio,
llama.cpp, vLLM or SGLang. It sends a fixed, repeatable chat workload and
records how quickly the model responds. Every run keeps its complete evidence.
You can keep a result to yourself, or submit it to the public registry at
**<https://www.omobench.org>**, where results can be filtered by CPU, graphics
card, memory, model and workload.

It is useful if you want to know:

- how a model will run on hardware you have, or are thinking of buying;
- how different models, quantizations or settings compare on one machine;
- how your setup compares with other people's, with evidence you can inspect.

## Get started

**Windows with LM Studio** is the simplest first setup:

1. Download the installer from **<https://www.omobench.org/run>**
   (**Download for Windows**). The page shows the current version and its
   SHA-256 checksum. The installer needs WSL2 and checks for it.
2. In LM Studio, load a model and turn on the API server.
3. Open OmoBench, press **Connect**, confirm where the model runs, and press
   **Run standard test**.
4. Review the result. Save it locally, or package it and open a pull request
   to publish it.

The **[user guide](docs/user-guide.md)** walks through every step, including
the tokenizer, hardware, comparing results and publishing.

Other setups:

- **Linux, macOS or WSL2 from source:** [install from a checkout](docs/user-guide.md#linux-macos-or-windows-without-the-installer)
  and [setup details](docs/quickstart.md).
- **Other model servers:** any server with an OpenAI-compatible, streaming
  `/v1/chat/completions` endpoint on the same computer. See
  [starting a model server](docs/model-server.md).
- **Something went wrong?** [Troubleshooting in the user guide](docs/user-guide.md#i-troubleshooting)
  and [more troubleshooting](docs/troubleshooting.md).

## Requirements

- The benchmark runner needs **Linux, macOS, or WSL2 on Windows**. It cannot run
  in native Windows Python. The Windows installer sets up WSL2 for you, apart
  from installing WSL itself (one administrator command and a restart) and,
  sometimes, Python's `venv` package inside Linux.
- Windows installer: 64-bit Windows 10 version 2004 or later, or Windows 11.
  No Git, Node.js or source checkout is needed.
- From source: Python 3.10 or newer (3.12 is what the project tests).
- A model server on the same computer. With the installer, a server running
  on Windows is reached through the companion's Windows bridge.
- CPU-only, GPU-only and split CPU/GPU execution can all be measured. GPU
  details are read for NVIDIA, AMD, Intel and others where the system reports
  them; this does not guarantee your runtime supports your GPU.

## What is measured, and what is not

Measured, for a fixed workload:

- **throughput**: output and total tokens per second, and requests per second;
- **latency**: time to first token and the gap between tokens, as median (p50)
  and slow-end (p95) values;
- **failures**: requests that failed or did not finish.

Not measured: answer quality, accuracy or safety, and general CPU or GPU
performance. A fast model is not a better model. Results describe a complete
setup (model, quantization, runtime, settings and hardware together), not one
component on its own.

Workloads are versioned [benchmark profiles](benchmarks/profiles/). Start with
`chat-small-v2`, the panel's **Standard chat**.

## Evidence and publication

Each run produces an **evidence bundle**: the normalized `result.json`, the
benchmark runner's raw report and log, the environment description, and
reproduction notes. It records the model, its exact file identity, the runtime,
the hardware and how each fact was established: detected, reported by the
server, or declared by you. Local paths, addresses and credentials are replaced
with placeholders before anything is written.

Nothing is uploaded by running or packaging. Publishing is optional: the panel
signs in to GitHub, shows you every file, and opens a pull request when you
confirm. A pull request is not a published result. It is checked automatically,
reviewed by a maintainer, merged, and appears on the website after the next
build. See [publishing in the user guide](docs/user-guide.md#h-review-package-and-publish).

Any eligible model can be published, not only those on the curated list in
[`catalog/models/`](catalog/models/). See
[which models may be submitted](docs/model-eligibility.md).

## Documentation

For people running benchmarks:

| Guide | Covers |
| --- | --- |
| [User guide](docs/user-guide.md) | The complete journey, from installation to publication |
| [Windows installer](docs/windows-installer.md) | What the installer does, where files go, updating and uninstalling |
| [Windows companion](docs/windows-companion.md) | The program that opens the panel and provides the Windows bridge |
| [Model servers](docs/model-server.md) | Starting llama.cpp and other servers, model identifiers |
| [Troubleshooting](docs/troubleshooting.md) | Setup and runtime problems in more depth |
| [Comparing results](docs/comparing-results.md) | How the Compare page decides what is comparable |
| [Model eligibility](docs/model-eligibility.md) | Which models may be published, and licences |

Reference and contributor documentation:

| Guide | Covers |
| --- | --- |
| [Setup from source](docs/quickstart.md) | Checkouts, Python environments, updates and direct CLI use |
| [Panel reference](docs/browser-control.md) | Every panel control, storage and recovery |
| [Submission bundle](docs/submission-bundle.md) | Bundle contents, CLI packaging and manual pull requests |
| [Community submissions](docs/community-submissions.md) | GitHub sign-in, forks and permissions |
| [Provenance](docs/provenance.md) | Serving hardware, transport and identity records |
| [Guided workflow](docs/guided-workflow.md) | The run, package and submit checks |
| [Bounded execution](docs/bounded-execution.md) | Time limits, interruptions and sleep |
| [Managed CPU setup](docs/guided-launcher.md) | Optional `omobench prepare`/`launch` commands for one reviewed CPU model |
| [AGENTS.md](AGENTS.md) | Project rules for contributors and coding agents |

## Develop OmoBench

Benchmark users do not need any of this.

The website (`app/`) is built with vinext and Vite and needs Node.js 22.13 or
later:

```bash
npm ci
npm run dev      # local development server
npm test         # build, then run the website tests
npm run lint
```

The CLI and panel (`cli/`) are Python:

```bash
python -m pip install -e './cli[ui,runner]'
python -m unittest discover -s cli/tests -v
python -m unittest discover -s tools/tests -v
python tools/validate_results.py
```

Published evidence lives in `results/` and is append-only. Read
[AGENTS.md](AGENTS.md) before changing the repository.

## Licence

OmoBench is licensed under the [Apache License 2.0](LICENSE), with attribution in
[NOTICE](NOTICE). Some included material keeps its own terms; see
[third-party notices](THIRD_PARTY_NOTICES.md). Models, model servers and other
runtimes have their own licences, which OmoBench's licence does not cover.
