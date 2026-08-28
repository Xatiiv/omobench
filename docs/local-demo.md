# Local comparison demo

`app/data/demo-results.ts` contains clearly labeled illustrative rows for
the local comparison table. They let developers exercise the presentation layer
before any machine runs a benchmark.

The comparison preview can be filtered by model, hardware, framework, and
profile. Filtering runs only in the browser and changes neither the data file
nor the benchmark registry.

Each row can open a local run-record preview. That page explains the metadata
and evidence bundle required from a real run, while deliberately keeping those
fields unfilled for the illustrative data.

The rows do not live under `results/`, do not validate as submitted evidence,
and must never be moved into the public registry. Real rows appear only after a
pinned GuideLLM execution has produced its raw evidence bundle and normalized
`result.json`.
