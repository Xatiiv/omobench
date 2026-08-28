# Local result registry

The website reads only normalized `result.json` files from this directory.
Keep the complete evidence bundle beside each summary:

```text
results/<model-slug>/<YYYY-MM-DD>-<run-id>/
  result.json
  guidellm-benchmarks.json.gz
  guidellm.stdout.log
  environment.json
  reproduce.md
```

After `omobench package` creates a local evidence bundle under
`.omobench-runs/`, copy the entire run directory here. Restart `npm run dev`
so the website includes the new `result.json`.

Do not place samples, hand-written metrics, secrets, or incomplete runs in
`results/`. The comparison table treats every loaded result as a genuine local
benchmark record.
