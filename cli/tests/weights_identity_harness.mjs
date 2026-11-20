// Reads packaged result.json files with the website's own reader and
// comparison, so a test can follow a weights identity from the panel all the
// way to what the site concludes. Run by test_weights_identity_flow.py.
//
//   node weights_identity_harness.mjs <repo root> <result.json> [<result.json> ...]
//
// Prints, for each file, what the site reads; and for the first file against
// each other one, the comparison's verdict under "repeat of the same setup".
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

const [root, ...files] = process.argv.slice(2);
const require = createRequire(pathToFileURL(`${root}/package.json`).href);
const ts = require("typescript");

async function load(path) {
  const { outputText } = ts.transpileModule(readFileSync(`${root}/${path}`, "utf8"),
    { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } });
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
}
const { readResultRecord } = await load("app/lib/result-record.ts");
const { assess, weightsIdentity } = await load("app/lib/comparison.ts");

const records = files.map(path => readResultRecord(path, JSON.parse(readFileSync(path, "utf8"))));
const out = {
  records: records.map(record => ({ id: record.id, weights: record.weights, identity: weightsIdentity(record) })),
  comparisons: records.slice(1).map(other => {
    const verdict = assess(records[0], other, "exact");
    return { other: other.id, suitability: verdict.suitability, blockers: verdict.blockers, differences: verdict.differences };
  }),
};
process.stdout.write(JSON.stringify(out));
