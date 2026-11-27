import os
import sys
import json
import re
import time
import random
import asyncio
import datetime
import discord
from discord import app_commands
from discord.ext import commands

# 解決 Windows / Linux 終端機編碼問題
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

import config
from scraper import scrape_recent_posts_with_status

# ==========================================
# 全域運行狀態（防止重連重複排程與並發瀏覽器 OOM）
# ==========================================
background_task = None
commands_synced = False
crawl_lock = asyncio.Lock()
auth_alert_active = False
last_auth_alert_ts = 0.0


# ==========================================
# 資料讀寫工具函式
# ==========================================
def load_subscriptions():
    """讀取伺服器成員的訂閱資料"""
    if os.path.exists(config.SUBSCRIPTIONS_FILE):
        try:
            with open(config.SUBSCRIPTIONS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                users = data.get("users", {})
                for u_info in users.values():
                    new_subs = []
                    for s in u_info.get("subs", []):
                        if isinstance(s, dict):
                            new_subs.append(s.get("keyword", ""))
                        elif isinstance(s, str):
                            new_subs.append(s)
                    u_info["subs"] = list(dict.fromkeys([x.strip() for x in new_subs if x.strip()]))
                return data
        except Exception as e:
            print(f"[警告] 讀取訂閱資料失敗：{e}", flush=True)
    return {"channel_id": None, "users": {}}


def save_subscriptions(data):
    """保存伺服器成員的訂閱資料"""
    try:
        with open(config.SUBSCRIPTIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[錯誤] 保存訂閱資料失敗：{e}", flush=True)


def load_seen_posts():
    """依出現順序讀取已處理過的貼文 ID 清單，保留順序以便裁切舊紀錄"""
    if os.path.exists(config.SEEN_POSTS_FILE):
        try:
            with open(config.SEEN_POSTS_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
                if isinstance(raw, list):
                    return list(dict.fromkeys(str(item) for item in raw if item))
        except Exception:
            return []
    return []


def save_seen_posts(seen_posts_list):
    """保存已處理過的貼文 ID，並限制最多保留最新 MAX_SEEN_POSTS 筆"""
    try:
        trimmed = seen_posts_list[-config.MAX_SEEN_POSTS:]
        with open(config.SEEN_POSTS_FILE, "w", encoding="utf-8") as f:
            json.dump(trimmed, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[錯誤] 保存貼文去重紀錄失敗：{e}", flush=True)


# ==========================================
# Discord 機器人初始化
# ==========================================
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


# ==========================================
# 憑證狀態推播輔助函式
# ==========================================
async def _send_auth_warning(channel, reason: str, force: bool = False):
    """在 Discord 通知頻道發送 FB 憑證失效警告（含冷卻時間避免洗版）。"""
    global auth_alert_active, last_auth_alert_ts

    now = time.time()
    cooldown_sec = config.AUTH_ALERT_COOLDOWN_MINUTES * 60
    auth_alert_active = True

    if not force and last_auth_alert_ts > 0 and (now - last_auth_alert_ts) < cooldown_sec:
        return

    embed = discord.Embed(
        title="⚠️ 【Facebook 登入憑證需要更新】",
        color=0xE74C3C,
        description=(
            f"機器人嘗試讀取 Facebook 社團時遭遇驗證失敗，目前無法抓取新貼文。\n\n"
            f"**原因：** {reason}\n\n"
            f"**處理方式：**\n"
            f"1. 在自己電腦用瀏覽器（建議無痕視窗）登入該 Facebook 帳號。\n"
            f"2. 使用 Cookie-Editor 匯出 JSON 並覆蓋本機專案內的 `cookies.json`。\n"
            f"3. 雙擊本機專案內的 `upload_session.bat`，即可加密轉換並上傳到雲端主機。\n"
            f"4. 上傳完成後下次自動巡邏（或執行 `/立即巡邏`）就會自動恢復。"
        ),
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )
    embed.set_footer(text="清交二手拍小幫手 • 憑證異常通知")

    try:
        await channel.send(embed=embed)
        last_auth_alert_ts = now
    except Exception as e:
        print(f"  [發送失敗] 無法發送憑證失效通知到 Discord：{e}", flush=True)


async def _send_auth_recovery(channel, message: str):
    """在 FB 登入狀態恢復時發送簡短綠色恢復通知。"""
    global auth_alert_active, last_auth_alert_ts

    embed = discord.Embed(
        title="✅ 【Facebook 登入憑證已恢復正常】",
        color=0x2ECC71,
        description=f"{message}\n社團監控已恢復自動巡邏。",
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )
    embed.set_footer(text="清交二手拍小幫手 • 狀態恢復通知")

    try:
        await channel.send(embed=embed)
    except Exception as e:
        print(f"  [發送失敗] 無法發送憑證恢復通知到 Discord：{e}", flush=True)
    finally:
        auth_alert_active = False
        last_auth_alert_ts = 0.0


# ==========================================
# 核心巡邏與比對推播邏輯
# ==========================================
async def _run_crawl_and_notify_locked(manual: bool = False):
    """在取得 crawl_lock 後執行一次社團爬取與推播。"""
    subs_data = load_subscriptions()
    channel_id = subs_data.get("channel_id")

    if not channel_id:
        print("[提示] 尚未設定通知頻道，請在 Discord 頻道中輸入 /設定通知頻道", flush=True)
        return {
            "status": "no_channel",
            "matches": 0,
            "message": "尚未設定通知頻道，請先在目標頻道輸入 `/設定通知頻道`！",
        }

    try:
        channel_id_int = int(channel_id)
    except (TypeError, ValueError):
        return {
            "status": "no_channel",
            "matches": 0,
            "message": "通知頻道 ID 格式無效，請重新執行 `/設定通知頻道`！",
        }

    channel = bot.get_channel(channel_id_int)
    if not channel:
        try:
            channel = await bot.fetch_channel(channel_id_int)
        except Exception as e:
            print(f"[錯誤] 無法取得通知頻道（ID: {channel_id}）：{e}", flush=True)
            return {
                "status": "no_channel",
                "matches": 0,
                "message": f"無法讀取通知頻道（ID: {channel_id}），請重新執行 `/設定通知頻道`。",
            }

    seen_posts_list = load_seen_posts()
    seen_posts_set = set(seen_posts_list)
    users_data = subs_data.get("users", {})

    print(f"\n[{datetime.datetime.now().strftime('%H:%M:%S')}] 開始執行社團巡邏...", flush=True)
    crawl_result = await asyncio.to_thread(scrape_recent_posts_with_status)
    status = crawl_result.get("status", "error")
    message = crawl_result.get("message", "")

    if status in ("session_missing", "auth_expired", "auth_failed"):
        await _send_auth_warning(channel, message, force=manual)
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 巡邏中止（{status}）：{message}\n", flush=True)
        return {"status": status, "matches": 0, "message": message}

    if status == "error":
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 巡邏發生錯誤：{message}\n", flush=True)
        return {"status": "error", "matches": 0, "message": message}

    if status == "ok_relogged" or auth_alert_active:
        recovery_msg = message if status == "ok_relogged" else "已重新驗證並成功讀取社團貼文。"
        await _send_auth_recovery(channel, recovery_msg)

    recent_posts = crawl_result.get("posts", [])
    match_count = 0

    for post in recent_posts:
        post_id = post["post_id"]
        if post_id in seen_posts_set:
            continue

        post_text = post["text"]
        post_url = post["url"]
        post_images = post.get("images", [])

        matched_users = []
        for user_id_str, user_info in users_data.items():
            for kw in user_info.get("subs", []):
                if kw.lower() in post_text.lower():
                    matched_users.append(user_id_str)
                    break

        if matched_users:
            mentions_text = " ".join([f"<@{uid}>" for uid in matched_users])
            display_desc = post_text[:4000]
            author = post.get("author", "社團賣家")
            title = "🔔 【FB 二手社團新商品】" if author == "社團賣家" else f"🔔 【FB 二手社團新商品】（賣家：{author}）"

            embed = discord.Embed(
                title=title,
                url=post_url,
                color=0x00FF88,
                description=display_desc,
                timestamp=datetime.datetime.now(datetime.timezone.utc),
            )
            embed.add_field(name="👥 關注成員", value=mentions_text, inline=False)
            embed.add_field(
                name="🔗 貼文直達連結",
                value=f"[點我前往 Facebook 查看完整貼文與相簿]({post_url})",
                inline=False,
            )

            if post_images:
                embed.set_image(url=post_images[0])
                if len(post_images) > 1:
                    embed.set_footer(text=f"清交二手拍小幫手 • 📸 本貼文共包含 {len(post_images)} 張照片（點上方連結看全部照片）")
                else:
                    embed.set_footer(text="清交二手拍小幫手")
            else:
                embed.set_footer(text="清交二手拍小幫手")

            try:
                await channel.send(
                    content=f"🚨 呼叫 {mentions_text}！社團出現你關注的物品囉：",
                    embed=embed,
                )
                print(f"  [成功通知] 已發送通知給 {len(matched_users)} 位成員！", flush=True)
                match_count += 1
            except Exception as e:
                print(f"  [發送失敗] 發送訊息到 Discord 失敗：{e}", flush=True)

        seen_posts_set.add(post_id)
        seen_posts_list.append(post_id)

    save_seen_posts(seen_posts_list)
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 巡邏結束，本次觸發通知數：{match_count}\n", flush=True)
    return {"status": status, "matches": match_count, "message": message}


async def perform_crawl_and_notify(manual: bool = False):
    """執行一次社團爬取，並透過 asyncio.Lock 防止同時開啟多個 Chromium 造成 OOM。"""
    if manual and crawl_lock.locked():
        return {
            "status": "busy",
            "matches": 0,
            "message": "目前正在執行巡邏中，請稍候幾秒再試！",
        }

    async with crawl_lock:
        return await _run_crawl_and_notify_locked(manual=manual)


# ==========================================
# 🛡️ 防封號隨機間隔巡邏任務（24/7 隨機 15 ~ 20 分鐘）
# ==========================================
async def random_monitor_background_loop():
    await bot.wait_until_ready()
    round_no = 1

    while not bot.is_closed():
        try:
            print(f"▶ 第 {round_no} 輪巡邏開始...", flush=True)
            await perform_crawl_and_notify(manual=False)
        except Exception as e:
            print(f"[排程異常] 背景巡邏遇到錯誤：{e}", flush=True)

        round_no += 1

        min_sec = config.CHECK_INTERVAL_MIN_MINUTES * 60
        max_sec = config.CHECK_INTERVAL_MAX_MINUTES * 60
        sleep_sec = random.randint(min_sec, max(min_sec, max_sec))

        next_time = (datetime.datetime.now() + datetime.timedelta(seconds=sleep_sec)).strftime("%H:%M:%S")
        mins, secs = divmod(sleep_sec, 60)
        print(f"💤 防封隨機休息中... 下次巡邏預計於【{next_time}】進行（間隔約 {mins} 分 {secs} 秒）\n", flush=True)

        await asyncio.sleep(sleep_sec)


# ==========================================
# 機器人啟動事件（防重連重複同步與重複建置 Task）
# ==========================================
@bot.event
async def on_ready():
    global background_task, commands_synced

    print("=" * 65, flush=True)
    print(f"🎉 機器人已連線！名稱：{bot.user.name} (ID: {bot.user.id})", flush=True)

    if not commands_synced:
        print("📡 正在同步斜線指令 (Slash Commands) 到伺服器...", flush=True)
        try:
            synced = await bot.tree.sync()
            commands_synced = True
            print(f"✅ 斜線指令同步成功！共註冊了 {len(synced)} 個指令：{[c.name for c in synced]}", flush=True)
        except Exception as e:
            print(f"❌ 斜線指令同步失敗：{e}", flush=True)

    if background_task is None or background_task.done():
        background_task = bot.loop.create_task(random_monitor_background_loop())
        print("🔄 背景 24/7 自動巡邏任務已啟動。", flush=True)
    else:
        print("ℹ️ 偵測到 Discord 斷線重連，背景巡邏任務持續運作中（未重複啟動）。", flush=True)

    print("=" * 65, flush=True)


# ==========================================
# 🎨 直觀的按鈕式取消關注 View
# ==========================================
class KeywordButtonItem(discord.ui.Button):
    def __init__(self, keyword: str):
        super().__init__(
            label=keyword,
            style=discord.ButtonStyle.secondary,
            emoji="❌",
        )
        self.keyword = keyword

    async def callback(self, interaction: discord.Interaction):
        view: DirectButtonUnsubscribeView = self.view
        user_id_str = view.user_id_str

        subs = load_subscriptions()
        users = subs.get("users", {})
        user_info = users.get(user_id_str)

        if user_info and "subs" in user_info:
            user_info["subs"] = [k for k in user_info["subs"] if k != self.keyword]
            save_subscriptions(subs)
            view.keywords = user_info["subs"]

        view.rebuild_items()

        if view.keywords:
            await interaction.response.edit_message(
                content=f"🗑️ 已取消關注「**{self.keyword}**」！\n若還要刪除其他商品，可直接點擊下方剩餘按鈕：",
                view=view,
            )
        else:
            await interaction.response.edit_message(
                content=f"🗑️ 已取消關注「**{self.keyword}**」！\n✅ **目前你的關注清單已經全部清空囉！**",
                view=None,
            )


class DirectButtonUnsubscribeView(discord.ui.View):
    def __init__(self, user_id_str: str, keywords: list):
        super().__init__(timeout=120)
        self.user_id_str = user_id_str
        self.keywords = list(keywords)
        self.rebuild_items()

    def rebuild_items(self):
        self.clear_items()

        for kw in self.keywords[:20]:
            self.add_item(KeywordButtonItem(kw))

        clear_all_btn = discord.ui.Button(
            label="💥 一鍵清空全部",
            style=discord.ButtonStyle.danger,
            row=4 if len(self.keywords) > 15 else (len(self.keywords) // 5 + 1),
        )
        clear_all_btn.callback = self.clear_all_callback
        self.add_item(clear_all_btn)

        close_btn = discord.ui.Button(
            label="關閉選單",
            style=discord.ButtonStyle.secondary,
            row=4 if len(self.keywords) > 15 else (len(self.keywords) // 5 + 1),
        )
        close_btn.callback = self.close_callback
        self.add_item(close_btn)

    async def clear_all_callback(self, interaction: discord.Interaction):
        subs = load_subscriptions()
        users = subs.get("users", {})
        user_info = users.get(self.user_id_str)

        if user_info:
            user_info["subs"] = []
            save_subscriptions(subs)

        await interaction.response.edit_message(
            content="💥 **已成功清空所有關注的商品！** 目前關注清單為空。",
            view=None,
        )

    async def close_callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            content="已關閉選單。",
            view=None,
        )


# ==========================================
# 斜線指令 (Slash Commands) - 具備雙重防重覆回應保護
# ==========================================
async def safe_defer(interaction: discord.Interaction, ephemeral: bool = True):
    """安全延遲回應，防止重複調用報錯"""
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=ephemeral)
    except Exception:
        pass


@bot.tree.command(name="設定通知頻道", description="將目前這個頻道設為二手社團新商品的通知廣播頻道")
async def set_channel(interaction: discord.Interaction):
    await safe_defer(interaction, ephemeral=False)
    subs = load_subscriptions()
    subs["channel_id"] = str(interaction.channel_id)
    save_subscriptions(subs)
    await interaction.followup.send(
        "📢 **通知頻道設定完成！**\n未來只要在社團發現成員關注的二手商品，機器人都會在此頻道發送貼文並 **@標記** 想買的人！"
    )


@bot.tree.command(
    name="關注",
    description="新增關注商品。多樣商品請用頓號「、」或逗號「,」分隔（例如：螢幕、滑鼠、便當）",
)
@app_commands.describe(
    商品清單="輸入想找的商品，多個商品請用頓號「、」或逗號「,」分開（例如：螢幕、滑鼠、便當、自取）"
)
async def subscribe(interaction: discord.Interaction, 商品清單: str):
    await safe_defer(interaction, ephemeral=True)

    raw_keywords = re.split(r"[,，、;；\n]+", 商品清單.strip())
    new_keywords = [k.strip() for k in raw_keywords if k.strip()]

    if not new_keywords:
        await interaction.followup.send(
            "❌ 請輸入想找的商品名稱！若要一次關注多樣，請用頓號或逗號隔開（例如：`螢幕、滑鼠、便當`）。",
            ephemeral=True,
        )
        return

    user_id_str = str(interaction.user.id)
    user_name = interaction.user.display_name

    subs = load_subscriptions()
    users = subs.setdefault("users", {})
    user_info = users.setdefault(user_id_str, {"name": user_name, "subs": []})
    user_info["name"] = user_name

    current_subs = list(user_info.get("subs", []))
    added_list = []
    already_list = []

    for kw in new_keywords:
        if kw not in current_subs:
            current_subs.append(kw)
            added_list.append(kw)
        else:
            already_list.append(kw)

    user_info["subs"] = current_subs
    save_subscriptions(subs)

    msg_lines = []
    if added_list:
        msg_lines.append("✅ **成功新增關注（共 {} 項）：** ".format(len(added_list)) + "、".join([f"`{k}`" for k in added_list]))
    if already_list:
        msg_lines.append("ℹ️ **已在關注清單中：** " + "、".join([f"`{k}`" for k in already_list]))

    msg_lines.append("\n💡 *小秘訣：想要同時關注多種商品，直接用「頓號、」或「逗號,」分隔即可一次全部加入！*")
    msg_lines.append("只要社團有人發布上述物品，機器人就會立刻在此 `@標記你` 喔！")

    await interaction.followup.send("\n".join(msg_lines), ephemeral=True)


@bot.tree.command(
    name="取消關注",
    description="取消關注商品（彈出每個商品的直觀按鈕，直接點擊即刪除）",
)
@app_commands.describe(
    欲取消商品="（選填）若想直接打字刪除，輸入商品名稱，多個用頓號或逗號分隔；若不填則彈出按鈕"
)
async def unsubscribe(interaction: discord.Interaction, 欲取消商品: str = None):
    await safe_defer(interaction, ephemeral=True)

    user_id_str = str(interaction.user.id)
    subs = load_subscriptions()
    users = subs.get("users", {})
    user_info = users.get(user_id_str)

    current_keywords = user_info.get("subs", []) if user_info else []

    if not current_keywords:
        await interaction.followup.send("你目前尚未關注任何商品喔！可以使用 `/關注` 來新增。", ephemeral=True)
        return

    if 欲取消商品:
        raw_del = re.split(r"[,，、;；\n]+", 欲取消商品.strip())
        targets = {k.strip().lower() for k in raw_del if k.strip()}

        deleted = []
        remaining = []
        for kw in current_keywords:
            if kw.lower() in targets:
                deleted.append(kw)
            else:
                remaining.append(kw)

        if deleted:
            user_info["subs"] = remaining
            save_subscriptions(subs)
            del_str = "、".join([f"`{k}`" for k in deleted])
            await interaction.followup.send(f"✅ 已成功為你取消關注 **{len(deleted)}** 個商品：{del_str}！", ephemeral=True)
        else:
            await interaction.followup.send("找不到符合你輸入的商品，請使用 `/我的清單` 查看目前已關注的物品。", ephemeral=True)
        return

    view = DirectButtonUnsubscribeView(user_id_str, current_keywords)
    await interaction.followup.send(
        "👇 **請直接點擊下方想取消的商品按鈕（點一下立刻刪除，可連續點擊）：**",
        view=view,
        ephemeral=True,
    )


@bot.tree.command(name="我的清單", description="查看自己目前關注的所有二手商品關鍵字")
async def my_list(interaction: discord.Interaction):
    await safe_defer(interaction, ephemeral=True)

    user_id_str = str(interaction.user.id)
    subs = load_subscriptions()
    users = subs.get("users", {})
    user_info = users.get(user_id_str)

    current_subs = user_info.get("subs", []) if user_info else []

    if not current_subs:
        await interaction.followup.send(
            "你目前還沒有關注任何商品！\n快使用 `/關注` 輸入想找的物品（多個可用頓號「、」或逗號「,」分隔）～",
            ephemeral=True,
        )
        return

    lines = [f"**{i}.** `{kw}`" for i, kw in enumerate(current_subs, 1)]

    embed = discord.Embed(
        title=f"📋 {interaction.user.display_name} 的關注清單（共 {len(current_subs)} 項）",
        description="\n".join(lines),
        color=0x3498DB,
    )
    embed.set_footer(text="提示：使用 /關注 可用頓號一次新增多項，使用 /取消關注 可直接點擊按鈕刪除")
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="立即巡邏", description="手動立即觸發一次 FB 社團檢查（不需等定時排程）")
async def check_now(interaction: discord.Interaction):
    await safe_defer(interaction, ephemeral=False)
    result = await perform_crawl_and_notify(manual=True)
    status = result.get("status", "error")
    matches = result.get("matches", 0)
    message = result.get("message", "")

    if status == "busy":
        await interaction.followup.send(f"⏳ {message}")
    elif status == "no_channel":
        await interaction.followup.send(f"📢 {message}")
    elif status in ("session_missing", "auth_expired", "auth_failed"):
        await interaction.followup.send(
            f"⚠️ **Facebook 登入憑證異常，本次巡邏未完成：**\n{message}\n"
            f"請更新 `cookies.json` 後執行 `upload_session.bat` 上傳新的憑證。"
        )
    elif status == "error":
        await interaction.followup.send(f"❌ **巡邏過程發生錯誤：** {message}")
    else:
        extra = "（並已自動完成 FB 重新登入）" if status == "ok_relogged" else ""
        await interaction.followup.send(f"✅ **巡邏檢查完成！**{extra} 本次發現並推播了 **{matches}** 篇符合成員條件的新貼文。")


@bot.tree.command(name="幫助", description="查看本機器人的所有使用指令說明")
async def help_command(interaction: discord.Interaction):
    await safe_defer(interaction, ephemeral=True)
    embed = discord.Embed(
        title="🤖 【清交二手拍拍賣小幫手】使用指南",
        description="自動巡邏 Facebook 二手社團，只要有人發布你想要的物品，就會在伺服器標記通知你！",
        color=0xF1C40F,
    )
    embed.add_field(
        name="📌 核心指令說明",
        value=(
            "• `/關注 <商品清單>`：設定想找的物品，**可用「頓號、」或「逗號,」分隔**（例如 `/關注 商品清單:螢幕、滑鼠、便當`）\n"
            "• `/取消關注`：**彈出商品獨立按鈕**，直接點一下即刪除，亦提供「一鍵清空」\n"
            "• `/我的清單`：查看目前關注的所有物品列表\n"
            "• `/設定通知頻道`：設定商品推播廣播頻道（由管理員設定）\n"
            "• `/立即巡邏`：立刻到社團抓取一次最新貼文\n"
        ),
        inline=False,
    )
    embed.set_footer(text="支援多人各自設定，社團新貼文自動 @標記 想買的人！")
    await interaction.followup.send(embed=embed, ephemeral=True)


# ==========================================
# 主程式進入點
# ==========================================
if __name__ == "__main__":
    if not config.DISCORD_BOT_TOKEN:
        print("❌ 請先在 .env 中填入 DISCORD_BOT_TOKEN！", flush=True)
        sys.exit(1)

    print("正在啟動 Discord 機器人...", flush=True)
    bot.run(config.DISCORD_BOT_TOKEN)
