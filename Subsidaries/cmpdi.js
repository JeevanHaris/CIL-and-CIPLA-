'use strict';
/* ═══════════════════════════════════════════════════════════════════
   ARIA-CIL · CMPDI Document Intelligence — Dashboard JS
   All API calls wired to http://localhost:5000
   ═══════════════════════════════════════════════════════════════════ */

const API = (window.location.protocol.startsWith('http') && window.location.port) ? window.location.origin : 'http://localhost:5000';

/* ── DOM & Formatting Helpers ───────────────────────────────────── */
const $   = id => document.getElementById(id);
const esc = s  => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const num = n  => (n == null || isNaN(n)) ? '—' : Number(n).toLocaleString();

/* ── Ambient Video Controls ──────────────────────────────────────── */
let _ambientMode = localStorage.getItem('aria_ambient_mode') || 'high';

function applyAmbientMode(mode) {
  _ambientMode = mode;
  localStorage.setItem('aria_ambient_mode', mode);
  const vid = $('bg-video');
  const txt = $('ambient-state-text');
  if (!vid) return;

  vid.classList.remove('ambient-low', 'ambient-off');
  if (mode === 'low') {
    vid.classList.add('ambient-low');
    if (vid.paused) vid.play().catch(()=>{});
    if (txt) txt.textContent = 'Ambience: Low';
  } else if (mode === 'off') {
    vid.classList.add('ambient-off');
    vid.pause();
    if (txt) txt.textContent = 'Ambience: Off';
  } else {
    // high
    if (vid.paused) vid.play().catch(()=>{});
    if (txt) txt.textContent = 'Ambience: High';
  }
}

function toggleBackgroundVideo() {
  if (_ambientMode === 'high') applyAmbientMode('low');
  else if (_ambientMode === 'low') applyAmbientMode('off');
  else applyAmbientMode('high');
  toast(`Ambience: ${_ambientMode.toUpperCase()}`, 'ok', 2000);
}

function toggleSidebar() {
  const sb = $('sidebar');
  if (sb) sb.classList.toggle('sidebar-open');
}

function setQuickQuery(text) {
  const inp = $('qqin');
  if (inp) {
    inp.value = text;
    quickQuery();
  }
}

/* ── Lightweight Markdown Renderer ───────────────────────────────── */
function renderMarkdown(md) {
  if (!md) return '';
  let s = esc(md);

  // Code blocks ```lang ... ```
  s = s.replace(/```([\s\S]*?)```/g, (m, code) => `<pre><code>${code.trim()}</code></pre>`);

  // Inline code `code`
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>');

  // Headings
  s = s.replace(/^### (.*$)/gim, '<h4>$1</h4>');
  s = s.replace(/^## (.*$)/gim, '<h3>$1</h3>');
  s = s.replace(/^# (.*$)/gim, '<h2>$1</h2>');

  // Bold & Italic
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/\*([^*]+)\*/g, '<em>$1</em>');

  // Bullet points
  s = s.replace(/^\s*[-*]\s+(.*)$/gim, '<li>$1</li>');
  s = s.replace(/(<li>[\s\S]*?<\/li>)/g, '<ul>$1</ul>');
  s = s.replace(/<\/ul>\s*<ul>/g, '');

  // Numbered lists
  s = s.replace(/^\s*(\d+)\.\s+(.*)$/gim, '<li>$2</li>');

  // Paragraphs
  const parts = s.split(/\n\n+/);
  if (parts.length > 1) {
    s = parts.map(p => {
      p = p.trim();
      if (p.startsWith('<pre>') || p.startsWith('<ul>') || p.startsWith('<h')) return p;
      return `<p>${p.replace(/\n/g, '<br/>')}</p>`;
    }).join('');
  } else {
    s = s.replace(/\n/g, '<br/>');
  }

  return s;
}

let _toastTimer;
function toast(msg, type='', ms=3500) {
  const el = $('toast');
  el.textContent = msg;
  el.className = 'toast' + (type ? ' '+type : '');
  el.style.display = 'block';
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { el.style.display='none'; }, ms);
}

function showLoad(msg='Processing…') { $('loading-msg').textContent=msg; $('loading-ov').style.display='flex'; }
function hideLoad() { $('loading-ov').style.display='none'; }

/* ── Tab Switching ───────────────────────────────────────────────── */
const _lazyLoaded = new Set();
function switchTab(name) {
  document.querySelectorAll('.pane').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(b=>b.classList.remove('active'));
  const pane = $(`pane-${name}`);
  const nav  = $(`nav-${name}`);
  if (pane) pane.classList.add('active');
  if (nav)  nav.classList.add('active');

  if (!_lazyLoaded.has(name)) {
    _lazyLoaded.add(name);
    if (name==='documents') loadDocuments();
    if (name==='analytics') refreshAnalytics();
    if (name==='knowledge') loadFacts();
    if (name==='reports')   loadReports();
  }
}

/* ── Health & Status ─────────────────────────────────────────────── */
async function checkHealth() {
  try {
    const r = await fetch(`${API}/api/health`);
    const d = await r.json();
    const dot = $('status-dot'); const txt = $('status-text');
    if (d.ollama_running) {
      dot.className='status-dot online'; txt.textContent='Ollama Online';
    } else {
      dot.className='status-dot offline'; txt.textContent='Ollama Offline';
    }
    if (d.default_model) $('model-chip').textContent = d.default_model;
  } catch {
    $('status-dot').className='status-dot offline';
    $('status-text').textContent='Server Offline';
  }
}

/* ── Dashboard ───────────────────────────────────────────────────── */
async function initDashboard() {
  try {
    const [kbR, docsR, confR] = await Promise.allSettled([
      fetch(`${API}/api/kb/stats`).then(r=>r.json()),
      fetch(`${API}/api/documents`).then(r=>r.json()),
      fetch(`${API}/api/conflicts`).then(r=>r.json()),
    ]);

    const kb   = kbR.status==='fulfilled'   ? kbR.value   : {};
    const docs = docsR.status==='fulfilled' ? docsR.value : {};
    const conf = confR.status==='fulfilled' ? confR.value : {};

    $('kv-docs').textContent    = num(kb.documents);
    $('kv-facts').textContent   = num(kb.facts);
    $('kv-vectors').textContent = num(kb.vectors);
    $('kv-conflicts').textContent = num(conf.total_conflicts);
    if (kb.documents != null) $('badge-docs').textContent = kb.documents;

    // KB summary
    const summary = $('kb-summary-list');
    if (kb.facts) {
      const s = await fetch(`${API}/api/knowledge/facts?limit=1`).then(r=>r.json()).catch(()=>({}));
      const rows = [
        ['Documents', num(kb.documents)],
        ['Facts Extracted', num(kb.facts)],
        ['Vector Embeddings', num(kb.vectors)],
        ['Organisations', (s.summary?.organizations?.length ?? '—')],
        ['Periods Covered', (s.summary?.periods?.slice(0,3).join(', ') || '—')],
      ];
      summary.innerHTML = rows.map(([k,v])=>
        `<div class="kb-s-item"><span class="kb-s-key">${esc(k)}</span><span class="kb-s-val">${esc(v)}</span></div>`
      ).join('');
    }

    // Recent docs mini-table
    const tbody = $('dash-docs-body');
    if (docs.documents?.length) {
      tbody.innerHTML = docs.documents.slice(0,7).map(d=>`
        <tr>
          <td title="${esc(d.filename)}">${esc(d.filename.slice(0,32))}${d.filename.length>32?'…':''}</td>
          <td><span class="badge b-blue">${esc(d.doc_type||'—')}</span></td>
          <td class="mono text-ac">${d.facts_count ?? '—'}</td>
          <td><span class="badge ${d.indexed?'b-green':'b-amber'}">${d.indexed?'✓ Indexed':'Pending'}</span></td>
        </tr>`).join('');
    } else {
      tbody.innerHTML='<tr><td colspan="4" class="tbl-empty">No documents yet</td></tr>';
    }
  } catch(e) { console.error('Dashboard error:',e); }
}

/* ── Quick Query ─────────────────────────────────────────────────── */
async function quickQuery() {
  const q = $('qqin').value.trim(); if (!q) return;
  const box = $('qq-answer');
  const btn = $('qq-btn');
  box.style.display = 'block';
  box.innerHTML = '<span class="spinner" style="vertical-align:middle;margin-right:8px"></span>Searching sovereign knowledge base…';
  if (btn) { btn.disabled = true; btn.textContent = '…'; }

  try {
    const r = await fetch(`${API}/api/query`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: q })
    });
    const d = await r.json();
    const answer = d.answer || d.error || 'No answer found in the knowledge base.';
    box.innerHTML = renderMarkdown(answer);

    if (d.sources?.length) {
      const src = document.createElement('div');
      src.style.cssText = 'margin-top:.85rem;padding-top:.6rem;border-top:1px solid var(--b2);font-size:11.5px;color:var(--t2);display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:.5rem';
      src.innerHTML = `<span><strong>Sources:</strong> ${esc(d.sources.join(' · '))}</span>
        <button class="btn-link" onclick="switchTab('query');setQ('${esc(q)}')">Deep Query Tracing →</button>`;
      box.appendChild(src);
    }
  } catch(e) {
    box.innerHTML = `<span style="color:var(--red)">Error querying knowledge base: ${esc(e.message)}</span>`;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Ask'; }
  }
}

/* ── Document Upload ─────────────────────────────────────────────── */
function handleDrop(e) {
  e.preventDefault(); $('dropzone').classList.remove('dz-over');
  uploadFiles([...(e.dataTransfer.files||[])]);
}
function handleFileSelect(inp) { uploadFiles([...inp.files]); inp.value=''; }

async function uploadFiles(files) {
  if (!files.length) return;
  const useLLM  = $('use-llm').checked;
  const org     = $('org-hint').value.trim();
  const period  = $('period-hint').value.trim();
  const progEl  = $('upload-progress');

  for (const file of files) {
    const itemId = 'up-'+Date.now()+'-'+Math.random().toString(36).slice(2,6);
    const item   = Object.assign(document.createElement('div'),{className:'up-item',id:itemId});
    item.innerHTML = `<span class="up-name">${esc(file.name)}</span>
      <span class="up-meta">${(file.size/1024).toFixed(0)} KB</span>
      <span class="up-tag ut-ing">Ingesting…</span>`;
    progEl.prepend(item);

    const fd = new FormData();
    fd.append('file', file);
    fd.append('use_llm', useLLM?'true':'false');
    if (org)    fd.append('organization', org);
    if (period) fd.append('period', period);

    try {
      const r = await fetch(`${API}/api/ingest`,{method:'POST',body:fd});
      const d = await r.json();
      const tag = item.querySelector('.up-tag');
      if (d.success) {
        item.querySelector('.up-meta').textContent =
          `${d.page_count}p · ${d.facts_extracted} facts · ${d.chunks_indexed} chunks`;
        tag.className='up-tag ut-ok'; tag.textContent='✓ Indexed';
        toast(`${file.name} ingested — ${d.facts_extracted} facts`, 'ok');
        initDashboard();
        loadDocuments();
      } else {
        tag.className='up-tag ut-err'; tag.textContent='✗ Failed';
        toast(d.error||'Ingestion failed','err');
      }
    } catch(e) {
      item.querySelector('.up-tag').className='up-tag ut-err';
      item.querySelector('.up-tag').textContent='✗ Network error';
      toast('Network error: '+e.message,'err');
    }
  }
}

/* ── Document Library ────────────────────────────────────────────── */
async function loadDocuments() {
  try {
    const d = await fetch(`${API}/api/documents`).then(r=>r.json());
    const tbody = $('doc-lib-body');
    const cnt   = d.documents?.length ?? 0;
    $('doc-total-count').textContent = `${cnt} document${cnt!==1?'s':''}`;
    $('badge-docs').textContent = cnt;

    if (!cnt) {
      tbody.innerHTML='<tr><td colspan="7" class="tbl-empty">No documents indexed yet.</td></tr>'; return;
    }
    tbody.innerHTML = d.documents.map(doc=>`
      <tr>
        <td title="${esc(doc.filename)}">${esc(doc.filename.slice(0,36))}${doc.filename.length>36?'…':''}</td>
        <td><span class="badge b-blue">${esc(doc.doc_type||'—')}</span></td>
        <td class="mono">${doc.page_count??'—'}</td>
        <td class="mono text-ac fw6">${doc.facts_count??'—'}</td>
        <td class="mono">${doc.chunk_count??'—'}</td>
        <td class="text-dim" style="font-size:11px">${fmt(doc.upload_time)}</td>
        <td>
          <button class="btn-ghost sm" style="color:var(--red)"
            onclick="deleteDoc('${esc(doc.id)}','${esc(doc.filename)}')">Delete</button>
        </td>
      </tr>`).join('');
  } catch(e) { console.error('loadDocuments:',e); }
}

async function deleteDoc(id, name) {
  if (!confirm(`Delete "${name}" from the knowledge base?`)) return;
  try {
    const r = await fetch(`${API}/api/documents/${id}`,{method:'DELETE'});
    const d = await r.json();
    if (d.success) { toast(`${name} removed`,'ok'); loadDocuments(); initDashboard(); }
    else toast(d.error||'Delete failed','err');
  } catch(e) { toast('Error: '+e.message,'err'); }
}

/* ── Query ───────────────────────────────────────────────────────── */
let _qBusy = false;
function setQ(text) { $('q-input').value=text; $('q-input').focus(); }

async function sendQuery() {
  if (_qBusy) return;
  const q   = $('q-input').value.trim(); if (!q) return;
  const org = $('qf-org').value.trim();
  const per = $('qf-period').value.trim();

  _qBusy=true;
  const btn=$('q-send-btn'); btn.disabled=true; btn.textContent='…';

  appendMsg('user', q);
  $('q-input').value='';

  const thinkEl = appendMsg('ai','Searching knowledge base…',true);

  try {
    const r = await fetch(`${API}/api/query`,{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({query:q, organization:org||undefined, period:per||undefined})
    });
    const d = await r.json();
    thinkEl.remove();

    appendMsg('ai', d.answer||d.error||'No answer',false,
      d.sources?.length ? 'Sources: '+d.sources.join(' · ') : '');

    renderEvidence(d.evidence, d.conflicts);
  } catch(e) {
    thinkEl.remove();
    appendMsg('ai','Error: '+e.message);
  }

  _qBusy=false; btn.disabled=false; btn.textContent='Ask →';
}

function appendMsg(role, text, dim=false, sources='') {
  const feed = $('chat-feed');
  $('chat-welcome')?.remove();
  const div = document.createElement('div');
  div.className = 'msg '+role;
  const msgId = 'msg-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6);
  div.id = msgId;

  const contentHtml = (role === 'ai' && !dim) ? renderMarkdown(text) : esc(text);

  div.innerHTML = `
    <div class="msg-av">${role==='user'?'👤':'🤖'}</div>
    <div style="flex:1;max-width:85%">
      <div class="msg-body${dim?' text-dim':''}">${contentHtml}</div>
      ${(role==='ai' && !dim) ? `
        <div class="msg-footer">
          <div class="msg-src">${esc(sources)}</div>
          <button class="msg-copy-btn" onclick="copyMessage('${msgId}')" title="Copy response">
            <svg viewBox="0 0 20 20" fill="currentColor" width="11" height="11"><path d="M8 3a1 1 0 011-1h2a1 1 0 110 2H9a1 1 0 01-1-1z"/><path d="M6 3a2 2 0 00-2 2v11a2 2 0 002 2h8a2 2 0 002-2V5a2 2 0 00-2-2 3 3 0 01-3 3H9a3 3 0 01-3-3z"/></svg>
            Copy
          </button>
        </div>` : (sources ? `<div class="msg-src">${esc(sources)}</div>` : '')}
    </div>`;
  feed.appendChild(div);
  feed.scrollTop = feed.scrollHeight;
  return div;
}

function copyMessage(msgId) {
  const el = $(msgId)?.querySelector('.msg-body');
  if (el) {
    navigator.clipboard.writeText(el.innerText || el.textContent)
      .then(() => toast('Response copied to clipboard', 'ok'))
      .catch(() => toast('Failed to copy', 'err'));
  }
}

function renderEvidence(evidence, conflicts) {
  const evArr  = evidence?.evidence||[];
  const cfArr  = conflicts||[];
  $('ev-count').textContent = `${evArr.length} source${evArr.length!==1?'s':''}`;

  $('ev-list').innerHTML = evArr.length
    ? evArr.map(e=>`
        <div class="ev-item">
          <div class="ev-src">${esc(e.source_doc||'Unknown')}</div>
          <div class="ev-meta">Page ${e.page_num||'—'}${e.section?' · '+esc(e.section):''}</div>
          ${e.value!=null?`<div class="ev-num">${e.value} ${esc(e.unit||'')}</div>`:''}
          <div class="ev-exc">${esc((e.excerpt||'').slice(0,160))}${(e.excerpt||'').length>160?'…':''}</div>
        </div>`).join('')
    : '<div class="empty-hint" style="padding:1.25rem">No traceable evidence found</div>';

  const banner = $('conflict-banner');
  const cfList = $('conflict-list');
  if (cfArr.length) {
    banner.style.display='block';
    cfList.innerHTML = cfArr.map(c=>`
      <div class="cf-item">
        <div class="cf-head${c.severity==='critical'?' crit':''}">
          ${esc(c.metric)} / ${esc(c.period)}
          <span class="badge ${c.severity==='critical'?'b-red':'b-amber'}" style="margin-left:.4rem">${esc(c.severity)}</span>
        </div>
        <div class="cf-val">${c.percent_deviation?.toFixed(1)}% deviation across ${c.sources?.length} sources</div>
        ${c.authoritative_value!=null?`<div style="font-size:11px;color:var(--green);margin-top:.2rem">✓ Authoritative: ${c.authoritative_value}</div>`:''}
      </div>`).join('');
  } else { banner.style.display='none'; }
}

/* ── Parliamentary ───────────────────────────────────────────────── */
let _parlToken = null;
async function submitParl() {
  const q   = $('parl-q').value.trim(); if(!q){toast('Enter a query','err');return;}
  const org = $('parl-org').value.trim();
  const y1  = parseInt($('parl-y1').value)||2020;
  const y2  = parseInt($('parl-y2').value)||2025;

  const btn=$('parl-btn');btn.disabled=true;btn.textContent='Generating…';
  $('parl-spinner').style.display='flex';

  try {
    const r = await fetch(`${API}/api/parliamentary`,{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({query:q,organization:org||undefined,period_range:[y1,y2]})
    });
    const d = await r.json();
    if(d.error){toast(d.error,'err');return;}

    $('parl-result').style.display='flex';
    $('parl-resp-text').textContent = d.response||'';

    // Evidence
    const evArr = d.evidence?.evidence||[];
    $('parl-ev-list').innerHTML = evArr.length
      ? '<div style="font-size:11.5px;font-weight:600;color:var(--t1);margin-bottom:.4rem">'+
          `Sources (${evArr.length})</div>`+
        evArr.slice(0,5).map(e=>`
          <div class="ev-item" style="font-size:11px">
            <span class="ev-src">${esc(e.source_doc||'')}</span> · Page ${e.page_num||'—'}
            ${e.excerpt?`<div class="ev-exc" style="margin-top:.2rem">${esc(e.excerpt.slice(0,120))}…</div>`:''}
          </div>`).join('')
      : '';

    // Generate DOCX report too
    const repR = await fetch(`${API}/api/report`,{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({request:q,title:'Parliamentary Response',organization:org,period:`${y1}-${String(y2).slice(-2)}`})
    }).catch(()=>null);
    if(repR){
      const repD=await repR.json().catch(()=>({}));
      if(repD.download_token){_parlToken=repD.download_token;$('parl-dl-btn').style.display='';}
    }

    toast('Parliamentary response generated','ok');
  } catch(e){toast('Error: '+e.message,'err');}

  btn.disabled=false; btn.textContent='Generate Response';
  $('parl-spinner').style.display='none';
}

function downloadParl() {
  if(!_parlToken){toast('No download ready','err');return;}
  const a=document.createElement('a');
  a.href=`${API}/api/doc-download/${_parlToken}`;
  a.download='Parliamentary_Response.docx'; a.click();
}
function copyText(id){
  navigator.clipboard.writeText($(id)?.textContent||'')
    .then(()=>toast('Copied','ok'));
}

/* ── Reports ─────────────────────────────────────────────────────── */
async function genReport() {
  const req   = $('rep-req').value.trim(); if(!req){toast('Enter a request','err');return;}
  const title = $('rep-title').value.trim();
  const org   = $('rep-org').value.trim();
  const per   = $('rep-period').value.trim();

  const btn=$('rep-btn'); btn.disabled=true; btn.textContent='Generating…';
  $('rep-spinner').style.display='flex';

  try {
    const r = await fetch(`${API}/api/report`,{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({request:req,title:title||undefined,organization:org||undefined,period:per||undefined})
    });
    const d = await r.json();
    if(d.error){toast(d.error,'err');return;}
    toast(`Report generated — ${d.sections} section(s)`,'ok');

    if(d.download_token){
      const a=document.createElement('a');
      a.href=`${API}/api/doc-download/${d.download_token}`;
      a.download=(title||'CMPDI_Report')+'.docx'; a.click();
    }
    loadReports();
    const cnt=parseInt($('badge-reports').textContent||'0')+1;
    $('badge-reports').textContent=cnt; $('badge-reports').style.display='';
  } catch(e){toast('Error: '+e.message,'err');}

  btn.disabled=false; btn.textContent='Generate Report';
  $('rep-spinner').style.display='none';
}

async function loadReports() {
  try {
    const d = await fetch(`${API}/api/reports`).then(r=>r.json());
    const list=$('rep-list');
    const cnt=d.reports?.length||0;
    $('rep-count-lbl').textContent=`${cnt} report${cnt!==1?'s':''}`;
    if(!cnt){list.innerHTML='<div class="empty-hint">No reports generated yet</div>';return;}
    list.innerHTML = d.reports.map(r=>`
      <div class="rep-card">
        <div class="rep-card-hdr">
          <div>
            <div class="rep-card-title">${esc(r.title)}</div>
            <div class="rep-card-meta">${r.sections} section(s) · ${fmt(r.generated_at)}</div>
          </div>
          <span class="badge ${r.success?'b-green':'b-red'}">${r.success?'✓ Ready':'Failed'}</span>
        </div>
        <div class="rep-issues">
          ${(r.issues||[]).slice(0,4).map(i=>
            `<span class="iss-tag${i.severity==='critical'?' crit':''}">${esc(i.type)}</span>`
          ).join('')}
        </div>
      </div>`).join('');
  } catch(e){console.error('loadReports:',e);}
}

/* ── Analytics ───────────────────────────────────────────────────── */
function refreshAnalytics(){loadWordCloud();loadTopics();}

async function loadWordCloud(){
  try{
    const d = await fetch(`${API}/api/analytics/wordcloud`,{
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({top_n:70})
    }).then(r=>r.json());
    const wrap=$('wcloud-container');
    if(!d.words?.length){wrap.innerHTML='<div class="empty-hint">No data — ingest documents first</div>';return;}
    const max=Math.max(...d.words.map(w=>w.value));
    const min=Math.min(...d.words.map(w=>w.value));
    wrap.innerHTML=d.words.map(w=>{
      const r=max>min?(w.value-min)/(max-min):0.5;
      const sz=Math.round(11+r*16);
      const op=(0.45+r*0.55).toFixed(2);
      return `<span class="wc-word" style="font-size:${sz}px;opacity:${op}" title="${esc(w.text)}: ${w.value}">${esc(w.text)}</span>`;
    }).join('');
  }catch(e){console.error('wordcloud:',e);}
}

async function loadTopics(){
  try{
    const d = await fetch(`${API}/api/analytics/topics`,{
      method:'POST',headers:{'Content-Type':'application/json'},body:'{}'
    }).then(r=>r.json());
    const list=$('topics-list');
    if(!d.topics?.length){list.innerHTML='<div class="empty-hint">No topics — ingest documents first</div>';return;}
    const max=Math.max(...d.topics.map(t=>t.score),1);
    list.innerHTML=d.topics.slice(0,8).map(t=>`
      <div class="topic-item">
        <div class="topic-lbl-row">
          <span class="fw6">${esc(t.topic)}</span>
          <span class="topic-kws">${esc(t.top_keywords.slice(0,3).join(', '))}</span>
        </div>
        <div class="topic-track">
          <div class="topic-fill" style="width:${Math.round(t.score/max*100)}%"></div>
        </div>
      </div>`).join('');
  }catch(e){console.error('topics:',e);}
}

let _tChart=null, _cChart=null;

async function loadTrend(){
  const metric=$('trend-metric').value;
  const org=$('trend-org').value.trim()||undefined;
  try{
    const d = await fetch(`${API}/api/analytics/trend`,{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({metric,organization:org,year_start:2015,year_end:2025})
    }).then(r=>r.json());
    renderLineChart('trend-chart-area',d,'trend','_tChart');
  }catch(e){toast('Trend error: '+e.message,'err');}
}

async function loadComparison(){
  const metric=$('comp-metric').value;
  const period=$('comp-period').value.trim();
  if(!period){toast('Enter a period (e.g. 2023-24)','err');return;}
  try{
    const d = await fetch(`${API}/api/analytics/compare`,{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({metric,period})
    }).then(r=>r.json());
    renderBarChart('comp-chart-area',d,'comp','_cChart');
  }catch(e){toast('Comparison error: '+e.message,'err');}
}

function renderLineChart(containerId, data, chartVar, prop){
  const area=$(containerId);
  if(!data.years?.length){area.innerHTML='<div class="empty-hint">No data available for this metric</div>';return;}
  area.innerHTML='<canvas id="'+chartVar+'-canvas"></canvas>';
  if(window._chartjs && window.Chart){
    if(window[prop]) window[prop].destroy();
    window[prop]=new Chart($(chartVar+'-canvas'),{
      type:'line',
      data:{labels:data.years,datasets:[{
        label:`${data.chart_title} (${data.unit})`,
        data:data.values,
        borderColor:'#38bdf8',backgroundColor:'rgba(56,189,248,.08)',
        tension:.35,pointBackgroundColor:'#38bdf8',pointRadius:4,spanGaps:true,
      }]},
      options:{responsive:true,plugins:{legend:{labels:{color:'#94b8d4',font:{size:11.5}}}},
        scales:{
          x:{ticks:{color:'#4f7a98'},grid:{color:'rgba(56,189,248,.05)'}},
          y:{ticks:{color:'#4f7a98'},grid:{color:'rgba(56,189,248,.05)'}},
        }}
    });
  } else {
    area.innerHTML='<div class="chart-fallback">'+
      data.years.map((y,i)=>
        `<div class="cf-point"><span class="cf-yr">${esc(y)}</span><span class="cf-vl">${data.values[i]??'—'}</span> <span class="text-dim" style="font-size:10px">${esc(data.unit)}</span></div>`
      ).join('')+'</div>';
  }
}

function renderBarChart(containerId, data, chartVar, prop){
  const area=$(containerId);
  if(!data.labels?.length){area.innerHTML='<div class="empty-hint">No data for this period</div>';return;}
  area.innerHTML='<canvas id="'+chartVar+'-canvas"></canvas>';
  if(window._chartjs && window.Chart){
    if(window[prop]) window[prop].destroy();
    window[prop]=new Chart($(chartVar+'-canvas'),{
      type:'bar',
      data:{labels:data.labels,datasets:[{
        label:`${data.chart_title} (${data.unit})`,
        data:data.values,
        backgroundColor:'rgba(56,189,248,.3)',borderColor:'#38bdf8',
        borderWidth:1.5,borderRadius:5,
      }]},
      options:{responsive:true,plugins:{legend:{labels:{color:'#94b8d4',font:{size:11.5}}}},
        scales:{
          x:{ticks:{color:'#4f7a98'},grid:{color:'rgba(56,189,248,.05)'}},
          y:{ticks:{color:'#4f7a98'},grid:{color:'rgba(56,189,248,.05)'}},
        }}
    });
  } else {
    area.innerHTML='<div class="chart-fallback">'+
      data.labels.map((l,i)=>
        `<div class="cf-point"><span class="cf-yr">${esc(l)}</span><span class="cf-vl">${data.values[i]??'—'}</span> <span class="text-dim" style="font-size:10px">${esc(data.unit)}</span></div>`
      ).join('')+'</div>';
  }
}

/* ── Knowledge Base ──────────────────────────────────────────────── */
async function loadFacts(){
  const org    = $('kb-org').value.trim()||undefined;
  const period = $('kb-period').value.trim()||undefined;
  const metric = $('kb-metric').value.trim()||undefined;
  try{
    const params=new URLSearchParams({limit:200});
    if(org)    params.append('organization',org);
    if(period) params.append('period',period);
    if(metric) params.append('metric',metric);
    const d = await fetch(`${API}/api/knowledge/facts?${params}`).then(r=>r.json());
    const tbody=$('facts-body');
    const cnt=d.facts?.length||0;
    $('fact-count-lbl').textContent=`${cnt} fact${cnt!==1?'s':''}`;
    if(!cnt){tbody.innerHTML='<tr><td colspan="8" class="tbl-empty">No facts match these filters</td></tr>';return;}
    tbody.innerHTML=d.facts.map(f=>{
      const conf=Math.round((f.confidence||0)*100);
      return `<tr>
        <td class="fw6">${esc(f.organization||'—')}</td>
        <td>${esc(f.activity||'—')}</td>
        <td class="mono text-ac fw6">${f.value??'—'}</td>
        <td class="mono text-dim">${esc(f.unit||'—')}</td>
        <td class="mono">${esc(f.period||'—')}</td>
        <td>
          <div class="conf-bar">
            <div class="conf-track"><div class="conf-fill" style="width:${conf}%"></div></div>
            <span class="mono text-dim" style="font-size:10.5px">${conf}%</span>
          </div>
        </td>
        <td class="text-dim mono" style="font-size:10.5px">${esc((f.doc_id||'').slice(0,12))}…</td>
        <td class="mono">${f.page_num??'—'}</td>
      </tr>`;
    }).join('');
  }catch(e){toast('Facts error: '+e.message,'err');}
}

async function loadConflicts(){
  try{
    showLoad('Scanning for data conflicts…');
    const d = await fetch(`${API}/api/conflicts`).then(r=>r.json());
    hideLoad();
    const cnt=d.total_conflicts||0;
    $('conflicts-count-lbl').textContent=`${cnt} conflict${cnt!==1?'s':''}`;
    const area=$('conflicts-area');
    if(!cnt){
      area.innerHTML='<div class="empty-hint" style="padding:2rem">✅ No data conflicts detected across the knowledge base</div>';
      return;
    }
    area.innerHTML=d.conflicts.map(c=>`
      <div class="confl-card${c.severity==='critical'?' crit':''}">
        <div class="confl-top">
          <span class="badge ${c.severity==='critical'?'b-red':'b-amber'}">${esc(c.severity.toUpperCase())}</span>
          <strong>${esc(c.metric)} / ${esc(c.period)} / ${esc(c.organization)}</strong>
          <span class="text-dim" style="margin-left:auto;font-size:11.5px">${c.percent_deviation?.toFixed(1)}% deviation</span>
        </div>
        <div class="confl-sources">
          ${(c.sources||[]).map(s=>`
            <div class="confl-src-row">
              <span class="cs-val">${s.value} ${esc(s.unit||'')}</span>
              <span class="cs-file">${esc(s.filename)}</span>
              <span class="cs-page">p${s.page_num}</span>
              <span class="badge b-blue">${esc(s.doc_type)}</span>
            </div>`).join('')}
        </div>
        ${c.authoritative_value!=null?`<div class="confl-auth">✓ Authoritative value: <strong>${c.authoritative_value}</strong></div>`:''}
      </div>`).join('');
  }catch(e){hideLoad();toast('Conflicts error: '+e.message,'err');}
}

/* ── Voice Dictation ─────────────────────────────────────────────── */
let _rec=null, _chunks=[], _dictTarget=null;

async function startDictation(targetId){
  if(_rec?.state==='recording'){_rec.stop();return;}
  _dictTarget=targetId;
  try{
    const stream=await navigator.mediaDevices.getUserMedia({audio:true});
    _rec=new MediaRecorder(stream); _chunks=[];
    _rec.ondataavailable=e=>_chunks.push(e.data);
    _rec.onstop=async()=>{
      stream.getTracks().forEach(t=>t.stop());
      const blob=new Blob(_chunks,{type:'audio/webm'});
      const fd=new FormData(); fd.append('audio',blob,'query.webm');
      try{
        const r=await fetch(`${API}/api/stt`,{method:'POST',body:fd});
        const d=await r.json();
        if(d.text){
          const el=$(_dictTarget);
          if(el){el.value=d.text;el.dispatchEvent(new Event('input'));}
          toast('Transcribed: '+d.text.slice(0,80)+(d.text.length>80?'…':''),'ok');
        }
      }catch(e){toast('Transcription failed: '+e.message,'err');}
      document.querySelectorAll('.mic-btn.recording').forEach(b=>b.classList.remove('recording'));
    };
    _rec.start();
    const micId='mic-'+targetId;
    $(micId)?.classList.add('recording');
    toast('Recording… click mic again to stop','');
    setTimeout(()=>{if(_rec?.state==='recording')_rec.stop();},30000);
  }catch(e){toast('Mic error: '+e.message,'err');}
}

/* ── Init ────────────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  applyAmbientMode(_ambientMode);
  checkHealth();
  initDashboard();
  setInterval(checkHealth, 30000);

  // Keyboard shortcut listener: / focuses query, Esc clears
  document.addEventListener('keydown', e => {
    if (e.key === '/' && !['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName)) {
      e.preventDefault();
      const pane = document.querySelector('.pane.active');
      if (pane?.id === 'pane-query') {
        $('q-input')?.focus();
      } else {
        $('qqin')?.focus();
      }
    }
  });

  // Dropzone click trigger
  $('dropzone')?.addEventListener('click', e => {
    if (e.target.tagName !== 'INPUT' && !e.target.closest('input')) {
      $('file-inp')?.click();
    }
  });
});
