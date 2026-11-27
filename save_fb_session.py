import os
import sys
import time
import ctypes
from playwright.sync_api import sync_playwright

# 解決 Windows 終端機編碼問題
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        ctypes.windll.kernel32.SetConsoleTitleW("FB 切換帳號登入")
    except Exception:
        pass


def clear_old_credentials(files=("fb_session.json", "cookies.json")):
    """刪除舊的 Facebook 登入憑證與 Cookie 匯出檔，確保從乾淨狀態重新登入。"""
    removed = []
    for file_path in files:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                removed.append(file_path)
            except OSError as e:
                print(f"⚠️ 無法刪除舊憑證 {file_path}：{e}")
    if removed:
        print(f"🧹 已清除舊帳號憑證：{', '.join(removed)}")
    else:
        print("🧹 未發現殘留的舊帳號憑證，將以全新狀態登入。")


def launch_clean_browser(playwright):
    """啟動乾淨的 Chrome 視窗（優先使用本機 Chrome，失敗時改用內建 Chromium）。"""
    launch_kwargs = {
        "headless": False,
        "ignore_default_args": ["--enable-automation"],
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--window-size=1200,850",
        ],
    }
    try:
        return playwright.chromium.launch(channel="chrome", **launch_kwargs)
    except Exception:
        return playwright.chromium.launch(**launch_kwargs)


def save_fb_session():
    """清除舊帳號憑證，開啟乾淨瀏覽器讓使用者登入新 FB 帳號並儲存 session。"""
    print("=" * 65)
    print("【FB 登入與身分儲存】正在清除舊帳號並啟動獨立 Chrome 瀏覽器...")
    print("=" * 65)

    clear_old_credentials()

    with sync_playwright() as p:
        browser = launch_clean_browser(p)
        context = browser.new_context(
            no_viewport=True,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="zh-TW",
        )
        page = context.new_page()

        print("\n正在前往 Facebook，請稍候...")
        try:
            # wait_until="domcontentloaded" 且 timeout=0（不設超時上限），避免因 FB 廣告/追蹤代碼過多而引發超時報錯
            page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=0)
        except Exception:
            print("(頁面已開啟，若有非致命載入提示已忽略)")

        print("\n" + "=" * 65)
        print("👉 瀏覽器已打開！請在瀏覽器中手動登入「新」FB 帳號。")
        print("👉 如果有兩步驟驗證，請正常輸入驗證碼。")
        print("   （★提示：若電腦版 2FA 仍卡住，直接在該網址列輸入 https://m.facebook.com）")
        print("👉 當你看到已經成功進入 Facebook 動態首頁後：")
        print("👉 請回到這個命令提示字元視窗，按下 [Enter] 鍵！")
        print("=" * 65 + "\n")

        cookies = []
        while True:
            try:
                input("【確認已使用新帳號登入並看到首頁後，請在此按 Enter 鍵】: ")
            except (EOFError, KeyboardInterrupt):
                print("\n已取消儲存，未寫入新的登入憑證。")
                try:
                    browser.close()
                except Exception:
                    pass
                return

            if not browser.is_connected():
                print("\n⚠️ 瀏覽器視窗已被關閉，取消儲存登入憑證。")
                return

            try:
                cookies = context.cookies("https://www.facebook.com")
            except Exception:
                print("\n⚠️ 無法讀取瀏覽器登入狀態（視窗可能已關閉），取消儲存。")
                try:
                    browser.close()
                except Exception:
                    pass
                return

            cookie_names = {c.get("name") for c in cookies}
            if {"c_user", "xs"}.issubset(cookie_names):
                break

            print("\n⚠️ 尚未偵測到完整的 Facebook 登入憑證（缺少 c_user 或 xs）。")
            print("請先在 Chrome 視窗完成新帳號登入並確認進入首頁後，再回來按一次 [Enter] 鍵！\n")

        print("\n正在保存新帳號的登入 Cookie 與身分憑證...")
        time.sleep(2)

        # 保存登入狀態到 fb_session.json
        context.storage_state(path="fb_session.json")
        print("\n" + "=" * 65)
        print(f"🎉 太棒了！新帳號登入身分已成功儲存為 'fb_session.json'！（共 {len(cookies)} 個 Cookie）")
        print("=" * 65)

        browser.close()
        time.sleep(3)


if __name__ == "__main__":
    save_fb_session()
