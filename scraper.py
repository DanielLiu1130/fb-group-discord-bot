import hashlib
import os
import random
import re
import time
from playwright.sync_api import sync_playwright

import config

_CHROMIUM_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
]

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_AUTH_URL_MARKERS = (
    "/login",
    "/checkpoint",
    "/two_step_verification",
    "/recover",
    "login.php",
)

_BLOCKED_RELOGIN_MARKERS = (
    "two_step_verification",
    "checkpoint",
    "recover",
    "captcha",
)

_last_relogin_attempt_ts = 0.0


def get_last_relogin_ts() -> float:
    """取得最近一次嘗試自動重登的時間戳記（供無狀態雲端環境同步冷卻時間）。"""
    return _last_relogin_attempt_ts


def set_last_relogin_ts(ts: float):
    """載入外部儲存的自動重登冷卻時間戳記。"""
    global _last_relogin_attempt_ts
    try:
        _last_relogin_attempt_ts = max(0.0, float(ts or 0.0))
    except (TypeError, ValueError):
        _last_relogin_attempt_ts = 0.0


def _restrict_file_permissions(path: str):
    """在 Linux/macOS 環境將憑證檔案權限設為僅擁有者可讀寫 (0600)。"""
    if os.name != "nt" and os.path.exists(path):
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def _has_valid_auth_cookies(context) -> bool:
    """檢查瀏覽器 context 是否包含完整的 Facebook 登入 Cookie (c_user 與 xs)。"""
    try:
        cookies = context.cookies("https://www.facebook.com")
        names = {c.get("name") for c in cookies}
        return {"c_user", "xs"}.issubset(names)
    except Exception:
        return False


def _is_auth_required(page, context) -> bool:
    """偵測目前頁面是否已被導向登入頁、安全檢查，或失去 Facebook 登入憑證。"""
    current_url = (page.url or "").lower()
    if any(marker in current_url for marker in _AUTH_URL_MARKERS):
        return True

    if not _has_valid_auth_cookies(context):
        return True

    feed = page.query_selector("div[role='feed']")
    if not feed:
        email_input = page.query_selector("input[name='email']")
        pass_input = page.query_selector("input[name='pass']")
        if email_input and pass_input:
            return True

    return False


def _extract_post_from_card(card, clean_url: str):
    """從單一社團貼文卡片擷取正文、賣家、專屬連結與圖片。"""
    post_message = ""
    msg_elem = card.query_selector(
        "div[data-ad-preview='message'], div[data-ad-rendering-role='story_message']"
    )
    if msg_elem:
        post_message = msg_elem.inner_text().strip()
    else:
        autos = card.query_selector_all("div[dir='auto']")
        candidates = []
        for a in autos:
            atxt = a.inner_text().strip()
            if (
                len(atxt) > 5
                and not atxt.startswith("http")
                and "Facebook" not in atxt
                and "Comment as" not in atxt
            ):
                candidates.append(atxt)
        if candidates:
            post_message = max(candidates, key=len)

    if not post_message:
        return None

    author = "社團賣家"
    author_elem = card.query_selector("h2 a, h3 a, strong a")
    if author_elem:
        a_txt = author_elem.inner_text().strip()
        if a_txt and "Facebook" not in a_txt:
            author = a_txt

    if any(exc in post_message for exc in config.DEFAULT_EXCLUDE_KEYWORDS):
        return None

    post_id = None
    post_url = None
    for link in card.query_selector_all("a"):
        href = link.get_attribute("href")
        if not href:
            continue

        for pattern in (
            r"/posts/(\d+)",
            r"set=pcb\.(\d+)",
            r"/permalink/(\d+)",
            r"multi_permalinks=(\d+)",
        ):
            match = re.search(pattern, href)
            if match:
                post_id = match.group(1)
                post_url = f"{clean_url}/posts/{post_id}/"
                break
        if post_id:
            break

    if not post_id:
        post_hash = hashlib.md5(post_message[:80].encode("utf-8")).hexdigest()[:12]
        post_id = f"hash_{post_hash}"
        post_url = clean_url

    images = []
    for img in card.query_selector_all("img"):
        src = img.get_attribute("src")
        if (
            src
            and ("scontent" in src or "fbcdn.net" in src)
            and "emoji.php" not in src
            and "rsrc.php" not in src
        ):
            box = img.bounding_box()
            if (box and box["width"] > 80 and box["height"] > 80) or not box:
                images.append(src)

    return {
        "post_id": str(post_id),
        "url": post_url,
        "author": author,
        "text": post_message,
        "images": images,
    }


def _try_auto_relogin():
    """
    若 .env 有設定 FB_EMAIL / FB_PASSWORD 且已過冷卻時間，嘗試自動無頭重登一次。
    遇到 2FA、安全檢查或驗證碼時立即停止，絕不嘗試繞過，並回報需手動更新憑證。
    """
    global _last_relogin_attempt_ts

    if not config.FB_EMAIL or not config.FB_PASSWORD:
        return (
            False,
            "未在 .env 設定自動重登帳密（FB_EMAIL / FB_PASSWORD），請手動更新 fb_session.json。",
        )

    now = time.time()
    cooldown_sec = config.AUTO_RELOGIN_COOLDOWN_MINUTES * 60
    if _last_relogin_attempt_ts > 0 and (now - _last_relogin_attempt_ts) < cooldown_sec:
        remain_mins = max(1, int((cooldown_sec - (now - _last_relogin_attempt_ts)) // 60))
        return (
            False,
            f"自動重登尚在冷卻時間內（約剩 {remain_mins} 分鐘），為保護帳號安全暫不重試，請手動更新憑證。",
        )

    _last_relogin_attempt_ts = now
    print("[自動重登] 偵測到 Facebook 登入憑證失效，嘗試自動重新登入一次...", flush=True)

    with sync_playwright() as p:
        browser = None
        try:
            browser = p.chromium.launch(headless=True, args=_CHROMIUM_ARGS)
            context = browser.new_context(user_agent=_USER_AGENT, locale="zh-TW")
            page = context.new_page()

            page.goto("https://www.facebook.com/login", wait_until="domcontentloaded", timeout=45000)

            try:
                page.wait_for_selector("input[name='email']", timeout=15000)
            except Exception:
                return False, "無法載入 Facebook 登入表單，需手動更新憑證。"

            page.fill("input[name='email']", config.FB_EMAIL)
            page.fill("input[name='pass']", config.FB_PASSWORD)

            login_btn = page.query_selector("button[name='login'], button[type='submit']")
            if login_btn:
                login_btn.click()
            else:
                page.keyboard.press("Enter")

            time.sleep(8)
            try:
                page.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass

            current_url = (page.url or "").lower()
            if any(marker in current_url for marker in _BLOCKED_RELOGIN_MARKERS):
                print("[自動重登] 遇到兩步驟驗證或安全檢查，已停止並等待人工更新。", flush=True)
                return False, "遇到 Facebook 兩步驟驗證或安全檢查，需手動更新憑證。"

            if "/login" in current_url or "login.php" in current_url or not _has_valid_auth_cookies(context):
                print("[自動重登] 登入驗證未通過，需手動更新憑證。", flush=True)
                return False, "自動重登未通過登入驗證，需手動更新憑證。"

            context.storage_state(path=config.SESSION_FILE)
            _restrict_file_permissions(config.SESSION_FILE)
            print(f"[自動重登] 成功重新登入並更新 {config.SESSION_FILE}！", flush=True)
            return True, f"已自動重新登入並更新 {config.SESSION_FILE}。"
        except Exception as e:
            print(f"[自動重登] 執行過程發生異常：{e}", flush=True)
            return False, f"自動重登過程發生異常（{e}），需手動更新憑證。"
        finally:
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass


def _scrape_once(session_file: str):
    """使用指定 session 檔執行單次無頭爬取，回傳 (status, posts, message)。"""
    collected_posts = []

    with sync_playwright() as p:
        browser = None
        try:
            browser = p.chromium.launch(headless=True, args=_CHROMIUM_ARGS)
            try:
                context = browser.new_context(
                    storage_state=session_file,
                    user_agent=_USER_AGENT,
                    locale="zh-TW",
                )
            except Exception as e:
                return "session_missing", [], f"無法讀取 {session_file}（檔案可能損毀）：{e}"

            page = context.new_page()
            total_groups = len(config.TARGET_GROUPS)

            for idx, group_url in enumerate(config.TARGET_GROUPS, 1):
                group_match = re.search(r"groups/([^/?]+)", group_url)
                group_id = group_match.group(1) if group_match else "unknown"
                clean_url = f"https://www.facebook.com/groups/{group_id}"
                target_url = f"{clean_url}?sorting_setting=CHRONOLOGICAL"

                print(f"[爬蟲巡邏] 正在掃描第 {idx}/{total_groups} 個目標社團...", flush=True)
                try:
                    page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
                except Exception as e:
                    print(f"[爬蟲提示] 頁面載入提醒：{e}", flush=True)

                time.sleep(3)

                if _is_auth_required(page, context):
                    print("[爬蟲警告] Facebook 登入憑證已過期或被要求重新驗證。", flush=True)
                    return "auth_expired", [], "Facebook 登入憑證已失效或被要求重新驗證。"

                for _ in range(config.SCROLL_COUNT):
                    page.evaluate("window.scrollBy(0, 1200)")
                    time.sleep(random.uniform(2.0, 3.0))

                try:
                    page.wait_for_selector("div[role='feed']", timeout=15000)
                except Exception:
                    pass

                if _is_auth_required(page, context):
                    print("[爬蟲警告] 頁面捲動後偵測到登入要求，憑證可能已失效。", flush=True)
                    return "auth_expired", [], "Facebook 登入憑證已失效或被要求重新驗證。"

                feed = page.query_selector("div[role='feed']") or page.query_selector("div[role='main']")
                if not feed:
                    print(f"[爬蟲警告] 第 {idx} 個社團未找到動態 feed。", flush=True)
                    continue

                for card in feed.query_selector_all(":scope > div"):
                    try:
                        post_data = _extract_post_from_card(card, clean_url)
                        if post_data:
                            collected_posts.append(post_data)
                    except Exception:
                        continue

                time.sleep(random.uniform(2.0, 3.5))
        except Exception as e:
            print(f"[爬蟲錯誤] 爬取過程發生異常：{e}", flush=True)
            return "error", [], f"爬取過程發生異常：{e}"
        finally:
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass

    print(f"[爬蟲完成] 本輪共解析出 {len(collected_posts)} 篇有效貼文。", flush=True)
    return "ok", collected_posts, f"本輪共解析出 {len(collected_posts)} 篇有效貼文。"


def scrape_recent_posts_with_status():
    """
    爬取 FB 社團最新貼文並回傳完整狀態字典：
    {"status": "ok" | "ok_relogged" | "session_missing" | "auth_expired" | "auth_failed" | "error",
     "posts": [...],
     "message": "..."}
    """
    session_file = config.SESSION_FILE

    if not config.TARGET_GROUPS:
        print("[錯誤] 未設定 TARGET_GROUPS 環境變數，無法執行爬取。", flush=True)
        return {
            "status": "error",
            "posts": [],
            "message": "未設定 TARGET_GROUPS 環境變數",
        }

    if not os.path.exists(session_file):
        print(f"[錯誤] 找不到 {session_file}！請先確認登入憑證存在。", flush=True)
        if config.FB_EMAIL and config.FB_PASSWORD:
            ok, relogin_msg = _try_auto_relogin()
            if ok:
                retry_status, retry_posts, retry_msg = _scrape_once(session_file)
                if retry_status == "ok":
                    return {"status": "ok_relogged", "posts": retry_posts, "message": relogin_msg}
                return {"status": "auth_failed", "posts": [], "message": f"自動重登後讀取仍失敗：{retry_msg}"}
            return {"status": "auth_failed", "posts": [], "message": relogin_msg}

        return {
            "status": "session_missing",
            "posts": [],
            "message": f"找不到 {session_file}，請執行 upload_session.bat 上傳新的 FB 登入憑證。",
        }

    status, posts, msg = _scrape_once(session_file)
    if status == "ok":
        return {"status": "ok", "posts": posts, "message": msg}

    if status in ("auth_expired", "session_missing"):
        if config.FB_EMAIL and config.FB_PASSWORD:
            ok, relogin_msg = _try_auto_relogin()
            if ok:
                retry_status, retry_posts, retry_msg = _scrape_once(session_file)
                if retry_status == "ok":
                    return {"status": "ok_relogged", "posts": retry_posts, "message": relogin_msg}
                return {"status": "auth_failed", "posts": [], "message": f"自動重登後讀取仍失敗：{retry_msg}"}
            return {"status": "auth_failed", "posts": [], "message": relogin_msg}

        return {"status": status, "posts": [], "message": msg}

    return {"status": "error", "posts": [], "message": msg}


def scrape_recent_posts():
    """向下相容介面：僅回傳有效貼文清單。"""
    return scrape_recent_posts_with_status()["posts"]
