# Local comparison demo

`app/data/demo-results.ts` contains three clearly labeled illustrative rows for
the local comparison table. They let developers exercise the presentation layer
before any machine runs a benchmark.

The rows do not live under `results/`, do not validate as submitted evidence,
and must never be moved into the public registry. Real rows appear only after a
pinned GuideLLM execution has produced its raw evidence bundle and normalized
`result.json`.
