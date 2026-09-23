/* ══════════════════════════════════════════════════════════════
   ARIA v2.0 — AI Voice Desktop Assistant · app.js
   Multi-Model Architecture: Gateway + Router + Orchestrator
══════════════════════════════════════════════════════════════ */

// ─── Config ──────────────────────────────────────────────────
const BACKEND_URL      = 'http://localhost:5000/api/chat';
const ORCHESTRATE_URL  = 'http://localhost:5000/api/orchestrate';
const AGENT_URL        = 'http://localhost:5000/api/agent';
const MODELS_URL       = 'http://localhost:5000/api/models';
const UPLOAD_DOC_URL   = 'http://localhost:5000/api/upload-doc';
const DOC_QUERY_URL    = 'http://localhost:5000/api/doc-query';
const DOC_EDIT_URL     = 'http://localhost:5000/api/doc-edit';
const DOC_DOWNLOAD_URL = 'http://localhost:5000/api/doc-download';
const AGENT_DL_URL     = 'http://localhost:5000/api/agent/download';
const STT_URL          = 'http://localhost:5000/api/stt';
const MEMORY_UPLOAD_URL  = 'http://localhost:5000/api/memory/upload';
const MEMORY_DOCS_URL    = 'http://localhost:5000/api/memory/docs';
const MEMORY_PROMOTE_URL = 'http://localhost:5000/api/memory/promote';
const MEMORY_CLEAR_URL   = 'http://localhost:5000/api/memory/clear';
// New feature endpoints
const OCR_IMAGE_URL    = 'http://localhost:5000/api/ocr-image';
const EXPORT_DRAFT_URL = 'http://localhost:5000/api/export-draft';


// ─── State ───────────────────────────────────────────────────
const state = {
  history: [],
  memories: [],
  memoryDocs: [],
  msgCount: 0,
  isLoading: false,
  isListening: false,
  currentTool: null,
  activeTimers: [],
  calcExpr: '',
  calcDisplay: '0',
  calcJustResult: false,
  availableModels: [],
  // ─── Document Q&A state ───
  doc: {
    id: null,
    filename: null,
    charCount: 0,
    history: [],   // Q&A turns for this document session
    isLoading: false,
    downloadToken: null,  // latest edit download token
    text: null,
  },
  settings: {
    model: 'llama3.2',
    maxTokens: 1024,
    assistantName: 'ARIA',
    systemPrompt: 'You are ARIA (Adaptive Reasoning Intelligence Assistant), a helpful and concise AI desktop assistant. When acknowledging voice commands like opening websites or apps, keep your reply to 1 short sentence. For questions and conversations, be clear and helpful. Use markdown for code or lists.',
    accent: '#00e5ff',
    fontSize: '15px',
    voiceEnabled: true,
    voiceRate: 1,
    voicePitch: 1,
    voiceName: '',
  },
  ttsMuted: true,  // starts muted — browsers block autoplay until user gesture
  deepThink: false, // when true, sends to /api/orchestrate instead of /api/chat
  agentRunning: false,
};

// ─── WEBSITE MAP ─────────────────────────────────────────────
const SITE_MAP = {
  'youtube':       'https://youtube.com',
  'google':        'https://google.com',
  'gmail':         'https://mail.google.com',
  'github':        'https://github.com',
  'netflix':       'https://netflix.com',
  'spotify':       'https://open.spotify.com',
  'wikipedia':     'https://wikipedia.org',
  'twitter':       'https://twitter.com',
  'x':             'https://x.com',
  'whatsapp':      'https://web.whatsapp.com',
  'whatsapp web':  'https://web.whatsapp.com',
  'chatgpt':       'https://chat.openai.com',
  'maps':          'https://maps.google.com',
  'google maps':   'https://maps.google.com',
  'amazon':        'https://amazon.in',
  'reddit':        'https://reddit.com',
  'linkedin':      'https://linkedin.com',
  'facebook':      'https://facebook.com',
  'instagram':     'https://instagram.com',
  'stackoverflow': 'https://stackoverflow.com',
  'stack overflow':'https://stackoverflow.com',
  'claude':        'https://claude.ai',
  'news':          'https://news.google.com',
  'google news':   'https://news.google.com',
  'drive':         'https://drive.google.com',
  'google drive':  'https://drive.google.com',
  'docs':          'https://docs.google.com',
  'sheets':        'https://sheets.google.com',
  'calendar':      'https://calendar.google.com',
  'meet':          'https://meet.google.com',
  'zoom':          'https://zoom.us',
  'canva':         'https://canva.com',
  'figma':         'https://figma.com',
  'codepen':       'https://codepen.io',
  'replit':        'https://replit.com',
  'vercel':        'https://vercel.com',
  'notion':        'https://notion.so',
};

// ─── Load Models from Backend ─────────────────────────────────
async function loadModels() {
  try {
    const res = await fetch(MODELS_URL);
    if (res.ok) {
      const data = await res.json();
      state.availableModels = data.models || [];
      populateModelDropdown();
    }
  } catch (err) {
    console.log('Could not fetch models from backend:', err.message);
  }
}

function populateModelDropdown() {
  const select = document.getElementById('model-select');
  if (!select || state.availableModels.length === 0) return;
  select.innerHTML = '';
  state.availableModels.forEach(m => {
    const opt = document.createElement('option');
    opt.value = m.id;
    opt.textContent = m.name;
    opt.title = m.description;
    select.appendChild(opt);
  });
  select.value = state.settings.model;
  if (!select.value && state.availableModels.length > 0) {
    select.value = state.availableModels[0].id;
    state.settings.model = select.value;
  }
}

// ─── VOICE COMMAND ROUTER ────────────────────────────────────
async function runVoiceCommand(rawText) {
  const text = rawText.trim();
  if (!text) return;

  removeWelcome();
  appendMessage('user', `🎤 ${text}`);
  state.history.push({ role: 'user', content: text });
  updateMsgCount();

  const lower = text.toLowerCase();

  // ── 1. OPEN WEBSITE ──
  const openMatch = lower.match(/^(?:open|go to|launch|visit|take me to)\s+(.+)$/);
  if (openMatch) {
    const target = openMatch[1].trim();
    const url = SITE_MAP[target];
    if (url) {
      window.open(url, '_blank');
      const reply = `Opening ${capitalize(target)} for you! 🚀`;
      respond(reply);
      flashAction(`✓ Opened ${capitalize(target)}`);
      return;
    }
  }

  // ── 2. BUILT-IN APPS ──
  if (/open calculator|launch calculator|calculator/i.test(lower) && !/search/i.test(lower)) {
    document.getElementById('calc-modal').style.display = 'flex';
    respond('Calculator is open! 🧮');
    flashAction('✓ Calculator opened');
    return;
  }

  if (/open notepad|notepad|open notes|take a note/i.test(lower)) {
    document.getElementById('notepad-modal').style.display = 'flex';
    respond('Notepad is open! Start typing your notes. 📝');
    flashAction('✓ Notepad opened');
    return;
  }

  // ── 3. SEARCH ──
  const ytSearchMatch = lower.match(/search (?:on )?youtube (?:for\s+)?(.+)/);
  if (ytSearchMatch) {
    const q = ytSearchMatch[1].trim();
    window.open(`https://www.youtube.com/results?search_query=${encodeURIComponent(q)}`, '_blank');
    respond(`Searching YouTube for "${q}" 🎬`);
    flashAction(`✓ YouTube: ${q}`);
    return;
  }

  const googleSearchMatch = lower.match(/^(?:search|google|find|look up|search for)\s+(.+)$/);
  if (googleSearchMatch) {
    const q = googleSearchMatch[1].replace(/^for\s+/i, '').trim();
    window.open(`https://www.google.com/search?q=${encodeURIComponent(q)}`, '_blank');
    respond(`Searching Google for "${q}" 🔍`);
    flashAction(`✓ Searching: ${q}`);
    return;
  }

  // ── 4. TIME & DATE ──
  if (/what(?:'s| is) the (?:current )?time|what time is it/i.test(lower)) {
    const t = new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    respond(`The current time is **${t}** 🕐`);
    return;
  }

  if (/what(?:'s| is)(?: today'?s?)? (?:the )?date|today(?:'s)? date/i.test(lower)) {
    const d = new Date().toLocaleDateString('en-US', { weekday:'long', year:'numeric', month:'long', day:'numeric' });
    respond(`Today is **${d}** 📅`);
    return;
  }

  if (/what(?:'s| is)(?: the)? (?:current )?(?:year|month|day)/i.test(lower)) {
    const now = new Date();
    respond(`It's **${now.toLocaleDateString('en-US',{weekday:'long'})}**, **${now.toLocaleString('default',{month:'long'})} ${now.getDate()}, ${now.getFullYear()}** 📅`);
    return;
  }

  // ── 5. TIMER ──
  const timerMatch = lower.match(/set (?:a )?timer (?:for\s+)?(\d+)\s*(second|seconds|sec|minute|minutes|min|hour|hours|hr)/i);
  if (timerMatch) {
    const amount = parseInt(timerMatch[1]);
    const unit = timerMatch[2].toLowerCase();
    let seconds = amount;
    if (/min/.test(unit)) seconds = amount * 60;
    if (/hour|hr/.test(unit)) seconds = amount * 3600;
    const label = `${amount} ${unit}`;
    setTimer(seconds, label);
    respond(`Timer set for **${label}**! I'll notify you when it's done. ⏱`);
    flashAction(`⏱ Timer: ${label}`);
    return;
  }

  // ── 6. FUN COMMANDS ──
  if (/flip (?:a )?coin/i.test(lower)) {
    respond(`You got ${Math.random() < 0.5 ? '**Heads** 🪙' : '**Tails** 🪙'}!`);
    return;
  }

  if (/roll (?:a )?(?:dice|die)|roll dice/i.test(lower)) {
    const n = Math.floor(Math.random() * 6) + 1;
    respond(`You rolled a **${n}** ${ ['⚀','⚁','⚂','⚃','⚄','⚅'][n-1]}`);
    return;
  }

  if (/random number|pick a number/i.test(lower)) {
    respond(`Your random number is **${Math.floor(Math.random() * 100) + 1}** 🎲`);
    return;
  }

  if (/motivate me|give me motivation|inspire me/i.test(lower)) {
    const quotes = [
      "The secret of getting ahead is getting started. — Mark Twain",
      "It always seems impossible until it's done. — Nelson Mandela",
      "Don't watch the clock; do what it does. Keep going. — Sam Levenson",
      "Believe you can and you're halfway there. — Theodore Roosevelt",
      "Success is not final, failure is not fatal. — Winston Churchill",
    ];
    respond(`💪 *"${quotes[Math.floor(Math.random() * quotes.length)]}"*`);
    return;
  }

  // ── 7. ASSISTANT CONTROL ──
  if (/stop (?:speaking|talking)|shut up|be quiet|silence/i.test(lower)) {
    window.speechSynthesis?.cancel();
    const reply = 'I stopped speaking. 🔇';
    appendMessage('assistant', reply);
    state.history.push({ role: 'assistant', content: reply });
    flashAction('✓ Stopped speaking');
    return;
  }

  if (/clear (?:the )?chat|new conversation|start over/i.test(lower)) {
    clearChat();
    const reply = 'Chat cleared! Ready for a new conversation. 🗑️';
    appendMessage('assistant', reply);
    speakText(reply);
    return;
  }

  if (/go to settings|open settings/i.test(lower)) {
    switchTab('settings', document.querySelector('[data-tab="settings"]'));
    respond('Switched to Settings! ⚙️');
    return;
  }

  if (/go to tools|open tools/i.test(lower)) {
    switchTab('tools', document.querySelector('[data-tab="tools"]'));
    respond('Switched to Tools! 🛠');
    return;
  }

  if (/go to voice|open voice/i.test(lower)) {
    switchTab('voice', document.querySelector('[data-tab="voice"]'));
    respond('Switched to Voice Commands! 🎙️');
    return;
  }

  if (/read (?:the )?last (?:message|reply)/i.test(lower)) {
    const msgs = document.querySelectorAll('.message.assistant .msg-bubble');
    if (msgs.length > 0) {
      speakText(msgs[msgs.length - 1].innerText);
      appendMessage('assistant', '🔊 Reading the last message…');
    } else {
      respond('No assistant messages yet!');
    }
    return;
  }

  if (/what can you do|help|list commands|show commands/i.test(lower)) {
    respond(`Here's what I can do:\n\n**🌐 Open websites** — "Open YouTube", "Open Gmail", "Open GitHub"…\n**🧮 Calculator** — "Open calculator"\n**📝 Notepad** — "Open Notepad"\n**🔍 Search** — "Search for Python tutorials", "Search YouTube for lofi"\n**⏱ Timers** — "Set a timer for 5 minutes"\n**🕐 Time & Date** — "What time is it?", "What is today's date?"\n**🎲 Fun** — "Flip a coin", "Roll a dice", "Motivate me"\n**💬 Chat** — Ask me anything! Powered by Groq open-source AI.\n**🎛 Control** — "Stop speaking", "Clear chat", "Go to settings"`);
    return;
  }

  // ── 8. WEATHER ──
  const weatherMatch = lower.match(/(?:what(?:'s| is) the )?weather(?: (?:in|for|at) (.+))?/i);
  if (weatherMatch) {
    const place = weatherMatch[1]?.trim() || 'my location';
    window.open(`https://www.google.com/search?q=weather+${encodeURIComponent(place)}`, '_blank');
    respond(`Opening weather for **${place}** in Google 🌦️`);
    flashAction(`✓ Weather: ${place}`);
    return;
  }

  // ── 9. FALL THROUGH → Groq AI ──
  await sendToAIWithTyping(text);
}

// ─── SEND TO AI (Gateway + Router) ────────────────────
async function sendToAIWithTyping(text) {
  const typingId = showTyping();
  setLoading(true);
  try {
    if (state.deepThink) {
      // Orchestrator mode: multi-step plan-execute-verify
      const result = await callOrchestrator(text);
      removeTyping(typingId);
      appendOrchestratorResult(result);
      const reply = result.synthesis || 'No result generated.';
      state.history.push({ role: 'assistant', content: reply });
      speakText(stripMarkdown(reply));
    } else {
      // Standard mode: single-shot via Gateway + Router
      const data = await callAI(state.history);
      removeTyping(typingId);
      const reply = data.content || data.text || JSON.stringify(data);
      const routing = data.routing || null;
      const memorySources = data.memory_sources || null;
      appendMessage('assistant', reply, routing, memorySources);
      state.history.push({ role: 'assistant', content: reply });
      speakText(stripMarkdown(reply));
      autoExtractMemory(text, reply);
    }
  } catch (err) {
    removeTyping(typingId);
    appendMessage('assistant', `⚠ ${err.message}`);
    speakText('Sorry, I encountered an error.');
  } finally {
    setLoading(false);
  }
}

function respond(text) {
  appendMessage('assistant', text);
  state.history.push({ role: 'assistant', content: text });
  speakText(stripMarkdown(text));
}

// ─── CHAT: SEND FROM TEXT INPUT ───────────────────────
async function sendMessage() {
  const input = document.getElementById('user-input');
  const text = input.value.trim();
  if (!text || state.isLoading) return;

  removeWelcome();
  appendMessage('user', text);
  state.history.push({ role: 'user', content: text });
  input.value = '';
  autoResize(input);
  updateMsgCount();

  await sendToAIWithTyping(text);
}

function sendQuick(text) { document.getElementById('user-input').value = text; sendMessage(); }

function handleKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
}

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 160) + 'px';
}

// ─── SPEECH RECOGNITION (faster-whisper via /api/stt) ───
let mediaRecorder = null;
let audioChunks = [];
let whisperStream = null;
let whisperAudioCtx = null;

async function startWhisperRecording() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    whisperStream = stream;
    mediaRecorder = new MediaRecorder(stream);
    audioChunks = [];

    mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) audioChunks.push(e.data);
    };

    mediaRecorder.onstop = async () => {
      const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
      await sendToWhisper(audioBlob);
      cleanupRecording();
    };

    mediaRecorder.start();

    // Start listening UI
    state.isListening = true;
    document.getElementById('listen-overlay').classList.add('active');
    document.getElementById('listen-transcript').textContent = 'Listening…';
    document.getElementById('listen-label').textContent = 'Listening…';
    document.getElementById('chat-mic-btn').classList.add('listening');
    document.getElementById('big-mic')?.classList.add('listening');
    if (document.getElementById('big-mic-label'))
      document.getElementById('big-mic-label').textContent = 'Listening…';
    document.getElementById('api-status').textContent = '🎙️ Listening';
    document.getElementById('status-dot').style.background = 'var(--accent)';
    document.getElementById('status-dot').style.boxShadow = '0 0 8px var(--accent)';

    // Auto-stop on silence (1.5s of silence threshold)
    startSilenceDetection(stream, 1500);

  } catch (err) {
    console.error('[ARIA] Mic access error:', err);
    appendMessage('assistant', `⚠ Mic error: ${err.message}. Please allow microphone access.`);
    stopListeningUI();
  }
}

function startSilenceDetection(stream, silenceThreshold) {
  try {
    const audioContext = new AudioContext();
    whisperAudioCtx = audioContext;
    const analyser = audioContext.createAnalyser();
    const source = audioContext.createMediaStreamSource(stream);
    source.connect(analyser);
    analyser.fftSize = 512;

    const dataArray = new Uint8Array(analyser.frequencyBinCount);
    let silenceStart = null;

    function checkSilence() {
      if (!mediaRecorder || mediaRecorder.state === 'inactive') return;

      analyser.getByteFrequencyData(dataArray);
      const volume = dataArray.reduce((a, b) => a + b, 0) / dataArray.length;

      if (volume < 10) {
        if (!silenceStart) silenceStart = Date.now();
        else if (Date.now() - silenceStart > silenceThreshold) {
          document.getElementById('listen-label').textContent = '✓ Got it!';
          stopWhisperRecording();
          return;
        }
      } else {
        silenceStart = null;
        // Update transcript to show we're hearing speech
        document.getElementById('listen-transcript').textContent = 'Hearing you…';
      }

      requestAnimationFrame(checkSilence);
    }
    checkSilence();
  } catch (e) {
    console.warn('[ARIA] Silence detection unavailable:', e);
  }
}

function stopWhisperRecording() {
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.stop();
  }
}

function cleanupRecording() {
  // Release mic tracks
  if (whisperStream) {
    whisperStream.getTracks().forEach(track => track.stop());
    whisperStream = null;
  }
  // Close audio context
  if (whisperAudioCtx) {
    whisperAudioCtx.close().catch(() => {});
    whisperAudioCtx = null;
  }
}

async function sendToWhisper(audioBlob) {
  document.getElementById('listen-transcript').textContent = 'Transcribing…';
  document.getElementById('listen-label').textContent = '⚙️ Processing';
  document.getElementById('api-status').textContent = 'Transcribing…';

  const formData = new FormData();
  formData.append('audio', audioBlob, 'recording.webm');

  try {
    const response = await fetch(STT_URL, {
      method: 'POST',
      body: formData
    });

    if (!response.ok) throw new Error(`STT failed: ${response.status}`);

    const data = await response.json();
    stopListeningUI();

    if (data.text && data.text.trim()) {
      runVoiceCommand(data.text.trim());
    } else {
      appendMessage('assistant', '⚠ No speech detected — try again.');
    }
  } catch (err) {
    console.error('[ARIA] STT error:', err);
    stopListeningUI();
    appendMessage('assistant', `⚠ Transcription failed: ${err.message}. Check that server.py is running.`);
  }
}

function toggleListening() {
  if (state.isListening) {
    stopWhisperRecording();
    stopListeningUI();
    cleanupRecording();
    return;
  }
  startWhisperRecording();
}

function stopListening() {
  stopWhisperRecording();
  stopListeningUI();
  cleanupRecording();
}

function stopListeningUI() {
  state.isListening = false;
  document.getElementById('listen-overlay').classList.remove('active');
  document.getElementById('listen-transcript').textContent = 'Say a command or ask me anything…';
  document.getElementById('chat-mic-btn').classList.remove('listening');
  document.getElementById('big-mic')?.classList.remove('listening');
  if (document.getElementById('big-mic-label'))
    document.getElementById('big-mic-label').textContent = 'Tap to Speak';
  document.getElementById('api-status').textContent = 'Ready';
  document.getElementById('status-dot').style.background = '#34d399';
  document.getElementById('status-dot').style.boxShadow = '0 0 6px #34d399';
}

// ─── TEXT-TO-SPEECH ───────────────────────────────────
let voices = [];
function loadVoices() {
  voices = window.speechSynthesis?.getVoices() || [];
  const sel = document.getElementById('voice-select');
  if (!sel) return;
  sel.innerHTML = '';
  if (voices.length === 0) { sel.innerHTML = '<option>No voices found</option>'; return; }
  const engVoices = voices.filter(v => v.lang.startsWith('en'));
  const list = engVoices.length > 0 ? engVoices : voices;
  list.forEach(v => {
    const opt = document.createElement('option');
    opt.value = v.name;
    opt.textContent = `${v.name} (${v.lang})`;
    if (v.name === state.settings.voiceName) opt.selected = true;
    sel.appendChild(opt);
  });
}

window.speechSynthesis?.addEventListener('voiceschanged', loadVoices);
setTimeout(loadVoices, 500);

function speakText(text) {
  if (!state.settings.voiceEnabled) return;
  if (!window.speechSynthesis) return;
  if (state.ttsMuted) {
    // Show hint that TTS is muted
    const hint = document.getElementById('tts-muted-hint');
    if (hint) hint.style.display = 'inline';
    return;
  }
  window.speechSynthesis.cancel();
  const utt = new SpeechSynthesisUtterance(text.slice(0, 500));
  utt.rate  = parseFloat(state.settings.voiceRate)  || 1;
  utt.pitch = parseFloat(state.settings.voicePitch) || 1;
  const voiceName = state.settings.voiceName || document.getElementById('voice-select')?.value;
  const found = voices.find(v => v.name === voiceName);
  if (found) utt.voice = found;
  const ttsEl = document.getElementById('tts-indicator');
  utt.onstart = () => { if (ttsEl) ttsEl.style.display = 'block'; };
  utt.onend = utt.onerror = () => { if (ttsEl) ttsEl.style.display = 'none'; };
  window.speechSynthesis.speak(utt);
}

/** Unmute TTS — called from the "Enable Voice" banner button */
function unmuteTTS() {
  state.ttsMuted = false;
  // Hide the unmute banner
  const banner = document.getElementById('unmute-banner');
  if (banner) banner.classList.add('hidden');
  // Hide the muted hint
  const hint = document.getElementById('tts-muted-hint');
  if (hint) hint.style.display = 'none';
  // Play the welcome greeting now that user has interacted
  speakText(`Hello! I'm ${state.settings.assistantName}. Press Space or click Speak to give a voice command.`);
}

function testVoice() {
  speakText(`Hello! I am ${state.settings.assistantName}, your AI voice assistant. Powered by Groq open-source AI.`);
}

function stripMarkdown(text) {
  return text
    .replace(/```[\s\S]*?```/g, 'code block')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/\*([^*]+)\*/g, '$1')
    .replace(/#+\s/g, '')
    .replace(/[-*]\s/g, '')
    .replace(/\n+/g, ' ')
    .trim();
}

// ─── TIMER ───────────────────────────────────────────
function setTimer(seconds, label) {
  const t = setTimeout(() => {
    const msg = `Your ${label} timer is done!`;
    document.getElementById('timer-msg').textContent = msg;
    document.getElementById('timer-notif').style.display = 'flex';
    speakText(msg);
    flashAction('⏱ Timer Done!');
  }, seconds * 1000);
  state.activeTimers.push(t);
}

// ─── ACTION FLASH ─────────────────────────────────────
function flashAction(msg) {
  const el = document.getElementById('action-flash');
  el.textContent = msg;
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 2500);
}

// ─── CALCULATOR ───────────────────────────────────────
function calcNum(n) {
  if (state.calcJustResult) { state.calcExpr = ''; state.calcJustResult = false; }
  if (state.calcDisplay === '0' || state.calcDisplay === 'Error') state.calcDisplay = n;
  else state.calcDisplay += n;
  state.calcExpr += n;
  updateCalc();
}

function calcInput(op) {
  state.calcJustResult = false;
  if (state.calcDisplay === 'Error') state.calcDisplay = '0';
  const last = state.calcExpr.slice(-1);
  if (['+','-','*','/'].includes(last)) state.calcExpr = state.calcExpr.slice(0,-1) + op;
  else state.calcExpr += op;
  state.calcDisplay += { '+':'+', '-':'−', '*':'×', '/':'÷' }[op] || op;
  updateCalc();
}

function calcEquals() {
  try {
    const result = Function('"use strict"; return (' + state.calcExpr + ')')();
    const display = parseFloat(result.toFixed(10)).toString();
    document.getElementById('calc-expr').textContent = state.calcExpr + ' =';
    state.calcExpr = display;
    state.calcDisplay = display;
    state.calcJustResult = true;
    document.getElementById('calc-display').textContent = display;
  } catch(e) {
    state.calcDisplay = 'Error'; state.calcExpr = ''; updateCalc();
  }
}

function calcClear() {
  state.calcDisplay = '0'; state.calcExpr = ''; state.calcJustResult = false;
  document.getElementById('calc-expr').textContent = '';
  updateCalc();
}

function calcDot() {
  if (state.calcJustResult) { state.calcDisplay = '0.'; state.calcExpr = '0.'; state.calcJustResult = false; }
  else if (!state.calcDisplay.split(/[+\-×÷]/).pop().includes('.')) {
    state.calcDisplay += '.'; state.calcExpr += '.';
  }
  updateCalc();
}

function calcToggleSign() {
  if (state.calcDisplay !== '0' && state.calcDisplay !== 'Error') {
    if (state.calcDisplay.startsWith('-')) {
      state.calcDisplay = state.calcDisplay.slice(1);
      state.calcExpr = state.calcExpr.slice(1);
    } else {
      state.calcDisplay = '-' + state.calcDisplay;
      state.calcExpr = '-' + state.calcExpr;
    }
    updateCalc();
  }
}

function updateCalc() {
  document.getElementById('calc-display').textContent = state.calcDisplay;
}

// ─── NOTEPAD ──────────────────────────────────────────
function copyNote() {
  navigator.clipboard?.writeText(document.getElementById('notepad-area').value)
    .then(() => flashAction('✓ Copied to clipboard'));
}

function clearNote() { document.getElementById('notepad-area').value = ''; }

// ─── API CALL (Gateway + Router) ──────────────────────
async function callAI(messages) {
  const res = await fetch(BACKEND_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      messages,
      auto_route: true,          // let the Router pick the best model
      max_tokens: state.settings.maxTokens,
      system: buildSystemPrompt(),
    })
  });
  if (!res.ok) {
    const e = await res.json().catch(() => ({}));
    throw new Error(e.error || `Backend error ${res.status}`);
  }
  return await res.json(); // returns { content, model, routing, usage, ... }
}

async function callOrchestrator(text) {
  const res = await fetch(ORCHESTRATE_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      request: text,
      system: buildSystemPrompt(),
    })
  });
  if (!res.ok) {
    const e = await res.json().catch(() => ({}));
    throw new Error(e.error || `Orchestrator error ${res.status}`);
  }
  return await res.json();
}

function buildSystemPrompt() {
  let p = state.settings.systemPrompt;
  if (state.memories.length > 0)
    p += '\n\n## User Memories:\n' + state.memories.map(m => `- ${m}`).join('\n');
  return p;
}

// ─── DEEP THINK TOGGLE ────────────────────────────────
function toggleDeepThink() {
  state.deepThink = !state.deepThink;
  const btn = document.getElementById('deep-think-btn');
  if (btn) {
    btn.classList.toggle('active', state.deepThink);
    btn.title = state.deepThink ? 'Deep Think ON — multi-step orchestration' : 'Deep Think OFF — single model response';
  }
  flashAction(state.deepThink ? '🧠 Deep Think ON' : '⚡ Deep Think OFF');
}

// ─── RENDER MESSAGES ──────────────────────────────────
function appendMessage(role, content, routing, memorySources) {
  const win = document.getElementById('chat-window');
  const div = document.createElement('div');
  div.className = `message ${role}`;
  const label = role === 'user' ? 'You' : state.settings.assistantName;
  const avatar = role === 'user' ? 'U' : 'AI';

  // Build routing badge for assistant messages
  let routingBadge = '';
  if (role === 'assistant' && routing && routing.model) {
    const taskIcon = { code: '💻', reasoning: '🧠', general: '💬', vision: '👁', user_selected: '🎯' }[routing.task_type] || '🤖';
    routingBadge = `<span class="routing-badge" title="${routing.reason || ''}">${taskIcon} ${routing.model}</span>`;
  }

  // Build memory sources badges
  let sourcesHtml = '';
  if (role === 'assistant' && memorySources && memorySources.length > 0) {
    sourcesHtml = `<div class="msg-memory-sources">` +
      memorySources.map(s => `<span class="memory-source-pill" onclick="previewMemoryDoc('${s.doc_id}')" title="Relevance score: ${s.score}\n\nMatched Context:\n${escHtml(s.excerpt)}">📄 Context: ${escHtml(s.filename)}</span>`).join('') +
      `</div>`;
  }

  div.innerHTML = `
    <div class="msg-avatar">${avatar}</div>
    <div class="msg-body">
      <div class="msg-label">${label} ${routingBadge}</div>
      <div class="msg-bubble">${renderMarkdown(content)}${sourcesHtml}</div>
    </div>`;
  win.appendChild(div);
  win.scrollTop = win.scrollHeight;

  // Attach export button to assistant messages long enough to be worth exporting
  if (role === 'assistant') {
    const bubble = div.querySelector('.msg-bubble');
    if (bubble) attachExportBtn(bubble, content);
  }
}


/** Render an orchestrator result with step-by-step visualization */
function appendOrchestratorResult(result) {
  const win = document.getElementById('chat-window');
  const div = document.createElement('div');
  div.className = 'message assistant orchestrated';

  let stepsHtml = '';
  if (result.steps && result.steps.length > 0) {
    stepsHtml = '<div class="orch-steps">';
    stepsHtml += `<div class="orch-steps-header">🧠 Plan (${result.steps.length} steps · ${result.total_time?.toFixed(1) || '?'}s)</div>`;
    for (const step of result.steps) {
      const taskIcon = { code: '💻', reasoning: '🧠', general: '💬' }[step.task_type] || '🤖';
      const statusIcon = step.error ? '❌' : (step.code_execution && !step.code_execution.success ? '⚠️' : '✅');
      stepsHtml += `
        <div class="orch-step">
          <div class="orch-step-header">
            <span class="orch-step-num">${statusIcon} Step ${step.step}</span>
            <span class="routing-badge">${taskIcon} ${step.model || '?'}</span>
          </div>
          <div class="orch-step-instruction">${escHtml(step.instruction)}</div>
        </div>`;
    }
    stepsHtml += '</div>';
  }

  let sourcesHtml = '';
  if (result.memory_sources && result.memory_sources.length > 0) {
    sourcesHtml = `<div class="msg-memory-sources">` +
      result.memory_sources.map(s => `<span class="memory-source-pill" onclick="previewMemoryDoc('${s.doc_id}')" title="Relevance score: ${s.score}\n\nMatched Context:\n${escHtml(s.excerpt)}">📄 Context: ${escHtml(s.filename)}</span>`).join('') +
      `</div>`;
  }

  const synthesis = result.synthesis || result.error || 'No result generated.';

  div.innerHTML = `
    <div class="msg-avatar">AI</div>
    <div class="msg-body">
      <div class="msg-label">${state.settings.assistantName} <span class="routing-badge deep-think-badge">🧠 Deep Think</span></div>
      ${stepsHtml}
      <div class="msg-bubble">${renderMarkdown(synthesis)}${sourcesHtml}</div>
    </div>`;
  win.appendChild(div);
  win.scrollTop = win.scrollHeight;
}

function renderMarkdown(text) {
  return text
    .replace(/```(\w*)\n?([\s\S]*?)```/g, (_,l,c) => `<pre><code>${escHtml(c.trim())}</code></pre>`)
    .replace(/`([^`]+)`/g, (_,c) => `<code>${escHtml(c)}</code>`)
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\*([^*]+)\*/g, '<em>$1</em>')
    .replace(/^### (.+)$/gm, '<h3>$1</h3>').replace(/^## (.+)$/gm, '<h2>$1</h2>').replace(/^# (.+)$/gm, '<h1>$1</h1>')
    .replace(/^[\-\*] (.+)$/gm, '<li>$1</li>').replace(/(<li>[\s\S]+?<\/li>)/g, '<ul>$1</ul>')
    .replace(/\n{2,}/g, '</p><p>').replace(/\n/g, '<br>');
}

function escHtml(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

function removeWelcome() {
  const w = document.getElementById('chat-welcome');
  if (w) w.remove();
}

// ─── TYPING INDICATOR ─────────────────────────────────
let typingCtr = 0;
function showTyping() {
  const id = `typing-${++typingCtr}`;
  const win = document.getElementById('chat-window');
  const div = document.createElement('div');
  div.id = id; div.className = 'message assistant';
  div.innerHTML = `<div class="msg-avatar">AI</div><div class="msg-body"><div class="msg-label">${state.settings.assistantName}</div><div class="msg-bubble"><div class="typing-dots"><span></span><span></span><span></span></div></div></div>`;
  win.appendChild(div);
  win.scrollTop = win.scrollHeight;
  return id;
}
function removeTyping(id) { document.getElementById(id)?.remove(); }

// ─── LOADING STATE ────────────────────────────────────
function setLoading(v) {
  state.isLoading = v;
  document.getElementById('send-btn').disabled = v;
  document.getElementById('api-status').textContent = v ? 'Thinking…' : 'Ready';
  document.getElementById('api-status').style.color = v ? 'var(--accent)' : '#34d399';
}

function updateMsgCount() { state.msgCount++; document.getElementById('msg-count').textContent = state.msgCount; }

// ─── CLEAR CHAT ───────────────────────────────────────
function clearChat() {
  state.history = []; state.msgCount = 0;
  document.getElementById('msg-count').textContent = 0;
  document.getElementById('chat-window').innerHTML = `
    <div class="chat-welcome" id="chat-welcome">
      <div class="welcome-orb"><div class="orb-ring r1"></div><div class="orb-ring r2"></div><div class="orb-ring r3"></div><div class="orb-core"></div></div>
      <h2>Hello, I'm ARIA</h2>
      <p>Your Adaptive Reasoning Intelligence Assistant.<br>
      Press <kbd>Space</kbd> or click <strong>Speak</strong> to give a voice command.</p>
      <div class="quick-prompts">
        <button class="quick-btn" onclick="runVoiceCommand('open youtube')">📺 Open YouTube</button>
        <button class="quick-btn" onclick="runVoiceCommand('open calculator')">🧮 Calculator</button>
        <button class="quick-btn" onclick="runVoiceCommand('what is the time')">🕐 What time is it?</button>
        <button class="quick-btn" onclick="runVoiceCommand('set a timer for 30 seconds')">⏱ Timer 30s</button>
        <button class="quick-btn" onclick="runVoiceCommand('open notepad')">📝 Notepad</button>
        <button class="quick-btn" onclick="runVoiceCommand('search for weather today')">🔍 Web Search</button>
        <button class="quick-btn" onclick="runVoiceCommand('tell me a joke')">😂 Tell a Joke</button>
        <button class="quick-btn" onclick="runVoiceCommand('flip a coin')">🪙 Flip a Coin</button>
      </div>
    </div>`;
}

// ─── TAB SWITCHING ────────────────────────────────────
function switchTab(id, btn) {
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + id).classList.add('active');
  if (btn) btn.classList.add('active');
}

// ─── TOOLS ───────────────────────────────────────────
const TOOL_CONFIG = {
  summarize:  { title:'📋 Summarize Text', placeholder:'Paste the text to summarize…', extra:null, prompt: t=>`Summarize concisely:\n\n${t}` },
  translate:  { title:'🌐 Translate Text',  placeholder:'Paste text to translate…', extra:'Target language (e.g. Spanish, Tamil)', prompt:(t,l)=>`Translate to ${l||'Spanish'}:\n\n${t}` },
  codereview: { title:'🔍 Code Review',     placeholder:'Paste your code…', extra:null, prompt: t=>`Review this code for bugs and best practices:\n\n\`\`\`\n${t}\n\`\`\`` },
  grammar:    { title:'✏️ Grammar Fix',     placeholder:'Paste your text…', extra:null, prompt: t=>`Fix all grammar and spelling. Return corrected version only:\n\n${t}` },
  explain:    { title:'💡 Explain',         placeholder:'Paste a concept or passage…', extra:'Target audience (e.g. beginner, expert)', prompt:(t,a)=>`Explain to a ${a||'general audience'}:\n\n${t}` },
  brainstorm: { title:'⚡ Brainstorm',      placeholder:'Describe the topic…', extra:'Number of ideas (default 10)', prompt:(t,n)=>`Generate ${n||10} creative ideas for:\n\n${t}\n\nNumbered list with brief explanations.` },
};

function runTool(id) {
  state.currentTool = id;
  const cfg = TOOL_CONFIG[id];
  document.getElementById('tool-modal-title').textContent = cfg.title;
  document.getElementById('tool-input').placeholder = cfg.placeholder;
  document.getElementById('tool-input').value = '';
  const ex = document.getElementById('tool-extra');
  if (cfg.extra) { ex.style.display='block'; ex.placeholder=cfg.extra; ex.value=''; } else ex.style.display='none';
  document.getElementById('tool-output').style.display = 'none';
  document.getElementById('tool-output').textContent = '';
  document.getElementById('tool-modal').style.display = 'flex';
}

function closeToolModal() { document.getElementById('tool-modal').style.display='none'; state.currentTool=null; }

async function executeTool() {
  const cfg = TOOL_CONFIG[state.currentTool];
  const text = document.getElementById('tool-input').value.trim();
  const extra = document.getElementById('tool-extra').value.trim();
  if (!text) return;
  const btn = document.querySelector('.run-btn');
  btn.disabled = true; btn.textContent = 'Running…';
  const outEl = document.getElementById('tool-output');
  outEl.style.display = 'block'; outEl.textContent = '…';
  try {
    const result = await callAI([{ role:'user', content: cfg.prompt(text, extra) }]);
    const output = (result && typeof result === 'object')
      ? (result.content || result.text || result.result || JSON.stringify(result, null, 2))
      : (result || '');
    outEl.textContent = output;
  } catch(e) { outEl.textContent = `Error: ${e.message}`; }
  finally { btn.disabled = false; btn.textContent = 'Run Tool'; }
}

document.addEventListener('click', e => { if (e.target.id === 'tool-modal') closeToolModal(); });

// ─── MEMORY ───────────────────────────────────────────
// ─── MEMORY & KNOWLEDGE BASE ──────────────────────────
let currentMemoryFilter = 'all';
let memorySearchQuery   = '';

function setMemoryFilter(filter, btn) {
  currentMemoryFilter = filter;
  document.querySelectorAll('.mem-pill-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');

  const docsSection = document.getElementById('memory-docs-section');
  const factsSection = document.getElementById('memory-facts-section');
  if (docsSection) {
    docsSection.style.display = (filter === 'all' || filter === 'docs') ? 'flex' : 'none';
  }
  if (factsSection) {
    factsSection.style.display = (filter === 'all' || filter === 'facts') ? 'flex' : 'none';
  }
}

function filterMemories(q) {
  memorySearchQuery = (q || '').trim().toLowerCase();
  const clearBtn = document.getElementById('memory-search-clear');
  if (clearBtn) clearBtn.style.display = memorySearchQuery ? 'block' : 'none';
  renderMemoryDocs();
  renderMemories();
}

function clearMemorySearch() {
  const input = document.getElementById('memory-search-input');
  if (input) input.value = '';
  filterMemories('');
}

function updateMemoryStats() {
  const docCount  = state.memoryDocs.length;
  const factCount = state.memories.length;

  const pillDoc = document.getElementById('pill-doc-count');
  if (pillDoc) pillDoc.textContent = docCount;

  const pillFact = document.getElementById('pill-fact-count');
  if (pillFact) pillFact.textContent = factCount;

  const badgeDoc = document.getElementById('memory-doc-count');
  if (badgeDoc) badgeDoc.textContent = `${docCount} ${docCount === 1 ? 'document' : 'documents'}`;

  const badgeFact = document.getElementById('memory-fact-count');
  if (badgeFact) badgeFact.textContent = `${factCount} ${factCount === 1 ? 'fact' : 'facts'}`;

  const statDocs = document.getElementById('stat-total-docs');
  if (statDocs) statDocs.textContent = docCount;

  const statFacts = document.getElementById('stat-total-facts');
  if (statFacts) statFacts.textContent = factCount;

  const totalChars = state.memoryDocs.reduce((acc, d) => acc + (d.char_count || 0), 0);
  const statChars = document.getElementById('stat-total-chars');
  if (statChars) statChars.textContent = totalChars.toLocaleString();
}

function addMemory(text) {
  const input = document.getElementById('memory-input');
  const val = text || input.value.trim();
  if (!val) return;
  state.memories.push(val);
  if (!text) input.value = '';
  saveMemories();
  renderMemories();
  flashAction('✓ Added custom memory');
}

function quickAddFact(text) {
  if (!state.memories.includes(text)) {
    addMemory(text);
  } else {
    flashAction('Fact already in memory');
  }
}

function copyFact(text) {
  navigator.clipboard?.writeText(text).then(() => {
    flashAction('✓ Copied fact to clipboard');
  });
}

function removeMemory(i) {
  state.memories.splice(i, 1);
  saveMemories();
  renderMemories();
  flashAction('✓ Removed memory');
}

function clearMemory() {
  state.memories = [];
  saveMemories();
  renderMemories();
}

function renderMemories() {
  updateMemoryStats();
  const list = document.getElementById('memory-list');
  if (!list) return;

  if (!state.memories.length) {
    list.innerHTML = `
      <div class="memory-empty">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="40">
          <ellipse cx="12" cy="5" rx="9" ry="3"/>
          <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/>
          <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>
        </svg>
        <p>No personal memories yet.<br>Memories are auto-learned from conversations or added manually above.</p>
      </div>`;
    return;
  }

  let filteredMemories = state.memories;
  if (memorySearchQuery) {
    filteredMemories = state.memories.filter(m => m.toLowerCase().includes(memorySearchQuery));
  }

  if (!filteredMemories.length) {
    list.innerHTML = `
      <div class="memory-empty">
        <p>No facts match "<strong>${escHtml(memorySearchQuery)}</strong>"</p>
      </div>`;
    return;
  }

  list.innerHTML = filteredMemories.map((m, i) => `
    <div class="memory-entry" onclick="copyFact('${escHtml(m)}')" title="Click to copy to clipboard">
      <div class="memory-entry-dot"></div>
      <div class="memory-entry-text">${escHtml(m)}</div>
      <div class="memory-entry-actions">
        <button class="memory-entry-btn" onclick="event.stopPropagation();copyFact('${escHtml(m)}')" title="Copy fact">📋</button>
        <button class="memory-entry-btn del" onclick="event.stopPropagation();removeMemory(${i})" title="Delete fact">✕</button>
      </div>
    </div>`).join('');
}

function autoExtractMemory(user, reply) {
  const nameMatch = user.match(/(?:my name is|i am|i'm|call me)\s+([A-Z][a-z]+)/i);
  if (nameMatch) {
    const m = `User's name is ${nameMatch[1]}`;
    if (!state.memories.includes(m)) addMemory(m);
  }
  const jobMatch = user.match(/(?:i(?:'m| am) a(?:n)?|i work as a(?:n)?)\s+([a-z ]+?)(?:\.|,|$)/i);
  if (jobMatch) {
    const m = `User works as: ${jobMatch[1].trim()}`;
    if (!state.memories.includes(m)) addMemory(m);
  }
}

function saveMemories() {
  try {
    localStorage.setItem('aria_memories', JSON.stringify(state.memories));
  } catch(e) {}
}

function loadMemories() {
  try {
    const s = localStorage.getItem('aria_memories');
    if (s) {
      state.memories = JSON.parse(s);
      renderMemories();
    }
  } catch(e) {}
  loadMemoryDocs();
}

// ─── DOCUMENT KNOWLEDGE BASE (Memory Docs) ────────────
function formatBytes(bytes) {
  if (!bytes || isNaN(bytes)) return '0 B';
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

async function loadMemoryDocs() {
  try {
    const res = await fetch(MEMORY_DOCS_URL);
    if (!res.ok) return;
    const data = await res.json();
    state.memoryDocs = data.documents || [];
    renderMemoryDocs();
  } catch(e) {
    console.warn('Could not load memory documents:', e);
  }
}

function renderMemoryDocs() {
  updateMemoryStats();
  const list = document.getElementById('memory-docs-list');
  if (!list) return;

  if (!state.memoryDocs.length) {
    list.innerHTML = `
      <div class="memory-empty">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="40">
          <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
          <polyline points="14 2 14 8 20 8"/>
        </svg>
        <p>No documents uploaded to memory yet.<br>Drop reference files above so the LLM can use their context in all chats.</p>
      </div>`;
    return;
  }

  let filteredDocs = state.memoryDocs;
  if (memorySearchQuery) {
    filteredDocs = state.memoryDocs.filter(d =>
      d.filename.toLowerCase().includes(memorySearchQuery) ||
      (d.summary && d.summary.toLowerCase().includes(memorySearchQuery))
    );
  }

  if (!filteredDocs.length) {
    list.innerHTML = `
      <div class="memory-empty">
        <p>No documents match "<strong>${escHtml(memorySearchQuery)}</strong>"</p>
      </div>`;
    return;
  }

  list.innerHTML = filteredDocs.map(doc => {
    const rawExt = (doc.filename.split('.').pop() || 'doc').toLowerCase();
    const badgeExt = rawExt.toUpperCase();
    let badgeClass = 'badge-default';
    if (rawExt === 'pdf') badgeClass = 'badge-pdf';
    else if (['docx', 'doc'].includes(rawExt)) badgeClass = 'badge-docx';
    else if (['txt', 'md', 'markdown'].includes(rawExt)) badgeClass = 'badge-txt';
    else if (['csv', 'xlsx', 'xls'].includes(rawExt)) badgeClass = 'badge-csv';

    const sizeStr = doc.size_bytes ? formatBytes(doc.size_bytes) : `${doc.char_count.toLocaleString()} chars`;
    return `
      <div class="memory-doc-card">
        <div class="memory-doc-badge ${badgeClass}">${escHtml(badgeExt)}</div>
        <div class="memory-doc-info">
          <div class="memory-doc-title" title="${escHtml(doc.filename)}">${escHtml(doc.filename)}</div>
          <div class="memory-doc-meta">
            <span>${doc.char_count.toLocaleString()} chars</span>
            <span>·</span>
            <span>${sizeStr}</span>
            <span>·</span>
            <span>${escHtml(doc.uploaded_at || '')}</span>
          </div>
        </div>
        <div class="memory-doc-actions">
          <button class="memory-doc-btn" onclick="previewMemoryDoc('${doc.doc_id}')" title="Preview document content">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="12" height="12"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
            Preview
          </button>
          <button class="memory-doc-btn chat-btn" onclick="askAboutDoc('${escHtml(doc.filename)}')" title="Ask question about this document in chat">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="12" height="12"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>
            Ask in Chat
          </button>
          <button class="memory-doc-btn" onclick="copyDocSnippet('${doc.doc_id}')" title="Copy document summary">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="12" height="12"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>
            Copy
          </button>
          <button class="memory-doc-btn del" onclick="deleteMemoryDoc('${doc.doc_id}')" title="Delete from memory">✕</button>
        </div>
      </div>
    `;
  }).join('');
}

function askAboutDoc(filename) {
  switchTab('chat', document.querySelector('[data-tab="chat"]'));
  const input = document.getElementById('user-input');
  if (input) {
    input.value = `Based on document "${filename}", `;
    input.focus();
    autoResize(input);
  }
  flashAction(`💬 Switched to Chat for ${filename}`);
}

function copyDocSnippet(docId) {
  const doc = state.memoryDocs.find(d => d.doc_id === docId);
  if (doc) {
    const textToCopy = doc.summary || doc.filename;
    navigator.clipboard?.writeText(textToCopy).then(() => {
      flashAction(`✓ Copied snippet of ${doc.filename}`);
    });
  }
}

async function uploadMemoryDoc(file) {
  const statusEl = document.getElementById('memory-upload-status');
  const dropArea = document.getElementById('memory-drop-area');

  if (statusEl) {
    statusEl.innerHTML = `<div class="doc-uploading"><div class="doc-spinner"></div><span>Adding <strong>${escHtml(file.name)}</strong> to memory…</span></div>`;
  }
  if (dropArea) dropArea.classList.add('uploading');

  const formData = new FormData();
  formData.append('file', file);

  try {
    const res = await fetch(MEMORY_UPLOAD_URL, { method: 'POST', body: formData });
    const data = await res.json();
    if (!res.ok) {
      if (statusEl) statusEl.innerHTML = `<div class="doc-error">⚠️ ${escHtml(data.error || 'Upload failed')}</div>`;
      if (dropArea) dropArea.classList.remove('uploading');
      return;
    }

    if (statusEl) statusEl.innerHTML = '';
    if (dropArea) dropArea.classList.remove('uploading');
    flashAction(`✓ Stored in Memory: ${file.name}`);
    await loadMemoryDocs();
  } catch(e) {
    if (statusEl) statusEl.innerHTML = `<div class="doc-error">⚠️ ${escHtml(e.message)}</div>`;
    if (dropArea) dropArea.classList.remove('uploading');
  }
}

function handleMemoryFileSelect(event) {
  const files = Array.from(event.target.files || []);
  if (!files.length) return;
  for (const file of files) {
    uploadMemoryDoc(file);
  }
  event.target.value = '';
}

function memoryDocDragOver(e) {
  e.preventDefault();
  document.getElementById('memory-drop-area')?.classList.add('dragover');
}

function memoryDocDragLeave(e) {
  document.getElementById('memory-drop-area')?.classList.remove('dragover');
}

function memoryDocDrop(e) {
  e.preventDefault();
  document.getElementById('memory-drop-area')?.classList.remove('dragover');
  const files = Array.from(e.dataTransfer.files || []);
  for (const file of files) {
    uploadMemoryDoc(file);
  }
}

async function deleteMemoryDoc(docId) {
  if (!confirm('Remove this document from memory? ARIA will no longer reference it in chats.')) return;
  try {
    const res = await fetch(`${MEMORY_DOCS_URL}/${docId}`, { method: 'DELETE' });
    if (res.ok) {
      flashAction('✓ Document removed from memory');
      await loadMemoryDocs();
    }
  } catch(e) {
    flashAction(`⚠ Error: ${e.message}`);
  }
}

async function previewMemoryDoc(docId) {
  const modal = document.getElementById('doc-preview-modal');
  const badge = document.getElementById('preview-modal-badge');
  const title = document.getElementById('preview-modal-title');
  const meta = document.getElementById('preview-modal-meta');
  const content = document.getElementById('preview-modal-content');

  if (badge) badge.textContent = 'DOC';
  if (title) title.textContent = 'Loading…';
  if (meta) meta.textContent = '';
  if (content) content.textContent = 'Fetching document content…';
  if (modal) modal.style.display = 'flex';

  try {
    const res = await fetch(`${MEMORY_DOCS_URL}/${docId}`);
    if (!res.ok) throw new Error('Document not found');
    const data = await res.json();
    const doc = data.document;

    const ext = (doc.filename.split('.').pop() || 'txt').toUpperCase();
    if (badge) badge.textContent = ext;
    if (title) title.textContent = doc.filename;
    if (meta) meta.textContent = `${doc.char_count.toLocaleString()} characters · ${formatBytes(doc.size_bytes || 0)} · Uploaded ${doc.uploaded_at}`;
    if (content) content.textContent = doc.text || doc.summary || 'No text content available.';
  } catch(e) {
    if (content) content.textContent = `Error: ${e.message}`;
  }
}

function closeDocPreview() {
  const modal = document.getElementById('doc-preview-modal');
  if (modal) modal.style.display = 'none';
}

async function promoteCurrentDocToMemory() {
  if (!state.doc.id) {
    flashAction('No document currently loaded in Docs tab');
    return;
  }
  const btn = document.getElementById('doc-memory-pin-btn');
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Saving…';
  }
  try {
    const res = await fetch(`${MEMORY_PROMOTE_URL}/${state.doc.id}`, { method: 'POST' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Failed to save to memory');

    flashAction(`✓ Saved to Memory: ${state.doc.filename}`);
    await loadMemoryDocs();
    if (btn) {
      btn.innerHTML = `<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M19 21l-7-5-7 5V5a2 2 0 012-2h10a2 2 0 012 2z"/></svg> Saved in Memory`;
      btn.style.background = 'var(--accent)';
      btn.style.color = '#0d0f14';
    }
  } catch(e) {
    flashAction(`⚠ ${e.message}`);
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Save to Memory';
    }
  }
}

async function confirmClearAllMemory() {
  const choice = confirm('Clear all memory documents and personal facts?\nClick OK to clear everything, or Cancel to abort.');
  if (!choice) return;
  state.memories = [];
  saveMemories();
  renderMemories();
  try {
    await fetch(MEMORY_CLEAR_URL, { method: 'POST' });
    await loadMemoryDocs();
    flashAction('✓ Memory and documents cleared');
  } catch(e) {
    console.error(e);
  }
}

// ─── SETTINGS ─────────────────────────────────────────
function saveSettings() {
  state.settings.model         = document.getElementById('model-select').value;
  state.settings.maxTokens     = parseInt(document.getElementById('max-tokens').value) || 1024;
  state.settings.assistantName = document.getElementById('assistant-name').value || 'ARIA';
  state.settings.systemPrompt  = document.getElementById('system-prompt').value;
  state.settings.voiceEnabled  = document.getElementById('voice-enabled').checked;
  state.settings.voiceRate     = document.getElementById('voice-rate').value;
  state.settings.voicePitch    = document.getElementById('voice-pitch').value;
  state.settings.voiceName     = document.getElementById('voice-select').value;
  try { localStorage.setItem('aria_settings', JSON.stringify(state.settings)); } catch(e){}
}

function loadSettings() {
  try { const s=localStorage.getItem('aria_settings'); if(s) Object.assign(state.settings, JSON.parse(s)); } catch(e){}
  document.getElementById('model-select').value   = state.settings.model;
  document.getElementById('max-tokens').value     = state.settings.maxTokens;
  document.getElementById('assistant-name').value = state.settings.assistantName;
  document.getElementById('system-prompt').value  = state.settings.systemPrompt;
  document.getElementById('voice-enabled').checked= state.settings.voiceEnabled;
  document.getElementById('voice-rate').value     = state.settings.voiceRate;
  document.getElementById('voice-pitch').value    = state.settings.voicePitch;
  document.getElementById('rate-val').textContent = parseFloat(state.settings.voiceRate).toFixed(1);
  document.getElementById('pitch-val').textContent= parseFloat(state.settings.voicePitch).toFixed(1);
  if (state.settings.accent) applyAccent(state.settings.accent);
  if (state.settings.fontSize) {
    document.documentElement.style.setProperty('--font-size', state.settings.fontSize);
    document.getElementById('font-size').value = state.settings.fontSize;
  }
}

function applyAccent(color) {
  document.documentElement.style.setProperty('--accent', color);
  const r=parseInt(color.slice(1,3),16), g=parseInt(color.slice(3,5),16), b=parseInt(color.slice(5,7),16);
  document.documentElement.style.setProperty('--accent-dim',    `rgba(${r},${g},${b},0.12)`);
  document.documentElement.style.setProperty('--accent-glow',   `rgba(${r},${g},${b},0.28)`);
  document.documentElement.style.setProperty('--border-accent', `rgba(${r},${g},${b},0.28)`);
}

function setAccent(color, btn) {
  document.querySelectorAll('.swatch').forEach(s => s.classList.remove('active'));
  btn.classList.add('active');
  state.settings.accent = color;
  applyAccent(color);
  saveSettings();
}

function applyFontSize() {
  const s = document.getElementById('font-size').value;
  state.settings.fontSize = s;
  document.documentElement.style.setProperty('--font-size', s);
  saveSettings();
}

// ─── UTILS ───────────────────────────────────────────
function capitalize(s) { return s.charAt(0).toUpperCase() + s.slice(1); }

// ─── DOCUMENT Q&A ─────────────────────────────────────

/** Show upload zone, hide chat area, reset doc state */
function clearDocSession() {
  state.doc.id        = null;
  state.doc.filename  = null;
  state.doc.charCount = 0;
  state.doc.text      = null;
  state.doc.history   = [];
  state.doc.isLoading = false;

  document.getElementById('doc-upload-zone').style.display   = 'flex';
  document.getElementById('doc-chat-container').style.display = 'none';
  document.getElementById('doc-clear-btn').style.display      = 'none';
  document.getElementById('doc-upload-status').textContent    = '';
  document.getElementById('doc-upload-status').className      = 'doc-upload-status';
  const previewEl = document.getElementById('doc-preview-text');
  if (previewEl) previewEl.textContent = '';
  // Reset file input so the same file can be re-selected
  const fi = document.getElementById('doc-file-input');
  if (fi) fi.value = '';
  // Clear chat messages (keep welcome)
  const win = document.getElementById('doc-chat-window');
  if (win) win.innerHTML = `
    <div class="doc-chat-welcome" id="doc-chat-welcome">
      <div class="doc-welcome-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="40" height="40">
          <circle cx="12" cy="12" r="10" />
          <path d="M9.09 9a3 3 0 015.83 1c0 2-3 3-3 3" />
          <line x1="12" y1="17" x2="12.01" y2="17" />
        </svg>
      </div>
      <h3>Document loaded!</h3>
      <p>Ask any question, or <strong>tell ARIA to edit it</strong> — e.g. <em>"Remove the row for John"</em> or
        <em>"Change the title to Q3 Report"</em>
      </p>
      <div class="quick-prompts" style="margin-top:16px">
        <button class="quick-btn" onclick="sendDocQuick('Summarize this document')">📋 Summarize</button>
        <button class="quick-btn" onclick="sendDocQuick('What are the key points?')">🔑 Key Points</button>
        <button class="quick-btn" onclick="sendDocQuick('What is this document about?')">❓ What is this?</button>
        <button class="quick-btn" onclick="sendDocQuick('List the main topics covered')">📑 Main Topics</button>
      </div>
    </div>`;
}

/** Toggle document preview pane in Document Studio */
function toggleDocViewer() {
  const pane = document.getElementById('doc-preview-pane');
  const btn  = document.getElementById('doc-toggle-view-btn');
  if (!pane) return;
  const isCollapsed = pane.classList.toggle('collapsed');
  if (btn) {
    if (isCollapsed) {
      btn.classList.remove('active');
    } else {
      btn.classList.add('active');
    }
  }
}

/** Copy full text of active document to clipboard */
function copyDocContent() {
  const text = state.doc?.text || document.getElementById('doc-preview-text')?.textContent || '';
  if (!text) {
    flashAction('No document text to copy');
    return;
  }
  navigator.clipboard.writeText(text).then(() => {
    flashAction('✓ Document text copied to clipboard');
  }).catch(() => {
    flashAction('Could not copy to clipboard');
  });
}

/** Called when user picks a file via the browse dialog */
function handleDocFileSelect(event) {
  const file = event.target.files[0];
  if (file) uploadDocument(file);
}

/** Drag-and-drop handlers */
function docDragOver(e) {
  e.preventDefault();
  document.getElementById('doc-drop-area').classList.add('dragover');
}
function docDragLeave(e) {
  document.getElementById('doc-drop-area').classList.remove('dragover');
}
function docDrop(e) {
  e.preventDefault();
  document.getElementById('doc-drop-area').classList.remove('dragover');
  const file = e.dataTransfer.files[0];
  if (file) uploadDocument(file);
}

/** Upload file to /api/upload-doc and store the doc_id */
async function uploadDocument(file) {
  const statusEl = document.getElementById('doc-upload-status');
  const dropArea = document.getElementById('doc-drop-area');

  statusEl.className = 'doc-upload-status uploading';
  statusEl.innerHTML = `
    <div class="doc-uploading">
      <div class="doc-spinner"></div>
      <span>Processing <strong>${escHtml(file.name)}</strong>…</span>
    </div>`;
  dropArea.classList.add('uploading');

  const formData = new FormData();
  formData.append('file', file);

  try {
    const res = await fetch(UPLOAD_DOC_URL, { method: 'POST', body: formData });
    const data = await res.json();

    if (!res.ok) {
      statusEl.className = 'doc-upload-status error';
      statusEl.innerHTML = `<div class="doc-error">⚠️ ${escHtml(data.error || 'Upload failed.')}</div>`;
      dropArea.classList.remove('uploading');
      return;
    }

    // Store doc info
    state.doc.id            = data.doc_id;
    state.doc.filename      = data.filename;
    state.doc.charCount     = data.char_count;
    state.doc.text          = data.text || data.preview || '';
    state.doc.history       = [];
    state.doc.downloadToken = null;
    // Hide download btn in case it was visible from a prior session
    const dlBtn = document.getElementById('doc-download-btn');
    if (dlBtn) dlBtn.style.display = 'none';

    // Switch to doc chat UI
    document.getElementById('doc-upload-zone').style.display    = 'none';
    document.getElementById('doc-chat-container').style.display = 'flex';
    document.getElementById('doc-clear-btn').style.display      = 'inline-flex';
    document.getElementById('doc-info-name').textContent        = data.filename;
    document.getElementById('doc-info-chars').textContent       = `${(data.char_count).toLocaleString()} chars`;

    // Populate Document Viewer
    const previewEl = document.getElementById('doc-preview-text');
    if (previewEl) {
      previewEl.textContent = state.doc.text || 'No text content available.';
    }
    const badgeEl = document.getElementById('doc-content-badge');
    if (badgeEl) {
      const ext = (data.filename.split('.').pop() || 'DOC').toUpperCase();
      badgeEl.textContent = ext;
    }
    const pane = document.getElementById('doc-preview-pane');
    if (pane) pane.classList.remove('collapsed');
    const viewBtn = document.getElementById('doc-toggle-view-btn');
    if (viewBtn) viewBtn.classList.add('active');

    document.getElementById('doc-input').focus();

    flashAction(`✓ Document loaded: ${data.filename}`);

  } catch (err) {
    statusEl.className = 'doc-upload-status error';
    statusEl.innerHTML = `<div class="doc-error">⚠️ Could not reach server: ${escHtml(err.message)}</div>`;
  } finally {
    dropArea.classList.remove('uploading');
  }
}

/** Send a message to /api/doc-query OR /api/doc-edit depending on intent */
async function sendDocMessage() {
  if (!state.doc.id || state.doc.isLoading) return;
  const input = document.getElementById('doc-input');
  const question = input.value.trim();
  if (!question) return;

  // Hide welcome on first message
  const welcome = document.getElementById('doc-chat-welcome');
  if (welcome) welcome.remove();

  // Show user message
  appendDocMessage('user', question);
  state.doc.history.push({ role: 'user', content: question });
  input.value = '';
  autoResize(input);

  // Detect whether this is an edit instruction or a Q&A query
  const looksLikeEdit = detectEditIntent(question);

  // Show typing
  const typingId = showDocTyping();
  state.doc.isLoading = true;
  document.getElementById('doc-send-btn').disabled = true;

  if (looksLikeEdit) {
    // ─ EDIT PATH ──────────────────────────────────────────────
    try {
      const res = await fetch(DOC_EDIT_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          doc_id:      state.doc.id,
          instruction: question,
          model:       state.settings.model,
        }),
      });
      const data = await res.json();
      removeDocTyping(typingId);

      if (!res.ok) {
        appendDocMessage('assistant', `⚠ Edit failed: ${data.error || 'Unknown error.'}`);
      } else {
        // Store token for download
        state.doc.downloadToken = data.download_token;
        state.doc.charCount     = data.char_count;
        if (data.text) {
          state.doc.text = data.text;
          const previewEl = document.getElementById('doc-preview-text');
          if (previewEl) previewEl.textContent = data.text;
        }
        // Update char count in banner
        const charsEl = document.getElementById('doc-info-chars');
        if (charsEl) charsEl.textContent = `${(data.char_count).toLocaleString()} chars (edited)`;
        // Show download button in banner
        const dlBtn = document.getElementById('doc-download-btn');
        if (dlBtn) dlBtn.style.display = 'inline-flex';
        // Render the edit result card in chat
        renderDocEditResult(data);
        state.doc.history.push({ role: 'assistant', content: '✅ ' + data.summary });
      }
    } catch (err) {
      removeDocTyping(typingId);
      appendDocMessage('assistant', `⚠ Network error: ${err.message}`);
    } finally {
      state.doc.isLoading = false;
      document.getElementById('doc-send-btn').disabled = false;
      document.getElementById('doc-input').focus();
    }

  } else {
    // ─ QUERY PATH ────────────────────────────────────────────
    try {
      const res = await fetch(DOC_QUERY_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          doc_id:   state.doc.id,
          question: question,
          model:    state.settings.model,
          history:  state.doc.history.slice(-10),
        }),
      });
      const data = await res.json();
      removeDocTyping(typingId);

      if (!res.ok) {
        appendDocMessage('assistant', `⚠ ${data.error || 'Unknown error.'}`);
      } else {
        const reply = data.content || '';
        appendDocMessage('assistant', reply);
        state.doc.history.push({ role: 'assistant', content: reply });
        speakText(stripMarkdown(reply));
      }
    } catch (err) {
      removeDocTyping(typingId);
      appendDocMessage('assistant', `⚠ Network error: ${err.message}`);
    } finally {
      state.doc.isLoading = false;
      document.getElementById('doc-send-btn').disabled = false;
      document.getElementById('doc-input').focus();
    }
  }
}

/** Quick-prompt helper for the doc tab */
function sendDocQuick(text) {
  document.getElementById('doc-input').value = text;
  sendDocMessage();
}

function handleDocKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendDocMessage(); }
}

// ─── EDIT INTENT DETECTION ──────────────────────────────
const EDIT_KEYWORDS = [
  'remove','delete','drop','erase','strip','exclude','clear',
  'replace','rename','change','update','modify','edit','correct',
  'fix','set','overwrite','rewrite',
  'add','insert','append','include',
  'sort','reorder','filter','hide','move',
];
function detectEditIntent(text) {
  const lower = text.toLowerCase();
  return EDIT_KEYWORDS.some(kw => new RegExp('\\b' + kw + '\\b').test(lower));
}

// ─── DOWNLOAD EDITED DOC ─────────────────────────────
function downloadEditedDoc() {
  if (!state.doc.downloadToken) return;
  // Navigate to the download URL — browser will trigger file download
  window.location.href = `${DOC_DOWNLOAD_URL}/${state.doc.downloadToken}`;
}

// ─── RENDER EDIT RESULT CARD ─────────────────────────
function renderDocEditResult(data) {
  const win = document.getElementById('doc-chat-window');
  const div = document.createElement('div');
  div.className = 'message assistant';

  const changesList = (data.changes || []).map(c =>
    `<li>${escHtml(c)}</li>`
  ).join('');

  div.innerHTML = `
    <div class="msg-avatar">AI</div>
    <div class="msg-body">
      <div class="msg-label">${escHtml(state.settings.assistantName)} <span class="routing-badge edit-badge">✎ Edit Applied</span></div>
      <div class="doc-edit-card">
        <div class="doc-edit-summary">✅ ${escHtml(data.summary)}</div>
        ${changesList ? `<ul class="doc-edit-changes">${changesList}</ul>` : ''}
        <div class="doc-edit-meta">
          <span>📄 ${escHtml(data.filename)}</span>
          <span>${(data.char_count || 0).toLocaleString()} chars after edit</span>
        </div>
        <button class="doc-edit-download-btn" onclick="downloadEditedDoc()">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
            <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/>
            <polyline points="7 10 12 15 17 10"/>
            <line x1="12" y1="15" x2="12" y2="3"/>
          </svg>
          Download Edited File
        </button>
      </div>
    </div>`;
  win.appendChild(div);
  win.scrollTop = win.scrollHeight;
}

/** Append a message bubble to the doc-chat-window */
function appendDocMessage(role, content) {
  const win = document.getElementById('doc-chat-window');
  const div = document.createElement('div');
  div.className = `message ${role}`;
  const label  = role === 'user' ? 'You' : state.settings.assistantName;
  const avatar = role === 'user' ? 'U' : 'AI';
  div.innerHTML = `
    <div class="msg-avatar">${avatar}</div>
    <div class="msg-body">
      <div class="msg-label">${label}</div>
      <div class="msg-bubble">${renderMarkdown(content)}</div>
    </div>`;
  win.appendChild(div);
  win.scrollTop = win.scrollHeight;
}

let docTypingCtr = 0;
function showDocTyping() {
  const id  = `doc-typing-${++docTypingCtr}`;
  const win = document.getElementById('doc-chat-window');
  const div = document.createElement('div');
  div.id = id; div.className = 'message assistant';
  div.innerHTML = `<div class="msg-avatar">AI</div><div class="msg-body"><div class="msg-label">${state.settings.assistantName}</div><div class="msg-bubble"><div class="typing-dots"><span></span><span></span><span></span></div></div></div>`;
  win.appendChild(div);
  win.scrollTop = win.scrollHeight;
  return id;
}
function removeDocTyping(id) { document.getElementById(id)?.remove(); }

// ─── KEYBOARD SHORTCUT: Space = toggle mic ────────────
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT') return;
  if (e.code === 'Space' && !e.ctrlKey && !e.altKey && !e.metaKey) {
    e.preventDefault();
    // Only toggle mic if not on the docs or agent tab
    const active = document.querySelector('.tab-panel.active');
    if (active && !['tab-docs','tab-agent'].includes(active.id)) {
      toggleListening();
    }
  }
  if (e.key === 'Escape') {
    stopListening();
    closeToolModal();
    document.getElementById('calc-modal').style.display = 'none';
    document.getElementById('notepad-modal').style.display = 'none';
  }
});

// ══════════════════════════════════════════════════════════
// AGENT MODE — v3.0
// ══════════════════════════════════════════════════════════

// ─── Update uploaded-file hint in Agent tab ───────────
function updateAgentFileHint() {
  const hint = document.getElementById('agent-file-hint');
  if (!hint) return;
  if (state.doc && state.doc.id && state.doc.filename) {
    hint.textContent = `📄 Using: ${state.doc.filename}`;
    hint.style.color = 'var(--accent)';
  } else {
    hint.textContent = 'No files uploaded. Upload via Docs tab first.';
    hint.style.color = 'var(--text-dim)';
  }
}

// ─── Set agent status badge ───────────────────────────
function setAgentStatus(text, cls) {
  const badge = document.getElementById('agent-status-badge');
  if (!badge) return;
  badge.textContent = text;
  badge.className = 'agent-status-badge' + (cls ? ' ' + cls : '');
}

// ─── Render plan steps list ───────────────────────────
function renderAgentPlan(steps) {
  const list = document.getElementById('agent-plan-list');
  if (!list) return;
  list.innerHTML = '';
  steps.forEach((step, i) => {
    const li = document.createElement('li');
    li.className = 'agent-plan-item';
    li.id = `plan-item-${i}`;
    // Detect tool call
    const toolMatch = step.match(/^([a-z_][a-z0-9_]*)\s*\(/);
    if (toolMatch) {
      li.innerHTML = `<span class="agent-tool-badge">${toolMatch[1]}</span> ${escHtml(step.replace(toolMatch[0], '').replace(/\)\s*$/, ''))}`;
    } else {
      li.textContent = step;
    }
    list.appendChild(li);
  });
}

// ─── Render step cards from result ───────────────────
function renderAgentSteps(steps) {
  const container = document.getElementById('agent-steps-list');
  if (!container) return;
  container.innerHTML = '';
  if (!steps || !steps.length) return;

  steps.forEach(step => {
    const card = document.createElement('div');
    card.className = 'agent-step-card';

    // Status icon
    const hasError  = !!step.error;
    const verified  = step.verified === true;
    const statusIcon = hasError ? '✗' : verified ? '✓' : '○';
    const statusCls  = hasError ? 'step-fail' : verified ? 'step-pass' : 'step-pending';

    // Model/tool badge
    const modelLabel = step.model || step.task_type || '';
    const isTool = modelLabel.startsWith('tool:');
    const badgeText = isTool ? modelLabel.replace('tool:', '') : modelLabel;
    const badgeCls  = isTool ? 'agent-tool-badge' : 'agent-model-badge';

    // Tool calls
    let toolCallsHtml = '';
    if (step.tool_calls && step.tool_calls.length) {
      toolCallsHtml = step.tool_calls.map(tc => {
        const tcStatus = tc.success ? '✓' : '✗';
        const tcCls    = tc.success ? 'tc-ok' : 'tc-err';
        return `<div class="agent-tool-call ${tcCls}"><span class="tc-icon">${tcStatus}</span><code>${escHtml(tc.tool)}</code><span class="tc-latency">${tc.latency}s</span></div>`;
      }).join('');
    }

    // Output preview
    const outputText = step.output ? escHtml(step.output.slice(0, 300)) + (step.output.length > 300 ? '…' : '') : (step.error ? `<span style="color:var(--error)">${escHtml(step.error)}</span>` : '');

    card.innerHTML = `
      <div class="agent-step-header">
        <span class="agent-step-num ${statusCls}">${statusIcon} ${step.step}</span>
        <span class="agent-step-instruction">${escHtml(step.instruction)}</span>
        <span class="${badgeCls}">${escHtml(badgeText)}</span>
      </div>
      ${toolCallsHtml}
      ${outputText ? `<div class="agent-step-output">${outputText}</div>` : ''}
    `;
    container.appendChild(card);
  });
}

// ─── Render generated file download cards ─────────────
function renderGeneratedFiles(files) {
  if (!files || !files.length) return;
  const panel = document.getElementById('agent-files-panel');
  const cards = document.getElementById('agent-file-cards');
  if (!panel || !cards) return;

  panel.style.display = 'block';
  cards.innerHTML = '';

  const icons = { docx: '📄', pptx: '📊', xlsx: '📈', default: '📁' };
  const colors = { docx: '#2e75b6', pptx: '#c55a11', xlsx: '#375623' };

  files.forEach(f => {
    const ext = (f.name.split('.').pop() || '').toLowerCase();
    const icon  = icons[ext]  || icons.default;
    const color = colors[ext] || '#555';
    const sizeKb = Math.round((f.size || 0) / 1024);
    const dlUrl  = f.download_token ? `${AGENT_DL_URL}/${f.download_token}` : '#';

    const card = document.createElement('a');
    card.href      = dlUrl;
    card.download  = f.name;
    card.className = 'agent-file-card';
    card.style.setProperty('--file-color', color);
    card.innerHTML = `
      <div class="afc-icon">${icon}</div>
      <div class="afc-meta">
        <div class="afc-name">${escHtml(f.name)}</div>
        <div class="afc-size">${sizeKb > 0 ? sizeKb + ' KB' : 'Ready'}</div>
      </div>
      <svg class="afc-dl" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16">
        <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/>
        <polyline points="7 10 12 15 17 10"/>
        <line x1="12" y1="15" x2="12" y2="3"/>
      </svg>
    `;
    cards.appendChild(card);
  });
}

// ─── Render synthesis ─────────────────────────────────
function renderAgentSynthesis(text) {
  if (!text) return;
  const panel   = document.getElementById('agent-synthesis-panel');
  const content = document.getElementById('agent-synthesis-content');
  if (!panel || !content) return;
  panel.style.display = 'block';
  content.innerHTML = formatMessage(text);
}

// ─── Main: Run Agent ──────────────────────────────────
async function runAgent() {
  const goalEl = document.getElementById('agent-goal-input');
  const goal   = goalEl ? goalEl.value.trim() : '';
  if (!goal) {
    goalEl && goalEl.focus();
    return;
  }
  if (state.agentRunning) return;
  state.agentRunning = true;

  // UI: show execution area, clear previous
  const execEl = document.getElementById('agent-execution');
  if (execEl) execEl.style.display = 'block';
  document.getElementById('agent-plan-list').innerHTML = '';
  document.getElementById('agent-steps-list').innerHTML = '';
  document.getElementById('agent-files-panel').style.display  = 'none';
  document.getElementById('agent-synthesis-panel').style.display = 'none';

  const runBtn = document.getElementById('agent-run-btn');
  if (runBtn) { runBtn.disabled = true; runBtn.textContent = 'Running…'; }

  setAgentStatus('Planning…', 'status-planning');

  // Build payload — include current doc if available
  const payload = { goal };
  if (state.doc && state.doc.id) payload.files = [state.doc.id];

  try {
    const resp = await fetch(AGENT_URL, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(payload),
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${resp.status}`);
    }

    const data = await resp.json();

    // Render plan
    if (data.plan && data.plan.length) {
      renderAgentPlan(data.plan);
    }

    // Render steps
    if (data.steps && data.steps.length) {
      renderAgentSteps(data.steps);
    }

    // Render generated files
    if (data.generated_files && data.generated_files.length) {
      renderGeneratedFiles(data.generated_files);
    }

    // Render synthesis
    if (data.synthesis) {
      renderAgentSynthesis(data.synthesis);
    }

    setAgentStatus(data.status === 'done' ? 'Done ✓' : data.status, data.status === 'done' ? 'status-done' : 'status-fail');

  } catch (err) {
    setAgentStatus('Error', 'status-fail');
    const container = document.getElementById('agent-steps-list');
    if (container) {
      container.innerHTML = `<div class="agent-error-msg">⚠ ${escHtml(err.message)}</div>`;
    }
  } finally {
    state.agentRunning = false;
    if (runBtn) { runBtn.disabled = false; runBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16"><polygon points="5 3 19 12 5 21 5 3"/></svg> Run Agent'; }
  }
}

// ─── Helper: escape HTML ──────────────────────────────
function escHtml(str) {
  return String(str || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ─── INIT ─────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  loadSettings();
  loadMemories();
  loadModels();
  loadVoices();
  updateAgentFileHint();
  document.getElementById('user-input').focus();
  // Greeting is deferred to unmuteTTS() — browsers block autoplay without user gesture
});


// ══════════════════════════════════════════════════════════════
//  FEATURE 1 — DOCUMENT STUDIO SUB-TABS
// ══════════════════════════════════════════════════════════════

/**
 * Switch between Q&A/Edit and Image OCR sub-panels inside the Docs tab.
 */
function switchDocStudioTab(tab, btn) {
  // Update button active state
  document.querySelectorAll('.doc-studio-tab').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');

  const qaPanel  = document.getElementById('doc-qa-panel');
  const ocrPanel = document.getElementById('doc-ocr-panel');

  if (tab === 'qa') {
    if (qaPanel)  qaPanel.style.display  = '';
    if (ocrPanel) ocrPanel.style.display = 'none';
  } else {
    if (qaPanel)  qaPanel.style.display  = 'none';
    if (ocrPanel) ocrPanel.style.display = '';
  }
}


// ══════════════════════════════════════════════════════════════
//  FEATURE 2 — IMAGE OCR
// ══════════════════════════════════════════════════════════════

// State for the current OCR session
const ocrState = {
  text: '',
  filename: '',
  method: '',
};

/** Handle file input change */
function handleOCRFileSelect(event) {
  const file = event.target.files[0];
  if (file) runOCR(file);
  event.target.value = ''; // reset so same file can be re-selected
}

/** Drag-over / leave / drop handlers for the OCR drop zone */
function ocrDragOver(event) {
  event.preventDefault();
  document.getElementById('ocr-drop-area').classList.add('drag-over');
}
function ocrDragLeave(event) {
  document.getElementById('ocr-drop-area').classList.remove('drag-over');
}
function ocrDrop(event) {
  event.preventDefault();
  document.getElementById('ocr-drop-area').classList.remove('drag-over');
  const file = event.dataTransfer.files[0];
  if (file) runOCR(file);
}

/**
 * Upload image to /api/ocr-image, display result.
 */
async function runOCR(file, customPrompt = '') {
  const statusEl  = document.getElementById('ocr-upload-status');
  const resultEl  = document.getElementById('ocr-result-panel');
  const uploadEl  = document.getElementById('ocr-upload-zone');

  if (statusEl) statusEl.innerHTML = `<div class="doc-uploading"><div class="doc-spinner"></div><span>Extracting text from <strong>${escHtml(file.name)}</strong>…</span></div>`;

  const formData = new FormData();
  formData.append('file', file);
  if (customPrompt) formData.append('prompt', customPrompt);

  try {
    const res  = await fetch(OCR_IMAGE_URL, { method: 'POST', body: formData });
    const data = await res.json();

    if (!res.ok) {
      if (statusEl) statusEl.innerHTML = `<div class="doc-error">⚠ ${escHtml(data.error || 'OCR failed.')}</div>`;
      return;
    }

    // Store state
    ocrState.text     = data.text || '';
    ocrState.filename = data.filename || file.name;
    ocrState.method   = data.method || 'tesseract';

    // Populate result panel
    const imgEl      = document.getElementById('ocr-preview-img');
    const nameEl     = document.getElementById('ocr-filename');
    const charEl     = document.getElementById('ocr-charcount');
    const methodEl   = document.getElementById('ocr-method-badge');
    const contentEl  = document.getElementById('ocr-text-content');

    if (imgEl) {
      const url = URL.createObjectURL(file);
      imgEl.src = url;
    }
    if (nameEl)    nameEl.textContent   = ocrState.filename;
    if (charEl)    charEl.textContent   = `${ocrState.text.length.toLocaleString()} characters extracted`;
    if (methodEl) {
      methodEl.textContent = ocrState.method === 'llava-vision' ? 'LLaVA Vision' : 'Tesseract OCR';
    }
    if (contentEl) contentEl.textContent = ocrState.text || '[No text found in image]';

    // Show result, hide upload zone upload status
    if (statusEl) statusEl.innerHTML = '';
    if (resultEl) resultEl.style.display = '';
    if (uploadEl) uploadEl.style.display  = 'none';

  } catch (err) {
    if (statusEl) statusEl.innerHTML = `<div class="doc-error">⚠ ${escHtml(err.message)}</div>`;
  }
}

/** Re-ask using a custom question about the image */
async function runOCRQuestion() {
  const qInput = document.getElementById('ocr-question-input');
  const runBtn = document.getElementById('ocr-run-btn');
  const question = qInput ? qInput.value.trim() : '';

  if (!question) return;

  // If we have an image in the file input, re-upload with the new prompt
  const fileInput = document.getElementById('ocr-file-input');
  if (!fileInput || !fileInput.files[0]) {
    // No file cached — tell user to re-upload
    alert('Please re-upload the image to ask a new question.');
    return;
  }

  if (runBtn) { runBtn.disabled = true; runBtn.textContent = '…'; }
  await runOCR(fileInput.files[0], question);
  if (runBtn) { runBtn.disabled = false; runBtn.textContent = 'Ask'; }
  if (qInput) qInput.value = '';
}

/** Copy OCR text to clipboard */
function copyOCRText() {
  if (!ocrState.text) return;
  navigator.clipboard.writeText(ocrState.text).then(() => {
    const btn = event.currentTarget;
    const orig = btn.innerHTML;
    btn.innerHTML = '✓ Copied!';
    setTimeout(() => { btn.innerHTML = orig; }, 1800);
  });
}

/** Reset OCR panel back to upload zone */
function resetOCRPanel() {
  ocrState.text = ocrState.filename = ocrState.method = '';
  const resultEl = document.getElementById('ocr-result-panel');
  const uploadEl = document.getElementById('ocr-upload-zone');
  const contentEl = document.getElementById('ocr-text-content');
  const imgEl  = document.getElementById('ocr-preview-img');

  if (resultEl) resultEl.style.display = 'none';
  if (uploadEl) uploadEl.style.display  = '';
  if (contentEl) contentEl.textContent  = '';
  if (imgEl) imgEl.src = '';
}

/** Export OCR text as a draft document */
function exportOCRasDraft() {
  openExportModal(ocrState.text, ocrState.filename ? ocrState.filename.replace(/\.[^.]+$/, '') : 'OCR Text');
}


// ══════════════════════════════════════════════════════════════
//  FEATURE 3 — EXPORT DRAFT (PDF / DOCX)
// ══════════════════════════════════════════════════════════════

// Store content being exported
const exportState = {
  content: '',
  format: 'pdf',
};

/**
 * Open the export modal pre-filled with content.
 * @param {string} content  - The text to export.
 * @param {string} suggestedTitle - Pre-fill the title input.
 */
function openExportModal(content, suggestedTitle = '') {
  exportState.content = content;
  exportState.format  = 'pdf';

  const modal     = document.getElementById('export-draft-modal');
  const titleIn   = document.getElementById('export-title-input');
  const previewEl = document.getElementById('export-preview-text');
  const statusEl  = document.getElementById('export-status');

  if (titleIn)   titleIn.value       = suggestedTitle;
  if (previewEl) previewEl.textContent = content.length > 600 ? content.slice(0, 600) + '…' : content;
  if (statusEl)  statusEl.textContent  = '';

  // Reset format selection
  document.querySelectorAll('.export-fmt-btn').forEach(b => b.classList.remove('active'));
  const pdfBtn = document.getElementById('export-fmt-pdf');
  if (pdfBtn) pdfBtn.classList.add('active');

  if (modal) modal.style.display = 'flex';
}

function closeExportModal() {
  const modal = document.getElementById('export-draft-modal');
  if (modal) modal.style.display = 'none';
}

function selectExportFormat(fmt, btn) {
  exportState.format = fmt;
  document.querySelectorAll('.export-fmt-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
}

/**
 * Call /api/export-draft and trigger file download.
 */
async function doExportDraft() {
  const titleIn   = document.getElementById('export-title-input');
  const statusEl  = document.getElementById('export-status');
  const dlBtn     = document.getElementById('export-download-btn');

  const title   = (titleIn && titleIn.value.trim()) || 'ARIA Draft';
  const content = exportState.content;
  const fmt     = exportState.format;

  if (!content) {
    if (statusEl) statusEl.textContent = '⚠ No content to export.';
    return;
  }

  if (statusEl) statusEl.innerHTML = '<span style="color:var(--accent)">⏳ Generating…</span>';
  if (dlBtn)    dlBtn.disabled = true;

  try {
    const res  = await fetch(EXPORT_DRAFT_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content, format: fmt, title }),
    });
    const data = await res.json();

    if (!res.ok) {
      if (statusEl) statusEl.textContent = `⚠ ${data.error || 'Export failed.'}`;
      return;
    }

    // Trigger download via the doc-download endpoint
    const dlUrl = `${DOC_DOWNLOAD_URL}/${data.download_token}`;
    const a = document.createElement('a');
    a.href     = dlUrl;
    a.download = data.filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);

    if (statusEl) statusEl.innerHTML = `<span style="color:#34d399">✓ Downloading ${escHtml(data.filename)}</span>`;
    setTimeout(closeExportModal, 1800);

  } catch (err) {
    if (statusEl) statusEl.textContent = `⚠ ${err.message}`;
  } finally {
    if (dlBtn) dlBtn.disabled = false;
  }
}

/**
 * Add an "Export" button below an assistant chat message.
 * Called from the chat rendering code when a new AI message arrives.
 * @param {HTMLElement} bubbleEl  - The .bubble element for the AI message.
 * @param {string} content        - Plain text content of the message.
 */
function attachExportBtn(bubbleEl, content) {
  if (!bubbleEl || !content || content.length < 100) return;

  const btn = document.createElement('button');
  btn.className = 'msg-export-btn';
  btn.innerHTML = `
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="11" height="11">
      <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/>
      <polyline points="7 10 12 15 17 10"/>
      <line x1="12" y1="15" x2="12" y2="3"/>
    </svg>
    Export as PDF / DOCX`;

  btn.addEventListener('click', () => openExportModal(content, 'ARIA Response'));
  bubbleEl.appendChild(btn);
}


