import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync, readdirSync } from "node:fs";
import ts from "typescript";

// Weights identity on the website: what each record establishes, and how the
// comparison uses it. Published records are read as published: two Qwen runs by
// name, and the whole registry wherever a rule must hold for every record. Every
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

test("the two published Qwen runs stay excluded, each with its own reason", () => {
  const older = published("run-cc80dd597abb"), newer = published("run-c758acaf0c4e");
  const olderIdentity = weightsIdentity(older);
  assert.equal(olderIdentity.established, false);
  assert.match(olderIdentity.why, /local folder path/);
  // The newer run's 64-character value is not read as a file hash, a commit or
  // anything else: its record never said what it is.
  const newerIdentity = weightsIdentity(newer);
  assert.equal(newerIdentity.established, false);
  assert.match(newerIdentity.why, /does not say what it identifies/);
  const verdict = assess(newer, older, "exact");
  assert.equal(verdict.suitability, "unsuitable");
  assert.ok(verdict.blockers.includes("Weights identity not established"));
  assert.ok(verdict.reasons.some(reason => /local folder path/.test(reason)));
  assert.ok(verdict.reasons.some(reason => /does not say what it identifies/.test(reason)));
});

test("two identical unexplained bare hashes do not establish identical weights", () => {
  // The published c758 record against itself, and a labelled fixture pair
  // carrying one bare value each of commit and SHA-256 length: equal strings
  // with no recorded kind are never counted as the same bytes.
  const newer = published("run-c758acaf0c4e");
  assert.ok(assess(newer, published("run-c758acaf0c4e"), "exact").blockers.includes("Weights identity not established"));
  for (const revision of ["0123456789abcdef0123456789abcdef01234567", hex("a"), "v1.0-release"]) {
    const bare = label => fixture(label, document => { document.model.revision = revision; delete document.model.weights; });
    const verdict = assess(bare("bare-one"), bare("bare-two"), "exact");
    assert.ok(verdict.blockers.includes("Weights identity not established"), revision);
    assert.ok(verdict.missing.some(text => /Weights identity is not established on the reference run/.test(text)), revision);
    assert.ok(verdict.missing.some(text => /Weights identity is not established on the other run/.test(text)), revision);
  }
});

test("folder paths and labels that move stay unestablished", () => {
  const base = fixture("base", () => {});
  for (const revision of ["D:\\OmoBench\\lmstudio-models\\Qwen", "\\\\server\\share\\model.gguf", "/home/me/model.gguf",
                          "./model.gguf", "~/models/model.gguf", "file:///C:/models/model.gguf", "https://example.invalid/model.gguf"]) {
    const identity = weightsIdentity({ ...base, modelRevision: revision, weights: null });
    assert.equal(identity.established, false, revision);
    assert.match(identity.why, /folder path|URL/, revision);
  }
  for (const revision of ["main", "master", "latest", "HEAD"]) {
    // The reader refuses these outright; a record built around it still says why.
    assert.throws(() => fixture("moving", document => { document.model.revision = revision; delete document.model.weights; }), /immutable/);
    const identity = weightsIdentity({ ...base, modelRevision: revision, weights: null });
    assert.equal(identity.established, false, revision);
    assert.match(identity.why, /label that moves/, revision);
  }
});

test("matching typed identities still establish the same weights", () => {
  const one = fixture("one", hashed("1", "verified-at-run-start"));
  assert.deepEqual(assess(one, fixture("one-again", hashed("1", "verified-at-run-start")), "exact").blockers, []);
  const commit = "d0a692ef765eefbf2fabb130b3cb2e8917e3d225";
  const declared = label => fixture(label, document => {
    document.model.revision = commit;
    document.model.weights = { kind: "repository", repository: "https://huggingface.co/Qwen/Qwen2.5-Coder-14B-Instruct-GGUF",
      repository_revision: commit, identity: "repository-revision", content: null, provenance: "contributor-declared" };
  });
  const verdict = assess(declared("repository-one"), declared("repository-two"), "exact");
  assert.deepEqual(verdict.blockers, []);
  assert.equal(weightsIdentity(declared("repository-one")).kind, "repository-commit");
});

test("reading and comparing the published records changes none of them", () => {
  const before = JSON.stringify(raw);
  const records = Object.keys(raw).map(published);
  for (const left of records) for (const right of records) assess(left, right, "exact");
  assert.equal(JSON.stringify(raw), before);
  for (const record of records) assert.equal(weightsIdentity(record).established, Boolean(record.weights), record.id);
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

// What each recorded provenance must be shown as, and where the fact came from.
// Written out here rather than imported, so the page text is checked, not echoed.
const SHOWN_PROVENANCE = {
  "verified-at-run-start": { value: "Verified: the file recorded when the run started", source: "read on that machine" },
  "contributor-selected": { value: "Chosen by the contributor after the run", source: "stated by the contributor" },
  "contributor-declared": { value: "Stated by the contributor", source: "stated by the contributor" },
};
const provenanceRow = record => summarize(record).sections.flatMap(section => section.rows)
  .find(row => row.label === "How the weights were identified");

test("how the identity was established is read, shown, and only when recorded", () => {
  // Labelled fixtures: one per recorded provenance, each shown as exactly that.
  for (const [provenance, shown] of Object.entries(SHOWN_PROVENANCE)) {
    const record = fixture(`provenance-${provenance}`, hashed("1", provenance));
    assert.equal(record.weights.provenance, provenance);
    const row = provenanceRow(record);
    assert.equal(row?.value, shown.value, provenance);
    assert.equal(row?.source, shown.source, provenance);
  }
  // Labelled fixtures without a recorded provenance: a typed identity from before
  // schema 1.2, and no typed identity at all. Neither gets a row invented for it.
  const typedWithout = fixture("typed-without-provenance", document => {
    hashed("1", undefined)(document);
    delete document.model.weights.provenance;
  });
  assert.equal(typedWithout.weights.provenance, null);
  assert.equal(weightsIdentity(typedWithout).established, true);
  assert.equal(provenanceRow(typedWithout), undefined);
  const untyped = fixture("untyped", document => { delete document.model.weights; });
  assert.equal(untyped.weights, null);
  assert.equal(provenanceRow(untyped), undefined);

  // Every published record, however many there are: the reader keeps exactly the
  // provenance its document recorded, and the page shows a row only for one.
  for (const [id, document] of Object.entries(raw)) {
    const recorded = document.model.weights?.provenance ?? null;
    const record = published(id);
    assert.equal(record.weights?.provenance ?? null, recorded, id);
    const row = provenanceRow(record);
    if (recorded === null) assert.equal(row, undefined, id);
    else {
      assert.equal(row?.value, SHOWN_PROVENANCE[recorded].value, id);
      assert.equal(row?.source, SHOWN_PROVENANCE[recorded].source, id);
    }
  }
  const pathRow = summarize(published("run-cc80dd597abb")).sections.flatMap(section => section.rows).find(row => row.label === "Weights");
  assert.match(pathRow.note, /local path or URL, which identifies no bytes/);
  assert.throws(() => fixture("bad-provenance", document => { hashed("1", "verified")(document); }), /weights provenance/);
  assert.throws(() => fixture("head", document => { document.model.revision = "HEAD"; delete document.model.weights; }), /immutable/);
});
