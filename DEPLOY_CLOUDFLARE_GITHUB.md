# Cloudflare Workers ＋ GitHub Actions 免費 24 小時託管指南

本指南使用 **100% 免費、免信用卡、免學生驗證** 的組合，將「清交二手拍 Discord 機器人」託管到雲端：
- **Cloudflare Workers ＋ Cloudflare KV**：負責 24 小時秒回所有 Discord 斜線指令與互動按鈕，並安全保存訂閱清單、已通知紀錄與 Facebook 登入憑證。
- **GitHub Actions（公開 Repo）**：免費無限分鐘數，每 **15～20 分鐘隨機**啟動官方 Playwright 容器巡邏社團並推播到 Discord（支援 `/立即巡邏` 隨時手動觸發）。

---

## 目錄
1. [步驟一：建立 Cloudflare 帳號與私有 KV 資料庫](#步驟一建立-cloudflare-帳號與私有-kv-資料庫)
2. [步驟二：建立公開 GitHub Repo 與設定加密 Secrets](#步驟二建立公開-github-repo-與設定加密-secrets)
3. [步驟三：部署 Cloudflare Worker（接管 Discord 指令）](#步驟三部署-cloudflare-worker接管-discord-指令)
4. [步驟四：設定 Discord Developer Portal 與註冊斜線指令](#步驟四設定-discord-developer-portal-與註冊斜線指令)
5. [步驟五：從電腦一鍵上傳 FB 憑證與現有訂閱資料](#步驟五從電腦一鍵上傳-fb-憑證與現有訂閱資料)
6. [步驟六：Discord 實測與日常維運注意事項](#步驟六discord-實測與日常維運注意事項)

---

## 步驟一：建立 Cloudflare 帳號與私有 KV 資料庫

1. 前往 [Cloudflare Dashboard](https://dash.cloudflare.com/) 免費註冊並登入（不需綁定信用卡）。
2. 左側選單進入 **Storage & Databases（儲存空間和資料庫）** → **Workers KV**（若畫面改版可從 **Compute (Workers)** 底下找到 **KV**）。
3. 點擊 **Create instance（建立命名空間）**：
   - 名稱輸入：`fb-bot-kv` → 點擊建立。
   - 記下這組 Namespace 的 **ID**（這就是 **`CF_KV_NAMESPACE_ID`**）。
4. 回到 Workers 總覽頁或首頁右側欄，複製您的 **Account ID**（這就是 **`CF_ACCOUNT_ID`**）。
5. 產生最小權限的 API Token（給 GitHub Actions 與本機 `upload_session.bat` 讀寫 KV 用）：
   - 點右上角頭像 → **My Profile** → **API Tokens** → **Create Token**。
   - 最下方選擇 **Create Custom Token（建立自訂權限權杖）**：
     - Token name：`fb-bot-kv-editor`
     - Permissions：**Account** → **Workers KV Storage** → **Edit**
     - Account Resources：Include → 選您的帳號
   - 點擊 **Continue to summary** → **Create Token**，立刻複製並妥善記下這組 Token（這就是 **`CF_API_TOKEN`**，頁面關掉後不會再顯示）。

---

## 步驟二：建立公開 GitHub Repo 與設定加密 Secrets

### 1. 重置 Discord Bot Token（強烈建議）
因為舊版 `config.py` 曾經寫過明文 Token，上雲端前建議先到 [Discord Developer Portal](https://discord.com/developers/applications) → 點您的 Bot 應用程式 → **Bot** → **Reset Token**，把新的 Bot Token 同時更新到本機 `.env` 並準備填進下一步的 GitHub Secrets。

### 2. 建立公開 GitHub Repository 並推送程式碼
1. 到 [GitHub New Repository](https://github.com/new) 建立一個 **Public（公開）** Repository，命名例如 `fb-group-discord-bot`（**不要**勾選 README 或 .gitignore）。
2. 在您電腦的專案資料夾 `C:\Users\User\Documents\AI專案\臉書爬蟲` 開啟 PowerShell，**先檢查準備提交的檔案清單**：
   ```powershell
   git status
   ```
   > ⚠️ **安全確認**：請確認輸出清單中**絕對沒有** `.env`、`cf_deploy.env`、`fb_session.json`、`cookies.json`、`subscriptions.json`、`notified_posts.json`！
3. 確認清單乾淨後，推送到您的公開 Repo（請將 `<您的帳號>` 換成您的 GitHub 使用者名稱）：
   ```powershell
   git add .
   git commit -m "Cloudflare Workers + GitHub Actions deployment"
   git branch -M main
   git remote add origin https://github.com/<您的帳號>/fb-group-discord-bot.git
   git push -u origin main
   ```

### 3. 設定 GitHub Actions Secrets（加密存放，訪客完全看不到）
進入您剛建立的 GitHub Repo → 上方分頁 **Settings** → 左側 **Secrets and variables** → **Actions** → **New repository secret**，依序新增以下 Secrets：

| Secret 名稱 | 填入內容 | 備註 |
| :--- | :--- | :--- |
| `DISCORD_BOT_TOKEN` | 您的 Discord Bot Token | 建議使用剛 Reset 過的新 Token |
| `TARGET_GROUPS` | 您的目標 FB 社團網址 | 多個可用英文逗號分隔，確保社團網址不進公開程式碼 |
| `CF_ACCOUNT_ID` | 步驟一取得的 Account ID | |
| `CF_KV_NAMESPACE_ID` | 步驟一建立的 KV Namespace ID | |
| `CF_API_TOKEN` | 步驟一產生的 `fb-bot-kv-editor` Token | |
| `FB_EMAIL` | *(選填)* FB 登入帳號 | 憑證失效時才嘗試自動重登 |
| `FB_PASSWORD` | *(選填)* FB 登入密碼 | 若該帳號有開 2FA，建議可不填，交給人工更新即可 |

### 4. 建立專供 `/立即巡邏` 觸發用的最小權限 GitHub Token
為了讓 Cloudflare Worker 的 `/立即巡邏` 指令能遠端啟動 GitHub Actions：
1. 開啟 GitHub 右上角頭像 → **Settings** → 左側最下方 **Developer settings** → **Personal access tokens** → **Fine-grained tokens** → **Generate new token**。
2. Token name：`fb-bot-dispatch`
3. Repository access：選 **Only select repositories** → 只勾選剛建立的 `fb-group-discord-bot`。
4. Permissions → **Repository permissions** → 只將 **Actions** 設為 **Read and write**（其他全維持 No access）。
5. 產生後立刻複製這組 `github_pat_...` Token（這就是等一下 Worker 要填的 **`GITHUB_TOKEN`**）。

---

## 步驟三：部署 Cloudflare Worker（接管 Discord 指令）

這裡提供**最簡單的網頁貼上法**（完全不用裝 Node 套件或跑 CLI 指令）：

1. 登入 [Cloudflare Dashboard](https://dash.cloudflare.com/) → 左側進入 **Workers & Pages** → 點 **Create** → **Create Worker**（不要選 Pages）。
2. 名稱輸入 `fb-discord-bot-worker` → 點 **Deploy**（先用預設範本建起來）。
3. 部署後點右上角 **Edit code（編輯程式碼）**：
   - 把編輯器原本的程式碼**全部清空**。
   - 用記事本打開本機專案裡的 `cloudflare-worker\src\index.js`，全選複製並貼上進去 → 點右上角 **Deploy** 儲存。
4. 回到該 Worker 的 **Settings（設定）** 頁面，完成兩項綁定：
   - **Bindings（繫結 / KV Namespace Bindings）** → Add binding：
     - Type：`KV Namespace`
     - Variable name：**`BOT_KV`**（必須完全一致，全大寫）
     - KV Namespace：選步驟一建好的 `fb-bot-kv` → Save。
   - **Variables and Secrets（環境變數與密鑰）** → Add：
     - `GITHUB_OWNER`（Text）：您的 GitHub 使用者名稱
     - `GITHUB_REPO`（Text）：您的 Repo 名稱（例如 `fb-group-discord-bot`）
     - `GITHUB_WORKFLOW_FILE`（Text）：`crawl.yml`
     - `GITHUB_REF`（Text）：`main`
     - `GITHUB_TOKEN`（選 **Secret** 加密）：貼上步驟二-4 產生的 Fine-grained GitHub Token
     - `DISCORD_PUBLIC_KEY`（選 **Secret** 加密）：下一步到 Discord Developer Portal 複製後回來填（填完再按 Deploy / Save）。
5. 記下這個 Worker 的公開網址（例如 `https://fb-discord-bot-worker.<您的子網域>.workers.dev`）。

*(進階選項：若您熟悉 CLI，本專案也附有 `cloudflare-worker/wrangler.toml`，可搭配 `npx wrangler deploy` 部署。)*

---

## 步驟四：設定 Discord Developer Portal 與註冊斜線指令

1. 打開 [Discord Developer Portal](https://discord.com/developers/applications) → 點選您的機器人應用程式。
2. 在 **General Information** 頁面：
   - 複製 **PUBLIC KEY**（一串 64 字元十六進位字串），先回到 Cloudflare Worker 的 Settings → Variables and Secrets，新增 Secret **`DISCORD_PUBLIC_KEY`** 貼上並儲存部署。
   - 回到 Discord 的 **General Information**，將剛才記下的 Worker 網址（例如 `https://fb-discord-bot-worker.<您的子網域>.workers.dev`）貼入 **INTERACTIONS ENDPOINT URL**，然後點擊 **Save Changes**。
   - Discord 會立刻發送一次 Ed25519 簽章驗證 `PING`，看到上方出現綠色「All your edits have been carefully saved」即代表 Worker 簽章驗證通過！
3. 在您電腦的專案資料夾開啟 PowerShell，執行一次指令註冊工具（將 6 個斜線指令全域同步到 Discord）：
   ```powershell
   python register_commands.py
   ```
   看到 `✅ 斜線指令註冊成功！共同步 6 個全域指令` 即可。

---

## 步驟五：從電腦一鍵上傳 FB 憑證與現有訂閱資料

1. 在您電腦的專案資料夾開啟 PowerShell，**第一次上線建議加上 `--with-data`**，把本機的 `fb_session.json`、現有訂閱清單（`subscriptions.json`）與已通知紀錄（`notified_posts.json`）一次加密上傳到 Cloudflare KV：
   ```powershell
   python upload_session.py --with-data
   ```
2. 初次執行會提示輸入 `CF_ACCOUNT_ID`、`CF_KV_NAMESPACE_ID`、`CF_API_TOKEN`，輸入後會自動儲存在本機 `cf_deploy.env`（已被 `.gitignore` 忽略）。
3. 之後若只需要更新 FB 登入憑證（不動到雲端上其他人新增的訂閱），直接雙擊專案資料夾裡的 **`upload_session.bat`** 即可。

---

## 步驟六：Discord 實測與日常維運注意事項

### 1. 立即實測
1. 在 Discord 頻道輸入 **`/我的清單`** → 確認能讀到您原本訂閱的關鍵字。
2. 試著執行 **`/取消關注`** → 確認按鈕彈出與點擊刪除正常，再用 **`/關注`** 加回來。
3. 如果先前還沒設定過或更換了頻道，執行一次 **`/設定通知頻道`**。
4. 輸入 **`/立即巡邏`** → 機器人會回覆已觸發雲端巡邏，約 20～40 秒後到您 GitHub Repo 的 **Actions** 分頁，即可看到 `FB Group Scraper` 成功跑完一輪（Log 只會顯示抓到幾篇、觸發幾則通知，不會外洩貼文與社團網址）。

### 2. 日常運作與維運須知
- **為什麼 Discord 成員清單裡機器人顯示「離線」？**
  這是 **Discord HTTP Interactions 架構的正常現象**！因為沒有常駐 WebSocket 連線而是由 Cloudflare Workers 即時接收指令，所有 `/關注`、`/取消關注`、`/我的清單`、`/立即巡邏` 依然 **24 小時秒回可用**。
- **Facebook 憑證過期怎麼辦？**
  1. 雲端巡邏遇到 FB 登入失效時，會先嘗試自動重登（若 `.env`/Secrets 有填帳密且未達 6 小時冷卻）；若遇到兩步驟驗證或安全檢查，會在 Discord 通知頻道發出紅色警告卡片。
  2. 在自己電腦開無痕瀏覽器登入該 FB 帳號 → 用 **Cookie-Editor** 匯出並貼到本機專案的 `cookies.json`。
  3. 雙擊 **`upload_session.bat`**，自動轉換、刪除暫存 `cookies.json` 並上傳到 Cloudflare KV 即完成更新。
- **GitHub Actions 60 天排程休眠提醒**：
  GitHub 官方規定公開 Repo 若超過 **60 天完全沒有任何 commit**，會自動暫停定時排程並寄 Email 通知您，屆時只要到 GitHub Actions 頁面點一下 **Enable workflow** 即可繼續免費運作。
- **請勿同時在本機與雲端雙開**：
  雲端上線後，本機就不需要再執行 `start.bat` 了，避免同一個 Facebook 帳號同時從台灣家裡與 GitHub 美國機房登入而觸發安全檢查。
