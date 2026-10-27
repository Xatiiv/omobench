import assert from "node:assert/strict";
import test from "node:test";
import { readdirSync } from "node:fs";

const { default: worker } = await import("../dist/server/index.js");

async function render(route) {
  const response = await worker.fetch(
    new Request(`http://localhost${route}`, { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
  return response.text();
}

test("renders OmoBench with a route to the benchmark and labelled demo data", async () => {
  const html = await render("/");
  assert.match(html, /<title>OmoBench/);
  assert.match(html, /href="\/run"/);
  assert.match(html, /Put your AI/);
  assert.match(html, /Illustrative local data/i);
  assert.match(html, /Benchmark registry/i);
});

test("offers the verified public Windows installer alongside the local launcher", async () => {
  const html = await render("/run");
  assert.match(html, /href="omobench:\/\/open"/);
  assert.match(html, /Download for Windows/);
  assert.match(html, /href="https:\/\/github\.com\/Xatiiv\/omobench\/releases\/download\/windows-v0\.1\.0\/OmoBenchSetup\.exe"/);
  assert.match(html, /afa11a567165b3aec76ac44b4385b5d05b6b8e0d5d2974f3ce8c4cbf0222ca27/);
  assert.match(html, /13a3842478e4/);
  assert.match(html, /This build is unsigned/);
  assert.match(html, /manual Linux installation step/);
  assert.doesNotMatch(html, /Public download coming soon/);
  assert.match(html, /WSL2/);
});

test("comparison starts with registry data and a private import path, never fictional measurements", async () => {
  const html = await render("/compare");
  assert.match(html, /See how your/);
  const resultFiles = readdirSync(new URL("../results/", import.meta.url), { recursive: true }).filter(path => path.endsWith("result.json"));
  if (!resultFiles.length) assert.match(html, /No community runs are included/);
  assert.match(html, /Open your result.json/);
  assert.match(html, /Illustrative preview/);
  assert.match(html, /never uploaded/);
  assert.doesNotMatch(html, /Example CPU setup A/);
});

test("the home page links to the visual comparison workspace", async () => {
  const html = await render("/");
  assert.match(html, /href="\/compare"/);
  assert.match(html, /Compare setups/);
});
