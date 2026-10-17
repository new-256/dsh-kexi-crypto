#!/usr/bin/env node
// 设置卡多候选落座回归（v1.9.20）。
//
// 起因：client 设置卡原来只注册 settings.plugin.item，而该槽位在 DSH 0.1.6
// 重组插件页时已移除——0.2.x 实时 Slot 树里不存在，register 抛错后 inject
// 等待一个永不出现的槽位、重试耗尽后静默放弃，设置卡（CEX 凭据/自主级别/
// 取数口径）在界面永久不可见，且无任何服务端痕迹。
//
// 本测试在 Node 内桩 window/document/React，加载真实 client.js，模拟四种
// 宿主槽位集合，断言 seatFirst 的落座选择与「只落一个、绝不重复注册」：
//   A. 0.2.x：plugins.row.config + plugins.bundle.config 都在 → 首选 row.config
//   B. 0.1.6：只有 plugins.bundle.config → 落座它
//   C. ≤0.1.5：只有 settings.plugin.item → 落座旧槽位（兼容）
//   D. 全不在 → 不崩、挂 slots.inject 等待、且不产生任何注册
'use strict';

const fs = require('fs');
const path = require('path');

const CLIENT = path.join(__dirname, '..', 'home-plugin', 'dsh-kexi-crypto', 'lib', 'client.js');

let passed = 0, failed = 0;
function ok(cond, msg) {
  if (cond) { passed++; }
  else { failed++; console.error('  ✗ ' + msg); }
}

// ── 浏览器环境桩（工厂加载时注入 <style>；不真渲染）──
const fakeStyle = { setAttribute() {}, textContent: '' };
let loadDesc = null;
global.window = {
  __ModuleLoader__: { load(desc) { loadDesc = desc; } },
  addEventListener() {}, removeEventListener() {},
};
global.document = {
  querySelector() { return null; },
  createElement() { return fakeStyle; },
  head: { appendChild() {} },
};
// React 桩：createElement 只在槽位渲染回调里被调用，注册阶段不触发
const reactStub = {
  createElement() { return {}; },
  useState() { return [null, () => {}]; },
  useEffect() {}, useRef() { return { current: null }; },
};

eval(fs.readFileSync(CLIENT, 'utf8'));
const plugin = loadDesc.factory((n) => {
  if (n === 'react') return reactStub;
  throw new Error('意外的 require: ' + n);
});

function drive(acceptedSlots) {
  const registered = [];
  const injected = [];
  const slots = {
    register(entry, comp) {
      if (!acceptedSlots.has(entry.name)) throw new Error('slot not declared: ' + entry.name);
      if (registered.some((r) => r.name === entry.name)) throw new Error('double register: ' + entry.name);
      registered.push({ name: entry.name, key: entry.key });
      return function dispose() {};
    },
    inject(name) { injected.push(name); },
  };
  const scope = { slots, get(n) { return n === 'slots' ? slots : undefined; } };
  plugin.apply({ inject(_deps, cb) { cb(scope); } });
  // 清掉 seatFirst 挂的 1.5s/6s 兜底定时器，避免测试进程挂住
  // （Node 里 setTimeout 持有事件循环；这里不推进时间，直接 unref 不可得，
  //  故用显式退出，不影响断言）
  return { registered, injected };
}

const HEADER = 'conversation.session.header.utilities';
const ROW = 'plugins.row.config';
const BUNDLE = 'plugins.bundle.config';
const LEGACY = 'settings.plugin.item';

// A. DSH 0.2.x：两个新槽位都在 → 首选 row.config，且只落一个
{
  const { registered, injected } = drive(new Set([HEADER, ROW, BUNDLE]));
  const seats = registered.filter((r) => r.name !== HEADER);
  ok(seats.length === 1, `A 设置卡应且只应注册一次，实际 ${seats.length} 次: ${JSON.stringify(seats)}`);
  ok(seats[0] && seats[0].name === ROW, `A 0.2.x 应首选 ${ROW}，实际 ${seats[0] && seats[0].name}`);
  ok(seats[0] && seats[0].key === 'dsh-kexi-crypto#kexi-dashboard',
    `A row.config 的 key 必须是 <包名>#<行id>，实际 ${seats[0] && seats[0].key}`);
  ok(!injected.some((n) => n !== HEADER), 'A 直接成功时不应挂任何 inject 等待');
}

// B. DSH 0.1.6：只有 bundle.config
{
  const { registered } = drive(new Set([HEADER, BUNDLE]));
  const seats = registered.filter((r) => r.name !== HEADER);
  ok(seats.length === 1 && seats[0].name === BUNDLE,
    `B 0.1.6 应落座 ${BUNDLE}，实际 ${JSON.stringify(seats)}`);
  ok(seats[0] && seats[0].key === 'dsh-kexi-crypto',
    `B bundle.config 的 key 必须是包名，实际 ${seats[0] && seats[0].key}`);
}

// C. DSH ≤0.1.5：只有旧槽位 → 兼容落座
{
  const { registered } = drive(new Set([HEADER, LEGACY]));
  const seats = registered.filter((r) => r.name !== HEADER);
  ok(seats.length === 1 && seats[0].name === LEGACY,
    `C ≤0.1.5 应落座旧槽 ${LEGACY}，实际 ${JSON.stringify(seats)}`);
  ok(seats[0] && seats[0].key === 'kexiDashboard',
    `C 旧槽 key 必须是 kexiDashboard，实际 ${seats[0] && seats[0].key}`);
}

// D. 设置槽位全不在（异常宿主）→ 不崩、零注册、三槽位都挂 inject 等待
{
  const { registered, injected } = drive(new Set([HEADER]));
  const seats = registered.filter((r) => r.name !== HEADER);
  ok(seats.length === 0, `D 无槽位时设置卡应零注册，实际 ${seats.length}`);
  for (const n of [ROW, BUNDLE, LEGACY]) {
    ok(injected.includes(n), `D 应对 ${n} 挂 inject 等待（槽位后声明时能接住）`);
  }
}

console.log(`test-settings-seat: ${passed}/${passed + failed} 通过`);
process.exit(failed === 0 ? 0 : 1);
