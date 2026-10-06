#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按会话标题自动发现主理人 + 其成员，输出紧凑进度快照。"""
import zstandard, json, os, sys, datetime, urllib.request

BASE = r'C:\Users\lcl\AppData\Roaming\DSH Desktop\dsh-home\sessions\--C-Users-lcl-Desktop-K~6790~5DE5~4F5C~6587~4EF6~5939--'
KOUT = r'C:\Users\lcl\Desktop\K析工作文件夹\kexi_out'
TITLE = sys.argv[1] if len(sys.argv) > 1 else 'pyth'
ARTS = ['PYTHUSDT_1d_klines.json', 'PYTHUSDT_1d_indicators.json', 'PYTHUSDT_final_report.json',
        'PYTHUSDT_1d_dashboard.html', 'pythusdt_dashboard.html', 'latest.json',
        'entry_plan.json', 'coin_profile.json', 'news_watch.json']


def load(sid):
    p = os.path.join(BASE, sid, 'session.v4.jsonl.zstd')
    if not os.path.exists(p):
        return None
    with open(p, 'rb') as f:
        txt = zstandard.ZstdDecompressor().stream_reader(f).read().decode('utf-8', 'replace')
    return [json.loads(l) for l in txt.splitlines() if l.strip().startswith('{')]


def find_lead():
    best = None
    for s in os.listdir(BASE):
        p = os.path.join(BASE, s, 'session.v4.jsonl.zstd')
        if not os.path.exists(p):
            continue
        evs = load(s)
        if not evs:
            continue
        title = ''
        child = ''
        for e in evs:
            if e.get('type') == 'session/title':
                title = (e.get('data') or {}).get('title') or ''
            if e.get('type') == 'subagent/catalog':
                child = (e.get('data') or {}).get('childId') or child
            if title and child:
                break
        if TITLE.lower() in (title or '').lower():
            best = (s, title, child)
            break
    return best


def summarize(sid):
    evs = load(sid)
    if not evs:
        return None
    turns = [(e.get('data') or {}).get('turn') for e in evs if e.get('type') == 'turn/start']
    steps = [(e.get('data') or {}).get('step') for e in evs if e.get('type') == 'step/start']
    tools = {}
    for e in evs:
        if e.get('type') == 'tool/call':
            n = str((e.get('data') or {}).get('name'))
            tools[n] = tools.get(n, 0) + 1
    err = None
    for e in evs:
        if e.get('type') == 'turn/end':
            r = (e.get('data') or {}).get('reason') or {}
            if r.get('kind') == 'error':
                err = ((r.get('error') or {}).get('code') or '') + ' ' + str((r.get('error') or {}).get('message', ''))[:40]
    return {
        'ev': len(evs), 'turn': max([t for t in turns if t] or [0]),
        'step': max([s for s in steps if s] or [0]), 'tools': tools, 'err': err,
        'mtime': os.path.getmtime(os.path.join(BASE, sid, 'session.v4.jsonl.zstd')),
    }


def main():
    found = find_lead()
    if not found:
        print('未找到标题含 %r 的会话' % TITLE)
        return
    lead_sid, title, child = found
    print('=== %s | %s ===' % (datetime.datetime.now().strftime('%H:%M:%S'), title))
    ids = [('lead', lead_sid)]
    # 成员：主理人日志里 team/member 的 member.id
    evs = load(lead_sid)
    for e in evs:
        if e.get('type') == 'team/member':
            m = (e.get('data') or {}).get('member') or {}
            if m.get('id') and m.get('name'):
                ids.append((m['name'], str(m['id'])))
    seen = set()
    for role, sid in ids:
        if sid in seen:
            continue
        seen.add(sid)
        s = summarize(sid)
        if not s:
            print('  %-9s 无日志' % role)
            continue
        age = datetime.datetime.now().timestamp() - s['mtime']
        kexi = sum(v for k, v in s['tools'].items() if k.startswith('kexi_'))
        non = sum(v for k, v in s['tools'].items() if not k.startswith('kexi_'))
        print('  %-9s ev=%-4d turn=%-2d step=%-3d kexi=%-3d other=%-3d 静默=%4.0fs %s'
              % (role, s['ev'], s['turn'], s['step'], kexi, non, age, ('ERR:' + s['err']) if s['err'] else ''))
    print('  --- 产物 ---')
    for a in ARTS:
        p = os.path.join(KOUT, a)
        if os.path.exists(p):
            t = datetime.datetime.fromtimestamp(os.path.getmtime(p)).strftime('%m-%d %H:%M')
            print('  %-32s %7.1f KB  %s' % (a, os.path.getsize(p) / 1024, t))
    try:
        with urllib.request.urlopen('http://127.0.0.1:47896/kexi-dashboard/activity', timeout=6) as r:
            d = json.loads(r.read().decode('utf-8'))
        s = [x for x in d.get('sessions', []) if x['id'] == lead_sid]
        if s:
            s = s[0]
            print('  --- 卡片 --- busy=%s nodes=%s eval=%s artifact=%s' % (
                s['busy'], s['stats']['nodes'],
                '有' if s.get('evaluation') else '无',
                (s.get('artifact') or {}).get('name', '无')))
    except Exception as e:
        print('  --- 卡片 --- 查询失败', e)


if __name__ == '__main__':
    main()





