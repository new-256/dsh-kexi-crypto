// home-plugin/dsh-kexi-crypto/lib/preset-roster.mjs — 设计 B：内部成员预设不出现在
// GUI 的「智能体预设」选择器里（v1.9.23）
//
// 上游事实（0.2.1-alpha.5 逐条取证，勿凭记忆改）：
//   ① @deepseek-ai/dsh-agent-preset 的 Config 只有 id/name/description/order/plugins
//      （lib/index.js L13-19）——**没有 hidden 字段**，声明侧无法隐藏；
//   ② GUI 侧 presetOptions() 只按 `broken` 过滤（dsh-client-ui-agent-preset/lib/client.js
//      L1168-1169），所以只要 roster 里有这五行，它们就会出现在选择器里；
//   ③ 浏览器 roster 的唯一入口是 `ctx.remote.agentPresets.list()`（同文件 L1112），
//      typert 端点 'agentPresets/list' 的实现是 `remoteExportList`
//      （dsh-agent-preset-registry/lib/typert.host.js L41-46），本体在
//      dsh-agent-preset-registry/lib/index.js L585-591；
//   ④ 网关取方法的方式是「作用域接收者 + implementation 映射」：
//        const callReceiver = receiverContext.extend({ invocation }).get(descriptor.service)
//        const method = Reflect.get(callReceiver, descriptor.implementation ?? descriptor.method)
//      （dsh-api-gateway/lib/index.js L1211-1213）
//      → 在服务**实例**上覆盖 remoteExportList（own property 遮蔽原型方法）即可生效。
//      ⚠ 实测教训：`ctx.get()` 返回的是**作用域代理**而不是原始实例，直接拿原始实例
//        做身份比较证明不了任何事；验证必须按上面这条网关路径来走。
//
// **语义边界（刻意，别为了"干净"下沉到 list()）**：
//   过滤只作用于**对外 roster**（`agentPresets/list` → GUI 选择器）。
//   `list()` / `readDocument()` / `resolve()` / `mount()` / `select()` 一律不动——
//   成员预设仍必须可被委派行引用、可被挂载、可被内部诊断读取。
//   把过滤下沉到 list() 会直接打断委派（test-preset-roster.mjs 与
//   check-preset-mount.mjs 都把这条钉死）。
//
// 纪律（用户规则 1：禁降级兜底）：
//   · 服务缺失 / remoteExportList 不是函数 / 返回值不含 presets 数组 / roster 行缺 id
//     → 一律 throw，让插件初始化或 roster 请求**当场失败并可见**，绝不 warn 后放行；
//   · 重复安装 → throw（同一进程里只能有一份过滤器：重复包裹会让 dispose 顺序错乱，
//     把原始方法还原成上一次的包裹函数）；
//   · dispose 时只有「当前属性值仍是我们装上去的函数」才还原，否则 throw
//     （说明被第三方替换过，静默还原会把别人的实现抹掉）；
//   · 返回值必须是**新对象**（不原地改宿主缓存/调用方手里的数组）。

import { isInternalPreset } from './members.mjs'

/**
 * 单进程唯一挂载标记。用 Symbol.for 而不是字符串键：不污染服务对象，
 * 但同一 realm 内任何一份本插件都能看到它（重复安装必须能被发现）。
 */
const FILTER_MARK = Symbol.for('kexi.rosterFilter.v1')

/**
 * 在 agentPresets 服务实例上安装 roster 过滤。
 * @param {object} service ctx.get('agentPresets') 的返回值
 * @returns {() => void} dispose：恢复实例原状（owner 用 ctx.effect 绑定生命周期）
 */
export function installPresetRosterFilter(service) {
  if (service === null || typeof service !== 'object') {
    throw new Error('kexi-dashboard: agentPresets 服务缺失——内部成员预设无法隐藏（拒绝静默放行）')
  }
  if (typeof service.remoteExportList !== 'function') {
    throw new Error('kexi-dashboard: agentPresets.remoteExportList 不存在或不是函数——上游 roster 契约已变，拒绝静默放行')
  }
  if (Object.getOwnPropertyDescriptor(service, FILTER_MARK) !== undefined) {
    throw new Error('kexi-dashboard: roster 过滤器已在本进程安装过（重复安装会让 dispose 顺序错乱，拒绝静默跳过）')
  }

  const original = service.remoteExportList
  // 正常形态是原型方法（自有属性不存在）；记录一次是为了 dispose 时精确还原。
  const originalOwn = Object.getOwnPropertyDescriptor(service, 'remoteExportList')

  const filtered = async function remoteExportListFiltered(...args) {
    const roster = await Reflect.apply(original, this, args)
    if (roster === null || typeof roster !== 'object' || !Array.isArray(roster.presets)) {
      throw new Error('kexi-dashboard: agentPresets.remoteExportList 返回值形状异常（缺 presets 数组），拒绝静默放行')
    }
    for (const row of roster.presets) {
      if (row === null || typeof row !== 'object' || typeof row.id !== 'string' || row.id === '') {
        throw new Error(`kexi-dashboard: roster 行缺少字符串 id，无法判定是否内部预设（实际 ${JSON.stringify(row)?.slice(0, 120)}）`)
      }
    }
    // 新对象 + 新数组：宿主缓存与其它调用方手里的对象保持原样。
    return { ...roster, presets: roster.presets.filter((row) => !isInternalPreset(row.id)) }
  }

  Object.defineProperty(service, FILTER_MARK, {
    configurable: true,
    enumerable: false,
    writable: false,
    value: Object.freeze({ wrapper: filtered, original }),
  })
  Object.defineProperty(service, 'remoteExportList', {
    configurable: true,
    enumerable: false,
    writable: true,
    value: filtered,
  })

  return function disposePresetRosterFilter() {
    // ⚠ 身份比较必须走 `getOwnPropertyDescriptor` 取**原始值**：宿主把服务包成
    //   作用域代理，`service.remoteExportList`（普通 get）拿到的是每次新建的绑定
    //   包装函数，永远不等于我们装上去的那个——用它判断会把"正常卸载"误判成
    //   "被替换"（本方案第一版实测就栽在这里）。descriptor 不经过那层绑定。
    const current = Object.getOwnPropertyDescriptor(service, 'remoteExportList')
    const mark = Object.getOwnPropertyDescriptor(service, FILTER_MARK)
    // 被人替换过：说明还有第三方在同一服务上做手脚，直接还原会抹掉别人的实现。
    // 这不是可恢复状态，必须让调用方看见（宿主会把 effect 清理期的异常记进日志）。
    if (current === undefined || current.value !== filtered || mark === undefined || mark.value?.wrapper !== filtered) {
      throw new Error('kexi-dashboard: 卸载 roster 过滤器时发现方法已被替换——拒绝盲目还原（会抹掉第三方实现）')
    }
    delete service[FILTER_MARK]
    if (originalOwn !== undefined) Object.defineProperty(service, 'remoteExportList', originalOwn)
    else delete service.remoteExportList
  }
}
