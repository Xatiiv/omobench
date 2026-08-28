# Result submission bundle

Each benchmark submission occupies one directory:

```text
results/<model-slug>/<YYYY-MM-DD>-<run-id>/
  result.json
  guidellm-benchmarks.json.gz
  guidellm.stdout.log
  environment.json
  reproduce.md
```

`result.json` is the normalized summary. It must validate against
`schema/benchmark-run.schema.json` and record the exact model revision, runtime
version, normalized startup arguments, driver, hardware, runner version, and
profile hash.

The remaining files are the evidence for the summary:

- `guidellm-benchmarks.json.gz` — unmodified raw GuideLLM output, compressed.
- `guidellm.stdout.log` — the runner's standard output after redaction.
- `environment.json` — detected operating system, CPU, RAM, GPU, driver, and
  package-version information.
- `reproduce.md` — exact non-secret commands needed to recreate the run.

Never commit API keys, access tokens, hostnames, serial numbers, user prompts,
model weights, or credentials. A submitter must redact those values before
creating a pull request.

## Repeats and duplicates

A `configuration_fingerprint` is a SHA-256 digest of the normalized model
revision, quantization, framework and version, startup arguments, hardware
class, driver, and profile. A `run_id` identifies one execution session.

- Same fingerprint and same raw-runner hash: accidental duplicate; rejected.
- Same fingerprint but a different raw-runner hash: valid repeat.
- Different submitter or host with the same configuration: valid replication.
- Changed model revision, quantization, runtime, driver, hardware, arguments,
  or benchmark profile: a new configuration.
