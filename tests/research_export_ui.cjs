const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync(process.argv[2], 'utf8');

async function scenario(code) {
  let handler, resolveResponse, calls = 0, downloads = 0;
  const status = {textContent: ''};
  const link = {
    dataset: {waiting: 'WAIT', busy: 'BUSY', error: 'ERROR', ready: 'READY'},
    href: 'http://localhost/ingress/api/research/export',
    attrs: {}, setAttribute(k, v) { this.attrs[k] = v; },
    removeAttribute(k) { delete this.attrs[k]; },
    addEventListener(_, fn) { handler = fn; },
  };
  const document = {
    getElementById: id => id === 'research-export' ? link : status,
    createElement: () => ({click() { downloads++; }, remove() {}}),
    body: {appendChild() {}},
  };
  vm.runInNewContext(source, {
    document, URL: {createObjectURL: () => 'blob:test', revokeObjectURL() {}},
    setTimeout: () => {},
    fetch: url => { calls++; assert.equal(url, link.href); return new Promise(r => { resolveResponse = r; }); },
  });
  const first = handler({preventDefault() {}});
  assert.equal(status.textContent, 'WAIT');
  assert.equal(link.attrs['aria-disabled'], 'true');
  await handler({preventDefault() {}});
  assert.equal(calls, 1);
  resolveResponse({ok: code === 200, status: code,
    headers: {get: key => key === 'Content-Type' ? 'application/octet-stream' : 'attachment; filename="mate-beta-bundle-123.matebeta"'},
    blob: async () => ({}),
  });
  await first;
  assert.equal(status.textContent, code === 200 ? 'READY' : code === 409 ? 'BUSY' : 'ERROR');
  assert.equal(downloads, code === 200 ? 1 : 0);
  assert.equal(link.attrs['aria-disabled'], undefined);
}
(async () => { for (const code of [200, 409, 500]) await scenario(code); })().catch(e => { console.error(e); process.exit(1); });
