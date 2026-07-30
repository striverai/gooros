from __future__ import annotations

import re
from pathlib import Path

from .constants import DASHBOARD_VERSION


DEMO_TOKENS = (
    "DEMO_STATE",
    "DEMO_CHAT",
    "DEMO_CONTENT_DOCS",
    "DEMO_CONTENT_TEXT",
    "DEMO_GOOROS_CRON",
    "Pulled 14 sources",
    "Routing directive #412",
    "Sweeping 14 sources",
    "node 0x9f",
    "claude-sonnet-4.5",
    "gemini-2.5-pro",
    "text-embed-3-large",
    "Outline next week's video script",
    "template preview",
    "hard-coded reply",
)


def _replace_required(text: str, old: str, new: str) -> str:
    if old not in text:
        raise RuntimeError(f"dashboard template patch anchor not found: {old[:80]}")
    return text.replace(old, new)


def _sub_required(text: str, pattern: str, repl: str, *, flags: int = 0, label: str = "") -> str:
    new_text, count = re.subn(pattern, repl, text, count=1, flags=flags)
    if count != 1:
        raise RuntimeError(f"dashboard template patch regex anchor not found: {label or pattern[:80]}")
    return new_text


def build_live_dashboard(template_path: Path, output_path: Path) -> None:
    text = template_path.read_text(encoding="utf-8")

    text = _sub_required(
        text,
        r"let AGENTS = \[.*?\];\s*let MODELS = \[.*?\];",
        """let AGENTS = [
  { code:'A-00', initials:'OR', name:'Orchestrator', role:'Coordinator', channel:'telegram', state:'IDLE', task:'No tasks logged yet.', load:0, tokens:'0 tasks', latency:'-', success:100, tasksToday:0, share:0, defaultModel:'' },
  { code:'A-01', initials:'SC', name:'Scout', role:'Research', channel:'#scout', state:'IDLE', task:'No tasks logged yet.', load:0, tokens:'0 tasks', latency:'-', success:100, tasksToday:0, share:0, defaultModel:'' },
  { code:'A-02', initials:'SB', name:'Scribe', role:'Writing', channel:'#scribe', state:'IDLE', task:'No tasks logged yet.', load:0, tokens:'0 tasks', latency:'-', success:100, tasksToday:0, share:0, defaultModel:'' },
  { code:'A-03', initials:'RE', name:'Reach', role:'Marketing', channel:'#reach', state:'IDLE', task:'No tasks logged yet.', load:0, tokens:'0 tasks', latency:'-', success:100, tasksToday:0, share:0, defaultModel:'' },
  { code:'A-04', initials:'DV', name:'Dev', role:'Engineering', channel:'#dev', state:'IDLE', task:'No tasks logged yet.', load:0, tokens:'0 tasks', latency:'-', success:100, tasksToday:0, share:0, defaultModel:'' },
];
let MODELS = [];""",
        flags=re.S,
        label="initial live placeholders",
    )
    text = text.replace("gpt-5.5 or MiniMax-M3", "live model routing")
    text = text.replace("— → gpt-5.5 · — → MiniMax-M3", "- -> live models")

    text = _sub_required(
        text,
        r"const LOGS = \(window\.LIVE && LIVE\.logs && LIVE\.logs\.length\) \? LIVE\.logs : \[.*?\];",
        "const LOGS = (window.LIVE && LIVE.logs && LIVE.logs.length) ? LIVE.logs : [];",
        flags=re.S,
        label="overview fallback logs",
    )
    text = _sub_required(
        text,
        r"const models = \(window\.LIVE && LIVE\.cost && LIVE\.cost\.length\) \? LIVE\.cost : \[.*?\];",
        "const models = (window.LIVE && LIVE.cost && LIVE.cost.length) ? LIVE.cost : [];",
        flags=re.S,
        label="overview fallback models",
    )
    text = _sub_required(
        text,
        r"const items = \(window\.LIVE && LIVE\.ticker && LIVE\.ticker\.length\) \? LIVE\.ticker : \[.*?\];",
        "const items = (window.LIVE && LIVE.ticker && LIVE.ticker.length) ? LIVE.ticker : [];",
        flags=re.S,
        label="overview fallback ticker",
    )
    text = _sub_required(
        text,
        r"const alogs = \(window\.LIVE && LIVE\.agentlogs && LIVE\.agentlogs\.length\) \? LIVE\.agentlogs : \[.*?\];",
        "const alogs = (window.LIVE && LIVE.agentlogs && LIVE.agentlogs.length) ? LIVE.agentlogs : [];",
        flags=re.S,
        label="agent logs fallback",
    )
    text = _sub_required(
        text,
        r"let TASKS = \[.*?\];\s*const COLUMNS =",
        "let TASKS = [];\nconst COLUMNS =",
        flags=re.S,
        label="task board fallback",
    )
    text = _sub_required(
        text,
        r"const HQ = \{.*?\};\s*const AGENTS_3D = \[.*?\];",
        """const HQ = { code:'OR', name:'Gooros HQ', role:'Orchestrator', task:'No live task yet.', model:'',
  state:'IDLE', load:0, tokens:'0 tasks', latency:'-', success:100, tasksToday:0,
  position:[0,0,0], size:[3.6,6.4,3.6], floors:15, windowCols:6, silhouette:'stepped', accent:EMBER, monument:'conductor' };

const AGENTS_3D = [
  { code:'SC', name:'Scout', role:'Research', task:'No live task yet.', model:'', state:'IDLE', load:0, tokens:'0 tasks', latency:'-', success:100, tasksToday:0, position:[-7.5,0,-5], size:[2.6,5.6,2.6], floors:14, windowCols:4, silhouette:'tower', accent:SPOTLIGHT, monument:'scout' },
  { code:'SB', name:'Scribe', role:'Writing', task:'No live task yet.', model:'', state:'IDLE', load:0, tokens:'0 tasks', latency:'-', success:100, tasksToday:0, position:[7.5,0,-5], size:[3.2,4.4,2.4], floors:10, windowCols:5, silhouette:'slab', accent:EMBER, monument:'scribe' },
  { code:'RE', name:'Reach', role:'Marketing', task:'No live task yet.', model:'', state:'IDLE', load:0, tokens:'0 tasks', latency:'-', success:100, tasksToday:0, position:[-7.5,0,5], size:[2.8,4.8,2.8], floors:12, windowCols:4, silhouette:'twin', accent:EMBER, monument:'herald' },
  { code:'DV', name:'Dev', role:'Engineering', task:'No live task yet.', model:'', state:'IDLE', load:0, tokens:'0 tasks', latency:'-', success:100, tasksToday:0, position:[7.5,0,5], size:[2.6,6.4,2.6], floors:16, windowCols:4, silhouette:'tower', accent:EMBER, monument:'smith' },
];""",
        flags=re.S,
        label="office fallback",
    )
    text = _replace_required(
        text,
        "let empireInited = false;",
        "const CODE_TO_PROFILE = { OR:'orchestrator', SC:'scout', SB:'scribe', RE:'reach', DV:'dev' };\nlet empireInited = false;",
    )
    text = _sub_required(
        text,
        r"    const dot = b\.state==='EXECUTING' \? 'bg-ember animate-breathe'\s*: b\.state==='THINKING'\s*\? 'bg-ember-soft'\s*: b\.state==='RETRY'\s*\? 'bg-ember animate-ticker'\s*: 'bg-cream/30';",
        "    const active = agentWorking(CODE_TO_PROFILE[b.code]);\n    const dot = active ? 'bg-ember animate-breathe' : 'bg-cream/30';",
        flags=re.S,
        label="office legend live working state",
    )

    text = re.sub(r"^\s*let DEMO_CONTENT_DOCS = .*?;\s*$\n?", "", text, flags=re.M)
    text = re.sub(r"^\s*const DEMO_CONTENT_TEXT = .*?;\s*$\n?", "", text, flags=re.M)
    text = re.sub(r"^\s*const DEMO_CHAT = \{.*?\n\};\s*$\n?", "", text, flags=re.S | re.M)
    text = re.sub(r"^\s*const DEMO_GOOROS_CRON = .*?;\s*$\n?", "", text, flags=re.M)
    text = re.sub(r"^\s*const DEMO_STATE = .*?;\s*$\n?", "", text, flags=re.M)

    text = _replace_required(
        text,
        "  CONTENT_DOCS = DEMO_CONTENT_DOCS;",
        "  CONTENT_DOCS = await (await fetch('/api/content', {cache:'no-store'})).json();",
    )
    text = _replace_required(
        text,
        "  let text = DEMO_CONTENT_TEXT[d.agent+'|'+d.filename] || ('# '+(d.title||'Untitled')+'\\n\\n_(preview mode — sample content)_');",
        "  let text = (await (await fetch('/api/content/read?agent='+encodeURIComponent(d.agent)+'&file='+encodeURIComponent(d.filename), {cache:'no-store'})).json()).text || '';",
    )
    text = _replace_required(
        text,
        "  document.getElementById('doc-del').onclick = async ()=>{ if(!confirm(translateText('Delete this document?', CURRENT_LANG)))return; const i=DEMO_CONTENT_DOCS.findIndex(x=>x.agent===d.agent&&x.filename===d.filename); if(i>=0) DEMO_CONTENT_DOCS.splice(i,1); CONTENT_SEL=null; loadContentDocs(); };",
        "  document.getElementById('doc-del').onclick = async ()=>{ if(!confirm(translateText('Delete this document?', CURRENT_LANG)))return; await fetch('/api/content/delete?agent='+encodeURIComponent(d.agent)+'&file='+encodeURIComponent(d.filename), {method:'POST'}); CONTENT_SEL=null; await loadContentDocs(); };",
    )
    text = _replace_required(
        text,
        "    DEMO_CONTENT_TEXT[d.agent+'|'+d.filename] = val;\n    CONTENT_SEL.text = val; openDoc(d); loadContentDocs();",
        "    await fetch('/api/content/save?agent='+encodeURIComponent(d.agent)+'&file='+encodeURIComponent(d.filename), {method:'POST', body:val});\n    CONTENT_SEL.text = val; await openDoc(d); await loadContentDocs();",
    )
    text = _replace_required(
        text,
        "    DEMO_CONTENT_TEXT[agent+'|'+file] = body;\n    DEMO_CONTENT_DOCS.unshift({ agent, filename:file, title, modified_at:new Date().toISOString() });",
        "    await fetch('/api/content/save?agent='+encodeURIComponent(agent)+'&file='+encodeURIComponent(file), {method:'POST', body});",
    )

    text = _replace_required(
        text,
        "      const h = DEMO_CHAT[key] || {telegram:false, messages:[]};",
        "      const h = await (await fetch('/api/chat/history?agent='+encodeURIComponent(key), {cache:'no-store'})).json();",
    )
    canned_block = """    await new Promise(r=>setTimeout(r,650));
    reply.typing=false;
    const canned = chatMeta(key).name+' (preview mode): this is a sample reply. Once the tutorial prompts wire the backend, every message runs a real agent turn — threaded into the live Telegram session for Aria.';
    const words = canned.split(' ');
    for(let i=0;i<words.length;i++){ reply.text += (i?' ':'')+words[i]; if(CHAT_CUR===key) renderMessages(); await new Promise(r=>setTimeout(r,42)); }
    reply.ts=Date.now();
    if(CHAT_CUR===key) setChatLive('idle');"""
    stream_block = """    const res = await fetch('/api/chat/send', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({agent:key, text})});
    reply.typing=false;
    if(!res.ok){ throw new Error(await res.text()); }
    if(res.body && res.body.getReader){
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      while(true){
        const chunk = await reader.read();
        if(chunk.done) break;
        reply.text += decoder.decode(chunk.value, {stream:true});
        if(CHAT_CUR===key) renderMessages();
      }
      reply.text += decoder.decode();
    } else {
      reply.text = await res.text();
    }
    reply.ts=Date.now();
    if(CHAT_CUR===key) setChatLive('idle');"""
    text = _replace_required(text, canned_block, stream_block)
    text = text.replace("reply.text='âš ï¸ preview error';", "reply.text='Agent turn failed: '+(e && e.message ? e.message : e);")

    text = _replace_required(
        text,
        "  const jobs = DEMO_GOOROS_CRON;",
        "  const jobs = (await (await fetch('/api/state', {cache:'no-store'})).json()).hermes_cron || [];",
    )
    text = _replace_required(
        text,
        "    alert(translateText('Preview mode — cron actions (run / pause / resume / delete) get wired to Gooros later by the tutorial prompts.', CURRENT_LANG));",
        "    const res = await fetch('/api/cron/action?action='+encodeURIComponent(action)+'&id='+encodeURIComponent(id), {method:'POST'});\n    if(!res.ok){ alert(await res.text()); return; }\n    await loadSchedule();",
    )

    text = _replace_required(
        text,
        "            fetch('/api/board/update?id='+t.boardId,{method:'POST',body:JSON.stringify({status:bm[t.status]})}); } }",
        "            fetch('/api/board/update?id='+t.boardId,{method:'POST',body:JSON.stringify({status:bm[t.status]})}).then(()=>hydrate()); } }",
    )
    text = _replace_required(
        text,
        """  async function createMission() {
    const title = (nmTitle.value || '').trim();
    if (!title) return;
    const col = document.getElementById('nm-col').value;              // todo/doing/done
    const prio = document.getElementById('nm-priority').value;        // P1/P2/P3
    TASKS.unshift({ id:'HRM-'+Math.floor(100+Math.random()*899), title,
      priority:prio, due:'—', tags:['new'], progress: col==='done'?100:col==='doing'?25:0,
      subtasks:{total:1,done:col==='done'?1:0}, status:col, updated:'just now' });
    nmTitle.value = '';
    nmForm.classList.add('hidden');
    renderTasks();
  }""",
        """  async function createMission() {
    const title = (nmTitle.value || '').trim();
    if (!title) return;
    const col = document.getElementById('nm-col').value;
    const prio = document.getElementById('nm-priority').value;
    await fetch('/api/board', {method:'POST', body:JSON.stringify({title, priority:PRIO_TO_BOARD[prio]||'medium', status:COL_TO_BOARD[col]||'pending'})});
    nmTitle.value = '';
    nmForm.classList.add('hidden');
    await hydrate();
  }""",
    )
    text = _replace_required(
        text,
        """    const i = TASKS.findIndex(t=>t.id===del.dataset.del); if(i>=0) TASKS.splice(i,1);
    renderTasks();""",
        """    const t = TASKS.find(x=>x.id===del.dataset.del); if(t && t.boardId){ await fetch('/api/board/delete?id='+encodeURIComponent(t.boardId), {method:'POST'}); }
    await hydrate();""",
    )

    text = re.sub(
        r"async function openAgent\(key\)\{.*?^\s*}\n\s*document\.getElementById\('agent-drawer-scrim'",
        _open_agent_function() + "\n  document.getElementById('agent-drawer-scrim'",
        text,
        flags=re.S | re.M,
    )

    text = _replace_required(
        text,
        "  applyState(DEMO_STATE);   // frozen real snapshot — no server, no SSE, no polling",
        "  hydrate(); connectSSE(); startPolling();",
    )
    text = _sub_required(
        text,
        r"\s*setHero\('routing-split', `\$\{r\.premium_calls\|\|0\}.*?MiniMax-M3`\);",
        "\n    const usageNames = (d.model_usage || []).map(m => m.name).filter(Boolean);\n    const premiumName = usageNames[0] || 'premium';\n    const fastName = usageNames[1] || 'fast';\n    setHero('routing-split', `${r.premium_calls||0} -> ${premiumName} · ${r.fast_calls||0} -> ${fastName}`);",
        flags=re.S,
        label="routing split model names",
    )
    text = _sub_required(
        text,
        r"\s*if \(d\.routing && d\.routing\.total\) LIVE\.ticker\.push\(\{src:'ROUTER', msg:`complexity routing.*?MiniMax-M3`\}\);",
        "\n    if (d.routing && d.routing.total) LIVE.ticker.push({src:'ROUTER', msg:`complexity routing · ${d.routing.premium_calls} -> ${premiumName} · ${d.routing.fast_calls} -> ${fastName}`});",
        flags=re.S,
        label="routing ticker model names",
    )
    text = _replace_required(
        text,
        "    setHero('office-lights', fleet.filter(a=>a.state==='EXECUTING').length);",
        "    setHero('office-lights', (d.working_agents||[]).length);",
    )
    text = _replace_required(
        text,
        "  const totalShare = AGENTS.reduce((s,a)=>s+a.share,0);",
        "  const rawShare = AGENTS.reduce((s,a)=>s+a.share,0);\n  const totalShare = rawShare || AGENTS.length;",
    )
    text = _replace_required(
        text,
        "    const pct = a.share/totalShare;",
        "    const pct = rawShare ? a.share/totalShare : 1/totalShare;",
    )
    text = _replace_required(
        text,
        "${totalShare}%</text>",
        "${rawShare}%</text>",
    )
    text = re.sub(r">v1\.0<", f">v{DASHBOARD_VERSION}<", text, count=1)

    leftovers = [token for token in DEMO_TOKENS if token in text]
    if leftovers:
        raise RuntimeError(f"dashboard patch left demo tokens: {', '.join(leftovers)}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8", newline="\n")


def _readonly_office_function() -> str:
    return r"""/* ============== OFFICE skyline (stdlib / no npm) ============== */
const EMBER='#e25822', EMBER_SOFT='#f59e6b', INK='#1a1410', INK_2='#241a13', SPOTLIGHT='#00e5ff';
const CODE_TO_PROFILE = { OR:'orchestrator', SC:'scout', SB:'scribe', RE:'reach', DV:'dev' };

const HQ = { code:'OR', name:'Gooros HQ', role:'Orchestrator', task:'No live task yet.', model:'',
  state:'IDLE', load:0, tokens:'0 tasks', latency:'-', success:100, tasksToday:0,
  position:[0,0,0], size:[3.6,6.4,3.6], floors:15, windowCols:6, silhouette:'stepped', accent:EMBER, monument:'conductor' };

const AGENTS_3D = [
  { code:'SC', name:'Scout', role:'Research', task:'No live task yet.', model:'', state:'IDLE', load:0, tokens:'0 tasks', latency:'-', success:100, tasksToday:0, position:[-7.5,0,-5], size:[2.6,5.6,2.6], floors:14, windowCols:4, silhouette:'tower', accent:SPOTLIGHT, monument:'scout' },
  { code:'SB', name:'Scribe', role:'Writing', task:'No live task yet.', model:'', state:'IDLE', load:0, tokens:'0 tasks', latency:'-', success:100, tasksToday:0, position:[7.5,0,-5], size:[3.2,4.4,2.4], floors:10, windowCols:5, silhouette:'slab', accent:EMBER, monument:'scribe' },
  { code:'RE', name:'Reach', role:'Marketing', task:'No live task yet.', model:'', state:'IDLE', load:0, tokens:'0 tasks', latency:'-', success:100, tasksToday:0, position:[-7.5,0,5], size:[2.8,4.8,2.8], floors:12, windowCols:4, silhouette:'twin', accent:EMBER, monument:'herald' },
  { code:'DV', name:'Dev', role:'Engineering', task:'No live task yet.', model:'', state:'IDLE', load:0, tokens:'0 tasks', latency:'-', success:100, tasksToday:0, position:[7.5,0,5], size:[2.6,6.4,2.6], floors:16, windowCols:4, silhouette:'tower', accent:EMBER, monument:'smith' },
];
const ALL_BUILDINGS = [HQ, ...AGENTS_3D];

let empireInited = false;
let officeSelected = null;
let empireApi = null;

function renderLegend() {
  document.getElementById('empire-legend').innerHTML = ALL_BUILDINGS.map(b => {
    const sel = officeSelected===b.code;
    const active = agentWorking(CODE_TO_PROFILE[b.code]);
    const dot = active ? 'bg-ember animate-breathe' : 'bg-cream/30';
    return `<button data-bcode="${b.code}" class="text-left rounded-[22px] p-3 reference-log-card transition-all ${sel?'bg-[rgb(247_240_225_/_0.08)]':'hover:bg-[rgb(247_240_225_/_0.08)]'}">
      <div class="flex items-center justify-between">
        <span class="font-mono text-[9px] tracking-[0.22em] uppercase text-cream/55">${b.code==='OR'?'HQ':b.role}</span>
        <span class="size-2 rounded-full ${dot}"></span>
      </div>
      <div class="font-display text-[20px] mt-1 leading-none">${b.name}</div>
      <div class="font-mono text-[10px] mt-1.5 text-cream/55 truncate">${b.model}</div>
    </button>`;
  }).join('');
  document.querySelectorAll('[data-bcode]').forEach(btn => {
    btn.addEventListener('click', () => selectBuilding(btn.dataset.bcode));
  });
  syncLanguage();
}

function renderDossier() {
  const mount = document.getElementById('dossier-mount');
  if (!officeSelected) { mount.innerHTML=''; syncLanguage(); return; }
  const a = ALL_BUILDINGS.find(b=>b.code===officeSelected);
  if (!a) return;
  const isHQ = a.code==='OR';
  mount.innerHTML = `<div class="absolute top-4 right-4 z-30 w-[320px] max-w-[calc(100%-2rem)] rounded-2xl bg-ink/95 backdrop-blur-xl border border-ember/30 p-5 text-cream shadow-2xl animate-rise">
    <div class="flex items-start justify-between gap-3">
      <div>
        <div class="flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.22em] text-ember">
          <i data-lucide="flame" class="size-3"></i> ${isHQ?'Orchestrator · HQ':`${a.role} · Specialist`}
        </div>
        <div class="font-display text-[28px] leading-none mt-1.5">${a.name}</div>
      </div>
      <button id="dossier-close" class="size-8 rounded-full bg-cream/5 border border-cream/10 grid place-items-center hover:bg-ember/20 hover:border-ember/40 transition">
        <i data-lucide="x" class="size-3.5"></i>
      </button>
    </div>
    <div class="mt-4 rounded-xl bg-cream/5 border border-cream/10 p-3">
      <div class="font-mono text-[9px] tracking-[0.2em] uppercase text-cream/55">Current task</div>
      <div class="font-sans text-[13px] leading-snug mt-1">${a.task}</div>
    </div>
    <div class="mt-3 grid grid-cols-2 gap-2">
      ${[['Load',a.load+'%','zap'],['Tokens 24h',a.tokens,'sparkles'],['Latency',a.latency,'cpu'],['Success',a.success+'%','flame']].map(([k,v,icon])=>`
        <div class="rounded-xl bg-cream/5 border border-cream/10 p-3">
          <div class="flex items-center gap-1.5 font-mono text-[9px] uppercase tracking-wider text-cream/55"><i data-lucide="${icon}" class="size-3"></i> ${k}</div>
          <div class="font-display text-[20px] tabular-nums mt-1 leading-none">${v}</div>
        </div>`).join('')}
    </div>
    <div class="mt-3 pt-3 border-t border-cream/10 flex items-center justify-between font-mono text-[10px] uppercase tracking-wider">
      <span class="text-cream/55">Model</span><span class="text-ember">${a.model}</span>
    </div>
    <div class="flex items-center justify-between font-mono text-[10px] uppercase tracking-wider mt-1.5">
      <span class="text-cream/55">State</span><span class="text-cream">${a.state}</span>
    </div>
    <div class="flex items-center justify-between font-mono text-[10px] uppercase tracking-wider mt-1.5">
      <span class="text-cream/55">Tasks today</span><span class="text-cream tabular-nums">${a.tasksToday}</span>
    </div>
  </div>`;
  document.getElementById('dossier-close').addEventListener('click', () => selectBuilding(null));
  lucide.createIcons();
  syncLanguage();
}

function selectBuilding(code) {
  officeSelected = code;
  if (empireApi) empireApi.setSelected(code);
  renderLegend();
  renderDossier();
}

async function initEmpire() {
  if (empireInited) { renderLegend(); renderDossier(); if (empireApi) empireApi.setSelected(officeSelected); return; }
  empireInited = true;
  renderLegend();
  const wrap = document.getElementById('empire-canvas-wrap');
  if (!wrap) return;
  const loading = document.getElementById('empire-loading');
  if (loading) loading.remove();
  wrap.innerHTML = `<div class="absolute inset-0 overflow-hidden rounded-[28px] bg-[radial-gradient(circle_at_50%_15%,rgba(226,88,34,.22),transparent_32%),linear-gradient(180deg,#120c08_0%,#241a13_62%,#120c08_100%)]">
    <div class="absolute inset-x-8 bottom-14 h-px bg-ember/50"></div>
    <div id="office-skyline" class="absolute inset-x-5 bottom-14 flex items-end justify-center gap-4 sm:gap-6"></div>
    <div class="absolute inset-x-10 bottom-8 h-6 rounded-full bg-ember/10 blur-xl"></div>
  </div>`;
  const skyline = wrap.querySelector('#office-skyline');
  function buildingMarkup(agent, index) {
    const profile = CODE_TO_PROFILE[agent.code] || 'orchestrator';
    const active = agentWorking(profile);
    const height = Math.max(96, Math.min(230, 88 + (agent.tasksToday || 0) * 6 + (agent.load || 0)));
    const width = agent.code === 'OR' ? 86 : 68;
    const glow = active ? SPOTLIGHT : (agent.accent || EMBER);
    const windows = Array.from({length: Math.max(10, Math.min(36, Math.round(height / 7)))}, (_, i) =>
      `<span class="block h-1.5 rounded-full" style="background:${i % 3 === 0 ? glow : 'rgba(247,240,225,.34)'}"></span>`
    ).join('');
    return `<button data-office-code="${agent.code}" class="group relative shrink-0 rounded-t-[18px] border border-cream/10 bg-ink/90 px-3 pb-3 pt-4 text-left shadow-2xl transition hover:-translate-y-1 focus:outline-none focus:ring-2 focus:ring-ember/60" style="height:${height}px;width:${width}px;box-shadow:0 0 32px ${glow}33">
      <div class="absolute -top-3 left-1/2 h-5 w-5 -translate-x-1/2 rotate-45 rounded-sm border border-cream/10" style="background:${glow}"></div>
      <div class="grid grid-cols-3 gap-1">${windows}</div>
      <div class="absolute inset-x-2 bottom-2 rounded-lg bg-cream/5 px-2 py-1">
        <div class="font-mono text-[9px] text-cream/60">${agent.code}</div>
        <div class="truncate font-sans text-[11px] text-cream">${agent.name}</div>
      </div>
    </button>`;
  }
  skyline.innerHTML = ALL_BUILDINGS.map(buildingMarkup).join('');
  skyline.querySelectorAll('[data-office-code]').forEach(btn => {
    btn.addEventListener('click', () => selectBuilding(btn.dataset.officeCode));
  });
  empireApi = {
    setSelected(code) {
      skyline.querySelectorAll('[data-office-code]').forEach(btn => {
        btn.classList.toggle('ring-2', btn.dataset.officeCode === code);
        btn.classList.toggle('ring-ember', btn.dataset.officeCode === code);
      });
    },
    colors() { return ALL_BUILDINGS.map(b => ({code:b.code, accent:b.accent || EMBER})); },
  };
  empireApi.setSelected(officeSelected);
  lucide.createIcons();
  syncLanguage();
}"""


def _open_agent_function() -> str:
    return r"""  async function openAgent(key){
    drawer.classList.remove('hidden');
    drawerBody.innerHTML = '<div class="font-mono text-[11px] text-muted-foreground uppercase tracking-widest py-10 text-center">loadingâ€¦</div>';
    try {
      const a = await (await fetch('/api/agent?key='+encodeURIComponent(key), {cache:'no-store'})).json();
      if(a.error){ drawerBody.innerHTML = '<p class="text-muted-foreground">Not found.</p>'; syncLanguage(); return; }
      const modelBars = (a.models||[]).map(m => {
        const pct = a.total ? Math.round(100*m.count/a.total) : 0;
        return `<div class="flex items-center gap-2 mb-1.5"><span class="font-mono text-[10px] text-ink w-28 truncate">${esc2(m.model)}</span>
          <div class="flex-1 h-2 rounded-full bg-secondary overflow-hidden"><div class="h-full rounded-full bg-ember" style="width:${pct}%"></div></div>
          <span class="font-mono text-[10px] text-muted-foreground w-14 text-right">${m.count}â‹… ${pct}%</span></div>`;
      }).join('') || '<span class="font-mono text-[10px] text-muted-foreground">no runs yet</span>';
      const rows = (a.recent||[]).map(r => `<div class="flex items-start gap-2 py-2 border-b border-border/60 last:border-0">
        <span class="mt-0.5 size-1.5 rounded-full shrink-0 ${r.status==='completed'?'bg-ember':'bg-red-500'}"></span>
        <div class="min-w-0 flex-1"><p class="font-sans text-[12px] text-ink truncate">${esc2(r.task)}</p>
          <p class="font-mono text-[9px] text-muted-foreground mt-0.5">${esc2(r.time)} Â· ${esc2(r.model)}</p></div></div>`).join('')
        || '<p class="font-mono text-[10px] text-muted-foreground">no recent tasks</p>';
      drawerBody.innerHTML = `
        <div class="flex items-center justify-between mb-5">
          <div class="flex items-center gap-3">
            <div class="size-12 rounded-2xl bg-ink text-cream grid place-items-center font-mono text-[13px] font-bold">${esc2(a.initials)}</div>
            <div><div class="font-display text-[24px] text-ink leading-none">${esc2(a.name)}</div>
              <div class="font-mono text-[9px] uppercase tracking-widest text-muted-foreground mt-1">${esc2(a.role)} Â· ${esc2(a.channel)}</div></div>
          </div>
          <button id="agent-drawer-x" class="size-9 rounded-full bg-cream border border-border grid place-items-center hover:bg-ember hover:text-cream transition"><i data-lucide="x" class="size-4"></i></button>
        </div>
        <div class="grid grid-cols-2 gap-3 mb-5">
          <div class="rounded-2xl bg-cream border border-border p-4"><div class="font-mono text-[9px] uppercase tracking-widest text-muted-foreground">Tasks</div><div class="font-display text-[32px] text-ink tabular-nums leading-none mt-1">${a.total}</div></div>
          <div class="rounded-2xl bg-cream border border-border p-4"><div class="font-mono text-[9px] uppercase tracking-widest text-muted-foreground">Success</div><div class="font-display text-[32px] text-ember tabular-nums leading-none mt-1">${a.success}%</div></div>
        </div>
        <div class="mb-5"><div class="font-mono text-[10px] uppercase tracking-widest text-muted-foreground mb-2">Model history</div>${modelBars}</div>
        ${a.last_error ? `<div class="mb-5 rounded-2xl bg-red-500/8 border border-red-500/25 p-3"><div class="font-mono text-[9px] uppercase tracking-widest text-red-500 mb-1">Last error</div><p class="font-sans text-[12px] text-ink">${esc2(a.last_error)}</p></div>` : ''}
        <div><div class="font-mono text-[10px] uppercase tracking-widest text-muted-foreground mb-1">Recent tasks</div>${rows}</div>`;
      lucide.createIcons();
      document.getElementById('agent-drawer-x').addEventListener('click', closeDrawer);
      syncLanguage();
    } catch(e){ drawerBody.innerHTML = '<p class="text-muted-foreground">Failed to load.</p>'; syncLanguage(); }
  }"""
