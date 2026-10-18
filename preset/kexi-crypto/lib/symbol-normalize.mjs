// symbol-normalize.mjs —— 交易对符号规范化的唯一真源（v1.9.22）
//
// 为什么必须独立成模块：这个缺陷曾让整个研判流程全灭，且**三处入口各写一遍
// .toUpperCase() 就是它长期没被修好的原因**。放在独立文件里，任何一处想放宽
// 或加特例，都必须改这一个文件，不会再出现"某个入口忘了补后缀"。
//
// 现场取证（session-44da005c，2026-10-18）：
//   · kexi_run(mode=fast, symbols="BTC,ETH,...") 传裸符号，
//     三条下游取数路径同时失败：
//       CG_IDS.get("BTC")      → 无此键（键是 BTCUSDT）→ no CoinGecko id mapping
//       Binance ?symbol=BTC    → HTTP 400
//       OKX instId=BTC         → 51001 Instrument ID does not exist
//   · 实测 fetch_binance('BTC') 9.38 秒失败 / fetch_binance('BTCUSDT') 0.61 秒成功
//   · 20 币 × ~6 秒 ≈ 120 秒，与会话 elapsed_ms=119636 精确吻合
//   · 同一会话内模型自行改用 'BTCUSDT' 后立刻成功 —— 这是**用户被迫规避缺陷**的证据
//
// 设计约束（用户规则 1：开发期禁降级兜底）：
//   规范化**只做一次确定性改写**，并把改写结果**回显**给调用方。
//   绝不"猜一个看起来对的结果然后假装成功"——调用方必须看得见自己传的 BTC 变成了 BTCUSDT。

/** 交易所常用计价币，按"最长优先"匹配（USDT 必须在 USD 之前，否则 USDUSDT 会被切错）。 */
export const QUOTE_ASSETS = Object.freeze(['USDT', 'USDC', 'BUSD', 'FDUSD', 'TUSD', 'BTC', 'ETH', 'BNB', 'USD'])

/**
 * 纯计价币：单独出现时只有"计价单位"含义、不可能是标的本身。
 * BTC/ETH/BNB 虽可作计价币（ETHBTC），但它们本身是标的，故**不在此列**——
 * 把 BTC 判成"计价币而非标的"就挡住了最常见的一次输入。
 * 与 Python 侧 kline_utils.PURE_QUOTES 必须保持一致。
 */
export const PURE_QUOTES = Object.freeze(['USDT', 'USDC', 'BUSD', 'FDUSD', 'TUSD', 'USD'])

/** 合法基币：字母数字，1~15 位。用于拒绝明显非法的输入（而不是把它补成垃圾符号）。 */
const BASE_RE = /^[A-Z0-9]{1,15}$/

/**
 * 规范化一个交易对符号。
 *
 * @param {unknown} raw 用户/模型传入的原始符号，如 "btc"、"BTC/USDT"、"eth-usdt"
 * @returns {{symbol: string|null, base: string|null, quote: string|null,
 *            changed: boolean, input: string, note: string|null, error: string|null}}
 */
export function normalizeSymbol(raw) {
  const input = raw == null ? '' : String(raw)
  // 去空白与常见分隔符：模型会写 BTC/USDT、BTC-USDT、BTC_USDT，都当成同一个东西。
  const cleaned = input.trim().toUpperCase().replace(/[\s/\\_-]+/g, '')

  if (!cleaned) {
    return { symbol: null, base: null, quote: null, changed: false, input, note: null, error: '符号为空' }
  }
  if (!/^[A-Z0-9]+$/.test(cleaned)) {
    return {
      symbol: null, base: null, quote: null, changed: false, input, note: null,
      error: `符号含非法字符：${input}`,
    }
  }

  // ① 已经带计价后缀 → 只做校验，不改写
  for (const q of QUOTE_ASSETS) {
    if (cleaned.length > q.length && cleaned.endsWith(q)) {
      const base = cleaned.slice(0, -q.length)
      if (!BASE_RE.test(base)) {
        return {
          symbol: null, base: null, quote: null, changed: false, input, note: null,
          error: `符号基币部分非法：${input}`,
        }
      }
      const changed = cleaned !== input
      return {
        symbol: cleaned, base, quote: q, changed, input,
        // 只有真改写了字符（去分隔符/转大写）才提示；补后缀走 ② 分支
        note: changed ? `已规范化为 ${cleaned}` : null,
        error: null,
      }
    }
  }

  // ② 没带计价后缀 → 补 USDT（现货主流计价），并**明确报告改写**
  //    这里不判断"这个币是否存在"——那是取数层的职责。本函数只回答
  //    "要发给交易所的符号应该长什么样"。
  //
  //    但纯计价币（USD/USDT）单独出现是**错误输入**，不是缺后缀：
  //    早先的实现会补成 USDUSDT 这种不存在的交易对，然后发请求、
  //    白白等完 5 host × 3 retry。宁可立刻报错也不要制造垃圾符号。
  //    注意 BTC/ETH/BNB 不在 PURE_QUOTES 里——它们本身是标的。
  if (PURE_QUOTES.includes(cleaned)) {
    return {
      symbol: null, base: null, quote: null, changed: false, input, note: null,
      error: `${cleaned} 是计价币而非标的，请给出完整交易对（如 BTCUSDT）`,
    }
  }
  if (!BASE_RE.test(cleaned)) {
    return {
      symbol: null, base: null, quote: null, changed: false, input, note: null,
      error: `符号基币部分非法：${input}`,
    }
  }
  const symbol = cleaned + 'USDT'
  return {
    symbol, base: cleaned, quote: 'USDT', changed: true, input,
    note: `未带计价后缀，已补为 ${symbol}（原输入 ${input}）`,
    error: null,
  }
}

/**
 * 批量规范化，保持输入顺序并去重。
 *
 * @param {unknown} raw 数组，或逗号分隔字符串
 * @returns {{symbols: string[], notes: string[], errors: string[], changed: boolean}}
 */
export function normalizeSymbols(raw) {
  let list = []
  if (Array.isArray(raw)) list = raw
  else if (typeof raw === 'string') list = raw.split(',')
  else if (raw != null) list = [raw]

  const symbols = []
  const notes = []
  const errors = []
  for (const item of list) {
    for (const part of String(item == null ? '' : item).split(',')) {
      const t = part.trim()
      if (!t) continue
      const r = normalizeSymbol(t)
      if (r.error) { errors.push(r.error); continue }
      if (r.note) notes.push(r.note)
      if (!symbols.includes(r.symbol)) symbols.push(r.symbol)
    }
  }
  return { symbols, notes, errors, changed: notes.length > 0 }
}
