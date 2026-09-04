# OmoBench local setup

This is the first developer setup, before running a benchmark.

## v1 scope

OmoBench v1 covers open-weight text-generation models served through an
OpenAI-compatible chat-completions endpoint. Only models listed in
`catalog/models/*.yaml` with `modality: text-generation` are eligible for
submission; see [docs/submission-bundle.md](submission-bundle.md) for the full
model-eligibility and runtime-provenance contract.

```bash
git clone git@github.com:YOUR-ORG/omobench.git
cd omobench
code .

python3 -m venv .venv
```

Activate the environment with one command for your operating system:

```bash
# macOS or Linux
source .venv/bin/activate
```

```bash
# Windows Git Bash
source .venv/Scripts/activate
```

```bash
pip install -e ./cli

omobench doctor
omobench profiles
```

`omobench doctor` reports whether the command-line runner, Git tooling, and an
NVIDIA GPU are visible to the shell. `omobench profiles` confirms that the
checkout contains the immutable benchmark definitions.

Windows is suitable for local development, website work, CLI checks, and
benchmark-intent files. OmoBench's `cli[runner]` extra pins
`guidellm[recommended]`, and that recommended dependency set includes
`uvloop`, which does not install on native Windows. This is a limitation of
OmoBench's current `cli[runner]` extra, not a claim that GuideLLM itself can
never run on Windows. On a Windows computer, use WSL2 or a separate Linux
benchmark host for the later `--execute` step.

After a successful runner execution, `omobench package` converts the raw
GuideLLM report into a local, schema-ready `result.json`. It does not publish
or submit anything.

## First runner dry run

Install the pinned runner only when you are ready to test an endpoint:

```bash
pip install -e './cli[runner]'
omobench run \
  --endpoint http://localhost:8000/v1 \
  --model Qwen/Qwen3-4B \
  --profile chat-small-v1
```

The command above is safe: it only prints the exact GuideLLM command. Add
`--execute` to run it and write a local evidence bundle under
`.omobench-runs/`, which Git ignores. It does not submit or publish anything.
