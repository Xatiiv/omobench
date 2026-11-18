import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync, readdirSync } from "node:fs";
import ts from "typescript";

// Weights identity on the website: what each record establishes, and how the
// comparison uses it. The two published Qwen runs are read as published; every
// other record here is a LABELLED FIXTURE built from one of them by changing
// only its weights identity, and none of it is published anywhere.

async function loadModule(path) {
  const { outputText } = ts.transpileModule(readFileSync(new URL(path, import.meta.url), "utf8"), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } });
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
}
const { readResultRecord } = await loadModule("../app/lib/result-record.ts");
const { assess, weightsIdentity } = await loadModule("../app/lib/comparison.ts");
const { summarize } = await loadModule("../app/lib/evidence.ts");

const resultsDirectory = new URL("../results/", import.meta.url);
const files = readdirSync(resultsDirectory, { recursive: true }).filter(path => path.endsWith("result.json"));
const raw = Object.fromEntries(files.map(path => {
  const document = JSON.parse(readFileSync(new URL(path.replaceAll("\\", "/"), resultsDirectory), "utf8"));
  return [document.run_id, document];
}));
const published = id => readResultRecord(id, raw[id]);

const hex = character => character.repeat(64);
function fixture(label, change) {
  const document = structuredClone(raw["run-cc80dd597abb"]);
  document.run_id = `run-fixture-${label}`;
  change(document);
  return readResultRecord(`labelled fixture: ${label}`, document);
}
const hashed = (digit, provenance) => document => {
  document.model.revision = `sha256-manifest:${hex(digit)}`;
  document.model.weights = { kind: "local-files", repository: null, repository_revision: null,
    identity: "content-hash", provenance,
    content: { manifest_sha256: `sha256:${hex(digit)}`,
      files: [{ name: "Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf", bytes: 8988110784, sha256: `sha256:${hex("e")}` }] } };
};

test("the two published Qwen runs stay excluded, and the reason names the folder path", () => {
  const older = published("run-cc80dd597abb"), newer = published("run-c758acaf0c4e");
  const olderIdentity = weightsIdentity(older);
  assert.equal(olderIdentity.established, false);
  assert.match(olderIdentity.why, /local folder path/);
  // The newer run's 64-character value is read only as what older records
  // meant by model.revision: a declared revision of its source. Its length
  // does not make it a file hash.
  const newerIdentity = weightsIdentity(newer);
  assert.equal(newerIdentity.established, true);
  assert.equal(newerIdentity.kind, "declared-revision");
  assert.match(newerIdentity.label, /what kind of revision it is was not recorded/);
  const verdict = assess(newer, older, "exact");
  assert.equal(verdict.suitability, "unsuitable");
  assert.ok(verdict.blockers.includes("Weights identity not established"));
  assert.ok(verdict.reasons.some(reason => /local folder path/.test(reason)));
});

test("two runs with the same file fingerprint are comparable when everything else matches", () => {
  const verified = fixture("verified", hashed("1", "verified-at-run-start"));
  const again = fixture("verified-again", hashed("1", "verified-at-run-start"));
  const verdict = assess(verified, again, "exact");
  assert.notEqual(verdict.suitability, "unsuitable", verdict.reasons.join(" "));
  assert.deepEqual(verdict.blockers, []);
});

test("a fingerprint chosen after the run still compares, and the comparison says it was chosen", () => {
  const verified = fixture("verified", hashed("1", "verified-at-run-start"));
  const chosen = fixture("chosen", hashed("1", "contributor-selected"));
  const verdict = assess(verified, chosen, "exact");
  assert.deepEqual(verdict.blockers, []);
  assert.ok(verdict.differences.some(text => /chosen by its contributor/.test(text)));
});

test("different files, different kinds of identity and unestablished identities are excluded", () => {
  const one = fixture("one", hashed("1", "verified-at-run-start"));
  const two = fixture("two", hashed("2", "verified-at-run-start"));
  assert.ok(assess(one, two, "exact").blockers.includes("Different weights"));

  const commit = "d0a692ef765eefbf2fabb130b3cb2e8917e3d225";
  const repository = fixture("repository", document => {
    document.model.revision = commit;
    document.model.weights = { kind: "repository", repository: "https://huggingface.co/Qwen/Qwen2.5-Coder-14B-Instruct-GGUF",
      repository_revision: commit, identity: "repository-revision", content: null, provenance: "contributor-declared" };
  });
  // A repository commit and a file fingerprint are never treated as the same thing.
  assert.ok(assess(one, repository, "exact").blockers.includes("Weights identified differently"));

  for (const revision of ["D:\\OmoBench\\lmstudio-models\\Qwen", "\\\\server\\share\\model.gguf", "/home/me/model.gguf",
                          "./model.gguf", "file:///C:/models/model.gguf"]) {
    const place = fixture("place", document => { document.model.revision = revision; delete document.model.weights; });
    const identity = weightsIdentity(place);
    assert.equal(identity.established, false, revision);
    const verdict = assess(one, place, "exact");
    assert.ok(verdict.blockers.includes("Weights identity not established"), revision);
    assert.ok(verdict.missing.some(text => /Weights identity is not established/.test(text)), revision);
  }
  const unestablishedPair = assess(published("run-cc80dd597abb"), published("run-cc80dd597abb"), "exact");
  assert.ok(unestablishedPair.blockers.includes("Weights identity not established"),
    "two silent identities are never an agreement");
});

test("the same weights do not override the other requirements", () => {
  const base = fixture("base", hashed("1", "verified-at-run-start"));
  const otherTokenizer = fixture("other-tokenizer", document => {
    hashed("1", "verified-at-run-start")(document);
    document.model.tokenizer.source = "Qwen/Qwen2.5-14B-Instruct";
  });
  assert.ok(assess(base, otherTokenizer, "exact").blockers.some(text => /tokenizer/i.test(text)));
  const otherQuantization = fixture("other-quant", document => {
    hashed("1", "verified-at-run-start")(document);
    document.model.quantization = "Q8_0";
  });
  assert.ok(assess(base, otherQuantization, "exact").blockers.includes("Different quantization"));
  const otherProfile = fixture("other-profile", document => {
    hashed("1", "verified-at-run-start")(document);
    document.benchmark.profile_id = "chat-long-v1";
  });
  assert.equal(assess(base, otherProfile, "exact").suitability, "unsuitable");
});

test("how the identity was established is read, shown, and only when recorded", () => {
  const verified = fixture("verified", hashed("1", "verified-at-run-start"));
  assert.equal(verified.weights.provenance, "verified-at-run-start");
  const rows = summarize(verified).sections.flatMap(section => section.rows);
  assert.equal(rows.find(row => row.label === "How the weights were identified")?.value,
    "Verified: the file recorded when the run started");
  const chosenRows = summarize(fixture("chosen", hashed("1", "contributor-selected"))).sections.flatMap(section => section.rows);
  assert.equal(chosenRows.find(row => row.label === "How the weights were identified")?.value,
    "Chosen by the contributor after the run");
  for (const id of Object.keys(raw)) {
    const labels = summarize(published(id)).sections.flatMap(section => section.rows).map(row => row.label);
    assert.ok(!labels.includes("How the weights were identified"), id);
  }
  const pathRow = summarize(published("run-cc80dd597abb")).sections.flatMap(section => section.rows).find(row => row.label === "Weights");
  assert.match(pathRow.note, /local path or URL, which identifies no bytes/);
  assert.throws(() => fixture("bad-provenance", document => { hashed("1", "verified")(document); }), /weights provenance/);
  assert.throws(() => fixture("head", document => { document.model.revision = "HEAD"; delete document.model.weights; }), /immutable/);
});
