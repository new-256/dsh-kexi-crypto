# tools/session-forensics — 会话取证工具

对**运行中的 DSH 会话**做离线取证，用来回答"团队现在到底在干什么 / 结论从哪来 / 有没有混用旧产物"。

这些脚本**不是插件的一部分**（不进 `package.json` 的 `files`，不随包分发），
只服务于"跟踪一个会话并复盘"这类开发调试工作。

## 前置：会话日志在哪里

DSH 把每个会话（主理人 + 每位成员各一个）写成一个 zstd 压缩的 JSONL：

```
%APPDATA%\DSH Desktop\dsh-home\sessions\--<工作目录转义>--\<session-id>\session.v4.jsonl.zstd
```

读取方式：

```python
import zstandard, json
with open(path, 'rb') as f:
    txt = zstandard.ZstdDecompressor().stream_reader(f).read().decode('utf-8', 'replace')
events = [json.loads(l) for l in txt.splitlines() if l.strip().startswith('{')]
```

每个事件带 `type`（`turn/start`、`tool/call`、`tool/result`、`team/member`、
`session/title`、`step/start`、`turn/end` …）和 `data`。

**成员会话与主理人会话是分开的**——`data.member.id` 既是 `team/member` 里的
成员 id，也是该成员自己的 session id，这是跨会话查成员活跃度的依据。

## 三个工具

### 1. `track.py` — 会话全景快照

按**标题**自动定位主理人会话，并把它派生的成员会话一起列出来。

```powershell
C:\Python313\python.exe tools\session-forensics\track.py "大盘"
```

输出：每个会话的事件数/回合/步数/kexi 调用数/静默时长，最后一行的产物清单，
以及 `http://127.0.0.1:<port>/kexi-dashboard/activity` 的卡片视角（busy / 节点数 /
评估结论 / 挂载的产物）。

**注意端口会变**：DSH 重启后插件端口会重新分配（实测见过 52028 / 61478 / 47896）。
先探明再用：

```powershell
foreach ($p in 61478,47896,54799,52028) {
  try { Invoke-RestMethod "http://127.0.0.1:$p/kexi-dashboard/status" -TimeoutSec 3; Write-Host "插件端口: $p"; break } catch {}
}
```

### 2. `tail_calls.py` — 最近 N 个工具调用（含参数与结果）

```powershell
C:\Python313\python.exe tools\session-forensics\tail_calls.py <session-id> 10
```

排查"主理人为什么这么做"最直接的工具：能看到每次 `kexi_run` 的完整参数、
返回值、以及工具错误。

**关键陷阱**：`tool/call` 事件的 `data.arguments` 是 **JSON 字符串**，不是对象。
早期 host 直接当对象用，导致卡片上所有工具标签退化（"派活给 成员"、不显示币种）。

### 3. `cmp_fast_team.py` — 快答 vs 团队深研 逐字段 diff

```powershell
C:\Python313\python.exe tools\session-forensics\cmp_fast_team.py
```

拿工作区里已有的快答产物与团队产物做逐项比对，分 `identical` / `divergent` /
`team_advantage` / `fast_only` 四类。

> 重要结论（别再重复踩）：identical 项几乎全是 `shared_engine`——快答
> `import` 的就是团队用的同一批脚本，**逐位一致是同代码同数据的必然结果，
> 不是团队的功劳**。而且这个一致性**只在两侧读同一份 K 线快照时成立**；
> 跨时间混用产物就会出现"两个结论"。

## 只读原则

这些工具**只读**会话日志与工作区产物，不修改任何东西。
如果要改代码，改完后必须：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run-all-tests.ps1
powershell -ExecutionPolicy Bypass -File scripts\sync-install.ps1 -Pack
```
