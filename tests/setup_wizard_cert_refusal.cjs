// The wizard's own saveCert(), run for real (#283) — the rendered page script arrives on argv[2].
//
// The server refuses an unreadable certificate in English and adds a `code`. What the owner reads
// must be the page's language: an Italian wizard answering in English is how "no login" gets
// filed with nothing else to go on.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

const source = fs.readFileSync(process.argv[2], 'utf8');
const els = new Map();

function makeEl(id) {
  return {
    id, value: '', textContent: '', innerHTML: '', className: '', placeholder: '', type: 'text',
    style: {}, dataset: {}, disabled: false, files: [],
    setAttribute() {}, getAttribute() { return null; },
    appendChild() {}, remove() {}, focus() {}, click() {}, addEventListener() {},
    querySelector() { return makeEl('anon'); }, querySelectorAll() { return []; },
    closest() { return null; },
    classList: { add() {}, remove() {}, toggle() { return false; }, contains() { return false; } },
  };
}

const document = {
  getElementById(id) { if (!els.has(id)) els.set(id, makeEl(id)); return els.get(id); },
  querySelector() { return makeEl('anon'); }, querySelectorAll() { return []; },
  createElement() { return makeEl('new'); }, body: { appendChild() {} }, addEventListener() {},
};

let answer = null;
const sandbox = {
  document, console, navigator: { language: 'en-US' }, window: { location: { href: '' } },
  alert() {}, setTimeout: () => {},
  fetch: async () => ({ ok: false, json: async () => answer }),
  FormData: class { append() {} },
};
const ctx = vm.createContext(sandbox);
vm.runInContext(source, ctx);
const run = code => vm.runInContext(code, ctx);

(async () => {
  run("if (!certPasteMode) toggleCertMode();");
  document.getElementById('paste-crt').value = 'x';
  document.getElementById('paste-key').value = 'y';

  const cases = { cert_unreadable: 'certUnreadable', key_unreadable: 'keyUnreadable',
                  key_mismatch: 'keyMismatch' };
  const langs = run('Object.keys(strings)');
  assert.ok(langs.length >= 8, `the wizard has ${langs.length} languages`);
  for (const lang of langs) {
    run(`setLang(${JSON.stringify(lang)})`);
    for (const [code, key] of Object.entries(cases)) {
      answer = { error: 'English text from the server', code };
      await run('saveCert()');
      const shown = document.getElementById('cert-error').textContent;
      const expected = run(`strings[${JSON.stringify(lang)}][${JSON.stringify(key)}]`);
      assert.ok(expected, `${lang} has no ${key}`);
      assert.equal(shown, expected, `${lang}: ${code} was shown as "${shown}"`);
    }
  }

  // A refusal the page has no words for still says something — the server's own sentence.
  run("setLang('it')");
  answer = { error: 'Something new went wrong' };
  await run('saveCert()');
  assert.equal(document.getElementById('cert-error').textContent, 'Something new went wrong');
})().catch(e => { console.error(e); process.exit(1); });
