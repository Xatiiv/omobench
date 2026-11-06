// Read one result through the website's evidence summary and print it, so the
// Python one can be compared against it row for row.
//
// Two implementations of the same explanation is two chances to explain it
// differently. They exist because one runs in a Python panel and the other in
// a React page; this is what stops them drifting.
import { readFileSync } from "node:fs";
import ts from "typescript";

async function loadModule(path) {
  const { outputText } = ts.transpileModule(readFileSync(path, "utf8"), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  });
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
}

const [, , recordPath, evidencePath, resultPath] = process.argv;
const { readResultRecord } = await loadModule(recordPath);
const { summarize } = await loadModule(evidencePath);

const document = JSON.parse(readFileSync(resultPath, "utf8"));
const record = readResultRecord("parity fixture", document);
process.stdout.write(JSON.stringify(summarize(record), null, 2));
