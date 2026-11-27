import json
import os
import sys

# 解決 Windows 終端機編碼問題
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def convert_cookie_editor_to_playwright(input_file="cookies.json", output_file="fb_session.json"):
    """
    將 Cookie-Editor 匯出的 JSON 格式轉換為 Playwright 所需的 storage_state (fb_session.json)
    """
    if not os.path.exists(input_file):
        print(f"❌ 找不到 {input_file}！請確保已將從 Cookie-Editor 複製的內容存成 {input_file}")
        return False

    with open(input_file, "r", encoding="utf-8") as f:
        try:
            raw_cookies = json.load(f)
        except Exception as e:
            print(f"❌ 讀取 {input_file} 失敗，格式可能不正確：{e}")
            return False

    playwright_cookies = []
    for c in raw_cookies:
        # 過濾與處理 sameSite
        same_site = c.get("sameSite", "Lax")
        if same_site not in ["Strict", "Lax", "None"]:
            same_site = "None" if c.get("secure", False) else "Lax"

        cookie_item = {
            "name": c.get("name"),
            "value": c.get("value"),
            "domain": c.get("domain"),
            "path": c.get("path", "/"),
            "expires": c.get("expirationDate", -1),
            "httpOnly": c.get("httpOnly", False),
            "secure": c.get("secure", False),
            "sameSite": same_site
        }
        playwright_cookies.append(cookie_item)

    cookie_names = {c.get("name") for c in playwright_cookies}
    if not {"c_user", "xs"}.issubset(cookie_names):
        print("⚠️ 警告：匯出的 cookies.json 缺少 c_user 或 xs，可能不是完整的 Facebook 登入憑證。")

    storage_state = {
        "cookies": playwright_cookies,
        "origins": []
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(storage_state, f, indent=2, ensure_ascii=False)

    print("=" * 65)
    print(f"🎉 成功轉換！已為您產生：'{output_file}'")
    print(f"共載入 {len(playwright_cookies)} 個登入身分憑證。")
    print("=" * 65)
    return True

if __name__ == "__main__":
    convert_cookie_editor_to_playwright()
