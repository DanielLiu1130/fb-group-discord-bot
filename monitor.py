import os
import sys
import json
import time
import re
import random
import datetime
import hashlib
import requests
from playwright.sync_api import sync_playwright

# 解決 Windows 終端機 CP950 編碼問題
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import config

def load_seen_posts():
    """讀取已經發過通知的貼文 ID 清單，避免重複響鈴"""
    if os.path.exists(config.SEEN_POSTS_FILE):
        try:
            with open(config.SEEN_POSTS_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception as e:
            print(f"[警告] 讀取已通知紀錄失敗：{e}，將建立新紀錄")
            return set()
    return set()

def save_seen_posts(seen_posts):
    """保存已通知貼文紀錄"""
    try:
        with open(config.SEEN_POSTS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(seen_posts), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[錯誤] 保存已通知紀錄失敗：{e}")

def extract_price(text):
    """
    從貼文文字中嘗試抓取價格數字
    例如：NT$ 15,000 / $12000 / 售 18000 / 15000元 / 價格: 16000
    """
    patterns = [
        r'(?:NT\$|\$|售|價格|售價|金額|原價|降價)?\s*([1-9]\d{1,2}(?:,\d{3})+|\d{2,6})\s*(?:元|塊)?',
    ]
    for pattern in patterns:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            raw_num = match.group(1).replace(",", "")
            try:
                price = int(raw_num)
                # 排除年份（如 2024、2025、2026）或不可能的價格
                if 50 <= price <= 500000 and price not in [2023, 2024, 2025, 2026]:
                    return price
            except ValueError:
                continue
    return None

def clean_post_text(raw_text):
    """過濾掉 FB 雜訊文字、發文時間混淆字元與底層按鈕"""
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    cleaned = []

    started = False
    for l in lines:
        if not started:
            # 濾除發文時間混淆字元（如 1\n6\nm\nl...）與單字元符號
            if len(l) <= 2 or l in ['.', '·', '•', '-']:
                continue
            started = True

        # 過濾雜訊與操作按鈕（例如 "Comment as Daniel"、"+6" 等）
        if re.match(r'^(Comment as.*|以.*身分留言.*|\+\d+|Like|Comment|Share|讚|留言|分享|追蹤|Follow)$', l, re.IGNORECASE):
            continue
        if l == 'Facebook' or l.startswith('http'):
            continue

        cleaned.append(l)

    return "\n".join(cleaned)

def send_discord_notification(post_info):
    """發送精美的 Discord 嵌入式卡片通知到手機"""
    clean_desc = post_info["text"][:300] + ("..." if len(post_info["text"]) > 300 else "")

    embed = {
        "title": "🔔 【FB 二手社團新商品上架通知】",
        "url": post_info["url"],
        "color": 0x2ECC71,  # 清新綠色
        "description": clean_desc,
        "fields": [
            {
                "name": "🔍 觸發關鍵字",
                "value": f"`{post_info['matched_keyword']}`",
                "inline": True
            },
            {
                "name": "💰 偵測價格",
                "value": f"**NT$ {post_info['price']}**" if post_info['price'] else "未標價 / 免費 / 議價",
                "inline": True
            },
            {
                "name": "🔗 貼文直達連結",
                "value": f"[點我直接前往 Facebook 查看貼文]({post_info['url']})",
                "inline": False
            }
        ],
        "footer": {
            "text": "FB 二手社團即時監控小幫手"
        },
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }

    payload = {
        "content": f"🚨 **發現符合條件的新商品！** （關鍵字：`{post_info['matched_keyword']}`）",
        "embeds": [embed]
    }

    try:
        res = requests.post(config.DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        if res.status_code == 204:
            print(f"  [推播成功] 已發送至 Discord！（{post_info['matched_keyword']}）")
        else:
            print(f"  [推播失敗] Discord 回應碼：{res.status_code}，{res.text}")
    except Exception as e:
        print(f"  [推播錯誤] 連線 Discord 失敗：{e}")

def check_group(page, group_url, seen_posts):
    """檢查單一 Facebook 社團中的最新貼文"""
    group_match = re.search(r"groups/([^/?]+)", group_url)
    group_id = group_match.group(1) if group_match else "unknown"

    clean_url = f"https://www.facebook.com/groups/{group_id}"
    target_url = f"{clean_url}?sorting_setting=CHRONOLOGICAL"

    print(f"\n[開始掃描] 社團網址：{target_url}")
    try:
        page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
    except Exception as e:
        print(f"[提示] 頁面載入提醒：{e}")

    time.sleep(3)

    for s in range(config.SCROLL_COUNT):
        page.evaluate("window.scrollBy(0, 1200)")
        time.sleep(random.uniform(2.0, 3.0))

    feed = page.query_selector("div[role='feed']")
    if not feed:
        print("[警告] 找不到社團動態 feed 容器，可能社團為私密或尚未加入。")
        return 0

    post_cards = feed.query_selector_all(":scope > div")
    print(f"[動態流] 找到 {len(post_cards)} 個動態區塊，正在解析內容...")

    new_matches = 0

    for i, card in enumerate(post_cards):
        try:
            raw_text = card.inner_text().strip()
            if len(raw_text) < 30 or "sort group feed by" in raw_text:
                continue

            cleaned_text = clean_post_text(raw_text)
            if not cleaned_text:
                continue

            post_id = None
            post_url = None

            links = card.query_selector_all("a")
            for link in links:
                href = link.get_attribute("href")
                if not href:
                    continue

                m_post = re.search(r"/posts/(\d+)", href)
                if m_post:
                    post_id = m_post.group(1)
                    post_url = f"{clean_url}/posts/{post_id}/"
                    break

                m_pcb = re.search(r"set=pcb\.(\d+)", href)
                if m_pcb:
                    post_id = m_pcb.group(1)
                    post_url = f"{clean_url}/posts/{post_id}/"
                    break

                m_perm = re.search(r"/permalink/(\d+)", href)
                if m_perm:
                    post_id = m_perm.group(1)
                    post_url = f"{clean_url}/posts/{post_id}/"
                    break

                m_multi = re.search(r"multi_permalinks=(\d+)", href)
                if m_multi:
                    post_id = m_multi.group(1)
                    post_url = f"{clean_url}/posts/{post_id}/"
                    break

            if not post_id:
                post_hash = hashlib.md5(cleaned_text[:80].encode("utf-8")).hexdigest()[:12]
                post_id = f"hash_{post_hash}"
                post_url = clean_url

            if post_id in seen_posts:
                continue

            if any(exc in cleaned_text for exc in config.DEFAULT_EXCLUDE_KEYWORDS):
                seen_posts.add(post_id)
                continue

            post_info = {
                "matched_keyword": "一般商品",
                "price": extract_price(cleaned_text),
                "text": cleaned_text,
                "url": post_url
            }
            send_discord_notification(post_info)

            seen_posts.add(post_id)
            new_matches += 1

            time.sleep(1.2)

        except Exception as e:
            continue

    print(f"[掃描完成] 本社團發現 {new_matches} 篇新上架商品！")
    return new_matches

def run_monitor():
    """執行單次社團檢查的主流程"""
    if not os.path.exists("fb_session.json"):
        print("[錯誤] 找不到 fb_session.json！請先執行登入身分保存。")
        return

    seen_posts = load_seen_posts()
    print("=" * 65)
    print(f"【FB 社團商品即時監控啟動】")
    print(f"目標社團數量：{len(config.TARGET_GROUPS)}")
    print(f"目前已記錄貼文庫存數：{len(seen_posts)}")
    print("=" * 65)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )
        context = browser.new_context(
            storage_state="fb_session.json",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="zh-TW"
        )
        page = context.new_page()

        for group_url in config.TARGET_GROUPS:
            if "填入你的社團網址" in group_url:
                continue
            check_group(page, group_url, seen_posts)
            time.sleep(random.uniform(2.5, 4.0))

        browser.close()

    save_seen_posts(seen_posts)
    print("=" * 65)
    print(f"本次掃描結束，已通知貼文紀錄已更新！")
    print("=" * 65)

if __name__ == "__main__":
    run_monitor()
