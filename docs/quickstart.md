# OmoBench local setup

This is the first developer setup, before running a benchmark.

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
benchmark-intent files. Run an actual GuideLLM benchmark from a supported Linux
or macOS environment; on a Windows computer, use WSL2 or a separate Linux
benchmark host for the later `--execute` step.

The next build slice converts the raw runner output to the result schema and
creates the GitHub pull-request submission bundle.

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
