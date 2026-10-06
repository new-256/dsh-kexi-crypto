#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打印某会话最近 N 个工具调用（参数 + 结果摘要）。"""
import zstandard, json, os, sys

BASE = r'C:\Users\lcl\AppData\Roaming\DSH Desktop\dsh-home\sessions\--C-Users-lcl-Desktop-K~6790~5DE5~4F5C~6587~4EF6~5939--'


def alltext(m):
    c = (m or {}).get('content') if isinstance(m, dict) else None
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return '\n'.join(b.get('text', '') for b in c if isinstance(b, dict))
    return ''


def main(sid, n=8):
    p = os.path.join(BASE, sid, 'session.v4.jsonl.zstd')
    with open(p, 'rb') as f:
        txt = zstandard.ZstdDecompressor().stream_reader(f).read().decode('utf-8', 'replace')
    evs = [json.loads(l) for l in txt.splitlines() if l.strip().startswith('{')]
    res = {}
    for e in evs:
        if e.get('type') == 'tool/result':
            m = (e.get('data') or {}).get('message') or {}
            res[m.get('toolCallId')] = (bool(m.get('isError')), alltext(m))
    calls = [e for e in evs if e.get('type') == 'tool/call']
    print('总工具调用 %d，显示最后 %d 个' % (len(calls), n))
    for e in calls[-n:]:
        d = e.get('data') or {}
        a = d.get('arguments')
        if isinstance(a, str):
            try:
                a = json.loads(a)
            except Exception:
                pass
        name = d.get('name')
        key = ''
        if isinstance(a, dict):
            key = a.get('command') or a.get('file_path') or json.dumps(a, ensure_ascii=False)
        print('\n>>> %s | %s' % (name, str(key)[:230].replace('\n', ' ')))
        err, body = res.get(d.get('callId'), (None, ''))
        head = (body or '').strip().replace('\n', ' | ')[:300]
        if head:
            print('    %s %s' % ('ERR' if err else '   ', head))


if __name__ == '__main__':
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 8)
