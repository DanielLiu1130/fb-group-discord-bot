import datetime
import json
import os
import random
import sys
import time
import requests

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

import config
import scraper
from cf_kv import CloudflareKV

DISCORD_API_BASE = "https://discord.com/api/v10"


def _normalize_subscriptions(raw) -> dict:
    """正規化訂閱資料結構，確保與本地 subscriptions.json 及 Worker KV 格式一致。"""
    if not isinstance(raw, dict):
        return {"channel_id": None, "users": {}}

    channel_id = raw.get("channel_id")
    users = raw.get("users", {})
    if not isinstance(users, dict):
        users = {}

    cleaned_users = {}
    for uid, u_info in users.items():
        if not isinstance(u_info, dict):
            continue
        subs = []
        for item in u_info.get("subs", []):
            if isinstance(item, dict):
                kw = str(item.get("keyword", "")).strip()
            else:
                kw = str(item).strip()
            if kw and kw not in subs:
                subs.append(kw)
        cleaned_users[str(uid)] = {
            "name": str(u_info.get("name", "成員")),
            "subs": subs,
        }

    return {
        "channel_id": str(channel_id) if channel_id else None,
        "users": cleaned_users,
    }


def _send_discord_message(channel_id: str, payload: dict) -> bool:
    """透過 Discord Bot REST API 發送訊息，遇限流 (429) 自動退避重試，絕不印出敏感內容。"""
    url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages"
    headers = {
        "Authorization": f"Bot {config.DISCORD_BOT_TOKEN}",
        "Content-Type": "application/json",
    }

    for attempt in range(3):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=20)
            if 200 <= resp.status_code < 300:
                return True
            if resp.status_code == 429 and attempt < 2:
                try:
                    retry_after = float(resp.json().get("retry_after", 2.0))
                except Exception:
                    retry_after = 2.0
                time.sleep(min(max(retry_after, 0.5), 10.0) + 0.25)
                continue
            print(f"[Discord 通知失敗] HTTP 狀態碼：{resp.status_code}", flush=True)
            return False
        except Exception as e:
            print(f"[Discord 連線異常] {type(e).__name__}", flush=True)
            if attempt < 2:
                time.sleep(1.5)
    return False


def _build_auth_warning_payload(reason: str) -> dict:
    """建立 Facebook 憑證失效 Discord 警告訊息。"""
    embed = {
        "title": "⚠️ 【Facebook 登入憑證需要更新】",
        "color": 0xE74C3C,
        "description": (
            "雲端爬蟲嘗試讀取 Facebook 社團時遭遇驗證失敗，目前無法抓取新貼文。\n\n"
            f"**原因：** {reason}\n\n"
            "**處理方式：**\n"
            "1. 在自己電腦用瀏覽器（建議無痕視窗）登入該 Facebook 帳號。\n"
            "2. 使用 Cookie-Editor 匯出 JSON 並存成本機專案內的 `cookies.json`。\n"
            "3. 雙擊本機專案內的 `upload_session.bat`，即可自動轉換並上傳到 Cloudflare KV。\n"
            "4. 上傳完成後下次自動巡邏（或執行 `/立即巡邏`）就會自動恢復。"
        ),
        "footer": {"text": "清交二手拍小幫手 • 憑證異常通知"},
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    return {"embeds": [embed]}


def _build_auth_recovery_payload(message: str) -> dict:
    """建立 Facebook 憑證恢復正常 Discord 通知訊息。"""
    embed = {
        "title": "✅ 【Facebook 登入憑證已恢復正常】",
        "color": 0x2ECC71,
        "description": f"{message}\n社團監控已恢復自動巡邏。",
        "footer": {"text": "清交二手拍小幫手 • 狀態恢復通知"},
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    return {"embeds": [embed]}


def _build_post_payload(post: dict, matched_users: list) -> dict:
    """建立符合關鍵字的新商品通知卡片（樣式與 bot.py 一致）。"""
    mentions_text = " ".join([f"<@{uid}>" for uid in matched_users])
    display_desc = post["text"][:4000]
    author = post.get("author", "社團賣家")
    post_url = post["url"]
    post_images = post.get("images", [])

    title = (
        "🔔 【FB 二手社團新商品】"
        if author == "社團賣家"
        else f"🔔 【FB 二手社團新商品】（賣家：{author}）"
    )

    embed = {
        "title": title,
        "url": post_url,
        "color": 0x00FF88,
        "description": display_desc,
        "fields": [
            {"name": "👥 關注成員", "value": mentions_text, "inline": False},
            {
                "name": "🔗 貼文直達連結",
                "value": f"[點我前往 Facebook 查看完整貼文與相簿]({post_url})",
                "inline": False,
            },
        ],
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    if post_images:
        embed["image"] = {"url": post_images[0]}
        if len(post_images) > 1:
            embed["footer"] = {
                "text": f"清交二手拍小幫手 • 📸 本貼文共包含 {len(post_images)} 張照片（點上方連結看全部照片）"
            }
        else:
            embed["footer"] = {"text": "清交二手拍小幫手"}
    else:
        embed["footer"] = {"text": "清交二手拍小幫手"}

    return {
        "content": f"🚨 呼叫 {mentions_text}！社團出現你關注的物品囉：",
        "embeds": [embed],
    }


def main():
    if not config.DISCORD_BOT_TOKEN:
        print("[錯誤] 缺少 DISCORD_BOT_TOKEN 環境變數。", flush=True)
        sys.exit(1)

    trigger_source = (os.getenv("TRIGGER_SOURCE", "manual").strip().lower() or "manual")
    print(f"[雲端巡邏] 啟動單次巡邏（觸發來源：{trigger_source}）", flush=True)

    if trigger_source == "schedule" and config.CLOUD_JITTER_MAX_SECONDS > 0:
        delay = random.randint(0, config.CLOUD_JITTER_MAX_SECONDS)
        print(f"[排程抖動] 隨機等待 {delay} 秒後開始巡邏（模擬真人不定時瀏覽）。", flush=True)
        time.sleep(delay)

    kv = CloudflareKV.from_env()
    subs_data = _normalize_subscriptions(kv.get_json("subscriptions"))
    channel_id = subs_data.get("channel_id")

    if not channel_id:
        print("[提示] 尚未在 Discord 執行 /設定通知頻道，略過本輪推播。", flush=True)
        return

    raw_seen = kv.get_json("notified_posts", default=[])
    if not isinstance(raw_seen, list):
        raw_seen = []
    seen_posts_list = list(dict.fromkeys(str(item) for item in raw_seen if item))
    seen_posts_set = set(seen_posts_list)

    auth_state = kv.get_json("auth_state", default={})
    if not isinstance(auth_state, dict):
        auth_state = {}
    initial_relogin_ts = float(auth_state.get("last_relogin_ts", 0.0) or 0.0)
    scraper.set_last_relogin_ts(initial_relogin_ts)

    fb_session = kv.get_json("fb_session", default=None)
    is_gh_actions = os.getenv("GITHUB_ACTIONS", "").lower() == "true"

    crawl_result = None
    updated_session_to_save = None

    try:
        if isinstance(fb_session, dict) and fb_session.get("cookies"):
            with open(config.SESSION_FILE, "w", encoding="utf-8") as f:
                json.dump(fb_session, f, ensure_ascii=False)
            if os.name != "nt":
                try:
                    os.chmod(config.SESSION_FILE, 0o600)
                except OSError:
                    pass
        elif is_gh_actions and os.path.exists(config.SESSION_FILE):
            try:
                os.remove(config.SESSION_FILE)
            except OSError:
                pass

        crawl_result = scraper.scrape_recent_posts_with_status()

        if crawl_result.get("status") == "ok_relogged" and os.path.exists(config.SESSION_FILE):
            try:
                with open(config.SESSION_FILE, "r", encoding="utf-8") as f:
                    updated_session_to_save = json.load(f)
            except Exception as e:
                print(f"[警告] 讀取重登後的 session 失敗：{type(e).__name__}", flush=True)
    finally:
        if is_gh_actions and os.path.exists(config.SESSION_FILE):
            try:
                os.remove(config.SESSION_FILE)
            except OSError:
                pass

    auth_state_changed = False
    new_relogin_ts = scraper.get_last_relogin_ts()
    if new_relogin_ts != initial_relogin_ts:
        auth_state["last_relogin_ts"] = new_relogin_ts
        auth_state_changed = True

    if updated_session_to_save:
        kv.put_json("fb_session", updated_session_to_save)
        print("[雲端同步] 已將自動重登後的新 FB 憑證回寫 Cloudflare KV。", flush=True)

    status = crawl_result.get("status", "error")
    message = crawl_result.get("message", "")
    now = time.time()

    if status in ("session_missing", "auth_expired", "auth_failed"):
        alert_active = bool(auth_state.get("alert_active", False))
        last_alert_ts = float(auth_state.get("last_alert_ts", 0.0) or 0.0)
        cooldown_sec = config.AUTH_ALERT_COOLDOWN_MINUTES * 60

        should_alert = (
            trigger_source == "discord"
            or last_alert_ts == 0.0
            or (now - last_alert_ts) >= cooldown_sec
        )
        if should_alert:
            if _send_discord_message(channel_id, _build_auth_warning_payload(message)):
                auth_state["last_alert_ts"] = now
                auth_state["alert_active"] = True
                auth_state_changed = True
        elif not alert_active:
            auth_state["alert_active"] = True
            auth_state_changed = True

        if auth_state_changed:
            kv.put_json("auth_state", auth_state)

        print(f"[巡邏結果] status={status}（憑證異常，已處理通知狀態）", flush=True)
        return

    if status == "error":
        if auth_state_changed:
            kv.put_json("auth_state", auth_state)
        print("[巡邏結果] status=error（發生未預期錯誤）", flush=True)
        sys.exit(1)

    if status == "ok_relogged" or auth_state.get("alert_active", False):
        recovery_msg = message if status == "ok_relogged" else "已重新驗證並成功讀取社團貼文。"
        _send_discord_message(channel_id, _build_auth_recovery_payload(recovery_msg))
        auth_state["alert_active"] = False
        auth_state["last_alert_ts"] = 0.0
        auth_state_changed = True

    posts = crawl_result.get("posts", [])
    users_data = subs_data.get("users", {})
    seen_changed = False
    new_seen_count = 0
    match_count = 0

    for post in posts:
        post_id = str(post.get("post_id", ""))
        if not post_id or post_id in seen_posts_set:
            continue

        post_text_lower = post.get("text", "").lower()
        matched_users = []
        for uid, u_info in users_data.items():
            for kw in u_info.get("subs", []):
                if kw.lower() in post_text_lower:
                    matched_users.append(uid)
                    break

        if matched_users:
            if _send_discord_message(channel_id, _build_post_payload(post, matched_users)):
                match_count += 1
            time.sleep(0.8)

        seen_posts_set.add(post_id)
        seen_posts_list.append(post_id)
        new_seen_count += 1
        seen_changed = True

    if seen_changed:
        kv.put_json("notified_posts", seen_posts_list[-config.MAX_SEEN_POSTS:])

    if auth_state_changed:
        kv.put_json("auth_state", auth_state)

    print(
        f"[巡邏完成] 共解析 {len(posts)} 篇貼文，新增紀錄 {new_seen_count} 筆，觸發 {match_count} 則通知。",
        flush=True,
    )


if __name__ == "__main__":
    main()
