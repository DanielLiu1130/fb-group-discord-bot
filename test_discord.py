import sys
import requests

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import config


def send_test_notification():
    """透過 .env 中的 DISCORD_WEBHOOK_URL 發送測試通知。"""
    if not config.DISCORD_WEBHOOK_URL:
        print("❌ 未設定 DISCORD_WEBHOOK_URL，請先在 .env 中填入 Webhook 網址。")
        sys.exit(1)

    payload = {
        "content": "🎉 **【測試成功】FB 二手拍賣監控機器人已就緒！**\n你的手機如果有裝 Discord App，現在應該已經收到通知囉！"
    }

    response = requests.post(config.DISCORD_WEBHOOK_URL, json=payload, timeout=15)
    if response.status_code == 204:
        print("✅ 測試訊息發送成功！快檢查你的 Discord 頻道與手機通知！")
    else:
        print(f"❌ 發送失敗，狀態碼：{response.status_code}，錯誤訊息：{response.text}")


if __name__ == "__main__":
    send_test_notification()
