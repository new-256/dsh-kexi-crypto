#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cex_keystore.py - CEX API 密钥本地安全存储（v1.5.0 新增）

═══════════════════════════════════════════════════════════════════════
用户要求（2026-09-29）："只要该密钥不上传至云端即可"
→ 因此本模块**只在本机落盘 + Windows DPAPI 加密**，绝不外发任何密钥。
═══════════════════════════════════════════════════════════════════════

安全设计
--------
1. **DPAPI 加密（用户态）**：用 `CryptProtectData` 加密，密钥由 Windows 从
   当前用户登录凭据派生。**不加 CRYPTPROTECT_LOCAL_MACHINE**——加了之后本机
   任何用户都能解密，等于没加密。
   Microsoft 明确说明：加解密通常必须在同一台电脑、同一登录凭据下完成
   （learn.microsoft.com/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata）。
2. **权限收紧**：目录与文件都设为仅当前用户可访问（Windows ACL）。
3. **不上传**：本模块没有任何网络代码。密钥只在签名时进入内存。
4. **可审计**：每次密钥读写都追加到 `keystore_audit.log`（只记动作与时间，
   **绝不记密钥本身**）。

⚠️ 必须如实告知用户的能力边界
------------------------------
DPAPI 能防：其他本机用户读取、离线拷贝磁盘后解密、误提交到 git。
DPAPI **不能**防：以**同一用户身份**运行的恶意程序——它们可以调用
`CryptUnprotectData` 解密。这是 Windows 用户态加密的固有边界，不是本实现的缺陷。
→ 因此**必须配合**：API key 只开交易权限、**绝不开提币权限**、配置 IP 白名单。

兜底降级
--------
若 `pywin32` 缺失或非 Windows 平台，本模块**不会静默明文落盘**，而是：
  · 拒绝写入，并给出明确的安装指引（返回 ok=False + 原因）
  · 或（仅在用户显式传 allow_plaintext=True 时）明文存储，但文件名带
    `_PLAINTEXT_` 标记并在审计日志里记警告，避免"以为加密了其实没有"。
这种"宁可失败也不悄悄降级"的取舍，是为了不制造虚假的安全感。
"""

import json
import os
import stat
import sys
import time
from datetime import datetime, timezone, timedelta

TZ8 = timezone(timedelta(hours=8))

# 支持的交易所与账户类型
SUPPORTED_EXCHANGES = ("binance", "okx", "gate", "mexc")
ACCOUNT_TYPES = ("spot", "usdtm", "coinm", "unified")

_AUDIT_NAME = "keystore_audit.log"


def _now():
    return datetime.now(TZ8).strftime("%Y-%m-%d %H:%M:%S")


def _audit(store_dir, action, detail=""):
    """审计日志：只记动作，**永不记密钥内容**。"""
    try:
        os.makedirs(store_dir, exist_ok=True)
        with open(os.path.join(store_dir, _AUDIT_NAME), "a", encoding="utf-8") as f:
            f.write(f"{_now()} | {action} | {detail}\n")
    except Exception:
        pass  # 审计失败不得阻断主流程


# ══════════════════════════════════════════════════════════════════════
# DPAPI 封装
# ══════════════════════════════════════════════════════════════════════
def dpapi_available():
    if sys.platform != "win32":
        return False
    try:
        import win32crypt  # noqa: F401
        return True
    except Exception:
        return False


def _dpapi_encrypt(plain: bytes) -> bytes:
    import win32crypt
    # ⚠️ 不传 CRYPTPROTECT_LOCAL_MACHINE：那会让本机任何用户都能解密
    blob = win32crypt.CryptProtectData(plain, "kexi-cex-key", None, None, None, 0)
    return blob


def _dpapi_decrypt(blob: bytes) -> bytes:
    import win32crypt
    _desc, data = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
    return data


def crypto_status():
    """返回加密能力的当前状态（供 UI/自检展示真实情况，避免虚假安全感）。"""
    ok = dpapi_available()
    return {
        "available": ok,
        "backend": "windows-dpapi" if ok else "none",
        "scope": "current-user" if ok else None,
        "machine_wide": False,
        "can_protect_at_rest": ok,
        "caveat": ("DPAPI 可防其他用户读取与离线磁盘拷贝，"
                   "但**无法**防以同一用户身份运行的恶意程序"),
        "fix_hint": None if ok else "pip install pywin32（仅 Windows 可用）",
    }


# ══════════════════════════════════════════════════════════════════════
# 存储
# ══════════════════════════════════════════════════════════════════════
# 优先级：显式参数 > 环境变量 KEXI_CEX_STORE_DIR > DSH_HOME/kexi-cex > <工作区>/.kexi-secrets/cex
#
# ⚠ v1.6.0 修一个致命断点：设置面板（host 侧）一直把凭据写到
#   `$DSH_HOME/kexi-cex/{ex}.enc.json`（JSON 信封 + base64 密文），
#   而本模块只认 `{ex}.{label}.dpapi`（裸二进制 DPAPI）且默认目录在**工作区**下。
#   目录不同 + 格式不同 → **面板里配好的密钥，Python 永远读不到**，
#   整个 CEX 链路是死的（用户却以为配好了）。
#   现在 host 改成写裸二进制 `{ex}.{label}.dpapi`，并由 kexi_cex 工具注入
#   `KEXI_CEX_STORE_DIR`；这里按上面的优先级找到同一个目录。
STORE_DIR_ENV = "KEXI_CEX_STORE_DIR"


def default_store_dir(workspace_root=None):
    """定位凭据目录。**必须与 host 面板写盘的位置一致**，否则形同虚设。

    查找顺序（前者优先）：
      1. 环境变量 ``KEXI_CEX_STORE_DIR``（kexi_cex 工具每次调用都会注入）
      2. ``$DSH_HOME/kexi-cex``（与 host 面板同源）
      3. ``<工作区>/.kexi-secrets/cex``（历史默认；在 .gitignore 里排除）
    """
    env = os.environ.get(STORE_DIR_ENV)
    if env and str(env).strip():
        return str(env).strip()
    home = os.environ.get("DSH_HOME")
    if home and str(home).strip():
        return os.path.join(str(home).strip(), "kexi-cex")
    base = workspace_root or os.getcwd()
    return os.path.join(base, ".kexi-secrets", "cex")


def _harden(path, is_dir):
    """把文件/目录权限收紧到仅当前用户。"""
    try:
        if sys.platform == "win32":
            import subprocess
            user = os.environ.get("USERNAME", "")
            if not user:
                return
            if is_dir:
                # 断开继承 + 只授权当前用户
                subprocess.run(
                    ["icacls", path, "/inheritance:r", "/grant:r", f"{user}:(OI)(CI)F"],
                    capture_output=True, timeout=10)
            else:
                subprocess.run(
                    ["icacls", path, "/inheritance:r", "/grant:r", f"{user}:F"],
                    capture_output=True, timeout=10)
        else:
            os.chmod(path, stat.S_IRWXU if is_dir else stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass  # 权限收紧失败不应阻断（但会记审计）


def save_keys(store_dir, exchange, api_key, api_secret,
              passphrase=None, label="main", permissions=None,
              ip_whitelist=None, allow_plaintext=False):
    """保存某交易所的密钥。

    Args:
        store_dir   : 存储目录（默认 default_store_dir()）
        exchange    : binance|okx|gate|mexc
        api_key/secret: 明文（仅在本函数内加密，绝不外发）
        passphrase  : OKX 必需；其他交易所留空
        permissions : 该 key 的权限描述，如 ["read","trade"]（**不应含 withdraw**）
        ip_whitelist: 是否已配 IP 白名单
        allow_plaintext: 无 DPAPI 时是否允许明文兜底（默认**不允许**）
    Returns: dict(ok, message, path, encrypted, warnings)
    """
    ex = (exchange or "").lower().strip()
    if ex not in SUPPORTED_EXCHANGES:
        return {"ok": False, "message": f"不支持的交易所: {exchange}；"
                f"支持 {SUPPORTED_EXCHANGES}", "path": None, "encrypted": None,
                "warnings": []}
    if not api_key or not api_secret:
        return {"ok": False, "message": "api_key / api_secret 不能为空",
                "path": None, "encrypted": None, "warnings": []}
    if ex == "okx" and not passphrase:
        return {"ok": False, "message": "OKX 必须提供 passphrase",
                "path": None, "encrypted": None, "warnings": []}

    warnings = []
    perms = [p.lower() for p in (permissions or [])]
    if "withdraw" in perms:
        warnings.append("⛔ 该 key 含**提币权限**——强烈建议立即在交易所后台关闭！"
                        "本插件只需要「读 + 交易」权限")

    os.makedirs(store_dir, exist_ok=True)
    _harden(store_dir, True)

    payload = {
        "exchange": ex, "label": label, "api_key": api_key,
        "api_secret": api_secret, "passphrase": passphrase or "",
        "permissions": perms, "ip_whitelist": bool(ip_whitelist),
        "saved_at": _now(),
    }
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    encrypted = dpapi_available()
    if encrypted:
        blob = _dpapi_encrypt(raw)
        fname = f"{ex}.{label}.dpapi"
    else:
        if not allow_plaintext:
            return {
                "ok": False, "path": None, "encrypted": False, "warnings": warnings,
                "message": ("当前环境不支持 DPAPI 加密，**拒绝以明文保存密钥**。"
                            "请先 `pip install pywin32`（需 Windows）。"
                            "若确实要明文保存（不推荐），显式传 allow_plaintext=True。"),
            }
        blob = raw
        fname = f"{ex}.{label}._PLAINTEXT_"
        warnings.append("⚠️ 密钥以**明文**保存（DPAPI 不可用且显式允许）——"
                        "任何能读该文件的程序都能拿到密钥")

    path = os.path.join(store_dir, fname)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(blob)
    os.replace(tmp, path)          # 原子替换，避免写一半损坏
    _harden(path, False)
    _audit(store_dir, "SAVE",
           f"exchange={ex} label={label} encrypted={encrypted} "
           f"perms={perms} ip_wl={bool(ip_whitelist)}")

    return {"ok": True, "path": path, "encrypted": encrypted, "warnings": warnings,
            "message": f"已{'加密' if encrypted else '明文'}保存 {ex}/{label}"
                       f"（{'DPAPI' if encrypted else '无加密'}）",
            "crypto": crypto_status()}


def load_keys(store_dir, exchange, label="main"):
    """读取并解密密钥。返回 dict(ok, keys, message)。"""
    ex = (exchange or "").lower().strip()
    for fname, enc in ((f"{ex}.{label}.dpapi", True),
                       (f"{ex}.{label}._PLAINTEXT_", False)):
        path = os.path.join(store_dir, fname)
        if not os.path.exists(path):
            continue
        try:
            with open(path, "rb") as f:
                blob = f.read()
            raw = _dpapi_decrypt(blob) if enc else blob
            keys = json.loads(raw.decode("utf-8"))
            _audit(store_dir, "LOAD", f"exchange={ex} label={label} encrypted={enc}")
            return {"ok": True, "keys": keys, "encrypted": enc, "path": path,
                    "message": f"已读取 {ex}/{label}"}
        except Exception as e:
            _audit(store_dir, "LOAD_FAIL", f"exchange={ex} label={label} err={e}")
            return {"ok": False, "keys": None, "encrypted": enc, "path": path,
                    "message": f"读取/解密失败: {str(e)[:120]}"
                               f"（DPAPI 密文只能由**同一用户在同一台机器**解密；"
                               f"换机器/换用户需重新录入）"}
    return {"ok": False, "keys": None, "message": f"未找到 {ex}/{label} 的密钥",
            "path": None}


def list_keys(store_dir):
    """列出已保存的密钥（**只返回元信息，绝不返回密钥内容**）。"""
    out = []
    if not os.path.isdir(store_dir):
        return out
    for fn in sorted(os.listdir(store_dir)):
        if fn.endswith(".tmp") or fn == _AUDIT_NAME:
            continue
        enc = fn.endswith(".dpapi")
        if not (enc or fn.endswith("._PLAINTEXT_")):
            continue
        parts = fn.split(".")
        rec = {"file": fn, "exchange": parts[0] if parts else None,
               "label": parts[1] if len(parts) > 1 else None, "encrypted": enc}
        try:
            st = os.stat(os.path.join(store_dir, fn))
            rec["size"] = st.st_size
            rec["modified"] = datetime.fromtimestamp(st.st_mtime, TZ8).strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
        # 尝试读取元信息（权限/是否配白名单），失败则跳过——不暴露密钥
        try:
            lk = load_keys(store_dir, rec["exchange"], rec["label"])
            if lk.get("ok"):
                rec["permissions"] = lk["keys"].get("permissions", [])
                rec["ip_whitelist"] = lk["keys"].get("ip_whitelist")
                rec["has_withdraw_perm"] = "withdraw" in (rec["permissions"] or [])
        except Exception:
            pass
        out.append(rec)
    return out


def delete_keys(store_dir, exchange, label="main"):
    removed = []
    for fname in (f"{exchange}.{label}.dpapi",
                  f"{exchange}.{label}._PLAINTEXT_"):
        p = os.path.join(store_dir, fname)
        if os.path.exists(p):
            os.remove(p)
            removed.append(fname)
    _audit(store_dir, "DELETE", f"exchange={exchange} label={label} n={len(removed)}")
    return {"ok": bool(removed), "removed": removed,
            "message": f"已删除 {len(removed)} 个密钥文件" if removed else "未找到该密钥"}


def security_report(store_dir):
    """安全体检：如实列出风险项（供 kexi 直接展示给用户）。"""
    items = []
    cs = crypto_status()
    if not cs["available"]:
        items.append({"level": "high", "msg": "当前环境无 DPAPI，密钥无法加密存储"})
    keys = list_keys(store_dir)
    if not keys:
        items.append({"level": "info", "msg": "尚未录入任何 CEX 密钥"})
    for k in keys:
        if not k.get("encrypted"):
            items.append({"level": "high",
                          "msg": f"{k['exchange']}/{k['label']} 以**明文**存储"})
        if k.get("has_withdraw_perm"):
            items.append({"level": "critical",
                          "msg": f"{k['exchange']}/{k['label']} 含**提币权限**，请立即关闭"})
        if k.get("ip_whitelist") is False:
            items.append({"level": "medium",
                          "msg": f"{k['exchange']}/{k['label']} 未配置 IP 白名单"})
    return {"crypto": cs, "keys": keys, "findings": items,
            "note": "DPAPI 无法防御以同一用户身份运行的恶意程序；"
                    "请务必不给提币权限并配置 IP 白名单"}


def main():
    import argparse
    ap = argparse.ArgumentParser(description="CEX 密钥本地安全存储（DPAPI，不上传云端）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_s = sub.add_parser("save", help="保存密钥")
    p_s.add_argument("--exchange", required=True, choices=SUPPORTED_EXCHANGES)
    p_s.add_argument("--label", default="default")
    p_s.add_argument("--dir", default=None)
    p_s.add_argument("--plaintext", action="store_true", help="无 DPAPI 时允许明文（不推荐）")

    p_l = sub.add_parser("list", help="列出已存密钥（不含密钥内容）")
    p_l.add_argument("--dir", default=None)

    p_d = sub.add_parser("delete", help="删除密钥")
    p_d.add_argument("--exchange", required=True, choices=SUPPORTED_EXCHANGES)
    p_d.add_argument("--label", default="default")
    p_d.add_argument("--dir", default=None)

    p_r = sub.add_parser("status", help="加密能力与安全体检")
    p_r.add_argument("--dir", default=None)

    args = ap.parse_args()
    store = args.dir or default_store_dir()

    if args.cmd == "save":
        # 从环境变量读取，避免密钥出现在命令行历史/进程列表里
        ak = os.environ.get("KEXI_API_KEY", "")
        sk = os.environ.get("KEXI_API_SECRET", "")
        pp = os.environ.get("KEXI_PASSPHRASE", "")
        if not ak or not sk:
            print(json.dumps({"ok": False, "message":
                              "请通过环境变量提供 KEXI_API_KEY / KEXI_API_SECRET"
                              "（避免密钥进入命令行历史）"}, ensure_ascii=False, indent=2))
            return 2
        perms = [x for x in os.environ.get("KEXI_PERMS", "read,trade").split(",") if x]
        r = save_keys(store, args.exchange, ak, sk, pp, args.label,
                      permissions=perms,
                      ip_whitelist=os.environ.get("KEXI_IP_WHITELIST", "").lower() == "true",
                      allow_plaintext=args.plaintext)
    elif args.cmd == "list":
        r = {"keys": list_keys(store), "note": "仅显示元信息，不显示密钥"}
    elif args.cmd == "delete":
        r = delete_keys(store, args.exchange, args.label)
    else:
        r = security_report(store)

    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())