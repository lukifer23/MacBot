/* MacBot Dashboard JS (externalized)
 * Robust event wiring + HTTP/SSE/Socket.IO integration.
 */
(function () {
  const state = {
    socket: null,
    sse: null,
    isRecording: false,
    mediaRecorder: null,
    audioChunks: [],
    mode: 'ptt', // 'ptt' | 'conversational'
    audioCtx: null,
    analyser: null,
    source: null,
    raf: null,
    services: {},
    voices: [],
  };

  const byId = (id) => document.getElementById(id);

  function setStatus(text, type) {
    const el = byId('status-banner');
    if (!el) return;
    el.style.display = text ? 'inline-block' : 'none';
    el.textContent = text || '';
    el.className = 'status-banner';
    if (type) el.classList.add('status-' + type);
  }
  window.setStatus = setStatus;

  function addChatMessage(message, sender) {
    const history = byId('chat-history');
    if (!history) return;
    const div = document.createElement('div');
    div.className = 'chat-message chat-' + (sender || 'system');
    div.textContent = message;
    history.appendChild(div);
    history.scrollTop = history.scrollHeight;
  }
  window.addChatMessage = addChatMessage;

  async function sendMessageHTTP(message) {
    try {
      const r = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message })
      });
      if (!r.ok) {
        const t = await r.text();
        addChatMessage('❌ Error: ' + t, 'system');
        return;
      }
      const data = await r.json();
      const reply = (data && (data.response || (data.data && data.data.response))) || 'No response';
      addChatMessage(reply, 'assistant');
      if (isSpeakEnabled()) speakViaAssistant(reply);
    } catch (e) {
      addChatMessage('❌ Network error: ' + e.message, 'system');
    }
  }
  window.sendMessageHTTP = sendMessageHTTP;

  function renderStats(data) {
    if (!data) return;
    const cpu = byId('cpu-usage');
    const ram = byId('ram-usage');
    const disk = byId('disk-usage');
    const net = byId('network-usage');
    if (cpu && data.cpu != null) cpu.textContent = data.cpu + '%';
    if (ram && data.ram != null) ram.textContent = data.ram + '%';
    if (disk && data.disk != null) disk.textContent = data.disk + '%';
    if (net && data.network) {
      const total = (data.network.bytes_sent || 0) + (data.network.bytes_recv || 0);
      net.textContent = formatBytes(total);
    }
  }
  window.renderStats = renderStats;

  function renderServiceStatus(data) {
    if (!data) return;
    state.services = data;
    const llm = byId('llm-status');
    const voice = byId('voice-status');
    const rag = byId('rag-status');
    const web = byId('web-status');
    if (llm) llm.innerHTML = 'Status: <span class="status-dot">' + (data.llama && data.llama.status === 'running' ? '🟢</span> Running' : '🔴</span> Stopped');
    if (voice) voice.innerHTML = 'Status: <span class="status-dot">' + (data.voice_assistant && data.voice_assistant.status === 'running' ? '🟢</span> Running' : '🔴</span> Stopped');
    if (rag) rag.innerHTML = 'Status: <span class="status-dot">' + (data.rag && data.rag.status === 'running' ? '🟢</span> Running' : '🔴</span> Stopped');
    if (web) web.innerHTML = 'Status: <span class="status-dot">' + (data.web_gui && data.web_gui.status === 'running' ? '🟢</span> Running' : '🔴</span> Stopped');
  }
  window.renderServiceStatus = renderServiceStatus;

  async function loadVoices() {
    try {
      const r = await fetch('http://localhost:8123/voices');
      const j = await r.json();
      if (!j.ok) return;
      state.voices = j.voices || [];
      const sel = byId('voice-select');
      if (!sel) return;
      sel.innerHTML = '';
      state.voices.forEach(v => {
        const opt = document.createElement('option');
        opt.value = v.path; opt.textContent = v.name;
        if (j.current && j.current === v.path) opt.selected = true;
        sel.appendChild(opt);
      });
    } catch (_) {}
  }

  function formatBytes(bytes) {
    if (!bytes) return '0 B';
    const k = 1024; const sizes = ['B','KB','MB','GB'];
    const i = Math.floor(Math.log(bytes)/Math.log(k));
    return (bytes/Math.pow(k,i)).toFixed(1) + ' ' + sizes[i];
  }

  async function updateStats() {
    try {
      const r = await fetch('/api/stats');
      const data = await r.json();
      renderStats(data);
    } catch (e) { console.warn('stats failed', e); }
  }
  window.updateStats = updateStats;

  async function updateServiceStatus() {
    try {
      const r = await fetch('/api/services');
      const data = await r.json();
      renderServiceStatus(data);
    } catch (e) { console.warn('services failed', e); }
  }
  window.updateServiceStatus = updateServiceStatus;

  function attachListeners() {
    const sendBtn = byId('chat-button');
    const input = byId('chat-input');
    const refreshBtn = byId('refresh-stats-btn');
    const clearBtn = byId('clear-chat-btn');
    const selfCheckBtn = byId('self-check-btn');
    const micBtn = byId('mic-access-btn');
    const voiceBtn = byId('voice-button');
    const pttBtn = byId('ptt-btn');
    const convBtn = byId('conv-btn');
    const endBtn = byId('end-voice-btn');

    if (sendBtn) sendBtn.addEventListener('click', onSend);
    if (input) input.addEventListener('keypress', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); onSend(); }
    });
    if (refreshBtn) refreshBtn.addEventListener('click', updateStats);
    if (clearBtn) clearBtn.addEventListener('click', () => {
      const history = byId('chat-history');
      if (history) history.innerHTML = '';
      addChatMessage('🧹 Conversation history cleared', 'system');
      setStatus('Ready', 'info');
      if (state.socket) state.socket.emit('clear_conversation');
    });
    if (micBtn) micBtn.addEventListener('click', requestMic);
    if (voiceBtn) voiceBtn.addEventListener('click', onVoiceToggle);
    if (pttBtn) pttBtn.addEventListener('click', () => { setMode('ptt'); addChatMessage('PTT mode enabled', 'system'); });
    if (convBtn) convBtn.addEventListener('click', () => { setMode('conversational'); addChatMessage('Conversational mode enabled', 'system'); });
    if (endBtn) endBtn.addEventListener('click', endConversation);
    if (selfCheckBtn) selfCheckBtn.addEventListener('click', runSelfCheck);
    const prevBtn = byId('preview-voice-btn');
    const applyBtn = byId('apply-voice-btn');
    if (prevBtn) prevBtn.addEventListener('click', async ()=>{
      const sel = byId('voice-select'); if (!sel || !sel.value) return;
      try { await fetch('http://localhost:8123/preview-voice', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ text: 'Hey there, how can I help?' })}); } catch(_){}
    });
    if (applyBtn) applyBtn.addEventListener('click', async ()=>{
      const sel = byId('voice-select'); if (!sel || !sel.value) return;
      try { await fetch('http://localhost:8123/set-voice', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ voice_path: sel.value })});
        setTimeout(updateMetrics, 500);
      } catch(_){}
    });
    // Spacebar PTT (only when focus is not in a text input/textarea)
    document.addEventListener('keydown', (e) => {
      const tag = (e.target && e.target.tagName) ? e.target.tagName.toLowerCase() : '';
      if (state.mode === 'ptt' && e.code === 'Space' && !e.repeat && tag !== 'input' && tag !== 'textarea') {
        e.preventDefault();
        if (!state.isRecording) onVoiceToggle();
      }
    });
    document.addEventListener('keyup', (e) => {
      const tag = (e.target && e.target.tagName) ? e.target.tagName.toLowerCase() : '';
      if (state.mode === 'ptt' && e.code === 'Space' && tag !== 'input' && tag !== 'textarea') {
        e.preventDefault();
        if (state.isRecording) onVoiceToggle();
      }
    });

    // default
    setMode('ptt');
  }
  async function runSelfCheck() {
    try {
      setStatus('Running self-check...', 'info');
      const r = await fetch('/api/pipeline-check');
      const j = await r.json();
      if (!r.ok) { addChatMessage('❌ Self-check failed: ' + (j.error || r.status), 'system'); return; }
      const lines = [];
      const ok = (b) => b ? '✅' : '❌';
      lines.push(`${ok(j.llm && j.llm.ok)} LLM`);
      lines.push(`${ok(j.stt && j.stt.bin_exists)} whisper-cli`);
      lines.push(`${ok(j.stt && j.stt.model_exists)} whisper model`);
      lines.push(`${ok(j.tts && j.tts.ok)} TTS engine`);
      lines.push(`${ok(j.rag && j.rag.ok)} RAG`);
      addChatMessage('Self-check: ' + (j.overall ? '✅ OK' : '❌ Issues found') + '\n' + lines.join('\n'), 'system');
      setStatus('Ready', 'info');
    } catch (e) {
      addChatMessage('❌ Self-check error: ' + e.message, 'system');
      setStatus('Ready', 'info');
    }
  }

  function onSend() {
    const input = byId('chat-input');
    if (!input) return;
    const msg = (input.value || '').trim();
    if (!msg) return;
    addChatMessage(msg, 'user');
    setStatus('Assistant is thinking...', 'speaking');
    input.value = '';
    if (state.socket && state.socket.connected) {
      state.socket.emit('chat_message', { message: msg });
    } else {
      sendMessageHTTP(msg);
    }
  }

  async function onVoiceToggle() {
    if (state.speakingNow) { addChatMessage('🔇 Assistant is speaking; mic paused.', 'system'); return; }
    if (!state.isRecording) {
      try {
        if (!isSecureOrigin()) {
          addChatMessage('❌ Microphone requires https or http://localhost/127.0.0.1. Please open the dashboard at http://localhost:3000', 'system');
          return;
        }
        const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
        state.audioChunks = [];
        // Choose a compatible container across browsers
        let mime = '';
        const candidates = ['audio/webm;codecs=opus','audio/ogg;codecs=opus','audio/mp4','audio/webm','audio/ogg'];
        if (window.MediaRecorder && typeof MediaRecorder.isTypeSupported === 'function') {
          for (const c of candidates) { if (MediaRecorder.isTypeSupported(c)) { mime = c; break; } }
        }
        try {
          state.mediaRecorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
        } catch (_) {
          state.mediaRecorder = new MediaRecorder(stream);
        }
        state.mediaRecorder.ondataavailable = (e) => { if (e.data && e.data.size) state.audioChunks.push(e.data); };
        state.mediaRecorder.onstop = async () => {
          const blob = new Blob(state.audioChunks, { type: 'audio/webm' });
          await processVoiceBlob(blob);
          if (state.mode === 'conversational') {
            // small delay then restart
            setTimeout(() => { try { onVoiceToggle(); } catch(_){} }, 500);
          }
        };
        state.mediaRecorder.start(100);
        state.isRecording = true;
        setStatus('Listening...', 'listening');
        setupWaveform(stream);
        const vb = byId('voice-button'); if (vb) vb.classList.add('recording');
      } catch (e) {
        addChatMessage('❌ Microphone access denied. Please allow microphone access.', 'system');
      }
    } else {
      try { state.mediaRecorder && state.mediaRecorder.stop(); } catch(_){}
      try { state.mediaRecorder && state.mediaRecorder.stream.getTracks().forEach(t => t.stop()); } catch(_){}
      state.isRecording = false;
      setStatus('Processing voice...', 'info');
      teardownWaveform();
      const vb = byId('voice-button'); if (vb) vb.classList.remove('recording');
    }
  }

  async function processVoiceBlob(blob) {
    try {
      const b64 = await blobToBase64(blob); // full DataURL
      const r = await fetch('/api/voice', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ audio: b64 })
      });
      if (!r.ok) {
        addChatMessage('❌ Voice processing failed', 'system'); setStatus('Ready','info'); return;
      }
      const data = await r.json();
      const text = (data && (data.transcription || (data.data && data.data.transcription))) || '';
      if (!text || text.trim().toLowerCase() === 'no speech detected') { addChatMessage('🔇 No speech detected', 'system'); setStatus('Ready','info'); return; }
      addChatMessage(text, 'user');
      setStatus('Assistant is thinking...', 'speaking');
      if (state.socket && state.socket.connected) state.socket.emit('chat_message', { message: text });
      else await sendMessageHTTP(text);
    } catch (e) {
      addChatMessage('❌ Voice processing error: ' + e.message, 'system'); setStatus('Ready','info');
    }
  }

  function blobToBase64(blob) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onloadend = () => resolve(reader.result);
      reader.onerror = reject;
      reader.readAsDataURL(blob);
    });
  }

  function setMode(m) {
    state.mode = m;
    const pttBtn = byId('ptt-btn');
    const convBtn = byId('conv-btn');
    const endBtn = byId('end-voice-btn');
    const modeBadge = byId('mode-badge');
    if (pttBtn) pttBtn.classList.toggle('active', m === 'ptt');
    if (convBtn) convBtn.classList.toggle('active', m === 'conversational');
    if (endBtn) endBtn.style.display = (m === 'conversational') ? 'inline-block' : 'none';
    if (modeBadge) modeBadge.textContent = 'Mode: ' + (m === 'ptt' ? 'PTT' : 'Conversational (auto-stop)');
  }

  function endConversation() {
    if (state.isRecording) {
      try { state.mediaRecorder.stop(); } catch(_){}
      try { state.mediaRecorder.stream.getTracks().forEach(t => t.stop()); } catch(_){}
      state.isRecording = false;
      teardownWaveform();
    }
    setMode('ptt');
    setStatus('Ready', 'info');
  }

  function setupWaveform(stream) {
    try {
      const wrap = byId('waveform-wrap');
      const canvas = byId('waveform-canvas');
      if (!wrap || !canvas) return;
      wrap.style.display = 'flex';
      state.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      state.source = state.audioCtx.createMediaStreamSource(stream);
      state.analyser = state.audioCtx.createAnalyser();
      state.analyser.fftSize = 256;
      state.source.connect(state.analyser);
      const ctx = canvas.getContext('2d');
      const bufferLength = state.analyser.frequencyBinCount;
      const dataArray = new Uint8Array(bufferLength);
      let lastActive = Date.now();
      const energyThresh = 0.02; // ~silence threshold
      const silenceMs = 900; // auto-stop after this much silence
      function draw() {
        state.raf = requestAnimationFrame(draw);
        state.analyser.getByteTimeDomainData(dataArray);
        const w = canvas.width = canvas.clientWidth;
        const h = canvas.height = canvas.clientHeight;
        ctx.clearRect(0,0,w,h);
        ctx.lineWidth = 2; ctx.strokeStyle = '#007aff'; ctx.beginPath();
        const slice = w / bufferLength;
        let x = 0;
        let energy = 0;
        for (let i=0;i<bufferLength;i++) {
          const v = dataArray[i] / 128.0; const y = v * h/2;
          if (i === 0) ctx.moveTo(x,y); else ctx.lineTo(x,y);
          x += slice;
          const dv = (dataArray[i] - 128) / 128.0; energy += dv*dv;
        }
        ctx.lineTo(w, h/2); ctx.stroke();

        energy = Math.sqrt(energy / bufferLength);
        if (energy > energyThresh) lastActive = Date.now();
        if (state.isRecording && state.mode === 'conversational') {
          if (Date.now() - lastActive > silenceMs) {
            // Auto-stop to process the utterance
            try { state.mediaRecorder && state.mediaRecorder.stop(); } catch(_){}
            try { state.mediaRecorder && state.mediaRecorder.stream.getTracks().forEach(t => t.stop()); } catch(_){}
            state.isRecording = false;
            setStatus('Processing voice...', 'info');
            teardownWaveform();
            const vb = byId('voice-button'); if (vb) vb.classList.remove('recording');
          }
        }
      }
      draw();
    } catch (e) { /* ignore */ }
  }

  function teardownWaveform() {
    try { if (state.raf) cancelAnimationFrame(state.raf); } catch(_){}
    state.raf = null;
    try { if (state.audioCtx) state.audioCtx.close(); } catch(_){}
    state.audioCtx = null; state.analyser = null; state.source = null;
    const wrap = byId('waveform-wrap'); if (wrap) wrap.style.display = 'none';
  }

  async function requestMic() {
    try {
      // Always test browser mic permission first
      await navigator.mediaDevices.getUserMedia({ audio: true });
      setStatus('Browser mic permission granted', 'info');
    } catch (e) {
      setStatus('Browser mic permission denied', 'interrupted');
    }
    try {
      // Only ping assistant if it is running
      const svc = state.services && state.services.voice_assistant;
      if (!svc || svc.status !== 'running') {
        setStatus('Assistant not running', 'interrupted');
        return;
      }
      const r = await fetch('/api/mic-check', { method: 'POST' });
      const data = await r.json();
      if (r.ok && data.ok) setStatus('Assistant mic ready', 'info');
      else setStatus('Assistant mic check failed', 'interrupted');
    } catch (e) { setStatus('Assistant mic check error', 'interrupted'); }
  }

  function initSocket() {
    if (typeof io !== 'function') return;
    try {
      state.socket = io({ transports: ['websocket','polling'], reconnection: true });
      state.socket.on('connect', () => console.log('WS connected'));
      state.socket.on('disconnect', () => console.log('WS disconnected'));
      state.socket.on('conversation_update', (data) => {
        if (!data) return;
        if (data.type === 'assistant_message') {
          addChatMessage(data.content, 'assistant'); setStatus('Ready', 'info');
          if (isSpeakEnabled()) speakViaAssistant(data.content);
        } else if (data.type === 'user_message') {
          addChatMessage(data.content, 'user');
        } else if (data.type === 'error_message') {
          addChatMessage(data.content, 'system'); setStatus('Ready','info');
        }
      });
      state.socket.on('assistant_state', (data) => {
        if (!data || !data.type) return;
        if (data.type === 'speaking_started') {
          state.speakingNow = true;
          if (state.isRecording) { try { state.mediaRecorder && state.mediaRecorder.stop(); } catch(_){} }
        } else if (data.type === 'speaking_ended' || data.type === 'speaking_interrupted') {
          setTimeout(()=>{ state.speakingNow = false; }, 250);
        }
      });
    } catch (e) { console.warn('WS init failed', e); }
  }

  async function speakViaAssistant(text) {
    try {
      const svc = state.services && state.services.voice_assistant;
      if (!svc || svc.status !== 'running') return; // avoid 500 spam when VA is down
      await fetch('/api/assistant-speak', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text })
      });
    } catch (_) {}
  }

  function isSpeakEnabled() {
    const el = document.getElementById('speak-toggle');
    return el ? !!el.checked : true;
  }

  function initSSE() {
    try {
      state.sse = new EventSource('/stream');
      state.sse.onmessage = (ev) => {
        try {
          const p = JSON.parse(ev.data);
          if (p.system_stats) renderStats(p.system_stats);
          if (p.service_status) renderServiceStatus(p.service_status);
        } catch (e) { /* ignore */ }
      };
      state.sse.onerror = () => { try { state.sse.close(); } catch (e) {} };
    } catch (e) { /* ignore */ }
  }

  function boot() {
    attachListeners();
    if (!isSecureOrigin()) {
      addChatMessage('ℹ️ Tip: Open the dashboard at http://localhost:3000 or http://127.0.0.1:3000 to enable microphone access (required by browsers).', 'system');
    }
    updateStats();
    updateServiceStatus();
    updateMetrics();
    initSocket();
    initSSE();
    setInterval(updateStats, 5000);
    setInterval(updateServiceStatus, 5000);
    setInterval(updateMetrics, 10000);
    loadVoices();
  }
  function isSecureOrigin() {
    // Browsers allow mic on https or on localhost/127.0.0.1
    const h = location.hostname;
    return location.protocol === 'https:' || h === 'localhost' || h === '127.0.0.1';
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
  async function updateMetrics() {
    try {
      const r = await fetch('/api/metrics');
      if (!r.ok) return;
      const m = await r.json();
      renderModelStatus(m);
    } catch (_) {}
  }

  function renderModelStatus(m) {
    if (!m) return;
    const llm = m.llm || {};
    const va = m.voice_assistant || {};
    const rag = m.rag || {};
    const elLlm = byId('model-llm');
    const elCtx = byId('model-ctx');
    const elMem = byId('model-mem');
    const elStt = byId('model-stt');
    const elTts = byId('model-tts');
    if (elLlm) elLlm.textContent = 'LLM: ' + (llm.model || 'unknown');
    if (elCtx) elCtx.textContent = 'Context: ' + (llm.context ?? '—') + ' • Threads: ' + (llm.threads ?? '—') + ' • GPU layers: ' + (llm.gpu_layers ?? '—');
    if (elMem) elMem.textContent = 'Memory: ' + ((llm.rss_mb != null) ? (llm.rss_mb + ' MB RSS') : '—');
    if (elStt) { const s = va.stt || {}; elStt.textContent = 'STT: ' + [s.impl, s.model].filter(Boolean).join(' • '); }
    if (elTts) { const t = va.tts || {}; elTts.textContent = 'TTS: ' + [t.engine, t.voice, t.engine_loaded ? 'loaded' : ''].filter(Boolean).join(' • '); }
  }
})();
