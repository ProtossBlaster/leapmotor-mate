// The wizard's own fork, run for real: the page's script is handed in on argv[2], already rendered
// by Jinja, so what runs here is what the browser gets. `chooseSetup()` decides, from one
// `/api/setup/cert-status` answer, what a new user is shown — and that decision is a line of
// browser JavaScript no Python test can reach. None of the answers is ever a certificate form:
// the build carries the app certificate and installs it at startup (01/10/2026).
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

const source = fs.readFileSync(process.argv[2], 'utf8');
const els = new Map();

function makeEl(id) {
  return {
    id, value: '', textContent: '', innerHTML: '', className: '', style: {}, dataset: {}, _attrs: {},
    setAttribute(k, v) { this._attrs[k] = String(v); },
    getAttribute(k) { return k in this._attrs ? this._attrs[k] : null; },
    appendChild() {}, remove() {}, focus() {}, click() {}, addEventListener() {},
    insertAdjacentElement() {},
    querySelector() { return makeEl('anon'); },
    querySelectorAll() { return []; },
    closest() { return null; },
    classList: { add() {}, remove() {}, toggle() { return false; }, contains() { return false; } },
  };
}

const document = {
  documentElement: { lang: 'en' },
  getElementById(id) {
    if (!els.has(id)) els.set(id, makeEl(id));
    return els.get(id);
  },
  querySelector() { return makeEl('anon'); },
  querySelectorAll() { return []; },
  createElement() { return makeEl('new'); },
  body: { appendChild() {} },
  addEventListener() {},
};

let answer = null;          // what /api/setup/cert-status replies for the scenario under test
const sandbox = {
  document,
  navigator: { language: 'en-US' },
  window: { location: { href: '' } },
  alert: () => {},
  fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve(answer) }),
  setTimeout: () => {},
  console,
  FormData: class { constructor() {} append() {} },
};
const ctx = vm.createContext(sandbox);
vm.runInContext(source, ctx);

const shown = id => document.getElementById(id).style.display;

async function choose(readiness) {
  answer = readiness;
  for (const id of ['cert-step', 'application-bundle-step', 'setup-form', 'managed-setup-error'])
    document.getElementById(id).style.display = 'none';
  await vm.runInContext('chooseSetup()', ctx);
}

(async () => {
  // ── 1. Not ready and no bundle needed: the build should have completed it ────────────────────
  // A new installation is provisioned from the build at startup, so cert-status answers `ready`.
  // If it still answers `provisioning_required`, the build could not install its own material:
  // the page says so. It never asks the user for a certificate.
  await choose({present: false, managed: true, state: 'provisioning_required',
                manual_upload_required: false});
  assert.equal(shown('managed-setup-error'), 'block',
    'an installation the build could not complete was not told so');
  assert.notEqual(shown('cert-step'), 'block', 'a new user was asked for a certificate');
  assert.notEqual(shown('application-bundle-step'), 'block',
    'a fresh install was sent to the ZIP bundle it cannot build');

  // ── 2. The bundle box is still reachable where it is the only way ────────────────────────────
  // No usable packaged profile: the three-file bundle really is the only path, so offer it.
  await choose({present: false, managed: true, state: 'provisioning_required',
                manual_upload_required: true});
  assert.equal(shown('application-bundle-step'), 'block',
    'an installation that needs a supplied bundle was not offered the upload');

  // ── 3. Material already there → straight to the account form ─────────────────────────────────
  await choose({present: true, managed: true, state: 'ready', manual_upload_required: false});
  assert.equal(shown('setup-form'), 'block', 'a ready installation was not shown the login form');

  // ── 4. The legacy client answers `present` alone: no certificate step for it either ──────────
  await choose({present: false});
  assert.equal(shown('managed-setup-error'), 'block', 'a legacy install without material was not told so');
  assert.notEqual(shown('cert-step'), 'block', 'the legacy client asked for a certificate');
})().catch(error => { console.error(error.message); process.exit(1); });
