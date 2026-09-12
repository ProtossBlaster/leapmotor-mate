// The wizard's own JavaScript, run for real — the page's script is handed in on argv[2], already
// rendered by Jinja, so what is exercised here is the code the browser gets, not a copy of it.
//
// Why a DOM this crude is enough: everything this file asserts happens in three hidden fields and
// one return value. The stub therefore remembers `value` per element id and swallows the rest.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

const source = fs.readFileSync(process.argv[2], 'utf8');

const alerts = [];
const els = new Map();

function makeEl(id) {
  return {
    id, value: '', textContent: '', innerHTML: '', className: '', placeholder: '', type: 'text',
    style: {}, dataset: {}, _attrs: {},
    setAttribute(k, v) { this._attrs[k] = String(v); },
    getAttribute(k) { return k in this._attrs ? this._attrs[k] : null; },
    appendChild() {}, remove() {}, focus() {}, click() {}, addEventListener() {},
    querySelector() { return makeEl('anon'); },
    querySelectorAll() { return []; },
    closest() { return null; },
    classList: { add() {}, remove() {}, toggle() { return false; }, contains() { return false; } },
  };
}

const document = {
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

const sandbox = {
  document,
  navigator: { language: 'en-US' },
  window: { location: { href: '' } },
  alert: m => alerts.push(String(m)),
  fetch: () => new Promise(() => {}),
  setTimeout: () => {},
  console,
  FormData: class { constructor() {} append() {} },
};
const ctx = vm.createContext(sandbox);
vm.runInContext(source, ctx);        // top-level init (setLang) runs against the stub

const run = code => vm.runInContext(code, ctx);
const field = id => document.getElementById(id).value;

function reset() {
  alerts.length = 0;
  run("Object.keys(CARS).forEach(k => delete CARS[k]);");
  for (const id of ['h-battery', 'h-vehicles', 'h-is-reev']) document.getElementById(id).value = '';
}

const C10 = {vin: 'VIN-C10', car_type: 'C10',
             battery_options: [{v: '67.0', label: 'RWD'}, {v: '81.9', label: 'AWD'}]};
const B10 = {vin: 'VIN-B10', car_type: 'B10',
             battery_options: [{v: '55.0', label: 'Pro'}, {v: '65.0', label: 'Pro Max'}]};
const UNMAPPED = {vin: 'VIN-X', car_type: 'X99', battery_options: []};

// ── 1. #280: two cars, a pack chosen on each, and the wizard must let them through ──────────
// The exact report: a C10 and a B10 on one account, both packs selected, and "Connect & Start"
// answered "Please select a battery variant first." Reproduced in a browser before this test
// existed: the multi-car branch returns before anything fills `h-battery`, which is the only
// field the guard used to read.
reset();
run(`buildCarCards([${JSON.stringify(C10)}, ${JSON.stringify(B10)}]);
     selectCarBattery('VIN-C10', '81.9', false);
     selectCarBattery('VIN-B10', '65.0', false);`);
const twoCarsPass = run('validateSubmit()');
assert.deepEqual(alerts, [], `two cars with a pack each were refused: ${alerts[0]}`);
assert.equal(twoCarsPass, true, 'validateSubmit() blocked a two-car account that chose both packs');

const posted = JSON.parse(field('h-vehicles'));
assert.equal(posted.length, 2, 'both cars must be posted');
assert.deepEqual(posted.map(c => c.battery), ['81.9', '65.0'], 'each car keeps its own pack');

// ── 2. …and the account-wide field must carry a pack somebody chose ──────────────────────────
// setup_submit() does float(battery) and falls back to 65.0 on an empty string. That number is
// the legacy global: what a car without its own row is measured with. Leaving it empty writes a
// B10 Pro Max pack onto an account whose first car is an 81.9 kWh AWD.
assert.equal(field('h-battery'), '81.9',
             'the account-wide battery field must mirror the first car, not stay empty');
assert.equal(field('h-is-reev'), '0', 'the account-wide range-extender flag must mirror it too');

// ── 3. A car the map does not know still has to stop the form ────────────────────────────────
// Mirroring the FIRST car alone would let this through: car one is fine, car two has no pack at
// all. The guard has to look at every car it is about to post.
reset();
const cardsHtml = run(`buildCarCards([${JSON.stringify(C10)}, ${JSON.stringify(UNMAPPED)}]);`);
const withUnmapped = run('validateSubmit()');
assert.equal(withUnmapped, false, 'a car with no pack was allowed through');
assert.deepEqual(alerts.length, 1, 'the user must be told, once');

// ── 4. …and that car must offer the manual field, or its owner is stuck ──────────────────────
// A model the map does not know draws an empty card: a "Battery pack" heading with nothing under
// it, and a form that now refuses to submit. With one car the wizard already answers this with a
// kWh field; a card has to do the same, or the only way out is to remove a car from the account.
const cardOf = vin => {
  const start = cardsHtml.indexOf(`data-car="${vin}"`);
  assert.notEqual(start, -1, `no card drawn for ${vin}`);
  const next = cardsHtml.indexOf('data-car="', start + 10);
  return cardsHtml.slice(start, next === -1 ? undefined : next);
};
assert.match(cardOf('VIN-X'), /<input[^>]+type="number"/,
             'the card of an unrecognised model offers no way to enter a pack');
assert.ok(!/<input[^>]+type="number"/.test(cardOf('VIN-C10')),
          'a card with a pack list must not also show the manual field');

alerts.length = 0;
run("selectCarBattery('VIN-X', '42.5', false);");
assert.equal(run('validateSubmit()'), true, 'a hand-entered pack still did not unblock the form');
assert.equal(JSON.parse(field('h-vehicles'))[1].battery, '42.5', 'the typed pack is not posted');
assert.deepEqual(alerts, [], 'a hand-entered pack must not be questioned');

// Emptying it puts the block back: a blank field is not an answer.
run("selectCarBattery('VIN-X', '', false);");
assert.equal(run('validateSubmit()'), false, 'an emptied manual field was accepted');

// ── 5. One car: unchanged ────────────────────────────────────────────────────────────────────
reset();
run("selectBattery('67.0', false);");
assert.equal(run('validateSubmit()'), true, 'the single-car path must still submit');
assert.deepEqual(alerts, [], 'the single-car path must not complain');

// ── 6. Nothing chosen at all: the original protection stays ──────────────────────────────────
reset();
assert.equal(run('validateSubmit()'), false, 'an empty form must still be refused');
assert.equal(alerts.length, 1, 'an empty form must still say why');

console.log("setup wizard: 6 scenarios ok");
