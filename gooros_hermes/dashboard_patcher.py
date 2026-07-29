from __future__ import annotations

import re
from pathlib import Path

from .constants import VERSION


DEMO_TOKENS = (
    "DEMO_STATE",
    "DEMO_CHAT",
    "DEMO_CONTENT_DOCS",
    "DEMO_CONTENT_TEXT",
    "DEMO_HERMES_CRON",
    "template preview",
    "hard-coded reply",
)


def _replace_required(text: str, old: str, new: str) -> str:
    if old not in text:
        raise RuntimeError(f"dashboard template patch anchor not found: {old[:80]}")
    return text.replace(old, new)


def build_live_dashboard(template_path: Path, output_path: Path) -> None:
    text = template_path.read_text(encoding="utf-8")

    text = re.sub(r"^\s*let DEMO_CONTENT_DOCS = .*?;\s*$\n?", "", text, flags=re.M)
    text = re.sub(r"^\s*const DEMO_CONTENT_TEXT = .*?;\s*$\n?", "", text, flags=re.M)
    text = re.sub(r"^\s*const DEMO_CHAT = \{.*?\n\};\s*$\n?", "", text, flags=re.S | re.M)
    text = re.sub(r"^\s*const DEMO_HERMES_CRON = .*?;\s*$\n?", "", text, flags=re.M)
    text = re.sub(r"^\s*const DEMO_STATE = .*?;\s*$\n?", "", text, flags=re.M)

    text = _replace_required(
        text,
        "  CONTENT_DOCS = DEMO_CONTENT_DOCS;",
        "  CONTENT_DOCS = await (await fetch('/api/content', {cache:'no-store'})).json();",
    )
    text = _replace_required(
        text,
        "  let text = DEMO_CONTENT_TEXT[d.agent+'|'+d.filename] || ('# '+(d.title||'Untitled')+'\\n\\n_(template preview — content is hard-coded)_');",
        "  let text = (await (await fetch('/api/content/read?agent='+encodeURIComponent(d.agent)+'&file='+encodeURIComponent(d.filename), {cache:'no-store'})).json()).text || '';",
    )
    text = _replace_required(
        text,
        "  document.getElementById('doc-del').onclick = async ()=>{ if(!confirm('Delete this document?'))return; const i=DEMO_CONTENT_DOCS.findIndex(x=>x.agent===d.agent&&x.filename===d.filename); if(i>=0) DEMO_CONTENT_DOCS.splice(i,1); CONTENT_SEL=null; loadContentDocs(); };",
        "  document.getElementById('doc-del').onclick = async ()=>{ if(!confirm('Delete this document?'))return; await fetch('/api/content/delete?agent='+encodeURIComponent(d.agent)+'&file='+encodeURIComponent(d.filename), {method:'POST'}); CONTENT_SEL=null; loadContentDocs(); };",
    )
    text = _replace_required(
        text,
        "    DEMO_CONTENT_TEXT[d.agent+'|'+d.filename] = val;\n    CONTENT_SEL.text = val; openDoc(d); loadContentDocs();",
        "    await fetch('/api/content/save?agent='+encodeURIComponent(d.agent)+'&file='+encodeURIComponent(d.filename), {method:'POST', body:val});\n    CONTENT_SEL.text = val; openDoc(d); loadContentDocs();",
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
    const canned = chatMeta(key).name+' (template preview): this is a hard-coded reply. Once the tutorial prompts wire the backend, every message runs a real agent turn — threaded into the live Telegram session for Aria.';
    const words = canned.split(' ');
    for(let i=0;i<words.length;i++){ reply.text += (i?' ':'')+words[i]; if(CHAT_CUR===key) renderMessages(); await new Promise(r=>setTimeout(r,42)); }
    reply.ts=Date.now();
    if(CHAT_CUR===key) setChatLive('idle');"""
    stream_block = """    reply.typing=false;
    const res = await fetch('/api/chat/send', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({agent:key,text})});
    if(!res.ok) throw new Error(await res.text());
    if(res.body){
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      while(true){
        const chunk = await reader.read();
        if(chunk.done) break;
        reply.text += dec.decode(chunk.value, {stream:true});
        if(CHAT_CUR===key) renderMessages();
      }
    } else {
      reply.text = await res.text();
    }
    reply.ts=Date.now();
    if(CHAT_CUR===key) setChatLive('idle');"""
    text = _replace_required(text, canned_block, stream_block)

    text = _replace_required(
        text,
        "  const jobs = DEMO_HERMES_CRON;",
        "  const jobs = (await (await fetch('/api/state', {cache:'no-store'})).json()).hermes_cron || [];",
    )
    text = _replace_required(
        text,
        "    alert('Template preview — cron actions (run / pause / resume / delete) get wired to Hermes later by the tutorial prompts.');",
        "    await fetch('/api/cron/action?action='+encodeURIComponent(action)+'&id='+encodeURIComponent(id), {method:'POST'}); await loadSchedule();",
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
    text = re.sub(r">v1\.0<", f">v{VERSION}<", text, count=1)

    leftovers = [token for token in DEMO_TOKENS if token in text]
    if leftovers:
        raise RuntimeError(f"dashboard patch left demo tokens: {', '.join(leftovers)}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8", newline="\n")


def _open_agent_function() -> str:
    return r"""  async function openAgent(key){
    drawer.classList.remove('hidden');
    drawerBody.innerHTML = '<div class="font-mono text-[11px] text-muted-foreground uppercase tracking-widest py-10 text-center">loadingâ€¦</div>';
    try {
      const a = await (await fetch('/api/agent?key='+encodeURIComponent(key), {cache:'no-store'})).json();
      if(a.error){ drawerBody.innerHTML = '<p class="text-muted-foreground">Not found.</p>'; return; }
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
    } catch(e){ drawerBody.innerHTML = '<p class="text-muted-foreground">Failed to load.</p>'; }
  }"""
