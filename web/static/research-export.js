/* Keep the native link as a no-JavaScript fallback. No bundle content is persisted. */
(() => {
  const link = document.getElementById('research-export');
  const status = document.getElementById('research-export-status');
  if (!link || !status) return;
  let pending = false;
  link.addEventListener('click', async event => {
    event.preventDefault();
    if (pending) return;
    pending = true;
    link.setAttribute('aria-disabled', 'true');
    link.setAttribute('aria-busy', 'true');
    status.textContent = link.dataset.waiting;
    try {
      const response = await fetch(link.href, {credentials: 'same-origin'});
      if (response.status === 409) {
        status.textContent = link.dataset.busy;
        return;
      }
      // Login redirects and HTML error pages must never become a fake .matebeta file.
      if (!response.ok || !(response.headers.get('Content-Type') || '').startsWith('application/octet-stream')) {
        throw new Error('export failed');
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const download = document.createElement('a');
      const disposition = response.headers.get('Content-Disposition') || '';
      const name = disposition.match(/mate-beta-bundle-\d+\.matebeta/);
      download.href = url;
      download.download = name ? name[0] : 'mate-beta-bundle.matebeta';
      document.body.appendChild(download);
      download.click();
      download.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      status.textContent = link.dataset.ready;
    } catch (_) {
      status.textContent = link.dataset.error;
    } finally {
      pending = false;
      link.removeAttribute('aria-disabled');
      link.removeAttribute('aria-busy');
    }
  });
})();
