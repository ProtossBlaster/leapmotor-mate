// The wizard's own JavaScript, run for real: argv[2] is the rendered page script, argv[3] the part
// to check. "posts": after one detection and another, only the last attempt's cars are submitted,
// the email is locked while a detection runs, and editing it later forgets the cars found.
// "note": a failed detection says the PIN is not saved, before setup can be finished by hand.
// "cut": a refused password longer than the Leapmotor app keeps says to try the part the app kept.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

const els = new Map();
const el = id => {
  if (!els.has(id)) els.set(id, {id, value: '', textContent: '', innerHTML: '', style: {}, disabled: false});
  return els.get(id);
};
const sandbox = {
  document: {getElementById: el, querySelector: () => el('anon'), querySelectorAll: () => [],
             addEventListener() {}},
  navigator: {language: 'en-US'}, window: {location: {href: ''}},
  alert() {}, setTimeout() {}, console, fetch: () => new Promise(() => {}),
};
const ctx = vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), ctx);
const run = code => vm.runInContext(code, ctx);

const answer = body => async () => ({ok: true, json: async () => body});
const ONE = answer({vin: 'LFZB10AAAAAAAAAA1', car_type: 'B10', battery_kwh: 65.0, battery_label: 'Pro Max'});
const TWO = answer({vin: 'LFZB10AAAAAAAAAA1', car_type: 'B10', vehicles: [
  {vin: 'LFZB10AAAAAAAAAA1', car_type: 'B10', battery_options: [{v: '65.0', label: 'Pro Max'}]},
  {vin: 'LFZC10BBBBBBBBBB2', car_type: 'C10', battery_options: [{v: '67.0', label: 'RWD'}]}]});
const REFUSED = async () => ({ok: false, json: async () => ({error: 'Login failed'})});
const OFFLINE = async () => { throw new TypeError('Failed to fetch'); };
const FAILURES = [[REFUSED, 'the cloud refuses'], [OFFLINE, 'no connection']];

async function detect(fetch, password = 'pw') {
  el('inp-email').value = 'owner@example.com';
  el('inp-password').value = password;
  el('inp-pin').value = '1234';
  ctx.fetch = fetch;
  await run('detectCar()');
  return {...posted(), html: el('detect-result').innerHTML};
}
const posted = () => ({vin: el('h-vin').value, car_type: el('h-car-type').value,
                       vehicles: el('h-vehicles').value, cars: run('Object.keys(CARS).length')});
const NOTHING = {vin: '', car_type: '', vehicles: '', cars: 0};
// A detection whose answer waits for release(): the page as it is while the cloud has not answered.
function held(fetch) {
  let release;
  const gate = new Promise(r => { release = r; });
  return {fetch: (...args) => gate.then(() => fetch(...args)), release};
}

const parts = {
  async posts() {
    for (const [answer, how] of [[ONE, 'a car found'], ...FAILURES]) {
      const {fetch, release} = held(answer);
      const running = detect(fetch);
      assert.equal(el('inp-email').readOnly, true, 'the email could change before the answer arrives');
      release();
      await running;
      assert.equal(el('inp-email').readOnly, false, `${how}: the email stays locked`);
    }
    for (const [first, cars] of [[ONE, 'one car'], [TWO, 'two cars']]) {
      for (const [then, why] of FAILURES) {
        await detect(first);
        await detect(then);
        assert.deepEqual(posted(), NOTHING, `${cars}, then ${why}: the earlier cars would be posted`);
      }
      await detect(first);
      run('forgetDetection()');                          // what typing in the email field calls
      assert.deepEqual(posted(), NOTHING, `${cars}, then another email: the earlier cars would be posted`);
      assert.equal(el('btn-submit').style.display, 'none', `${cars}, then another email: setup could be finished`);
      assert.equal(el('detect-result').style.display, 'none', `${cars}, then another email: the cars still shown`);
    }
    await detect(TWO);
    const after = await detect(ONE);
    assert.equal(after.vehicles, '', 'two cars, then one: the earlier list would outrank the car just found');
    assert.equal(after.vin, 'LFZB10AAAAAAAAAA1');
  },
  async note() {
    const note = run('strings.en.pinNotSaved');
    assert.ok(note && note.includes('Settings'), 'the English note must point at Settings');
    assert.deepEqual([...run('Object.keys(strings).filter(l => !strings[l].pinNotSaved)')], [],
                     'every language says it');
    for (const [failure, why] of FAILURES) {
      el('btn-submit').style.display = 'none';
      assert.ok((await detect(failure)).html.includes(note), `${why}: no word that the PIN is not saved`);
      assert.equal(el('btn-submit').style.display, 'block', `${why}: setup can still be finished by hand`);
    }
    assert.ok(!(await detect(ONE)).html.includes(note), 'a detected car stores its PIN, so the note would be false');
  },
  async cut() {
    const hint = run('strings.en.pwdCutShort');
    assert.ok(hint && hint.includes('16'), 'the English hint must name the 16 characters');
    assert.deepEqual([...run('Object.keys(strings).filter(l => !strings[l].pwdCutShort)')], [],
                     'every language says it');
    const refused = async () => ({ok: false, json: async () => ({
      error: 'API=302010108 (incorrect account or password)', password_refused: true})});
    const LONG = 'Abcdefgh_1234567xyz', KEPT = LONG.slice(0, 16);
    assert.ok((await detect(refused, LONG)).html.includes(hint), 'a long refused password gets no hint');
    assert.ok(!(await detect(refused, KEPT)).html.includes(hint), 'a password the app keeps whole gets the hint');
    assert.ok(!(await detect(refused, `  ${KEPT}  `)).html.includes(hint), 'spaces Mate trims count as characters');
    assert.ok(!(await detect(REFUSED, LONG)).html.includes(hint), 'a refusal of another kind gets the hint');
    assert.ok(!(await detect(ONE, LONG)).html.includes(hint), 'a detected car gets the hint');
  },
};

parts[process.argv[3]]().catch(err => { console.error(err); process.exit(1); });
