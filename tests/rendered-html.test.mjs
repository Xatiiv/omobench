import assert from "node:assert/strict";
import test from "node:test";

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

test("offers the local launcher without inventing a public installer download", async () => {
  const html = await render("/run");
  assert.match(html, /href="omobench:\/\/open"/);
  assert.match(html, /Public download coming soon/);
  assert.match(html, /WSL2/);
  assert.doesNotMatch(html, /href="[^"]*OmoBenchSetup\.exe"/);
});
