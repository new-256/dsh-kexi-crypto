#!/usr/bin/env node
// Extracts a human-readable trace from a decoded DSH session JSONL file.
// Usage: node tools/trace-session.mjs <decoded.jsonl> [out.md]
import fs from 'node:fs';

const src = process.argv[2];
const out = process.argv[3] || null;
if (!src) {
  console.error('usage: node tools/trace-session.mjs <decoded.jsonl> [out.md]');
  process.exit(2);
}

const lines = fs.readFileSync(src, 'utf8').split(/\r?\n/).filter(Boolean);
const rows = [];

function clip(s, n) {
  s = String(s ?? '');
  return s.length > n ? s.slice(0, n) + `\n…[+${s.length - n} chars]` : s;
}

for (const raw of lines) {
  let e;
  try { e = JSON.parse(raw); } catch { rows.push('```\n[UNPARSEABLE] ' + raw.slice(0, 200) + '\n```'); continue; }
  const d = e.data || {};
  const t = e.type;
  const time = e.time ? new Date(e.time).toISOString() : '';

  if (t === 'session') {
    rows.push(`# Session ${d.id || e.id}\n- cwd: \`${d.cwd || e.cwd}\`\n- agentPreset: \`${e.agentPreset || d.agentPreset}\`\n`);
    continue;
  }
  if (t === 'session/title') { rows.push(`> **Title**: ${d.title || JSON.stringify(d).slice(0, 120)}`); continue; }
  if (t === 'user/message') {
    const m = d.message || d;
    const text = (m.content || []).filter(c => c.type === 'text').map(c => c.text).join('\n');
    rows.push(`\n## 👤 USER (seq ${e.seq})\n${clip(text, 4000)}`);
    continue;
  }
  if (t === 'assistant/message') {
    const m = d.message || d;
    const parts = (m.content || []);
    const txt = parts.filter(c => c.type === 'text').map(c => c.text).join('\n');
    const tools = parts.filter(c => c.type === 'tool_use' || c.type === 'tool-call' || c.type === 'tool_call');
    if (txt) rows.push(`\n### 🤖 ASSISTANT (seq ${e.seq})\n${clip(txt, 3000)}`);
    for (const tu of tools) rows.push(`\n- 🔧 tool_use \`${tu.name}\`\n\`\`\`json\n${clip(JSON.stringify(tu.input ?? tu.arguments, null, 2), 2500)}\n\`\`\``);
    continue;
  }
  if (t === 'tool/call') {
    const msg = d.message || d;
    const name = msg.name || msg.toolName || d.name || d.toolName;
    const args = msg.input ?? msg.arguments ?? d.arguments ?? d.input;
    rows.push(`\n- 🔧 **CALL** \`${name}\` (seq ${e.seq})\n\`\`\`json\n${clip(typeof args === 'string' ? args : JSON.stringify(args, null, 2), 3000)}\n\`\`\``);
    continue;
  }
  if (t === 'tool/result') {
    const msg = d.message || d;
    let content = msg.content ?? d.content ?? d.result ?? d.output;
    if (Array.isArray(content)) content = content.map(c => c.text ?? JSON.stringify(c)).join('\n');
    if (content && typeof content === 'object') content = JSON.stringify(content, null, 2);
    const flag = msg.isError || d.isError ? '❌ ERROR' : '✅';
    rows.push(`\n- ${flag} **RESULT** (seq ${e.seq})\n\`\`\`\n${clip(content, 6000)}\n\`\`\``);
    continue;
  }
  if (t === 'team/member') {
    rows.push(`- 👥 team/member: ${clip(JSON.stringify(d), 400)}`);
    continue;
  }
  if (t === 'agent/inbox/spliced') {
    rows.push(`- 📥 inbox/spliced: ${clip(JSON.stringify(d), 600)}`);
    continue;
  }
  if (t === 'step/start' || t === 'step/end' || t === 'turn/start' || t === 'turn/end') {
    rows.push(`\n<sub>${t} seq=${e.seq} ${time}</sub>`);
    continue;
  }
  if (t === 'job/registered' || t === 'job/settled') {
    rows.push(`- 🧵 ${t}: ${clip(JSON.stringify(d), 300)}`);
    continue;
  }
  rows.push(`- · ${t}: ${clip(JSON.stringify(d), 300)}`);
}

const text = rows.join('\n');
if (out) fs.writeFileSync(out, text, 'utf8');
else process.stdout.write(text);
