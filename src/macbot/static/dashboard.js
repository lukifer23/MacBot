(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let csrf = '';
  let snapshot = null;

  async function api(path, options = {}) {
    const headers = {'Content-Type': 'application/json', ...(options.headers || {})};
    if (csrf && options.method && options.method !== 'GET') headers['X-CSRF-Token'] = csrf;
    const response = await fetch(path, {...options, headers});
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
    return body;
  }

  function showError(error) { $('error').textContent = error ? String(error.message || error) : ''; }

  async function refresh() {
    showError();
    snapshot = await api('/api/diagnostics');
    $('assistant').textContent = JSON.stringify(snapshot.assistant, null, 2);
    $('supervisor').textContent = JSON.stringify(snapshot.supervisor, null, 2);
    $('updated').textContent = `Updated ${new Date().toLocaleTimeString()}`;
    $('connection').textContent = 'Read-only connected';
  }

  function connected() {
    $('login').hidden = true;
    $('diagnostics').hidden = false;
    refresh().catch(showError);
  }

  $('login-form').addEventListener('submit', async event => {
    event.preventDefault();
    try {
      const result = await api('/auth/exchange', {method: 'POST', body: JSON.stringify({token: $('login-code').value})});
      csrf = result.csrf;
      connected();
    } catch (error) { showError(error); }
  });
  $('refresh').onclick = () => refresh().catch(showError);
  $('download').onclick = () => {
    if (!snapshot) return;
    const link = document.createElement('a');
    link.href = URL.createObjectURL(new Blob([JSON.stringify(snapshot, null, 2)], {type: 'application/json'}));
    link.download = `macbot-diagnostics-${new Date().toISOString().replaceAll(':', '-')}.json`;
    link.click();
    URL.revokeObjectURL(link.href);
  };
  $('logout').onclick = async () => { try { await api('/auth/logout', {method: 'POST', body: '{}'}); } finally { location.reload(); } };
  api('/auth/session').then(result => { csrf = result.csrf; connected(); }).catch(() => {});
})();
