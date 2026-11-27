# ==========================================
# Facebook 二手拍賣 Discord 機器人設定檔
# 所有敏感資訊請設定於 .env，切勿寫死於此檔
# ==========================================

import os

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass


def _get_int_env(name: str, default: int, minimum: int = 1) -> int:
    """安全讀取整數環境變數，避免空字串或無效值導致啟動失敗。"""
    raw = os.getenv(name, "").strip()
    if not raw:
        return max(minimum, default)
    try:
        return max(minimum, int(raw))
    except ValueError:
        return max(minimum, default)


def _get_target_groups() -> list:
    """從環境變數 TARGET_GROUPS（逗號分隔）讀取社團清單（公開 Repo 不提供預設網址）。"""
    raw = os.getenv("TARGET_GROUPS", "").strip()
    if not raw:
        return []
    return [g.strip() for g in raw.split(",") if g.strip()]


# 1. Discord 憑證（從 .env 讀取）
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

# 2. 監控的 Facebook 社團網址清單
TARGET_GROUPS = _get_target_groups()

# 3. 預設排除關鍵字（若貼文包含這些詞，視為買家發文自動忽略）
DEFAULT_EXCLUDE_KEYWORDS = [
    "徵", "收", "求", "想買", "收購"
]

# 4. 資料與憑證儲存檔案名稱
SUBSCRIPTIONS_FILE = "subscriptions.json"
SEEN_POSTS_FILE = "notified_posts.json"
SESSION_FILE = "fb_session.json"
MAX_SEEN_POSTS = 2000

# 5. 背景自動巡邏頻率（分鐘）：24/7 隨機於 MIN ~ MAX 之間取樣
CHECK_INTERVAL_MIN_MINUTES = _get_int_env("CHECK_INTERVAL_MIN_MINUTES", 15, minimum=5)
CHECK_INTERVAL_MAX_MINUTES = _get_int_env(
    "CHECK_INTERVAL_MAX_MINUTES", 20, minimum=CHECK_INTERVAL_MIN_MINUTES
)
CHECK_INTERVAL_MINUTES = CHECK_INTERVAL_MIN_MINUTES  # 向下相容舊版欄位
CLOUD_JITTER_MAX_SECONDS = _get_int_env("CLOUD_JITTER_MAX_SECONDS", 300, minimum=0)

# 6. 每次檢查時頁面捲動次數
SCROLL_COUNT = _get_int_env("SCROLL_COUNT", 2, minimum=1)

# 7. 選填：FB 登入失效時自動重登帳密與冷卻設定（分鐘）
FB_EMAIL = os.getenv("FB_EMAIL", "").strip()
FB_PASSWORD = os.getenv("FB_PASSWORD", "")
AUTO_RELOGIN_COOLDOWN_MINUTES = _get_int_env("AUTO_RELOGIN_COOLDOWN_MINUTES", 360, minimum=30)
AUTH_ALERT_COOLDOWN_MINUTES = _get_int_env("AUTH_ALERT_COOLDOWN_MINUTES", 360, minimum=30)
