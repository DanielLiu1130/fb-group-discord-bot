import sys
import requests

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

import config

DISCORD_API_BASE = "https://discord.com/api/v10"

COMMANDS = [
    {
        "name": "設定通知頻道",
        "description": "將目前這個頻道設為二手社團新商品的通知廣播頻道",
        "type": 1,
    },
    {
        "name": "關注",
        "description": "新增關注商品。多樣商品請用頓號「、」或逗號「,」分隔（例如：螢幕、滑鼠、便當）",
        "type": 1,
        "options": [
            {
                "name": "商品清單",
                "description": "輸入想找的商品，多個商品請用頓號「、」或逗號「,」分開（例如：螢幕、滑鼠、便當、自取）",
                "type": 3,
                "required": True,
            }
        ],
    },
    {
        "name": "取消關注",
        "description": "取消關注商品（彈出每個商品的直觀按鈕，直接點擊即刪除）",
        "type": 1,
        "options": [
            {
                "name": "欲取消商品",
                "description": "（選填）若想直接打字刪除，輸入商品名稱，多個用頓號或逗號分隔；若不填則彈出按鈕",
                "type": 3,
                "required": False,
            }
        ],
    },
    {
        "name": "我的清單",
        "description": "查看自己目前關注的所有二手商品關鍵字",
        "type": 1,
    },
    {
        "name": "立即巡邏",
        "description": "手動立即觸發一次 FB 社團檢查（不需等定時排程）",
        "type": 1,
    },
    {
        "name": "幫助",
        "description": "查看本機器人的所有使用指令說明",
        "type": 1,
    },
]


def main():
    token = config.DISCORD_BOT_TOKEN
    if not token:
        print("❌ 未設定 DISCORD_BOT_TOKEN，請先在 .env 中填入 Discord Bot Token。")
        sys.exit(1)

    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
    }

    me_resp = requests.get(f"{DISCORD_API_BASE}/oauth2/applications/@me", headers=headers, timeout=15)
    if me_resp.status_code != 200:
        print(f"❌ 取得 Discord Application ID 失敗（HTTP {me_resp.status_code}），請檢查 Token 是否有效。")
        sys.exit(1)

    app_id = me_resp.json().get("id")
    if not app_id:
        print("❌ 回應中缺少 Application ID。")
        sys.exit(1)

    put_resp = requests.put(
        f"{DISCORD_API_BASE}/applications/{app_id}/commands",
        headers=headers,
        json=COMMANDS,
        timeout=20,
    )
    if put_resp.status_code not in (200, 201):
        print(f"❌ 註冊斜線指令失敗（HTTP {put_resp.status_code}）：{put_resp.text}")
        sys.exit(1)

    registered = put_resp.json()
    names = [c.get("name") for c in registered]
    print(f"✅ 斜線指令註冊成功！共同步 {len(names)} 個全域指令：{names}")


if __name__ == "__main__":
    main()
