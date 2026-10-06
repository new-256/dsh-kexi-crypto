// dsh-kexi-crypto — browser half（家级 client 插件）。
//
// 三个 UI 面（需求 1/2/3）：
//   1. 标题栏研判灯（原有）+ 【实时进度弹窗】：轮询 /kexi-dashboard/activity，
//      把 host 订阅 session/event 得到的节点流渲染成时间线——每一步在干什么、
//      谁在干活（子智能体/本地 CLI）、拿到了什么结果，都可视化。解决"长时间不知道
//      插件在干什么"的痛点。任务跑起来自动弹出（可在设置里关）。
//   2. CLI 节点：agy/codebuddy/mimo 的工具调用以 🤖 单独成节点，显示"哪个 CLI
//      在跑、跑了多久、结论是什么"，让本地 CLI 的参与看得见。
//   3. 设置面板：注册进 settings.plugin.item 槽（与 dsh-pet / dream-skin 同款），
//      编辑 host 落盘的 kexi-settings.json（弹窗开关、CLI 优先级、筛选阈值、
//      仓位预算参数），POST /kexi-dashboard/settings 持久化。
//
// 实现纪律（对齐 agy-first / dsh-pet 实战）：
//   - 轮询只用浏览器原生 setInterval/clearInterval，组件内不引用 Cordis ctx；
//   - apply 用 ctx.inject(['slots'], scope => ...) 等 slots 就绪后再注册；
//   - 设置卡通过 ctx.get('settingsScope'|'webUiSettings') 防御式获取（缺失只降级
//     设置卡，不影响灯与弹窗）；
//   - 【关键契约】__ModuleLoader__.load 的模块 id 必须 === npm 包名
//     "dsh-kexi-crypto"；slot 注册 id 用 "kexi-dashboard-home"。

window.__ModuleLoader__.load({
  id: "dsh-kexi-crypto",
  factory: (require) => {
    var module = { exports: {} };
    var exports = module.exports;
    Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
    var react = require("react");

    const CSS = [
      // ── 灯 ──────────────────────────────────────────────────────────────
      ".kexi-ind{display:inline-flex;align-items:center;gap:6px;height:24px;padding:0 9px;border-radius:12px;border:1px solid var(--dsw-alias-border-l1);background:var(--dsw-alias-bg-layer-1);font-size:12px;line-height:1;color:var(--dsw-alias-label-secondary);white-space:nowrap;user-select:none}",
      ".kexi-ind:hover{border-color:var(--dsw-alias-border-l2);cursor:pointer}",
      ".kexi-dot{width:8px;height:8px;border-radius:50%;flex:0 0 auto;background:var(--dsw-alias-label-secondary)}",
      ".kexi-ind b{font-weight:600}",
      ".kexi-run .kexi-dot{background:var(--dsw-static-blue-500,#3b82f6);animation:kexi-pulse 1s ease-in-out infinite}",
      ".kexi-ok .kexi-dot{background:var(--dsw-static-green-500,#22c55e)}",
      ".kexi-fail .kexi-dot{background:var(--dsw-alias-state-error-primary)}",
      ".kexi-run{color:var(--dsw-static-blue-500,#3b82f6);border-color:var(--dsw-static-blue-500,#3b82f6)}",
      ".kexi-ok{color:var(--dsw-static-green-500,#22c55e);border-color:var(--dsw-static-green-500,#22c55e)}",
      "@keyframes kexi-pulse{0%{opacity:1;transform:scale(1)}50%{opacity:.35;transform:scale(.72)}100%{opacity:1;transform:scale(1)}}",
      // ── 弹窗 ────────────────────────────────────────────────────────────
      ".kexi-pop-overlay{position:fixed;inset:0;background:rgba(0,0,0,.32);z-index:10000;display:flex;align-items:center;justify-content:center}",
      ".kexi-pop-panel{width:880px;max-width:94vw;max-height:88vh;display:flex;flex-direction:column;background:var(--dsw-alias-bg-layer-1);border:1px solid var(--dsw-alias-border-l2);border-radius:12px;box-shadow:0 12px 40px rgba(0,0,0,.35);font-size:12px;line-height:1.5;color:var(--dsw-alias-label-primary);overflow:hidden}",
      ".kexi-pop-head{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:10px 14px;border-bottom:1px solid var(--dsw-alias-border-l1);font-weight:600}",
      ".kexi-pop-close{border:1px solid var(--dsw-alias-border-l1);background:transparent;color:var(--dsw-alias-label-secondary);border-radius:6px;width:22px;height:22px;line-height:1;font-size:13px;cursor:pointer;flex:0 0 auto}",
      ".kexi-pop-close:hover{color:var(--dsw-alias-label-primary);border-color:var(--dsw-alias-border-l2)}",
      ".kexi-pop-gear{border:1px solid var(--dsw-alias-border-l1);background:transparent;color:var(--dsw-alias-label-secondary);border-radius:6px;width:22px;height:22px;line-height:1;font-size:12px;cursor:pointer;flex:0 0 auto;padding:0;display:inline-flex;align-items:center;justify-content:center}",
      ".kexi-pop-gear:hover{color:var(--dsw-alias-label-primary);border-color:var(--dsw-alias-border-l2);background:rgba(59,130,246,.08)}",
      ".kexi-set-panel{width:560px}",
      ".kexi-set-body{padding:14px 18px}",
      ".kexi-team{margin-bottom:14px;padding:10px 12px;border:1px solid var(--dsw-alias-border-l1);border-radius:8px;background:rgba(59,130,246,.03)}",
      ".kexi-team-title{font-weight:600;font-size:12px;margin-bottom:8px;display:flex;align-items:center;justify-content:space-between}",
      ".kexi-team-state{font-size:10px;font-weight:400;color:var(--dsw-alias-label-secondary)}",
      ".kexi-team-state-on{color:var(--dsw-static-green-500,#22c55e)}",
      ".kexi-team-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}",
      // ── 成果标签 + 内嵌看板（需求：成果在页面直接可见） ──
      ".kexi-tabs{display:flex;gap:4px;padding:8px 14px 0;border-bottom:1px solid var(--dsw-alias-border-l1)}",
      ".kexi-tab{appearance:none;border:1px solid transparent;border-bottom:none;background:transparent;color:var(--dsw-alias-label-secondary);font-size:12px;padding:7px 12px;border-radius:8px 8px 0 0;cursor:pointer}",
      ".kexi-tab:hover{color:var(--dsw-alias-label-primary);background:var(--dsw-alias-bg-layer-1)}",
      ".kexi-tab-on{color:var(--dsw-alias-label-primary);background:var(--dsw-alias-bg-layer-1);border-color:var(--dsw-alias-border-l1);font-weight:600}",
      ".kexi-result-list{display:flex;flex-direction:column;gap:12px}",
      ".kexi-result-item{border:1px solid var(--dsw-alias-border-l1);border-radius:10px;overflow:hidden;background:#0d1017}",
      ".kexi-result-head{display:flex;align-items:center;justify-content:space-between;padding:8px 12px;background:var(--dsw-alias-bg-layer-1);font-size:12px;font-weight:600}",
      ".kexi-result-open{color:#58a6ff;font-weight:400;text-decoration:none}",
      ".kexi-result-open:hover{text-decoration:underline}",
      ".kexi-result-frame{display:block;width:100%;height:560px;border:0;background:#fff}",
      ".kexi-member{border:1px solid var(--dsw-alias-border-l1);border-radius:7px;padding:7px 8px;transition:border-color .2s,box-shadow .2s}",
      ".kexi-member-on{border-color:var(--dsw-static-blue-500,#3b82f6);box-shadow:0 0 0 1px rgba(59,130,246,.15)}",
      ".kexi-member-head{display:flex;align-items:center;gap:5px}",
      ".kexi-member-ava{width:20px;height:20px;border-radius:50%;background:linear-gradient(135deg,#3b82f6,#8b5cf6);color:#fff;font-size:10px;font-weight:600;display:inline-flex;align-items:center;justify-content:center;flex:0 0 auto}",
      ".kexi-member-name{font-weight:600;font-size:12px;flex:1}",
      ".kexi-member-dot{width:6px;height:6px;border-radius:50%;background:var(--dsw-alias-border-l2);flex:0 0 auto}",
      ".kexi-member-dot-on{background:var(--dsw-static-green-500,#22c55e);box-shadow:0 0 4px rgba(34,197,94,.5)}",
      ".kexi-member-role{font-size:10px;color:var(--dsw-alias-label-secondary);margin-top:4px;line-height:1.35}",
      ".kexi-member-state{font-size:10px;color:var(--dsw-alias-label-secondary);margin-top:3px}",
      ".kexi-member-on .kexi-member-state{color:var(--dsw-static-blue-500,#3b82f6);font-weight:600}",
      ".kexi-member-note{font-size:10px;color:var(--dsw-alias-label-secondary);margin-top:4px;line-height:1.4;word-break:break-all;max-height:52px;overflow:hidden}",
      ".kexi-member-on .kexi-member-note{color:var(--dsw-alias-label-primary)}",
      // ── 评估结论（v1.5.1：最终研判在卡片中直接可见） ──
      ".kexi-eval{margin-bottom:14px;padding:12px 14px;border:1px solid var(--dsw-alias-border-l1);border-radius:8px;background:rgba(34,197,94,.04);max-height:260px;overflow:auto}",
      ".kexi-eval-title{font-weight:600;font-size:12px;margin-bottom:8px;display:flex;align-items:center;justify-content:space-between}",
      // v1.5.4：轮询失败横幅（旧实现静默失败 → 卡片与"无活动"无法区分）
      ".kexi-err{margin:0 0 10px;padding:8px 10px;border:1px solid var(--dsw-alias-border-danger,#e5484d);"
        + "border-radius:8px;background:rgba(229,72,77,0.08);color:var(--dsw-alias-label-primary,#fff);"
        + "font-size:11px;line-height:1.6}",
      // CEX 凭据区
      ".kexi-set-sec{margin:16px 0 6px;padding-top:10px;border-top:1px solid var(--dsw-alias-border,#2a2a2a);"
        + "font-size:12px;font-weight:600;opacity:0.85}",
      ".kexi-set-msg{margin:6px 0 0;font-size:11px;opacity:0.85;color:var(--dsw-alias-label-secondary,#aaa)}",
      ".kexi-eval-text{font-size:12px;line-height:1.7;color:var(--dsw-alias-label-primary);white-space:pre-wrap;word-break:break-word}",
      ".kexi-member-cli{border-style:dashed;border-color:rgba(139,92,246,.45)}",
      ".kexi-member-cli.kexi-member-on{border-color:#a78bfa;box-shadow:0 0 0 1px rgba(139,92,246,.2)}",
      ".kexi-member-cli .kexi-member-ava{background:linear-gradient(135deg,#8b5cf6,#06b6d4)}",
      ".kexi-member-cli.kexi-member-on .kexi-member-state{color:#a78bfa}",
      ".kexi-pop-tabs{display:flex;gap:6px;padding:8px 14px 0}",
      ".kexi-tab{border:1px solid var(--dsw-alias-border-l1);background:transparent;color:var(--dsw-alias-label-secondary);border-radius:6px;padding:3px 10px;font-size:11px;cursor:pointer}",
      ".kexi-tab-on{color:var(--dsw-alias-label-primary);border-color:var(--dsw-static-blue-500,#3b82f6);background:rgba(59,130,246,.08)}",
      ".kexi-pop-body{overflow:auto;padding:12px 14px}",
      ".kexi-pop-empty{color:var(--dsw-alias-label-secondary);text-align:center;padding:18px 0}",
      ".kexi-pop-sess{margin-bottom:12px;padding-bottom:12px;border-bottom:1px dashed var(--dsw-alias-border-l1)}",
      ".kexi-pop-sess:last-child{border-bottom:none;margin-bottom:0;padding-bottom:0}",
      ".kexi-pop-head2{font-weight:600;margin-bottom:4px;display:flex;align-items:center;gap:6px;flex-wrap:wrap}",
      ".kexi-pop-mono{font-family:Consolas,Menlo,monospace;font-size:11px;color:var(--dsw-alias-label-secondary)}",
      ".kexi-pop-line{padding:1px 0}",
      ".kexi-badge{font-size:10px;padding:1px 6px;border-radius:8px;border:1px solid var(--dsw-alias-border-l1);color:var(--dsw-alias-label-secondary)}",
      ".kexi-badge-run{border-color:var(--dsw-static-blue-500,#3b82f6);color:var(--dsw-static-blue-500,#3b82f6)}",
      ".kexi-badge-ok{border-color:var(--dsw-static-green-500,#22c55e);color:var(--dsw-static-green-500,#22c55e)}",
      ".kexi-badge-err{border-color:var(--dsw-alias-state-error-primary);color:var(--dsw-alias-state-error-primary)}",
      // ── 时间线节点 ──────────────────────────────────────────────────────
      ".kexi-tl{margin:6px 0 0;padding:0;list-style:none;position:relative}",
      ".kexi-tl:before{content:'';position:absolute;left:9px;top:4px;bottom:4px;width:1px;background:var(--dsw-alias-border-l1)}",
      ".kexi-node{position:relative;padding:2px 0 2px 26px;margin:0}",
      ".kexi-node-ico{position:absolute;left:0;top:1px;width:19px;height:19px;display:flex;align-items:center;justify-content:center;border-radius:50%;background:var(--dsw-alias-bg-layer-1);border:1px solid var(--dsw-alias-border-l1);font-size:10px}",
      ".kexi-node-run .kexi-node-ico{border-color:var(--dsw-static-blue-500,#3b82f6);animation:kexi-pulse 1.2s ease-in-out infinite}",
      ".kexi-node-ok .kexi-node-ico{border-color:var(--dsw-static-green-500,#22c55e)}",
      ".kexi-node-err .kexi-node-ico{border-color:var(--dsw-alias-state-error-primary)}",
      ".kexi-node-label{display:block}",
      ".kexi-node-detail{color:var(--dsw-alias-label-secondary);font-size:11px;word-break:break-all}",
      ".kexi-node-res{color:var(--dsw-static-green-500,#22c55e);font-size:11px;word-break:break-all}",
      ".kexi-node-res-err{color:var(--dsw-alias-state-error-primary);font-size:11px;word-break:break-all}",
      ".kexi-node-meta{color:var(--dsw-alias-label-secondary);font-size:10px;opacity:.75}",
      ".kexi-progress{height:4px;border-radius:2px;background:var(--dsw-alias-border-l1);overflow:hidden;margin:6px 0 2px}",
      ".kexi-progress>i{display:block;height:100%;background:var(--dsw-static-blue-500,#3b82f6);transition:width .3s}",
      // ── 设置面板 ────────────────────────────────────────────────────────
      ".kexi-set{padding:4px 0;font-size:12px;color:var(--dsw-alias-label-primary)}",
      ".kexi-set-row{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:6px 0;border-bottom:1px solid var(--dsw-alias-border-l1)}",
      ".kexi-set-row:last-child{border-bottom:none}",
      ".kexi-set-lab{display:flex;flex-direction:column;gap:2px}",
      ".kexi-set-desc{color:var(--dsw-alias-label-secondary);font-size:11px}",
      ".kexi-set input[type=number],.kexi-set input[type=text]{width:110px;padding:3px 6px;border-radius:6px;border:1px solid var(--dsw-alias-border-l2);background:var(--dsw-alias-bg-layer-1);color:var(--dsw-alias-label-primary);font-size:12px}",
      ".kexi-set-actions{display:flex;align-items:center;gap:8px;padding-top:10px}",
      ".kexi-btn{border:1px solid var(--dsw-alias-border-l2);background:transparent;color:var(--dsw-alias-label-primary);border-radius:6px;padding:4px 12px;font-size:12px;cursor:pointer}",
      ".kexi-btn-pri{border-color:var(--dsw-static-blue-500,#3b82f6);color:var(--dsw-static-blue-500,#3b82f6)}",
      ".kexi-hint{color:var(--dsw-alias-label-secondary);font-size:11px}",
      ".kexi-chips{display:flex;gap:6px;flex-wrap:wrap}",
      ".kexi-chip{border:1px solid var(--dsw-alias-border-l1);border-radius:10px;padding:1px 8px;font-size:11px;cursor:pointer;color:var(--dsw-alias-label-secondary)}",
      ".kexi-chip-on{border-color:var(--dsw-static-blue-500,#3b82f6);color:var(--dsw-static-blue-500,#3b82f6)}",
      // ── 团队关系图（v1.6.2）───────────────────────────────────────────
      // 手绘 SVG（本文件无构建步骤、不能引 D3/ECharts），布局是确定性的：
      // 主理人固定在顶部中间，成员沿一条弧排在下面。不用力导向——那会让
      // 每次刷新节点乱跳，反而更难读。
      ".kexi-graph-wrap{margin:6px 0 2px;overflow-x:auto}",
      // min-width 600：窗口很窄时不要把图缩到看不清。
      // 实测 420px 视口下若不设下限，SVG 缩到 0.57 倍，8.5px 的字只剩 ~4.8px
      // ——几何上"不重叠"了，但人根本读不出来，等于白画。
      // 下限保证缩放比不低于 ~0.94；更窄就横向滚，图下面的文字清单永远可读。
      ".kexi-graph{width:100%;min-width:600px;height:auto;display:block;overflow:visible}",
      ".kexi-gnode{cursor:default}",
      // 忙碌成员的呼吸环：只给"正在干活"的节点加，避免满图都在闪
      ".kexi-gbusy .kexi-gring{animation:kexi-gpulse 1.4s ease-in-out infinite;transform-origin:center;transform-box:fill-box}",
      "@keyframes kexi-gpulse{0%{opacity:.9;r:23}70%{opacity:0;r:31}100%{opacity:0;r:31}}",
      ".kexi-glabel{font-size:10px;fill:var(--dsw-alias-label-primary);text-anchor:middle;font-weight:600}",
      ".kexi-gact{font-size:8.5px;fill:var(--dsw-alias-label-secondary);text-anchor:middle}",
      ".kexi-gcat{font-size:8px;text-anchor:middle}",
      // catFrom=role（只是按角色推测）用虚线圈 + 降透明度，与"真看到在跑什么"区分开
      ".kexi-guess{opacity:.62}",
      ".kexi-gedge{fill:none;stroke-width:1.3;opacity:.42}",
      ".kexi-gedge-running{opacity:.95;stroke-dasharray:5 4;animation:kexi-gflow 1s linear infinite}",
      "@keyframes kexi-gflow{to{stroke-dashoffset:-18}}",
      ".kexi-gedge-report{stroke-dasharray:2 3}",
      ".kexi-glegend{display:flex;flex-wrap:wrap;gap:8px;margin-top:6px;font-size:10px;color:var(--dsw-alias-label-secondary)}",
      ".kexi-glegend i{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:4px;vertical-align:middle}",
      ".kexi-gempty{color:var(--dsw-alias-label-secondary);font-size:11px;padding:10px 0}",
      // ── 版本徽标（v1.6.4）─────────────────────────────────────────────
      ".kexi-ver{font:10px/1 ui-monospace,SFMono-Regular,Consolas,monospace;color:var(--dsw-alias-label-secondary);opacity:.75;border:1px solid var(--dsw-alias-border-l1);border-radius:4px;padding:3px 5px;cursor:help;user-select:none}"
    ].join("");
    if (typeof document !== "undefined" && document.querySelector("style[data-plugin-css=\"kexi-dashboard\"]") === null) {
      const tag = document.createElement("style");
      tag.setAttribute("data-plugin", "kexi-dashboard");
      tag.setAttribute("data-plugin-css", "kexi-dashboard");
      tag.textContent = CSS;
      document.head.appendChild(tag);
    }

    // ── 轮询工具 ──────────────────────────────────────────────────────────
    function usePoll(url, ms) {
      const st = react.useState(null);
      const v = st[0]; const setV = st[1];
      react.useEffect(function () {
        let alive = true; let t = null;
        const tick = function () {
          fetch(url, { cache: "no-store" })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (j) { if (alive) setV(j); })
            .catch(function () { /* 静默，保持上次 */ });
        };
        tick(); t = setInterval(tick, ms);
        return function () { alive = false; if (t !== null) clearInterval(t); };
      }, [url, ms]);
      return v;
    }

    function fmtDur(ms) {
      if (!ms || ms < 0) return "";
      if (ms < 1000) return ms + "ms";
      if (ms < 60000) return (ms / 1000).toFixed(1) + "s";
      return Math.floor(ms / 60000) + "m" + Math.round((ms % 60000) / 1000) + "s";
    }
    function fmtAgo(t) {
      const d = Date.now() - (t || 0);
      if (d < 5000) return "刚刚";
      if (d < 60000) return Math.floor(d / 1000) + "s 前";
      if (d < 3600000) return Math.floor(d / 60000) + "m 前";
      return Math.floor(d / 3600000) + "h 前";
    }
    function nodeCls(status) {
      if (status === "running") return " kexi-node-run";
      if (status === "error") return " kexi-node-err";
      if (status === "ok") return " kexi-node-ok";
      return "";
    }

    // ── 节点时间线 ────────────────────────────────────────────────────────
    function Timeline(props) {
      const nodes = props.nodes || [];
      if (!nodes.length) return react.createElement("div", { className: "kexi-pop-empty" }, "暂无活动节点（等待任务开始）");
      return react.createElement("ul", { className: "kexi-tl" },
        nodes.map(function (n, i) {
          return react.createElement("li", { key: n.seq !== undefined ? n.seq : i, className: "kexi-node" + nodeCls(n.status) },
            react.createElement("span", { className: "kexi-node-ico" }, n.icon || "•"),
            react.createElement("span", { className: "kexi-node-label" }, n.label || n.kind),
            n.detail ? react.createElement("div", { className: "kexi-node-detail" }, n.detail) : null,
            n.result ? react.createElement("div", { className: n.status === "error" ? "kexi-node-res-err" : "kexi-node-res" }, "↳ " + n.result) : null,
            react.createElement("div", { className: "kexi-node-meta" },
              fmtAgo(n.t) + (n.durationMs ? " · 耗时 " + fmtDur(n.durationMs) : "") + (n.tool ? " · " + n.tool : "")));
        }));
    }

    // ── 团队名册（v1.5.9 起六人团队：主理人 + 五位成员，证伪为红队） ─────────
    const TEAM = [
      { key: "shuma", cn: "数脉", tool: "market_data_specialist", role: "行情取数 · 数据质检" },
      { key: "zhibei", cn: "指北", tool: "technical_indicators", role: "指标计算 · 形态量价" },
      { key: "wangchao", cn: "望潮", tool: "trend_forecaster", role: "三情景概率 · 证伪条件" },
      { key: "shouzhuo", cn: "守拙", tool: "risk_assessor", role: "风险定级 · 可执行提示" },
      { key: "zhengfu", cn: "证伪", tool: "falsification_challenger", role: "唱反调 · 攻击结论前提", red: true }
    ];

    /** 本地 CLI 元数据（仅用于把启用的 CLI 作为团队成员展示）。 */
    const CLI_TEAM = [
      { key: "agy", cn: "agy", role: "Gemini/Antigravity 本地 CLI" },
      { key: "codebuddy", cn: "codebuddy", role: "腾讯 CodeBuddy 本地 CLI" },
      { key: "mimo", cn: "mimo", role: "小米 MiMo 本地 CLI" }
    ];

    /** 从忙碌会话的节点里判断哪位成员正在干活（节点标签含成员名 / subagent）。
     *  v1.5.1：优先用 host 团队名册（team/member 事件权威状态），节点标签只作兜底。 */
    function activeMembers(activity) {
      const set = {};
      const sessions = (activity && Array.isArray(activity.sessions)) ? activity.sessions : [];
      sessions.forEach(function (s) {
        // ① host 团队名册（v1.5.1）：member.phase/busy 直接点亮
        if (s.team && s.team.members) {
          s.team.members.forEach(function (m) {
            if (m.busy || m.phase === "provisioning") set[m.name] = m;
          });
        }
        // ② 节点标签兜底（subagent/团队消息节点）
        if (!s.busy) return;
        (s.nodes || []).forEach(function (n) {
          const lab = String(n.label || "") + " " + String(n.tool || "") + " " + String(n.member || "");
          TEAM.forEach(function (m) { if (lab.indexOf(m.cn) >= 0 || lab.indexOf(m.tool) >= 0 || lab.indexOf(m.key) >= 0) set[m.key] = m; });
          // CLI 节点工具形如 agy_run / codebuddy_run / mimo_run
          CLI_TEAM.forEach(function (c) { if (new RegExp("(^|\\b)" + c.key + "_").test(String(n.tool || ""))) set[c.key] = c; });
        });
      });
      return set;
    }

    /** 成员最近动向文本（v1.5.1：卡片上直接看到"正在干什么"）。 */
    function memberNote(activity, key) {
      const sessions = (activity && Array.isArray(activity.sessions)) ? activity.sessions : [];
      for (let i = 0; i < sessions.length; i++) {
        const s = sessions[i];
        if (!s.team || !s.team.members) continue;
        for (let j = 0; j < s.team.members.length; j++) {
          const m = s.team.members[j];
          if (m.name === key && m.lastNote) return m.lastNote;
        }
      }
      return null;
    }

    /** 主理人最终评估结论（v1.5.1：评估结果在卡片中展示）。 */
    function evaluationOf(activity) {
      const sessions = (activity && Array.isArray(activity.sessions)) ? activity.sessions : [];
      let best = null;
      sessions.forEach(function (s) {
        if (s.evaluation && (!best || (s.evaluation.at || 0) > (best.at || 0))) best = s.evaluation;
      });
      return best;
    }

    // ── 团队关系图（v1.6.2）──────────────────────────────────────────────
    // 回答两个问题：**谁加入了工作** / **每个人在干什么（哪一类活）**。
    // 手绘 SVG，无外部依赖（本文件无构建步骤，引不了 D3/ECharts）。
    // 布局确定性：主理人固定顶部中央，成员沿弧线排在下方——不用力导向，
    // 否则每次轮询节点都会乱跳，反而更难读。
    const G_CAT = {
      collect:     { c: "#06b6d4", label: "数据搜集" },
      analyze:     { c: "#3b82f6", label: "数据分析" },
      risk:        { c: "#f59e0b", label: "风控执行" },
      falsify:     { c: "#f43f5e", label: "质疑复核" },
      orchestrate: { c: "#8b5cf6", label: "编排派活" },
      other:       { c: "#94a3b8", label: "其他工作" },
    };
    function gCat(key) { return G_CAT[key] || G_CAT.other; }

    function TeamGraph(props) {
      const sess = props.session;
      const g = sess && sess.graph;
      if (!g || !g.nodes || !g.nodes.length) {
        return react.createElement("div", { className: "kexi-gempty" },
          "本会话尚未组建团队（调用 kexi_ensure_team 或跑一次 kexi_run 后，关系图会出现在这里）");
      }
      const lead = g.nodes.filter(function (n) { return n.isLead; })[0];
      const members = g.nodes.filter(function (n) { return !n.isLead; });
      // ⚠ viewBox 宽度要贴近弹窗实际宽度（~640），不能图省事写 360。
      // SVG 会等比缩放到容器宽：viewBox 360 配 870px 容器 = 放大 2.4 倍，
      // 设计时 8.5px 的字实际渲染成 ~20px，相邻节点的活动文字直接横向重叠、
      // 还会顺着 overflow:visible 溢到图例区被裁掉（真浏览器截图才看得到）。
      // 宽度贴近实际 → 缩放比接近 1，字号即所见。
      const W = 640, H = 300;
      const cx = W / 2, leadY = 34;
      const n = Math.max(members.length, 1);
      // 每列可用宽度：文字换行宽度必须 ≤ 列宽，否则照样撞车
      const colW = Math.max(96, (W - 80) / n);
      const spanX = n === 1 ? 0 : colW * (n - 1);
      const memberY = 180;
      const pos = {};
      if (lead) pos[lead.id] = { x: cx, y: leadY };
      members.forEach(function (m, i) {
        const t = n === 1 ? 0.5 : i / (n - 1);
        pos[m.id] = { x: cx - spanX / 2 + spanX * t, y: memberY };
      });
      // 按列宽把活动文本折成 ≤2 行（而不是硬截 18 字——那样仍会溢出列宽）
      const wrapAct = function (txt, maxChars) {
        const s = String(txt || "");
        if (s.length <= maxChars) return [s];
        return [s.slice(0, maxChars), s.slice(maxChars, maxChars * 2)];
      };

      const edgeEls = (g.edges || []).map(function (e, i) {
        const a = pos[e.from], b = pos[e.to];
        // 两端都在图里才画；否则会出现指向虚空的线（比不画更让人困惑）
        if (!a || !b) return null;
        const running = e.status === "running";
        const stroke = e.rel === "report" ? "#22c55e" : gCat((g.nodes.filter(function (x) { return x.id === e.to; })[0] || {}).cat).c;
        // 二次贝塞尔：从 a 垂直下垂到 b 的方向，中点下沉，避免直线穿过节点
        const my = (a.y + b.y) / 2 + 16;
        const d = "M" + a.x + " " + (a.y + 21) + " Q" + a.x + " " + my + " " + ((a.x + b.x) / 2) + " " + my
          + " T" + b.x + " " + (b.y - 22);
        return react.createElement("path", {
          key: "e" + i, d: d,
          className: "kexi-gedge" + (running ? " kexi-gedge-running" : "")
            + (e.rel === "report" ? " kexi-gedge-report" : ""),
          stroke: stroke,
        });
      });

      const nodeEl = function (nd) {
        const p = pos[nd.id]; if (!p) return null;
        const cat = gCat(nd.cat);
        const guess = nd.catFrom === "role";   // 只是按角色推测 → 弱化显示
        const failed = nd.state === "failed";
        const kids = [
          // 忙碌呼吸环（在 .kexi-gbusy 容器上做动画）
          nd.busy ? react.createElement("circle", {
            key: "ring", className: "kexi-gring", cx: p.x, cy: p.y, r: 23,
            fill: "none", stroke: cat.c, strokeWidth: 1.5,
          }) : null,
          react.createElement("circle", {
            // ⚠ SVG 属性必须用 **camelCase**。写 `stroke-width` / `fill-opacity` /
            // `stroke-dasharray` 时 React 会警告并**直接丢弃**——属性不生效，
            // 描边宽度、透明填充、虚线圈（= 区分"实测/推测"）会一起消失。
            // 这类 bug 只在真浏览器里渲染才暴露，静态断言看不出来。
            key: "disc", cx: p.x, cy: p.y, r: 20,
            fill: cat.c, fillOpacity: nd.busy ? 0.9 : 0.35,
            stroke: failed ? "#ef4444" : cat.c, strokeWidth: failed ? 2 : 1.2,
            strokeDasharray: guess ? "3 2" : "none",
          }),
          // 成员首字（主理人两个字会挤，只取一字）
          react.createElement("text", {
            key: "ch", x: p.x, y: p.y + 4, className: "kexi-glabel",
            fill: nd.busy ? "#fff" : "inherit", style: { fontSize: "12px" },
          }, nd.cn.slice(0, 1)),
          react.createElement("text", { key: "name", x: p.x, y: p.y + 34, className: "kexi-glabel" }, nd.cn),
          react.createElement("text", {
            // key 必须与上面那颗 disc 区分开——重名会让 React 丢节点
            key: "cat", x: p.x, y: p.y + 47, className: "kexi-gcat", fill: cat.c,
          }, nd.catLabel + (guess ? "（推测）" : "")),
        ];
        if (nd.activity) {
          // 按列宽折行，绝不让相邻节点的活动文字横向撞在一起
          const per = Math.max(5, Math.floor(colW / 8));
          wrapAct(nd.activity, per).forEach(function (line, li) {
            if (!line) return;
            kids.push(react.createElement("text", {
              key: "act" + li, x: p.x, y: p.y + 62 + li * 11, className: "kexi-gact",
            }, line));
          });
        }
        return react.createElement("g", {
          key: nd.id,
          className: "kexi-gnode" + (nd.busy ? " kexi-gbusy" : "") + (guess ? " kexi-guess" : ""),
        }, kids);
      };

      const used = {};
      g.nodes.forEach(function (nd) { used[nd.cat] = true; });
      const legend = Object.keys(G_CAT).filter(function (k) { return used[k]; });

      return react.createElement("div", { className: "kexi-graph-wrap" },
        react.createElement("svg", {
          className: "kexi-graph", viewBox: "0 0 " + W + " " + H,
          preserveAspectRatio: "xMidYMid meet", role: "img",
          "aria-label": "团队关系图：谁加入了工作、各自负责哪一类活",
        }, edgeEls, g.nodes.map(nodeEl)),
        react.createElement("div", { className: "kexi-glegend" },
          legend.map(function (k) {
            return react.createElement("span", { key: k },
              react.createElement("i", { style: { background: G_CAT[k].c } }), G_CAT[k].label);
          }),
          react.createElement("span", { style: { opacity: ".75" } },
            "虚线圈 = 未在跑脚本，分类按角色推测")),
        // 图下面给一份文字表：SVG 里的文字很小，且读屏/复制都不友好
        react.createElement("div", { className: "kexi-glegend", style: { marginTop: "4px" } },
          g.nodes.map(function (nd) {
            return react.createElement("span", { key: "r" + nd.id },
              (nd.busy ? "⟳ " : "· ") + nd.cn + "：" + nd.catLabel
              + (nd.activity ? " — " + String(nd.activity).slice(0, 24) : ""));
          })));
    }

    function TeamRoster(props) {
      const active = activeMembers(props.activity);
      const anyActive = Object.keys(active).length > 0;
      // 仅当「把 CLI 纳入团队成员」开启时，追加启用的 CLI 卡片（外部成员）。
      const aset = (props.activity && props.activity.settings) || {};
      const cliOn = aset.cliAsMembers !== false;
      const en = aset.cliEnabled || {};
      const cliMembers = cliOn
        ? CLI_TEAM.filter(function (c) { return en[c.key] !== false; })
        : [];
      const memberCard = function (m, on, external, note) {
        return react.createElement("div", {
            key: m.key,
            className: "kexi-member" + (on ? " kexi-member-on" : "") + (external ? " kexi-member-cli" : ""),
            title: m.tool || m.role
          },
          react.createElement("div", { className: "kexi-member-head" },
            react.createElement("span", { className: "kexi-member-ava" }, external ? "🤖" : m.cn.slice(0, 1)),
            react.createElement("span", { className: "kexi-member-name" }, m.cn),
            react.createElement("span", { className: "kexi-member-dot" + (on ? " kexi-member-dot-on" : "") })),
          react.createElement("div", { className: "kexi-member-role" }, m.role),
          react.createElement("div", { className: "kexi-member-state" },
            external ? (on ? "⟳ 复核中" : "· 可调用") : (on ? "⟳ 工作中" : "· 待命")),
          note ? react.createElement("div", { className: "kexi-member-note" }, "▸ " + note) : null);
      };
      return react.createElement("div", { className: "kexi-team" },
        react.createElement("div", { className: "kexi-team-title" },
          "🧑‍🤝‍🧑 研判团成员（主理人 K析 + 五位专员" + (cliMembers.length ? " + 本地 CLI" : "") + "）",
          react.createElement("span", { className: "kexi-team-state" + (anyActive ? " kexi-team-state-on" : "") },
            anyActive ? Object.keys(active).length + " 位在干活" : "待命")),
        react.createElement("div", { className: "kexi-team-grid" },
          TEAM.map(function (m) { return memberCard(m, !!active[m.key], false, memberNote(props.activity, m.key)); }),
          cliMembers.map(function (c) { return memberCard(c, !!active[c.key], true, memberNote(props.activity, c.key)); })));
    }

    // ── 弹窗（实时进度） ──────────────────────────────────────────────────
    function ProgressPopup(props) {
      const act = props.activity;
      const sessions = (act && Array.isArray(act.sessions)) ? act.sessions : [];
      const busy = sessions.filter(function (s) { return s.busy; });
      const shown = busy.length ? busy.concat(sessions.filter(function (s) { return !s.busy; })) : sessions;
      // 成果（已产出离线看板的会话，最近的在前）
      const artifacts = shown.map(function (s) { return s.artifact; }).filter(Boolean);
      // 主理人最终评估结论（v1.5.1：评估结果在卡片中展示，不只藏在聊天记录里）
      const evaluation = evaluationOf(act);
      const tabt = react.useState(artifacts.length ? "result" : "progress");
      const tab = tabt[0]; const setTab = tabt[1];
      const rows = shown.length
        ? shown.map(function (s, i) {
            const cur = s.current;
            return react.createElement("div", { key: s.id || i, className: "kexi-pop-sess" },
              react.createElement("div", { className: "kexi-pop-head2" },
                react.createElement("span", null, s.busy ? "⟳ " : "· ", s.name || s.cwd || s.id),
                s.busy ? react.createElement("span", { className: "kexi-badge kexi-badge-run" }, "进行中") : null,
                s.stats && s.stats.cli ? react.createElement("span", { className: "kexi-badge" }, "CLI×" + s.stats.cli) : null,
                s.stats ? react.createElement("span", { className: "kexi-badge" }, "工具 " + s.stats.tools) : null,
                react.createElement("span", { className: "kexi-pop-mono" }, fmtAgo(s.updatedAt))),
              cur ? react.createElement("div", { className: "kexi-pop-line" }, "当前：" + (cur.icon || "") + " " + cur.label) : null,
              react.createElement(Timeline, { nodes: (s.nodes || []).slice().reverse() }));
          })
        : react.createElement("div", { className: "kexi-pop-empty" },
            "本会话暂无研判活动。研判链路（kexi_run / kexi_cex / 团队派活）一启动就会显示在这里；"
            + "纯聊天与普通工具调用**不计入**（v1.6.1 起只展示与研判相关的内容）");

      // v1.5.4：轮询失败横幅。旧实现失败时静默 → 卡片与"无活动"完全无法区分。
      const errBanner = props.feedErr
        ? react.createElement("div", { className: "kexi-err" },
            "⚠ 活动接口不可用：" + (props.feedErr.code ? "HTTP " + props.feedErr.code : "网络错误")
            + (props.feedErr.msg && !props.feedErr.code ? "（" + props.feedErr.msg + "）" : "")
            + " —— 卡片数据未能刷新（非" + "「暂无活动" + "」）。")
        : null;

      // 成果视图：每个已产出看板一块，内嵌 iframe 直接可见，可新标签打开。
      const resultBody = artifacts.length
        ? react.createElement("div", { className: "kexi-result-list" },
            artifacts.map(function (a, i) {
              return react.createElement("div", { key: a.id || i, className: "kexi-result-item" },
                react.createElement("div", { className: "kexi-result-head" },
                  react.createElement("span", null, "📊 " + a.name),
                  react.createElement("a", { href: a.url, target: "_blank", rel: "noreferrer", className: "kexi-result-open", title: "在新标签打开看板" }, "新标签打开 ↗")),
                react.createElement("iframe", { className: "kexi-result-frame", src: a.url, title: a.name, loading: "lazy" }));
            }))
        : react.createElement("div", { className: "kexi-pop-empty" }, "暂无最终成果；研判跑完后离线看板会显示在这里");

      // 评估结论视图（v1.5.1）：主理人最终研判文本，直接在卡片中可读。
      const evalBody = evaluation
        ? react.createElement("div", { className: "kexi-eval" },
            react.createElement("div", { className: "kexi-eval-title" },
              "📝 最终评估结论",
              react.createElement("span", { className: "kexi-pop-mono" }, fmtAgo(evaluation.at))),
            react.createElement("div", { className: "kexi-eval-text" }, evaluation.text),
            // v1.6.4：不能无条件承诺"完整看板在最终成果"。
            // 实测 2026-09-30：结论被 clipStr 截断在 1200 字（第 4 点「撤回条件」
            // 断在半句），而那一轮**看板没登记成功**，「最终成果」标签是空的——
            // 于是"完整内容在那边"变成一句落空的指引，用户两头都拿不到完整结论。
            // 看板存在才提；不存在就直说已截断、去对话里看。
            artifacts.length
              ? react.createElement("div", { className: "kexi-hint", style: { marginTop: "8px" } },
                  "以上为主理人汇编的研判文本"
                  + (String(evaluation.text || "").trim().endsWith("…") ? "（较长，已截断）" : "")
                  + "；完整看板在「最终成果」标签。")
              : react.createElement("div", { className: "kexi-hint", style: { marginTop: "8px" } },
                  "本轮未产出离线看板，"
                  + (String(evaluation.text || "").trim().endsWith("…")
                    ? "上方结论较长已被截断，完整内容请看对话正文。"
                    : "以上即完整结论。")))
        : react.createElement("div", { className: "kexi-pop-empty" }, "暂无评估结论；主理人完成汇编后会显示在这里");

      // 关系图视图（v1.6.2）：谁加入了工作 / 各自在干哪一类活
      // ⚠ 成员子会话也要排除：他们跑 kexi_run 会通过服务端的 kexi 过滤，
      // 于是并排冒出 5 个「尚未组建团队」的成员会话——纯噪音，
      // 而且它们的工作已经作为节点画在主会话的图里了。
      const graphSessions = shown.filter(function (s) { return !s.isMemberSession; });
      const graphBody = graphSessions.length
        ? react.createElement("div", null, graphSessions.map(function (s, i) {
            return react.createElement("div", { key: "g" + (s.id || i), className: "kexi-pop-sess" },
              react.createElement("div", { className: "kexi-pop-head2" },
                react.createElement("span", null, s.busy ? "⟳ " : "· ", s.name || s.cwd || s.id),
                react.createElement("span", { className: "kexi-pop-mono" }, fmtAgo(s.updatedAt))),
              react.createElement(TeamGraph, { session: s }));
          }))
        : react.createElement("div", { className: "kexi-pop-empty" },
            shown.length
              ? "本会话的研判活动全部来自团队成员，成员工作已并入主会话的关系图。"
              : "暂无会话");

      const tabBtn = function (key, label, n) {
        return react.createElement("button", {
          className: "kexi-tab" + (tab === key ? " kexi-tab-on" : ""),
          onClick: function () { setTab(key); }
        }, label + (n ? " " + n : ""));
      };
      return react.createElement("div", { className: "kexi-pop-overlay", onClick: props.onClose },
        react.createElement("div", { className: "kexi-pop-panel", onClick: function (e) { e.stopPropagation(); } },
          react.createElement("div", { className: "kexi-pop-head" },
            react.createElement("span", null, "K析研判" + (act && act.busySessions ? " · " + act.busySessions + " 个会话在跑" : "")),
            react.createElement("span", { style: { display: "inline-flex", alignItems: "center", gap: "6px" } },
              // v1.6.4：版本号印在标题栏右侧。
              // 为什么非印不可：host 插件代码是**进程启动时**加载的，刷新浏览器
              // 不会重新加载——于是会出现"标签页是新的（客户端文件从磁盘读）、
              // 但服务端行为是旧的"这种界面看着正常、功能却是旧版的局面。
              // 这次排查就撞上了：v1.6.1~1.6.3 全部没生效，用户看到的关系图
              // 标签页在，但服务端从不返回 graph。印上版本号，看到什么就是什么。
              // ⚠ 版本号只能取自 act.version（= /activity 响应）。ProgressPopup
              //   拿不到 /status 的 s（Indicator 里的局部变量），所以服务端
              //   两个端点都带了 version 字段，这里只认 activity 那份。
              react.createElement("span", {
                className: "kexi-ver",
                title: "当前运行的插件版本（host 进程启动时加载；改代码后需重启 DSH 才会变）",
              }, "v" + ((act && act.version) || "?")),
              react.createElement("button", { className: "kexi-pop-gear", title: "打开 K析研判团设置（CLI 开关 / 筛选 / 风控）", onClick: props.onSettings }, "⚙"),
              react.createElement("button", { className: "kexi-pop-close", title: "关闭 (Esc)", onClick: props.onClose }, "✕"))),
          react.createElement("div", { className: "kexi-tabs" },
            tabBtn("result", "📊 最终成果", artifacts.length || ""),
            tabBtn("graph", "🕸 关系图", ""),
            tabBtn("progress", "⚙ 实时进度", ""),
            evaluation ? tabBtn("eval", "📝 评估结论", "") : null),
          react.createElement("div", { className: "kexi-pop-body" },
            errBanner,
            tab === "result" ? resultBody
              : tab === "eval" ? evalBody
              : tab === "graph" ? graphBody
              : [react.createElement(TeamRoster, { key: "roster", activity: act }), rows])));
    }

    // ── 设置模态（插件内部直达，不依赖外部设置页） ───────────────────────
    function SettingsPopup(props) {
      return react.createElement("div", { className: "kexi-pop-overlay", onClick: props.onClose },
        react.createElement("div", { className: "kexi-pop-panel kexi-set-panel", onClick: function (e) { e.stopPropagation(); } },
          react.createElement("div", { className: "kexi-pop-head" },
            react.createElement("span", null, "⚙ K析研判团 · 设置"),
            react.createElement("button", { className: "kexi-pop-close", title: "关闭 (Esc)", onClick: props.onClose }, "✕")),
          react.createElement("div", { className: "kexi-pop-body kexi-set-body" },
            react.createElement(SettingsCard, null))));
    }

    // ── 原状态弹窗（研判灯详情） ──────────────────────────────────────────
    function StatusPopup(props) {
      const s = props.s;
      const rows = [];
      if (s && Array.isArray(s.sessions) && s.sessions.length) {
        s.sessions.forEach(function (x, i) {
          const badge = "[" + x.phase + (x.step ? " " + x.step : "") + "]";
          rows.push(react.createElement("div", { key: "s" + i, className: "kexi-pop-sess" },
            react.createElement("div", { className: "kexi-pop-head2" },
              (x.phase === "running" ? "⟳ " : x.phase === "ok" ? "✓ " : x.phase === "failed" ? "✗ " : "") + (x.name || x.cwd),
              " ", x.symbol ? react.createElement("span", null, x.symbol + " ") : null,
              react.createElement("span", { className: "kexi-pop-mono" }, badge)),
            react.createElement("div", { className: "kexi-pop-mono" }, x.cwd),
            (x.step && x.stepIndex && x.stepTotal)
              ? react.createElement("div", { className: "kexi-pop-line", style: { marginTop: "4px" } },
                  react.createElement("div", { className: "kexi-progress" },
                    react.createElement("i", { style: { width: (Math.round(100 * x.stepIndex / x.stepTotal)) + "%" } })))
              : null,
            x.lastStatus ? react.createElement("div", { className: "kexi-pop-mono", style: { marginTop: "4px" } }, "last=" + x.lastStatus) : null));
        });
      } else {
        rows.push(react.createElement("div", { key: "empty", className: "kexi-pop-empty" }, "暂无 K析研判活动"));
      }
      return react.createElement("div", { className: "kexi-pop-overlay", onClick: props.onClose },
        react.createElement("div", { className: "kexi-pop-panel", onClick: function (e) { e.stopPropagation(); } },
          react.createElement("div", { className: "kexi-pop-head" },
            react.createElement("span", null, "K析研判状态" + (s && s.state ? " · " + s.state : "") + (props.mode || "")),
            react.createElement("button", { className: "kexi-pop-close", title: "关闭 (Esc)", onClick: props.onClose }, "✕")),
          react.createElement("div", { className: "kexi-pop-body" }, rows)));
    }

    const PRESET_UNKNOWN = "\u0000unknown";
    function isKexiPreset(id) { return id === "kexi-crypto"; }
    function subscribeNoop() { return function () { }; }
    function presetOfSummary(sum) {
      if (!sum) return PRESET_UNKNOWN;
      if (sum.projectionValues && typeof sum.projectionValues.agentPreset === "string") return sum.projectionValues.agentPreset;
      if (typeof sum.agentPreset === "string") return sum.agentPreset;
      return PRESET_UNKNOWN;
    }
    function presetOfState(state, sessionId) {
      try {
        if (!state || !sessionId || !state.byId) return PRESET_UNKNOWN;
        return presetOfSummary(state.byId[sessionId]);
      } catch (e) { return PRESET_UNKNOWN; }
    }

    function pillClass(phase) {
      if (phase === "running") return " kexi-run";
      if (phase === "ok") return " kexi-ok";
      if (phase === "failed") return " kexi-fail";
      return "";
    }
    function pillText(s) {
      if (s.phase === "running") return "⟳ K析" + (s.symbol ? " " + s.symbol : "");
      if (s.phase === "ok") return "✓ K析" + (s.symbol ? " " + s.symbol : "");
      if (s.phase === "failed") return "✗ K析";
      return "K析";
    }
    function pillTitle(s) {
      const parts = ["session: " + (s.name || s.cwd)];
      if (s.step) parts.push(s.step);
      if (s.lastStatus) parts.push("last=" + s.lastStatus);
      return "K析研判 [" + s.phase + "] " + s.cwd + (parts.length ? "\n" + parts.join("\n") : "");
    }

    function Pill(props) {
      const s = props.s;
      return react.createElement("div", { className: "kexi-ind" + pillClass(s.phase), title: pillTitle(s), onClick: props.onClick },
        react.createElement("span", { className: "kexi-dot" }),
        react.createElement("span", null, react.createElement("b", null, pillText(s))));
    }

    // ── 主指示灯 + 双弹窗 ─────────────────────────────────────────────────
    function Indicator(props) {
      const p = props || {};
      const sessionId = (typeof p.sessionId === "string" && p.sessionId) || (typeof p.injectedSessionId === "string" && p.injectedSessionId) || undefined;
      const useSess = typeof p.useSessions === "function" ? p.useSessions : null;
      const sessionsSvc = p.sessionsSvc;
      const st = react.useState(null); const s = st[0]; const setS = st[1];
      const et = react.useState(null); const act = et[0]; const setE = et[1];
      // v1.5.4：轮询失败原因（供 UI 显示"接口 401 / 网络错误"，避免静默空白）
      const ft = react.useState(null); const feedErr = ft[0]; const setFeedErr = ft[1];
      const ot = react.useState(false); const openStat = ot[0]; const setOpenStat = ot[1];
      const pt = react.useState(false); const openProg = pt[0]; const setOpenProg = pt[1];
      const stt = react.useState(false); const openSet = stt[0]; const setOpenSet = stt[1];

      let myPreset;
      if (useSess) {
        myPreset = useSess(function (state) { return presetOfState(state, sessionId); });
      } else {
        myPreset = react.useSyncExternalStore(
          (sessionsSvc && sessionsSvc.list) ? sessionsSvc.list.subscribe : subscribeNoop,
          function () {
            try {
              if (!sessionsSvc || !sessionsSvc.list) return PRESET_UNKNOWN;
              return presetOfState(sessionsSvc.list.getSnapshot(), sessionId);
            } catch (e) { return PRESET_UNKNOWN; }
          });
      }
      const iAmKexi = isKexiPreset(myPreset) ? true : (myPreset === PRESET_UNKNOWN ? null : false);

      react.useEffect(function () {
        let alive = true; let t1 = null; let t2 = null;
        // v1.5.4：轮询失败不再静默吞掉。
        // 旧实现 `.then(r => r.ok ? r.json() : null)` + `.catch(() => {})`
        // 会让**任何非 2xx（含 401）与网络错误都表现为"卡片完全空白且零反馈"**——
        // 用户只能看到"没反应"，无法区分"没活动"与"接口挂了"。这正是长期排不掉的
        // 原因：服务端数据完全正常也照样显示不出来。
        // 现在把失败原因记进 feedErr，直接显示在状态灯与弹窗里。
        const noteErr = function (where, code, msg) {
          if (!alive) return;
          setFeedErr({ where: where, code: code || 0, msg: String(msg || "").slice(0, 120), at: Date.now() });
        };
        const noteOk = function () { if (alive) setFeedErr(null); };
        const tick1 = function () {
          fetch("/kexi-dashboard/status", { cache: "no-store", credentials: "include" })
            .then(function (r) {
              if (!r.ok) { noteErr("status", r.status, "HTTP " + r.status); return null; }
              return r.json();
            })
            .then(function (v) { if (alive && v) { setS(v); noteOk(); } })
            .catch(function (e) { noteErr("status", 0, e && e.message ? e.message : "network error"); });
        };
        // 有活动数据时才高频轮询 activity，空闲降为低频（省资源）
        // v1.6.1：带上当前 sessionId —— 卡片只显示**这个会话**的研判进度。
        // 取不到就退化为"只显示有 kexi 活动的会话"（服务端已按此兜底）。
        const tick2 = function () {
          fetch("/kexi-dashboard/activity" + (sessionId ? ("?sessionId=" + encodeURIComponent(sessionId)) : ""),
            { cache: "no-store", credentials: "include" })
            .then(function (r) {
              if (!r.ok) { noteErr("activity", r.status, "HTTP " + r.status); return null; }
              return r.json();
            })
            .then(function (v) {
              if (!alive || !v) return;
              // 弹窗不自动弹出：只更新活动数据，由用户点状态灯手动唤出。
              setE(v);
              noteOk();
            })
            .catch(function (e) { noteErr("activity", 0, e && e.message ? e.message : "network error"); });
        };
        tick1(); tick2();
        t1 = setInterval(tick1, 1400);
        t2 = setInterval(tick2, 1500);
        return function () { alive = false; if (t1) clearInterval(t1); if (t2) clearInterval(t2); };
        // v1.6.1：sessionId 进依赖——切换会话后必须重新拉对应会话的活动，
        // 否则会一直显示上一个会话残留的研判进度。
      }, [sessionId]);

      react.useEffect(function () {
        if (!openStat && !openProg && !openSet) return;
        const h = function (e) { if (e.key === "Escape") { setOpenStat(false); setOpenProg(false); setOpenSet(false); } };
        window.addEventListener("keydown", h);
        return function () { window.removeEventListener("keydown", h); };
      }, [openStat, openProg, openSet]);

      const hasData = s && Array.isArray(s.sessions) && s.sessions.length;
      const readyShow = iAmKexi === true || (iAmKexi === null && !!(s && s.presetActive));
      const actBusy = et && Number(et.busySessions) > 0;
      if (!hasData && !readyShow && !actBusy) return null;

      const showProg = function () { setOpenStat(false); setOpenSet(false); setOpenProg(true); };
      const showStat = function () { setOpenProg(false); setOpenSet(false); setOpenStat(true); };

      let light;
      if (actBusy) {
        // 有实时活动时优先显示活动数（更强的"正在干活"信号）
        light = react.createElement("div", { className: "kexi-ind kexi-run", title: "K析研判进行中 — 点击查看实时进度节点", onClick: showProg },
          react.createElement("span", { className: "kexi-dot" }),
          react.createElement("span", null, react.createElement("b", null, "⟳ K析 " + et.busySessions)));
      } else if (hasData) {
        light = react.createElement("div", { style: { display: "inline-flex", alignItems: "center", gap: "6px" } },
          s.sessions.map(function (x, i) { return react.createElement(Pill, { key: x.cwd || ("s" + i), s: x, onClick: showStat }); }));
      } else {
        const state = s ? s.state : "idle";
        let text = "K析 就绪";
        if (state === "running") text = "K析 研判中";
        else if (state === "failed") text = "K析 失败";
        light = react.createElement("div", { className: "kexi-ind" + pillClass(state === "running" ? "running" : state === "ok" ? "ok" : state === "failed" ? "failed" : "idle"), title: "K析研判 [" + state + "]", onClick: showProg },
          react.createElement("span", { className: "kexi-dot" }), react.createElement("span", null, text));
      }

      const modeText = iAmKexi === true ? " · 本会话 K析研判团" : (iAmKexi === false ? " · 普通模式" : (s && s.presetActive ? " · K析（其他会话）" : ""));
      const closeProg = function () { setOpenProg(false); };
      return react.createElement(react.Fragment, null, light,
        openStat ? react.createElement(StatusPopup, { s: s, mode: modeText, onClose: function () { setOpenStat(false); } }) : null,
        openProg ? react.createElement(ProgressPopup, {
          // v1.5.4 关键修复：这里原来传的是 useState 元组 `et`（[值,setter]），
          // 于是弹窗内 props.activity.sessions 恒为 undefined → 永远显示
          // 「暂无 K析研判活动」。服务端 /activity 数据完全正常也照样空白，
          // 这就是"卡片不工作"长期无解的真凶（不是鉴权、不是数据、不是字段名）。
          activity: act,
          feedErr: feedErr,
          onClose: closeProg,
          onSettings: function () { setOpenProg(false); setOpenSet(true); },
        }) : null,
        openSet ? react.createElement(SettingsPopup, { onClose: function () { setOpenSet(false); } }) : null);
    }

    // ── 设置面板卡（需求 3） ──────────────────────────────────────────────
    const KEXI_SETTINGS_NS = "kexiDashboard";

    function SettingsCard() {
      const st = react.useState(null); const cfg = st[0]; const setCfg = st[1];
      const s2 = react.useState("idle"); const status = s2[0]; const setStatus = s2[1];
      const s3 = react.useState(""); const msg = s3[0]; const setMsg = s3[1];

      // ── CEX API 凭据 + 实盘自主级别（v1.6.0）─────────────────────────
      // ⚠ 必须放在**所有早退 return 之前**。Rules of Hooks：hook 调用顺序
      //     在两次渲染之间必须一致。这些 hook 曾被放在下面两行早退之后：
      //     if (status === "unavailable") return ...
      //     if (!cfg) return ...
      // 于是首次渲染 cfg=null → 早退 → 一个 hook 都不调；fetch 回来后二次
      //     渲染 → 调了 5 个 → React 抛 "Rendered more hooks than during the
      //     previous render" → **整棵组件树卸载，状态灯一起消失**。
      //     现场症状：点「设置」→ 弹窗关闭 → 状态灯入口彻底不见了。
      // 安全要点：输入框 type=password；GET 只回传"是否已配置 + 掩码尾号"，
      //     页面永远拿不到明文。留空提交 = 不改动（后端 PATCH 语义），
      //     避免"点一下保存"就把已存的密钥抹掉。
      //
      // v1.6.0 的三处实质变化（都源于"要能读持仓 + 能实盘"这个需求）：
      //   ① 自主级别独立成段、可选四档——它是**唯一的实盘开关**，
      //      写进 autonomy.json 供 Python 侧只读，模型无法翻转。
      //   ② 交易所可**启用/停用**，且默认全列出来——"接了哪几家"由用户配置决定，
      //      而不是代码写死一张固定表。
      //   ③ 支持 label 多账户（同一交易所挂多个 Key），并**一次性列出所有账户状态**，
      //      不用再像以前那样切下拉框一家一家看。
      const CEX = ["binance", "okx", "gate", "mexc"];
      const AUTONOMY_OPTS = [
        ["readonly", "只读", "只能读余额/持仓，任何写操作一律拒绝。默认档。"],
        ["confirm", "提议+确认", "AI 生成订单草案，你点确认后才真实发送。"],
        ["limited", "受限自动", "限额内自动下单（必须带止损），超限转待确认。"],
        ["full", "全自动", "AI 自主下单，仅受风控硬上限约束。风险最高。"],
      ];
      // ⚠ 告警条/其它散落处以前直接插 cexAutonomy 原始值（readonly/confirm/…），
      //   用户看到的是英文档位代码。这里给一份查表，任何地方要显示档位都走它。
      const autonomyLabel = function (k) {
        for (var i = 0; i < AUTONOMY_OPTS.length; i++) {
          if (AUTONOMY_OPTS[i][0] === k) return AUTONOMY_OPTS[i][1];
        }
        return k;                       // 未知档位才回落到原值（不编一个中文名）
      };
      // 风控档：以前下拉框直接显示 conservative/balanced/aggressive 三个英文词，
      // 而旁边的说明全是中文——夹在一堆中文里的裸英文最刺眼。
      const PROFILE_OPTS = [
        ["conservative", "保守", "默认：只做多、必带止损、单笔与总仓位上限最紧"],
        ["balanced", "均衡", "放宽单标的权重，仍强制止损"],
        ["aggressive", "激进", "上限更高、允许更激进仓位；仍受不可突破的硬顶约束"],
      ];
      const profileLabel = function (k) {
        for (var i = 0; i < PROFILE_OPTS.length; i++) {
          if (PROFILE_OPTS[i][0] === k) return PROFILE_OPTS[i][1];
        }
        return k;
      };
      // ── 账户标签预设（v1.9.3）────────────────────────────────────────
      // value 是**稳定的英文 slug**（它会变成磁盘文件名 `{ex}.{label}.dpapi`，
      // 用中文做文件名在 Windows 上能跑但没必要冒险）；
      // 选项文字是**人话**——用户看到的是「现货」而不是 `spot`。
      // 顺序按"最常见的用法在前"排。
      const LABEL_PRESETS = [
        ['main', '主账户（只有一个就用这个）'],
        ['spot', '现货　—　只买卖现货'],
        ['perp', '永续合约　—　USDT 本位杠杆合约'],
        ['futures', '交割合约　—　有到期日的合约'],
        ['margin', '杠杆/逐仓　—　带借币的杠杆账户'],
        ['read', '只读监控　—　只查余额持仓，不打算下单'],
      ]
      const LABEL_CUSTOM = '__custom__'
      // 已配置的标签一定要在选项里，否则升级成下拉会把老账户"弄丢"
      const _labelOptions = function (info) {
        var out = LABEL_PRESETS.slice()
        var acc = (info && info.accounts) || []
        for (var i = 0; i < acc.length; i++) {
          var lab = acc[i] && acc[i].label
          if (lab && !out.some(function (o) { return o[0] === lab })) {
            out.push([lab, lab + '　—　（你已用过的标签）'])
          }
        }
        out.push([LABEL_CUSTOM, '其它　—　自己起名字'])
        return out
      }
      const _labelKnown = function (lab) {
        if (!lab) return false
        for (var i = 0; i < LABEL_PRESETS.length; i++) {
          if (LABEL_PRESETS[i][0] === lab) return true
        }
        return false
      }
      // 凭据完整性自检。**留空 = 不改动**（已配置的账户只改一项也合法），
      // 所以只有在"全新建号"（没有已保存的掩码）时才要求填全。
      // 返回缺失项的中文说明，齐了返回 null。
      const _cxMissing = function (form, info) {
        var ex = form && form.exchange;
        var schema = info && info.credential_schema && info.credential_schema[ex];
        if (!schema) return null;                    // schema 未就绪 → 不拦
        var cur = info && info.exchanges && info.exchanges[ex];
        var existing = cur && cur.labels && cur.labels.filter(function (l) {
          return l.label === (form && form.label);
        })[0];
        var missing = [];
        var has = function (k, v) {
          if (String(v || "").trim()) return true;
          // 留空不改动：只有该字段本来就有值，才算"已具备"
          return !!(existing && k === "api_key") || !!existing;
        };
        if (!has("api_key", form.api_key)) missing.push("API 密钥");
        if (!has("api_secret", form.api_secret)) missing.push("Secret Key");
        if (schema.passphrase && !has("passphrase", form.passphrase)) {
          missing.push("密码短语（" + String(ex).toUpperCase() + " 必填）");
        }
        return missing.length ? "缺 " + missing.join("、") : null;
      };
      const cx = react.useState({
        exchange: "binance", label: "main", customLabel: "",
        api_key: "", api_secret: "", passphrase: "",
      });
      const cxForm = cx[0]; const setCxForm = cx[1];
      const cxs = react.useState(null); const cexInfo = cxs[0]; const setCexInfo = cxs[1];
      const cxs2 = react.useState(""); const cexMsg = cxs2[0]; const setCexMsg = cxs2[1];
      const cxs3 = react.useState(false); const cexBusy = cxs3[0]; const setCexBusy = cxs3[1];

      react.useEffect(function () {
        let alive = true;
        fetch("/kexi-dashboard/cex", { cache: "no-store", credentials: "include" })
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (v) { if (alive && v) setCexInfo(v); })
          .catch(function () { });
        return function () { alive = false; };
      }, []);

      function cexSubmit(forget) {
        setCexBusy(true); setCexMsg("");
        const opt = forget
          ? { method: "DELETE", credentials: "include" }
          : { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
              body: JSON.stringify(cxForm) };
        const url = forget
          ? ("/kexi-dashboard/cex?exchange=" + encodeURIComponent(cxForm.exchange)
             + "&label=" + encodeURIComponent(cxForm.label || "main"))
          : "/kexi-dashboard/cex";
        fetch(url, opt)
          .then(function (r) { return r.json(); })
          .then(function (v) {
            setCexBusy(false);
            if (v && v.ok) {
              setCexMsg(forget
                ? ("已删除 " + cxForm.exchange + "/" + (cxForm.label || "main") + " 的凭据")
                : ("已保存 " + cxForm.exchange + "/" + (cxForm.label || "main") + "（已 DPAPI 加密，Python 侧可读）"));
              setCxForm({ exchange: cxForm.exchange, label: cxForm.label || "main", api_key: "", api_secret: "", passphrase: "" });
              if (v.status) setCexInfo(v.status);
              else fetch("/kexi-dashboard/cex", { credentials: "include" }).then(function (r) { return r.json(); }).then(function (x) { if (x) setCexInfo(x); }).catch(function () { });
            } else {
              setCexMsg("失败：" + String((v && v.error) || "未知错误"));
            }
          })
          .catch(function (e) { setCexBusy(false); setCexMsg("请求失败：" + String((e && e.message) || e)); });
      }
      const cexRow = function (label, key, ph) {
        return react.createElement("div", { className: "kexi-set-row", key: key },
          react.createElement("div", { className: "kexi-set-lab" },
            react.createElement("span", null, label),
            react.createElement("span", { className: "kexi-set-desc" }, ph)),
          react.createElement("input", {
            className: "kexi-set-in", type: "password", autoComplete: "new-password",
            placeholder: "留空 = 不改动", value: cxForm[key],
            onChange: function (e) {
              const v = e.target.value;
              setCxForm(function (o) { const n = Object.assign({}, o); n[key] = v; return n; });
            },
          }));
      };
      const cexCurrent = cexInfo && cexInfo.exchanges ? cexInfo.exchanges[cxForm.exchange] : null;
      const cexAutonomy = (cexInfo && cexInfo.autonomy) || "readonly";
      const cexAccounts = (cexInfo && cexInfo.accounts) || [];
      const cexRegistry = (cexInfo && cexInfo.registry) || CEX;
      const cexEnabled = (cexInfo && cexInfo.enabled) || CEX;

      react.useEffect(function () {
        let alive = true;
        fetch("/kexi-dashboard/settings", { cache: "no-store" })
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (j) { if (alive && j && j.settings) { setCfg(j.settings); setStatus("ready"); } })
          .catch(function () { if (alive) setStatus("unavailable"); });
        return function () { alive = false; };
      }, []);

      const save = function (patch) {
        const next = Object.assign({}, cfg || {}, patch);
        setCfg(next);
        fetch("/kexi-dashboard/settings", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(next)
        }).then(function (r) { return r.json(); })
          .then(function (j) {
            if (j && j.ok) { setCfg(j.settings); setMsg("已保存 ✓"); setTimeout(function () { setMsg(""); }, 1600); }
            else setMsg("保存失败 " + ((j && j.error) || ""));
          })
          .catch(function () { setMsg("保存请求失败"); });
      };

      const num = function (key, label, desc, step, min, max) {
        const v = cfg ? cfg[key] : "";
        return react.createElement("div", { className: "kexi-set-row", key: key },
          react.createElement("div", { className: "kexi-set-lab" },
            react.createElement("span", null, label),
            desc ? react.createElement("span", { className: "kexi-set-desc" }, desc) : null),
          react.createElement("input", {
            type: "number", value: v === undefined || v === null ? "" : v, step: step || 1, min: min, max: max,
            onChange: function (e) { const n = Number(e.target.value); save({ [key]: Number.isFinite(n) ? n : v }); }
          }));
      };
      const bool = function (key, label, desc) {
        const v = !!(cfg && cfg[key]);
        return react.createElement("div", { className: "kexi-set-row", key: key },
          react.createElement("div", { className: "kexi-set-lab" },
            react.createElement("span", null, label),
            desc ? react.createElement("span", { className: "kexi-set-desc" }, desc) : null),
          react.createElement("input", { type: "checkbox", checked: v, onChange: function (e) { save({ [key]: e.target.checked }); } }));
      };
      const cliChips = function () {
        const en = (cfg && cfg.cliEnabled) || {};
        const pri = (cfg && Array.isArray(cfg.cliPriority)) ? cfg.cliPriority : ["agy", "codebuddy", "mimo"];
        const all = ["agy", "codebuddy", "mimo"];
        return react.createElement("div", { className: "kexi-set-row" },
          react.createElement("div", { className: "kexi-set-lab" },
            react.createElement("span", null, "本地 CLI 参与"),
            react.createElement("span", { className: "kexi-set-desc" }, "优先级顺序（点击切换启用；顺序决定成员优先派给哪个 CLI）：" + pri.join(" > "))),
          react.createElement("div", { className: "kexi-chips" },
            all.map(function (k) {
              const on = en[k] !== false;
              return react.createElement("span", {
                key: k,
                className: "kexi-chip" + (on ? " kexi-chip-on" : ""),
                onClick: function () {
                  const next = Object.assign({}, en);
                  next[k] = !on;
                  // 启用的排前、禁用的排后，保持确定性顺序
                  const nextPri = all.slice().sort(function (a, b) {
                    const ea = next[a] !== false ? 0 : 1, eb = next[b] !== false ? 0 : 1;
                    if (ea !== eb) return ea - eb;
                    return pri.indexOf(a) - pri.indexOf(b);
                  });
                  save({ cliEnabled: next, cliPriority: nextPri });
                }
              }, (on ? "✓ " : "✗ ") + k);
            })));
      };

      if (status === "unavailable") {
        return react.createElement("div", { className: "kexi-set" },
          react.createElement("div", { className: "kexi-hint" }, "K析研判团设置不可用（host 未就绪）"));
      }
      if (!cfg) return react.createElement("div", { className: "kexi-set" }, react.createElement("div", { className: "kexi-hint" }, "加载设置…"));

      return react.createElement("div", { className: "kexi-set" },
        react.createElement("div", { className: "kexi-set-row" },
          react.createElement("div", { className: "kexi-set-lab" },
            react.createElement("span", { style: { fontWeight: 600 } }, "K析研判团"),
            react.createElement("span", { className: "kexi-set-desc" }, "预设 kexi-crypto · 实时进度与本地 CLI 编排")),
          react.createElement("span", { className: "kexi-badge" }, status === "ready" ? "已连接" : status)),
        bool("persistentTeam", "会话开始自动创建持久团队", "会话第一步预置数脉/指北/望潮/守拙四位持久成员，直接显示在会话页智能体团队面板；关闭则仅按需调用"),
        bool("showProgressPopup", "启用实时进度弹窗", "任务运行时点状态灯查看节点时间线（谁在干活/拿到什么）；弹窗不自动弹出"),
        bool("showCliNodes", "显示本地 CLI 节点", "把 agy / codebuddy / mimo 的调用单独标为 🤖 节点"),
        bool("autoCliCrosscheck", "流水线自动 CLI 交叉验证", "kexi_run 第 5 步自动派本地 CLI 独立复核报告形态；结论仅作参考意见，不改权威字段；无 CLI 时如实记为「跳过」"),
        num("cliCrosscheckTimeoutSec", "交叉验证超时(秒)", "单个 CLI 复核的最长等待（30–300），超时记为「跳过」，不阻塞看板渲染", 30, 30, 300),
        num("maxEventNodes", "最多显示节点数", "每会话保留的活动节点上限（20–200）", 10, 20, 200),
        bool("cliAsMembers", "把本地 CLI 纳入团队成员", "开启后 agy/codebuddy/mimo 也作为团队一员承担子任务（按上方启用项与优先级）；关闭则 CLI 仅做交叉验证参考。CLI 结论始终是参考意见，与脚本数据冲突时以脚本为准"),
        cliChips(),
        num("screenerTop", "筛选标的池大小", "screener 扫描的市值前 N 币（10–400）", 100, 10, 400),
        num("screenerWorkers", "筛选并发线程数", "并发取数线程（1–16）。1=旧的严格串行口径；实测 8 线程比串行快约 3.8 倍", 6, 1, 16),
        num("minAdvUsd", "ADV20 流动性门槛(USD)", "20 日均成交额低于此值一票否决", 3000000, 0, 1000000000),
        num("riskPerTrade", "单笔风险预算", "仓位预算：每笔风险占总资金比例（0.0005–0.1）", 0.005, 0.0005, 0.1),
        num("maxWeight", "单标的权重上限", "ATR 等风险预算下的最大仓位权重（0.01–1）", 0.25, 0.01, 1),
        num("defaultLimit", "默认取数根数", "pipeline 默认拉取 K 线根数（60–1000）", 20, 60, 1000),

        // ── CEX 实盘自主级别（v1.6.0）────────────────────────────────
        // 这是**唯一的实盘开关**。它写进 autonomy.json，Python 侧只读，
        // 模型拿不到任何改写它的途径。默认 readonly = 任何写操作都被拒。
        react.createElement("div", { className: "kexi-set-sec" },
          "CEX 实盘自主级别（唯一实盘开关 · AI 无法自行更改）"),
        react.createElement("div", { className: "kexi-set-row" },
          react.createElement("div", { className: "kexi-set-lab" },
            react.createElement("span", null, "自主级别"),
            react.createElement("span", { className: "kexi-set-desc" },
              "当前：**" + ((cexInfo && cexInfo.autonomy_levels && cexInfo.autonomy_levels[cexAutonomy]
                && cexInfo.autonomy_levels[cexAutonomy].label) || cexAutonomy) + "**。"
              + "从「只读」往上逐级放开。改这里等于授权 AI 用你的真钱下单，请确认风控档与限额。") ),
          react.createElement("select", {
            className: "kexi-set-in",
            value: cexAutonomy,
            onChange: function (e) {
              const v = e.target.value;
              save({ cexAutonomy: v });
              fetch("/kexi-dashboard/cex", { credentials: "include" })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (x) { if (x) setCexInfo(x); })
                .catch(function () { });
            },
          }, AUTONOMY_OPTS.map(function (o) {
            return react.createElement("option", { key: o[0], value: o[0] },
              o[1] + "　—　" + o[2]);
          }))),
        cexAutonomy === "readonly" ? null : react.createElement("div", {
          className: "kexi-set-msg",
        }, "⚠ 已放开实盘能力（「" + autonomyLabel(cexAutonomy) + "」）。AI 现在可以真实下单。"
          + "把自主级别调回「只读」可立即停止一切写操作——这个开关随时生效，无需重启。"),
        react.createElement("div", { className: "kexi-set-row" },
          react.createElement("div", { className: "kexi-set-lab" },
            react.createElement("span", null, "风控档"),
            react.createElement("span", { className: "kexi-set-desc" },
              "决定单笔/单币/总仓位上限、杠杆上限、是否强制止损、是否需要确认。"
              + "当前：**" + profileLabel(String((cfg && cfg.cexProfile) || "conservative")) + "**。") ),
          react.createElement("select", {
            className: "kexi-set-in", value: String((cfg && cfg.cexProfile) || "conservative"),
            onChange: function (e) { save({ cexProfile: e.target.value }); },
          }, PROFILE_OPTS.map(function (p) {
            return react.createElement("option", { key: p[0], value: p[0] },
              p[1] + "　—　" + p[2]);
          }))),
        react.createElement("div", { className: "kexi-set-row" },
          react.createElement("div", { className: "kexi-set-lab" },
            react.createElement("span", null, "受限自动单笔上限"),
            react.createElement("span", { className: "kexi-set-desc" },
              "仅对「受限自动」档生效：超过这个美元金额的单一律拒绝，不会自动放行。") ),
          num("cexAutoNotionalCapUsd", "美元上限", "", 10, 1, 1000000)),

        // ── 已配置账户一览（不用再一家家切下拉框看）─────────────────────
        react.createElement("div", { className: "kexi-set-sec" },
          "已配置账户（" + cexAccounts.length + " 个）"),
        cexAccounts.length === 0
          ? react.createElement("div", { className: "kexi-set-desc" },
            "尚未配置任何凭据。填好下面的表单点「保存凭据」即可；"
            + "同一个交易所可以用不同账户标签（label）挂多个 Key。")
          : react.createElement("div", { className: "kexi-set-desc" },
            cexAccounts.map(function (a) {
              return a.exchange + "/" + a.label + "（" + (a.api_key_masked || "***") + "）";
            }).join("　·　")),

        // ── CEX API 凭据区 ──────────────────────────────────────────────
        react.createElement("div", { className: "kexi-set-sec" }, "CEX API 凭据（交易所账户）"),
        react.createElement("div", { className: "kexi-set-row" },
          react.createElement("div", { className: "kexi-set-lab" },
            react.createElement("span", null, "交易所"),
            react.createElement("span", { className: "kexi-set-desc" },
              cexInfo ? ("当前状态：" + (cexCurrent && cexCurrent.configured
                ? ("已配置 " + (cexCurrent.labels || []).map(function (l) {
                    return l.label + " " + (l.api_key_masked || "***");
                  }).join("、")
                  + (cexCurrent.labels || []).some(function (l) { return l.has_passphrase; })
                    ? "（含附加口令）" : "")
                : "未配置")) : "加载中…")),
          react.createElement("select", {
            className: "kexi-set-in",
            value: cxForm.exchange,
            onChange: function (e) {
              const v = e.target.value;
              setCxForm(function (o) { const n = Object.assign({}, o); n.exchange = v; return n; });
            },
          }, cexRegistry.map(function (x) {
            return react.createElement("option", { key: x, value: x },
              x + (cexEnabled.indexOf(x) < 0 ? "（未启用）" : ""));
          }))),
        // ── 账户标签：改成**下拉选可理解的用途**，不再让用户自己编 ──────
        // 用户 2026-09-30 反馈：「账户标签改成用户能看得懂的选项而不是让用户自己蒙」。
        // 原来是一个自由输入框 + placeholder "如 main"，用户得自己想名字，
        // 而这个名字会直接变成磁盘文件名 `{ex}.{label}.dpapi`。
        // 折中方案：**下拉给常见用途**（选项文字是人话），保留「自定义」兜底
        // 给真的要按自己习惯命名的人；已存在的标签一定出现在列表里，
        // 不会因为升级成下拉就把老账户"弄丢"。
        react.createElement("div", { className: "kexi-set-row" },
          react.createElement("div", { className: "kexi-set-lab" },
            react.createElement("span", null, "这个账户用来做什么"),
            react.createElement("span", { className: "kexi-set-desc" },
              "同一交易所可以挂多个账户，用途区分开才不会互相覆盖。"
              + "选一个就行；真要自己起名字，选最下面「其它」再填。") ),
          react.createElement("div", { style: { display: "flex", gap: "6px", alignItems: "center" } },
            react.createElement("select", {
              className: "kexi-set-in",
              value: _labelKnown(cxForm.label) ? cxForm.label : "__custom__",
              onChange: function (e) {
                var v = e.target.value;
                setCxForm(function (o) {
                  var n = Object.assign({}, o);
                  n.label = (v === "__custom__") ? (o.customLabel || "") : v;
                  n.customLabel = (v === "__custom__") ? (o.customLabel || "") : n.customLabel;
                  return n;
                });
              },
            }, _labelOptions(cexInfo).map(function (o) {
              return react.createElement("option", { key: o[0], value: o[0] },
                o[1] + (cexCurrent && (cexCurrent.labels || []).some(function (l) { return l.label === o[0]; })
                  ? "（已配置）" : ""));
            })),
            (_labelKnown(cxForm.label) ? null : react.createElement("input", {
              className: "kexi-set-in", value: cxForm.customLabel || "",
              placeholder: "自己起个名字",
              onChange: function (e) {
                var v = e.target.value;
                setCxForm(function (o) { var n = Object.assign({}, o); n.customLabel = v; n.label = v; return n; });
              },
            })))),
        cexRow((cexInfo && cexInfo.credential_schema
                && cexInfo.credential_schema[cxForm.exchange]
                && cexInfo.credential_schema[cxForm.exchange].key_name) || "API 密钥",
               "api_key", "交易所后台创建；建议关闭提现权限"),
        cexRow((cexInfo && cexInfo.credential_schema
                && cexInfo.credential_schema[cxForm.exchange]
                && cexInfo.credential_schema[cxForm.exchange].secret_name) || "API 密钥口令",
               "api_secret", "只写入本机 DPAPI 加密存储，页面不回显"),
        // ⚠ passphrase **按交易所条件渲染**（用户 2026-09-30 实测反馈）：
        //   OKX 是「密码短语 + API Key + Secret Key」**三样**；
        //   Binance / Gate / MEXC 只有 API Key + Secret Key **两样**。
        //   以前这个输入框无条件常驻——Binance 用户会看到一个根本不存在的字段，
        //   而且旧文案把 Gate 也列了进去，而 cex_keystore.py:189 的真实规则是
        //   **只有 OKX** 需要，Gate 那半句本身就是错的。
        //   字段构成由 host 的 /cex credential_schema 下发（单一事实来源），
        //   它与 Python 侧规则的一致性由 test-cex-credential-schema.py 钉死。
        ((cexInfo && cexInfo.credential_schema
          && cexInfo.credential_schema[cxForm.exchange]
          && cexInfo.credential_schema[cxForm.exchange].passphrase)
          ? cexRow("密码短语（OKX 必填）", "passphrase",
                   "OKX 创建 API 时的那一串口令；三样凭据缺一不可，填错会验签失败")
          // ⚠ schema 还没加载回来时**什么都不说**——宁可空着也不能谎报"只需两样"，
          //   那会让用户以为 Gate 也只要两样。
          : (cexInfo && cexInfo.credential_schema
             ? react.createElement("div", { className: "kexi-set-desc", style: { margin: "2px 0 6px" } },
                 "当前交易所 " + String(cxForm.exchange || "").toUpperCase()
                 + " 只需两样凭据（API Key + Secret Key），不需要密码短语。")
             : null)),
        react.createElement("div", { className: "kexi-set-row" },
          react.createElement("div", { className: "kexi-set-lab" },
            react.createElement("span", { className: "kexi-set-desc" },
          react.createElement("div", { className: "kexi-set-desc", style: { margin: "2px 0 6px" } },
              "密钥经 Windows DPAPI 加密保存在本机（与你的 Windows 账户绑定），页面永不回显明文。"
              + "留空提交表示不改动。Python 侧能原样读回（存为 " + ((cexInfo && cexInfo.storeDir) || "本机") + "）。")),
          // ⚠ 提交前先拦一道（用户 2026-09-30 反馈衍生的真实缺口）：
          //   host 存凭据**不校验** passphrase 非空，所以 OKX 少填密码短语
          //   会「保存成功」，直到 Python 侧 cex_keystore.py:189 才拒——
          //   而那时用户已经离开设置面板了。在这里拦住，并说清缺哪一样。
          _cxMissing(cxForm, cexInfo) ? react.createElement("div", {
            className: "kexi-set-msg", style: { color: "var(--dsw-alias-state-error-primary,#ef4444)" },
          }, "⚠ 填不完整：" + _cxMissing(cxForm, cexInfo) + "。" + String(cxForm.exchange || "").toUpperCase()
             // ⚠ 措辞必须跟着 schema 走：写死"三样"会让只用两样的
             //   Binance/Gate/MEXC 用户以为自己还漏了一项。
             + " 这家要 " + ((cexInfo.credential_schema[cxForm.exchange] || {}).fields || []).length
             + " 样凭据齐全，缺了会在真正下单时才报错（那时你已不在这里了）。") : null),
          react.createElement("div", null,
            react.createElement("button", {
              className: "kexi-btn kexi-btn-pri", disabled: cexBusy,
              onClick: function () { cexSubmit(false); },
            }, cexBusy ? "处理中…" : "保存凭据"),
            " ",
            cexCurrent && cexCurrent.configured
              ? react.createElement("button", {
                  className: "kexi-btn", disabled: cexBusy,
                  onClick: function () { cexSubmit(true); },
                }, "删除本账户")
              : null)),
        // 启用/停用开关：停用后 kexi_cex 不再碰这家，但凭据仍留在盘上
        react.createElement("div", { className: "kexi-set-row" },
          react.createElement("div", { className: "kexi-set-lab" },
            react.createElement("span", null, "已启用的交易所"),
            react.createElement("span", { className: "kexi-set-desc" },
              "停用只是让 AI 不再碰这家账户，凭据仍保留在盘上，随时可重新启用。") ),
          react.createElement("div", null, cexRegistry.map(function (x) {
            const on = cexEnabled.indexOf(x) >= 0;
            return react.createElement("label", {
              key: x, style: { marginRight: "10px", cursor: "pointer" },
            }, react.createElement("input", {
              type: "checkbox", checked: on,
              onChange: function (e) {
                const v = e.target.checked;
                const next = Object.assign({}, (cfg && cfg.cexEnabled) || {});
                next[x] = v;
                save({ cexEnabled: next });
                fetch("/kexi-dashboard/cex", { credentials: "include" })
                  .then(function (r) { return r.ok ? r.json() : null; })
                  .then(function (y) { if (y) setCexInfo(y); })
                  .catch(function () { });
              },
            }), " " + x);
          }))),
        cexMsg ? react.createElement("div", { className: "kexi-set-msg" }, cexMsg) : null,
        react.createElement("div", { className: "kexi-set-actions" },
          react.createElement("button", { className: "kexi-btn kexi-btn-pri", onClick: function () { save({}); } }, "保存"),
          react.createElement("span", { className: "kexi-hint" }, msg || "改动即时保存到 kexi-settings.json")));
    }

    /** 已注册条目的 disposer 集合（由 seat 填充）。 */
    let bookings = [];

    // 槽位落座：先试直接 register（插件通常加载在产品模块之后，槽位规范已存在），
    // 失败则回退 slots.inject 等待槽位出现 + 延迟重试（对齐 web-search-panel 纪律）。
    // 注意：registry 走**已解析的 slots 服务句柄**（而不是 ctx.slots 属性）——
    // 服务句柄是 apply 里确证存在的，属性暴露形态随版本可变。
    function seat(scope, entry, Component, label) {
      const reg = (scope.slots && typeof scope.slots.register === "function")
        ? (e, c) => scope.slots.register(e, c)
        : (scope.get("slots") && typeof scope.get("slots").register === "function" ? (e, c) => scope.get("slots").register(e, c) : null);
      const inj = (scope.slots && typeof scope.slots.inject === "function") ? (n, f) => scope.slots.inject(n, f) : null;
      if (reg === null) { console.warn("[kexi] seat " + label + ": slots.register unavailable"); return; }
      let done = false;
      const doRegister = function (via) {
        if (done) return true;
        try {
          const d = reg(entry, function (props) { return react.createElement(Component, props); });
          bookings.push(typeof d === "function" ? d : function () { });
          done = true;
          return true;
        } catch (e) {
          console.warn("[kexi] seat " + label + " via " + via + " failed: " + String((e && e.message) || e));
          return false;
        }
      };
      if (doRegister("direct")) return;
      if (inj === null) { console.warn("[kexi] seat " + label + ": slots.inject unavailable"); return; }
      try {
        inj(entry.name, function () { return doRegister("inject"); });
      } catch (e) {
        console.warn("[kexi] seat " + label + " inject unavailable: " + String((e && e.message) || e));
        // 最后一招：延迟重试数次（产品槽位可能稍后就绪）
        let tries = 0;
        const t = setInterval(function () {
          if (++tries > 20 || doRegister("retry")) clearInterval(t);
        }, 500);
      }
    }

    function apply(ctx) {
      if (typeof ctx.inject !== "function") return;
      ctx.inject(["slots"], function (scope) {
        const slots = scope.get("slots");
        if (slots === undefined) return;

        // 1) 标题栏灯 + 实时进度弹窗
        seat(scope, {
          name: "conversation.session.header.utilities",
          id: "kexi-dashboard-home",
          order: 51,
          inject: function (injectedSessionId) { return { injectedSessionId: injectedSessionId, sessionsSvc: scope.get("sessions") }; }
        }, Indicator, "header-utilities");

        // 2) 设置面板卡。
        //    配置读写走本插件自己的 host 端点 /kexi-dashboard/settings（落盘
        //    kexi-settings.json），因此不依赖已被移除的 settingsScope 服务
        //    （本机 DSH 版本强行注入 settingsScope 会白屏，见 web-search-panel 补丁说明）。
        seat(scope, {
          name: "settings.plugin.item",
          key: KEXI_SETTINGS_NS,
          inject: function () { return {}; }
        }, SettingsCard, "settings.plugin.item");
      });
    }

    exports.apply = apply;
    exports.inject = ["slots"];
    return module.exports;
  },
});
