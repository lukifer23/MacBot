'use strict';
const $ = id => document.getElementById(id);
let csrf = sessionStorage.getItem('macbot_csrf') || '';
let cursor = 0, epoch = '', listening = false, recorder = null, socket = null;
let connected = false, pollTimer = null, refreshing = false, sending = false;
let activeModels = null, pendingSettings = false;
const messages = new Map(), approvalTimers = new Map(), serviceRows = new Map();
const labels = {assistant: 'Assistant', dashboard: 'Dashboard', llm: 'Language model', rag: 'Document search'};
const toolLabels = {open_app: 'Open an app', screenshot: 'Take a screenshot', browse_website: 'Open a website', web_search: 'Search the web', weather: 'Check weather', system_info: 'Mac system status', rag_search: 'Search local documents'};
const ms = n => Number.isFinite(n) ? (n >= 1000 ? (n / 1000).toFixed(2) + ' s' : Math.round(n) + ' ms') : '—';
const memory = n => Number.isFinite(n) ? (n / 1024 ** 3).toFixed(2) + ' GiB' : '—';
function fail(e) { $('error').textContent = e.message || String(e); $('error-banner').hidden = false; }
function clearError() { $('error').textContent = ''; $('error-banner').hidden = true; }
function connection(text, tone) { $('connection').textContent = text; $('connection').dataset.tone = tone; }
async function api(path, data, method) {
  const options = {method: method || (data === undefined ? 'GET' : 'POST'), headers: {'X-CSRF-Token': csrf}, signal: AbortSignal.timeout(30000)};
  if (data !== undefined) {
    if (data instanceof FormData) options.body = data;
    else {options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(data);}
  }
  const response = await fetch(path, options);
  const obj = await response.json();
  if (!response.ok) {const error = new Error(obj.error || JSON.stringify(obj.errors || obj)); error.status = response.status; throw error;}
  return obj;
}
async function busy(button, text, action) {
  if (button.disabled) return;
  const before = button.textContent; button.disabled = true; button.textContent = text;
  try {clearError(); return await action();} catch (e) {fail(e);} finally {button.disabled = false; button.textContent = before;}
}
function removeApproval(turnId) {
  clearInterval(approvalTimers.get(turnId)); approvalTimers.delete(turnId);
  document.getElementById('approval-' + turnId)?.remove();
}
function resetHistory() {
  for (const key of approvalTimers.keys()) removeApproval(key);
  $('history').querySelectorAll('.message, .approval').forEach(el => el.remove());
  messages.clear(); $('empty-state').hidden = false;
}
function message(key, role, text, append = false, detail = false) {
  const history = $('history');
  const follow = history.scrollHeight - history.scrollTop - history.clientHeight < 90;
  $('empty-state').hidden = true;
  let entry = messages.get(key);
  if (!entry) {
    const root = document.createElement('article'); root.className = 'message'; root.dataset.role = role;
    const title = document.createElement('strong'); title.textContent = role;
    entry = document.createElement(detail ? 'pre' : 'div'); entry.className = 'message-content';
    if (detail) {const details = document.createElement('details'); const summary = document.createElement('summary'); summary.textContent = 'View tool result'; details.append(summary, entry); root.append(title, details);}
    else root.append(title, entry);
    history.append(root); messages.set(key, entry);
    // Bound the rendered transcript as well as the server journal.
    if (messages.size > 300) {const oldest = messages.keys().next().value; messages.get(oldest).closest('.message').remove(); messages.delete(oldest);}
  }
  entry.textContent = append ? entry.textContent + text : text;
  if (follow) history.scrollTop = history.scrollHeight;
}
function setPhase(phase, state = 'running') {
  const text = {idle: 'Ready', queued: 'Queued', generating: 'Thinking', transcribing: 'Transcribing', speaking: 'Speaking', approval: 'Needs confirmation', tool: 'Using a tool', completed: 'Ready', interrupted: 'Stopped', failed: 'Needs attention'};
  $('turn-state').textContent = text[phase] || text[state] || 'Working';
  $('turn-state').dataset.tone = state === 'failed' ? 'bad' : phase === 'approval' ? 'warn' : 'neutral';
  $('interrupt').disabled = ['idle', 'completed', 'interrupted', 'failed'].includes(state) || phase === 'idle';
}
function acceptEpoch(value) {
  if (!value) return true;
  if (epoch && BigInt(value) < BigInt(epoch)) return false;
  if (epoch && value !== epoch) {resetHistory(); cursor = 0; setPhase('idle'); message('restart-' + value, 'Connection', 'Assistant restarted. This is a new conversation; previous pending actions were cancelled.');}
  epoch = value; return true;
}
function events(data) {
  if (!acceptEpoch(data.epoch)) return;
  if (data.gap) message('gap-' + data.cursor, 'Connection', 'Older events expired. The latest available conversation is shown.');
  for (const e of data.events) {
    if (e.seq <= cursor) continue;
    cursor = e.seq;
    if (e.kind === 'user') message(e.turn_id + '-user', 'You', e.data.text);
    if (e.kind === 'delta') message(e.turn_id, 'MacBot', e.data.text, true);
    if (e.kind === 'text') message(e.turn_id, 'MacBot', e.data.text);
    if (e.kind === 'listening') setListening(e.data.enabled);
    if (['generating', 'transcribing', 'speaking', 'tool'].includes(e.kind)) setPhase(e.kind, e.state);
    if (e.state === 'accepted') setPhase('queued', 'accepted');
    if (e.kind === 'context') {showContext(e.data); if (e.data.pruned_turns) message(e.turn_id + '-context-' + e.seq, 'Context', e.data.pruned_turns + ' earlier turns were removed from model context to fit the token budget. The visible transcript is unchanged.');}
    if (e.kind === 'approval') {approval(e); setPhase('approval');}
    if (e.kind === 'tool_result') {
      removeApproval(e.turn_id);
      const outcome = e.data.result.status === 'denied' ? ' · denied' : e.data.result.status === 'failed' ? ' · failed' : '';
      message(e.turn_id + '-tool-' + e.seq, (toolLabels[e.data.tool] || e.data.tool) + outcome, JSON.stringify(e.data.result, null, 2), false, true);
    }
    if (e.state === 'failed') {fail(e.data.message || e.data.error || 'Operation failed'); removeApproval(e.turn_id); setPhase('failed', 'failed');}
    if (e.state === 'interrupted') {message(e.turn_id + '-stopped', 'Stopped', 'Response interrupted.'); removeApproval(e.turn_id); setPhase('interrupted', 'interrupted');}
    if (e.state === 'completed' && e.kind !== 'cleared') {removeApproval(e.turn_id); setPhase('completed', 'completed'); if (e.data.metrics) showMetric(e.data.metrics);}
    if (e.kind === 'no_speech') message(e.turn_id + '-empty', 'Microphone', 'No speech was recognized. Please try again.');
    if (e.kind === 'cleared') {resetHistory(); setPhase('idle', 'idle');}
  }
  cursor = Math.max(cursor, data.cursor);
}
function approval(e) {
  if (document.getElementById('approval-' + e.turn_id)) return;
  $('empty-state').hidden = true;
  const box = document.createElement('section'); box.className = 'approval'; box.id = 'approval-' + e.turn_id;
  const title = document.createElement('h3'); title.textContent = 'Your confirmation is needed';
  const description = document.createElement('p'); description.textContent = (toolLabels[e.data.tool] || e.data.tool) + '. Only the exact action below will be approved.';
  const args = document.createElement('pre'); args.textContent = JSON.stringify(e.data.arguments, null, 2);
  const countdown = document.createElement('p'); countdown.className = 'countdown';
  const row = document.createElement('div'); row.className = 'row'; box.append(title, description, args, countdown, row);
  const expiry = e.data.expires_at ? e.data.expires_at * 1000 : Date.now() + e.data.expires_in * 1000;
  for (const [label, approve] of [['Confirm action', true], ['Deny', false]]) {
    const button = document.createElement('button'); button.textContent = label; if (!approve) button.className = 'secondary';
    button.onclick = async () => {
      for (const b of row.querySelectorAll('button')) b.disabled = true;
      countdown.textContent = approve ? 'Executing the approved action…' : 'Denying this action…';
      clearInterval(approvalTimers.get(e.turn_id));
      try {await api('/api/approve', {action_id: e.data.action_id, turn_id: e.turn_id, approve}); removeApproval(e.turn_id);}
      catch (error) {fail(error); removeApproval(e.turn_id);}
    }; row.append(button);
  }
  const tick = () => {const seconds = Math.max(0, Math.ceil((expiry - Date.now()) / 1000)); countdown.textContent = seconds ? 'Expires in ' + seconds + 's · Single use' : 'Approval expired. Ask again to request a new action.'; if (!seconds) {row.querySelectorAll('button').forEach(b => b.disabled = true); clearInterval(approvalTimers.get(e.turn_id));}};
  approvalTimers.set(e.turn_id, setInterval(tick, 1000)); tick(); $('history').append(box); box.scrollIntoView({block: 'nearest'});
}
function setListening(enabled, status = {}) {
  listening = !!enabled; $('listen').textContent = enabled ? 'Stop hands-free' : 'Start hands-free';
  $('mute').disabled = !enabled; $('voice-indicator').dataset.active = String(!!enabled || !!status.browser_recording);
  $('audio-state').textContent = status.browser_recording ? 'Browser microphone is on' : enabled ? 'Listening on your Mac' : status.audio_ready ? 'Microphone is muted' : 'Microphone is off';
  $('audio-detail').textContent = status.browser_recording ? 'Finish recording to send your message.' : enabled ? (status.aec ? 'Native voice processing is active. You can interrupt a reply.' : 'Native capture is active; checking voice processing…') : status.audio_ready ? 'The playback engine is ready. Native input is muted.' : 'Start when you’re ready. Text chat is always available.';
  $('listen').disabled = !!status.browser_recording;
}
function definitionList(id, pairs) {
  const root = $(id); root.replaceChildren();
  for (const [label, value] of pairs) {const row = document.createElement('div'); const dt = document.createElement('dt'); const dd = document.createElement('dd'); dt.textContent = label; dd.textContent = value; row.append(dt, dd); root.append(row);}
}
function showMetric(m) {
  $('metric-ttft').textContent = ms(m?.ttft_ms); $('metric-audio').textContent = ms(m?.first_audio_scheduled_ms);
  definitionList('metrics', [['Transcription', ms(m?.stt_ms)], ['First response text', ms(m?.ttft_ms)], ['TTS first chunk', ms(m?.tts_first_chunk_ms)], ['Turn duration', ms(m?.total_ms)]]);
}
function showContext(c = {}) {
  definitionList('context-metrics', [['Prompt tokens', Number.isFinite(c.prompt_tokens) ? c.prompt_tokens.toLocaleString() : '—'], ['Reserved for reply', Number.isFinite(c.reserved_output_tokens) ? c.reserved_output_tokens.toLocaleString() : '—'], ['Context limit', Number.isFinite(c.limit) ? c.limit.toLocaleString() : '—'], ['Pruned turns in last request', String(c.pruned_turns || 0)]]);
}
function showStatus(s) {
  if (!acceptEpoch(s.epoch)) return;
  setListening(s.listening, s); if ((s.cursor ?? cursor) >= cursor) setPhase(s.phase, s.turn_state);
  showContext(s.context);
  activeModels = s.models;
  if (activeModels) $('model-label').textContent = activeModels.llm + ' · ' + activeModels.llm_backend + ' · ' + activeModels.tts_voice + ' voice';
  const metrics = s.metrics || []; const latest = metrics.at(-1); showMetric(latest);
  const samples = metrics.map(m => m.ttft_ms).filter(Number.isFinite).sort((a,b) => a-b);
  $('sample-count').textContent = metrics.length + (metrics.length === 1 ? ' turn' : ' turns');
  $('metric-ttft-note').textContent = samples.length ? 'p95 ' + ms(samples[Math.ceil(samples.length * .95) - 1]) + ' · ' + samples.length + ' recent turns' : 'No completed turns yet';
  $('metric-queue').textContent = (s.turn_queue + s.speech_queue) + ' / ' + s.audio_dropped;
  $('metric-queue-note').textContent = s.errors + ' turn errors · ' + s.playback_chunks + ' playback chunks';
  definitionList('pipeline', [['Capture', s.browser_recording ? 'Browser' : s.listening ? 'Native · active' : 'Off'], ['Voice processing', s.audio_ready ? s.aec ? 'Enabled' : 'Unavailable' : 'Not started'], ['Transcription', s.stt_loaded ? s.models?.stt || 'Loaded' : 'Not loaded'], ['Voice synthesis', s.tts_loaded ? 'Piper · loaded' : 'Not loaded'], ['Turn / speech queue', s.turn_queue + ' / ' + s.speech_queue], ['Capture frames queued', String(s.audio_queue)]]);
}
async function restartService(name, button) {
  if (!confirm('Restart ' + (labels[name] || name) + '? Active work in that service will stop.')) return;
  await busy(button, 'Restarting…', async () => {await api('/api/service/' + name + '/restart', {}); await refresh();});
}
function showServices(data) {
  let total = data.supervisor_rss_bytes || 0, known = true;
  for (const [name, info] of Object.entries(data.services)) {
    let row = serviceRows.get(name);
    if (!row) {
      const root = document.createElement('div'); root.className = 'service-row'; const body = document.createElement('div');
      const dot = document.createElement('span'); dot.className = 'service-dot'; const label = document.createElement('span'); label.className = 'service-name'; label.textContent = labels[name] || name;
      const meta = document.createElement('span'); meta.className = 'service-meta'; const button = document.createElement('button'); button.className = 'quiet'; button.textContent = 'Restart'; button.setAttribute('aria-label', 'Restart ' + (labels[name] || name)); button.onclick = () => restartService(name, button);
      body.append(dot, label, meta); root.append(body, button); $('services').append(root); row = {dot, meta}; serviceRows.set(name, row);
    }
    row.dot.dataset.ready = String(info.ready); row.dot.dataset.failed = String(!!info.error || !info.running);
    row.meta.textContent = (info.error ? info.error : info.ready ? 'Ready' : info.running ? 'Starting' : 'Stopped') + ' · ' + memory(info.rss_bytes);
    if (Number.isFinite(info.rss_bytes)) total += info.rss_bytes; else if (info.running) known = false;
  }
  $('metric-memory').textContent = known ? memory(total) : 'Unavailable';
  $('metric-memory-note').textContent = total > 8 * 1024 ** 3 ? 'Above the 8 GiB workload target' : 'Owned services and their children';
  $('updated').textContent = 'Updated ' + new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'});
}
async function refresh() {
  if (refreshing || !connected) return;
  refreshing = true;
  try {
    const results = await Promise.allSettled([api('/api/status'), api('/api/services')]);
    if (results[0].status === 'fulfilled') showStatus(results[0].value); else {setPhase('failed', 'failed'); $('model-label').textContent = 'Assistant unavailable · retrying'; $('metric-ttft').textContent = '—'; $('metric-audio').textContent = '—'; $('metric-queue').textContent = '—'; $('metric-queue-note').textContent = 'Assistant telemetry unavailable';}
    if (results[1].status === 'fulfilled') showServices(results[1].value); else {$('updated').textContent = 'Telemetry unavailable'; $('metric-memory').textContent = '—'; for (const row of serviceRows.values()) {row.dot.dataset.ready = 'false'; row.meta.textContent = 'State unknown · connection unavailable';}}
    const denied = results.find(r => r.status === 'rejected' && r.reason.status === 401);
    if (denied) {disconnect(); fail(new Error('Session expired. Run macbot open to reconnect.'));}
  } finally {refreshing = false;}
}
function scheduleRefresh() {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {if (connected && !document.hidden) await refresh(); if (connected) scheduleRefresh();}, 2500);
}
async function documents() {
  const data = await api('/api/documents'); $('documents').replaceChildren(); $('document-count').textContent = data.documents.length;
  if (!data.documents.length) {const p = document.createElement('p'); p.className = 'caption'; p.textContent = 'No documents yet. Import a file to make it searchable.'; $('documents').append(p);}
  for (const doc of data.documents) {
    const row = document.createElement('div'); row.className = 'document-row'; const title = document.createElement('span'); title.textContent = doc.title;
    const button = document.createElement('button'); button.className = 'quiet'; button.textContent = 'Delete'; button.setAttribute('aria-label', 'Delete ' + doc.title);
    button.onclick = async () => {if (confirm('Delete this document: ' + doc.title + '?')) await busy(button, 'Deleting…', async () => {await api('/api/documents/' + encodeURIComponent(doc.id), undefined, 'DELETE'); await documents(); $('results').replaceChildren();});};
    row.append(title, button); $('documents').append(row);
  }
}
async function connect() {
  socket?.disconnect(); clearTimeout(pollTimer);
  // A settings read also verifies the browser session before showing the workspace.
  const settings = await api('/api/settings'); connected = true;
  $('login').hidden = true; $('application').hidden = false; $('logout').hidden = false;
  $('voice').replaceChildren(); for (const voice of settings.voices) {const option = document.createElement('option'); option.value = option.textContent = voice; $('voice').append(option);}
  $('voice').value = settings.models.tts_voice; $('tokens').value = settings.models.max_tokens; $('speed').value = settings.models.tts_speed;
  socket = io({auth: {csrf}, transports: ['websocket', 'polling']});
  socket.on('turn_events', events);
  socket.on('connect', () => {connection('Connected locally', 'good'); api('/api/events?after=' + cursor + (epoch ? '&epoch=' + encodeURIComponent(epoch) : '')).then(events).catch(fail);});
  socket.on('disconnect', () => connection('Reconnecting…', 'warn'));
  socket.on('connect_error', () => connection('Live updates unavailable', 'warn'));
  await refresh(); scheduleRefresh(); await documents();
}
function disconnect() {
  connected = false; clearTimeout(pollTimer); socket?.disconnect(); socket = null; csrf = ''; sessionStorage.removeItem('macbot_csrf');
  resetHistory(); cursor = 0; epoch = ''; $('application').hidden = true; $('login').hidden = false; $('logout').hidden = true; connection('Not connected', 'neutral');
}
$('login-form').onsubmit = async e => {e.preventDefault(); await busy(e.submitter, 'Connecting…', async () => {const data = await api('/auth/exchange', {token: $('login-code').value}); csrf = data.csrf; sessionStorage.setItem('macbot_csrf', csrf); $('login-code').value = ''; await connect();});};
$('chat-form').onsubmit = async e => {
  e.preventDefault(); if (sending || !$('message').value.trim()) return; sending = true;
  const text = $('message').value;
  await busy($('send'), 'Sending…', async () => {await api('/api/chat', {message: text, speak: $('speak').checked}); if ($('message').value === text) $('message').value = ''; setPhase('queued', 'accepted');}); sending = false;
};
$('message').onkeydown = e => {if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {e.preventDefault(); $('chat-form').requestSubmit($('send'));}};
document.querySelectorAll('[data-prompt]').forEach(button => button.onclick = () => {$('message').value = button.dataset.prompt; $('message').focus();});
$('listen').onclick = () => busy($('listen'), listening ? 'Stopping…' : 'Starting…', async () => {const d = await api('/api/listen', {enabled: !listening}); setListening(d.listening, d);}).then(refresh);
$('mute').onclick = () => busy($('mute'), 'Muting…', async () => {await api('/api/listen', {enabled: false}); setListening(false);});
$('interrupt').onclick = () => busy($('interrupt'), 'Stopping…', async () => {await api('/api/interrupt', {}); setPhase('interrupted', 'interrupted');});
$('clear').onclick = () => busy($('clear'), 'Clearing…', async () => {await api('/api/clear', {}); resetHistory();});
$('preview').onclick = () => busy($('preview'), 'Preparing…', async () => {await api('/api/preview-voice', {text: 'Hello. This is the active local voice.'});});
$('settings').onsubmit = async e => {e.preventDefault(); await busy($('save-settings'), 'Saving…', async () => {await api('/api/settings', {tts_voice: $('voice').value, tts_speed: Number($('speed').value), max_tokens: Number($('tokens').value)}); pendingSettings = true; $('settings-note').textContent = 'Saved. The current voice stays active until the assistant restarts.'; $('apply-settings').hidden = false;});};
$('apply-settings').onclick = async () => {await restartService('assistant', $('apply-settings')); if (activeModels && activeModels.tts_voice === $('voice').value && activeModels.tts_speed === Number($('speed').value) && activeModels.max_tokens === Number($('tokens').value)) {pendingSettings = false; $('apply-settings').hidden = true; $('settings-note').textContent = 'Settings are active.';}};
$('upload').onsubmit = async e => {e.preventDefault(); if (!$('files').files.length) {fail(new Error('Choose at least one document first.')); return;} const data = new FormData(); for (const file of $('files').files) data.append('files', file); await busy($('import-button'), 'Importing…', async () => {const result = await api('/api/upload-documents', data); await documents(); $('files').value = ''; $('document-status').textContent = 'Import completed.'; if (result.errors?.length) fail(new Error(JSON.stringify(result.errors)));});};
$('search').onsubmit = async e => {e.preventDefault(); await busy(e.submitter, 'Searching…', async () => {const data = await api('/api/search', {query: $('query').value}); $('results').replaceChildren(); if (!data.results.length) {const p = document.createElement('p'); p.className = 'caption'; p.textContent = 'No matching passages found. Try a different phrase.'; $('results').append(p);} for (const result of data.results) {const card = document.createElement('article'); card.className = 'result'; const title = document.createElement('h3'); title.textContent = result.metadata.title; const p = document.createElement('p'); p.textContent = result.content; const source = document.createElement('small'); source.textContent = 'Chunk ' + (Number(result.metadata.chunk) + 1) + ' · Local document'; card.append(title, p, source); $('results').append(card);}});};
$('ptt').onclick = async () => {
  let stream;
  try {
    clearError(); if (recorder?.state === 'recording') {recorder.stop(); return;}
    $('ptt').disabled = true;
    const lease = await api('/api/browser-recording', {enabled: true}); setListening(false, {browser_recording: true});
    stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true}});
    recorder = new MediaRecorder(stream); const activeRecorder = recorder; const activeStream = stream; const chunks = []; let deadline;
    recorder.ondataavailable = e => {if (e.data.size) chunks.push(e.data);};
    recorder.onerror = () => {if (activeRecorder.state !== 'inactive') activeRecorder.stop(); fail(new Error('Browser recording failed. Try again or use native capture.'));};
    recorder.onstop = async () => {
      clearTimeout(deadline); activeStream.getTracks().forEach(t => t.stop()); $('ptt').disabled = true; $('ptt').textContent = 'Transcribing…';
      try {const blob = new Blob(chunks, {type: activeRecorder.mimeType}); const audio = await new Promise((resolve, reject) => {const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = () => reject(new Error('Could not read recorded audio')); reader.readAsDataURL(blob);}); await api('/api/voice', {audio});}
      catch (e) {fail(e);} finally {await api('/api/browser-recording', {enabled: false}).catch(fail); $('ptt').textContent = 'Record browser voice'; $('ptt').disabled = false; setListening(false); await refresh();}
    };
    recorder.start(); $('ptt').textContent = 'Finish recording'; $('ptt').disabled = false;
    deadline = setTimeout(() => {if (activeRecorder.state === 'recording') activeRecorder.stop();}, Math.min(29000, lease.max_duration_ms - 100));
  } catch (e) {stream?.getTracks().forEach(t => t.stop()); await api('/api/browser-recording', {enabled: false}).catch(fail); $('ptt').disabled = false; setListening(false); fail(e);}
};
function selectTab(button) {document.querySelectorAll('[role=tab]').forEach(tab => {const selected = tab === button; tab.setAttribute('aria-selected', selected); tab.tabIndex = selected ? 0 : -1; $(tab.getAttribute('aria-controls')).hidden = !selected;});}
const tabs = Array.from(document.querySelectorAll('[role=tab]'));
tabs.forEach((button, index) => {button.onclick = () => selectTab(button); button.onkeydown = e => {let next; if (e.key === 'ArrowRight') next = (index + 1) % tabs.length; if (e.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length; if (e.key === 'Home') next = 0; if (e.key === 'End') next = tabs.length - 1; if (next !== undefined) {e.preventDefault(); selectTab(tabs[next]); tabs[next].focus();}};});
$('dismiss-error').onclick = clearError;
$('logout').onclick = async () => {if (recorder?.state === 'recording' || listening) {fail(new Error('Stop microphone capture before disconnecting.')); return;} await busy($('logout'), 'Disconnecting…', async () => {await api('/auth/logout', {}); disconnect();});};
document.addEventListener('visibilitychange', () => {if (!document.hidden && connected) refresh();});
window.addEventListener('beforeunload', e => {if (recorder?.state === 'recording' || pendingSettings) {e.preventDefault(); e.returnValue = '';}});
(async () => {
  const fragment = new URLSearchParams(location.hash.slice(1)); const token = fragment.get('token'); history.replaceState(null, '', location.pathname);
  if (token) {try {const data = await api('/auth/exchange', {token}); csrf = data.csrf; sessionStorage.setItem('macbot_csrf', csrf);} catch (e) {fail(e);}}
  if (csrf) {try {await connect();} catch (e) {fail(e); connection('Login required', 'warn');}}
})();
