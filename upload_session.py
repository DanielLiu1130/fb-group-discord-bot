import argparse
import json
import os
import sys
from pathlib import Path

if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

import config
from cf_kv import CloudflareKV

BASE_DIR = Path(__file__).resolve().parent
CF_DEPLOY_ENV_FILE = BASE_DIR / "cf_deploy.env"
SESSION_FILE = BASE_DIR / "fb_session.json"
COOKIES_FILE = BASE_DIR / "cookies.json"
SUBSCRIPTIONS_FILE = BASE_DIR / "subscriptions.json"
NOTIFIED_POSTS_FILE = BASE_DIR / "notified_posts.json"


def validate_session_file(session_path: Path):
    """確認 fb_session.json 格式正確且包含 c_user 與 xs Cookie。"""
    if not session_path.exists():
        return False, f"找不到 {session_path.name}。"

    try:
        data = json.loads(session_path.read_text(encoding="utf-8"))
    except Exception as e:
        return False, f"無法解析 {session_path.name}：{e}"

    cookies = data.get("cookies", [])
    if not isinstance(cookies, list):
        return False, f"{session_path.name} 的 cookies 欄位格式異常。"

    names = {c.get("name") for c in cookies if isinstance(c, dict)}
    missing = [req for req in ("c_user", "xs") if req not in names]
    if missing:
        return False, f"{session_path.name} 缺少必要登入憑證：{', '.join(missing)}"

    return True, f"{session_path.name} 驗證通過（共 {len(cookies)} 個 Cookie，含 c_user 與 xs）。"


def ensure_session_ready() -> bool:
    """若存在 cookies.json 則先轉換成 fb_session.json 並刪除暫存檔，再驗證憑證完整性。"""
    os.chdir(BASE_DIR)

    if COOKIES_FILE.exists():
        print("🔄 偵測到 cookies.json，正在轉換為 fb_session.json...")
        import import_cookies

        if not import_cookies.convert_cookie_editor_to_playwright(
            str(COOKIES_FILE), str(SESSION_FILE)
        ):
            return False

        ok, msg = validate_session_file(SESSION_FILE)
        if not ok:
            print(f"❌ 轉換後的憑證不完整：{msg}")
            print("⚠️ 為避免遺失資料，暫時保留 cookies.json，請重新匯出完整的 Facebook Cookie。")
            return False

        try:
            COOKIES_FILE.unlink()
            print("🧹 已安全刪除暫存的 cookies.json。")
        except OSError as e:
            print(f"⚠️ 無法刪除 cookies.json（請手動刪除）：{e}")
        print(f"✅ {msg}")
        return True

    ok, msg = validate_session_file(SESSION_FILE)
    if ok:
        print(f"✅ {msg}")
        return True

    print(f"❌ {msg}")
    print("請先用 Cookie-Editor 匯出 cookies.json 放到專案目錄，或執行切換FB帳號.bat 產生 fb_session.json。")
    return False


def _load_env_file(path: Path) -> dict:
    values = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def _save_cf_deploy_env(cfg: dict):
    content = "\n".join(
        [
            "# 本機 Cloudflare KV 上傳設定（已被 .gitignore 排除）",
            f"CF_ACCOUNT_ID={cfg['CF_ACCOUNT_ID']}",
            f"CF_KV_NAMESPACE_ID={cfg['CF_KV_NAMESPACE_ID']}",
            f"CF_API_TOKEN={cfg['CF_API_TOKEN']}",
            "",
        ]
    )
    CF_DEPLOY_ENV_FILE.write_text(content, encoding="utf-8")


def ensure_cf_config() -> dict:
    """從 cf_deploy.env（或環境變數）讀取 Cloudflare KV 設定，不足時互動引導並存檔。"""
    cfg = _load_env_file(CF_DEPLOY_ENV_FILE)
    for key in ("CF_ACCOUNT_ID", "CF_KV_NAMESPACE_ID", "CF_API_TOKEN"):
        if not cfg.get(key) and os.getenv(key, "").strip():
            cfg[key] = os.getenv(key, "").strip()

    if all(cfg.get(k) for k in ("CF_ACCOUNT_ID", "CF_KV_NAMESPACE_ID", "CF_API_TOKEN")):
        return cfg

    print("\n" + "=" * 65)
    print("🔧 尚未設定 Cloudflare KV 連線資訊，請依提示輸入（設定後會存入 cf_deploy.env）：")
    print("=" * 65)

    account_id = cfg.get("CF_ACCOUNT_ID") or input("1. Cloudflare Account ID: ").strip()
    ns_id = cfg.get("CF_KV_NAMESPACE_ID") or input("2. Cloudflare KV Namespace ID: ").strip()
    api_token = cfg.get("CF_API_TOKEN") or input("3. Cloudflare API Token (Workers KV Storage: Edit): ").strip()

    cfg = {
        "CF_ACCOUNT_ID": account_id,
        "CF_KV_NAMESPACE_ID": ns_id,
        "CF_API_TOKEN": api_token,
    }
    _save_cf_deploy_env(cfg)
    print(f"💾 已儲存連線設定到 {CF_DEPLOY_ENV_FILE.name}。\n")
    return cfg


def _normalize_subscriptions_for_kv(raw: dict) -> dict:
    """將 channel_id 轉為字串，確保 Cloudflare Worker JS 不會遺失 64 位元精確度。"""
    channel_id = raw.get("channel_id")
    users = raw.get("users", {})
    cleaned_users = {}
    if isinstance(users, dict):
        for uid, info in users.items():
            if not isinstance(info, dict):
                continue
            subs = []
            for item in info.get("subs", []):
                kw = str(item.get("keyword", "") if isinstance(item, dict) else item).strip()
                if kw and kw not in subs:
                    subs.append(kw)
            cleaned_users[str(uid)] = {
                "name": str(info.get("name", "成員")),
                "subs": subs,
            }
    return {
        "channel_id": str(channel_id) if channel_id else None,
        "users": cleaned_users,
    }


def upload_to_kv(cfg: dict, with_data: bool) -> bool:
    """透過 HTTPS 將 fb_session.json（與選填本機訂閱資料）上傳到 Cloudflare KV。"""
    try:
        kv = CloudflareKV(
            account_id=cfg["CF_ACCOUNT_ID"],
            namespace_id=cfg["CF_KV_NAMESPACE_ID"],
            api_token=cfg["CF_API_TOKEN"],
        )

        session_data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        print("📤 正在加密上傳 fb_session 到 Cloudflare KV...")
        kv.put_json("fb_session", session_data)
        print("✅ 已更新雲端 KV 的 fb_session。")

        existing_auth = kv.get_json("auth_state", default={})
        if not isinstance(existing_auth, dict):
            existing_auth = {}
        existing_auth["alert_active"] = False
        existing_auth["last_alert_ts"] = 0.0
        kv.put_json("auth_state", existing_auth)

        if with_data:
            if SUBSCRIPTIONS_FILE.exists():
                subs_raw = json.loads(SUBSCRIPTIONS_FILE.read_text(encoding="utf-8"))
                subs_norm = _normalize_subscriptions_for_kv(subs_raw)
                kv.put_json("subscriptions", subs_norm)
                print("✅ 已同步本機 subscriptions.json 到雲端 KV。")
            else:
                print("ℹ️ 找不到 subscriptions.json，略過同步。")

            if NOTIFIED_POSTS_FILE.exists():
                seen_raw = json.loads(NOTIFIED_POSTS_FILE.read_text(encoding="utf-8"))
                if isinstance(seen_raw, list):
                    seen_clean = list(dict.fromkeys(str(x) for x in seen_raw if x))[-config.MAX_SEEN_POSTS:]
                    kv.put_json("notified_posts", seen_clean)
                    print(f"✅ 已同步 {len(seen_clean)} 筆已通知貼文紀錄到雲端 KV。")
            else:
                print("ℹ️ 找不到 notified_posts.json，略過同步。")

        print("\n🎉 上傳完成！雲端機器人將於下次自動巡邏（或執行 /立即巡邏）立刻套用新憑證。")
        return True
    except Exception as e:
        print(f"❌ 上傳到 Cloudflare KV 失敗：{e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="驗證並透過 HTTPS 安全上傳 Facebook 登入憑證到 Cloudflare KV")
    parser.add_argument("--check-only", action="store_true", help="僅在本機驗證 fb_session.json 是否有效，不進行上傳")
    parser.add_argument("--with-data", action="store_true", help="一併上傳本機 subscriptions.json 與 notified_posts.json")
    args = parser.parse_args()

    if not ensure_session_ready():
        sys.exit(1)

    if args.check_only:
        sys.exit(0)

    cfg = ensure_cf_config()
    if not upload_to_kv(cfg, with_data=args.with_data):
        sys.exit(1)


if __name__ == "__main__":
    main()
