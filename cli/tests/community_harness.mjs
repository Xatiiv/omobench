// Production auth UI and both complete page scripts, with a deterministic DOM.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { createContext, runInContext } from 'node:vm';
const root = resolve(process.argv[2]);
const authSource = readFileSync(`${root}/community.js`, 'utf8');
const elements = new Map();
function el(id) {
  if (!elements.has(id)) elements.set(id, {id, value: id === 'submission-mode' ? 'community' : '', hidden: false,
    disabled: false, checked: false, textContent: '', href: '', listeners: {},
    addEventListener(name, handler) { this.listeners[name] = handler; },
  });
  return elements.get(id);
}
let invalidations = 0;
const calls = [];
let response = { enabled: true, generation: 'one', status: 'signed_out', account: null };
const context = createContext({document: {getElementById: el}, setTimeout: () => 0, clearTimeout() {},
  api: async (path, body) => { calls.push([path, body]); return response; }, invalidate: () => { invalidations += 1; }});
runInContext(authSource + '\nvar widget = new CommunitySubmission(api, invalidate);', context);
const w = context.widget;
await w.refresh();
assert.equal(el('github-sign-in').disabled, false);
const ticket = w.ticket();
response = {...response, status: 'pending', user_code: 'ABCD-EFGH', verification_uri: 'https://evil.invalid'};
await w.act('start');
assert.equal(el('github-code').textContent, 'ABCD-EFGH');
assert.equal(el('github-verify').href, 'https://github.com/login/device');
assert.equal(el('github-device').hidden, false);
assert.equal(w.current(ticket), false);
assert.equal(calls.filter(([p]) => p.includes('/submit')).length, 0);
response = {...response, status: 'signed_in', account: {login: 'alice', id: 1}};
await w.refresh();
assert.equal(el('submitter').value, 'alice');
assert.equal(el('github-code').textContent, '');
assert.equal(el('github-sign-out').hidden, false);
el('submitter').value = 'my team';
response = {...response, generation: 'two', account: {login: 'bob', id: 2}};
const oldTicket = w.ticket(); await w.refresh();
assert.equal(w.current(oldTicket), false);
assert.equal(el('submitter').value, 'my team');
const changed = invalidations;
el('submission-mode').value = 'origin'; el('submission-mode').listeners.change();
assert.ok(invalidations > changed);
assert.equal(el('github-auth').hidden, true);
assert.equal(w.mode, 'origin');
assert.throws(() => w.payload(null, true));
assert.equal(w.payload({can_submit: true, mode: 'community', review_id: 'receipt'}, true).review_id, 'receipt');
response = {...response, status: 'signed_out', account: null, generation: 'three'};
await w.act('sign-out'); assert.equal(el('github-sign-in').hidden, false);
response = {...response, enabled: false}; await w.refresh();
assert.equal(el('github-sign-in').disabled, true);
assert.match(el('github-auth-status').textContent, /download/);
// A poll started before sign-out must not restore the old account afterwards.
let resolveOld;
w.api = async (path) => path.endsWith('/sign-out')
  ? {enabled: true, generation: 'signed-out', status: 'signed_out', account: null}
  : new Promise((resolve) => { resolveOld = resolve; });
const delayed = w.refresh();
await w.act('sign-out');
resolveOld({enabled: true, generation: 'obsolete', status: 'signed_in', account: {login: 'old', id: 9}});
await delayed;
assert.equal(w.state.status, 'signed_out');
assert.equal(w.generation, 'signed-out');
for (const page of ['guided.html', 'index.html']) {
  const html = readFileSync(`${root}/${page}`, 'utf8');
  for (const id of elements.keys()) assert.ok(html.includes(`id="${id}"`), `${page} missing ${id}`);
  const main = page === 'guided.html' ? 'guided.js' : 'app.js';
  assert.ok(html.indexOf('/community.js') < html.indexOf('/' + main));
}
console.log('Community sign-in rendering, review invalidation, and both page contracts passed.');
