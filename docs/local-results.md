# Show a packaged result on the local website

The OmoBench website loads a packaged result from `results/`; it deliberately
does not read the temporary `.omobench-runs/` directory directly.

When a real runner execution and `omobench package` have completed, copy the
whole evidence directory into the registry layout:

```bash
mkdir -p results/qwen3-4b/2026-08-28-run-example123
cp -R .omobench-runs/run-example123/. results/qwen3-4b/2026-08-28-run-example123/
```

The destination must contain these five files:

```text
result.json
guidellm-benchmarks.json.gz
guidellm.stdout.log
environment.json
reproduce.md
```

Restart the local website after copying the directory:

```bash
npm run dev
```

The local registry section on the home page will then show the normalized
metrics from `result.json`. Selecting **View details** shows the exact model
revision, runtime, hardware, profile, configuration fingerprint, and the
hashes of the four evidence files.

This remains a local workflow. Nothing is sent to GitHub and no result becomes
public until a later submission workflow is deliberately added.
