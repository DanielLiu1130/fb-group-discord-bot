const INTERACTION_TYPE = {
  PING: 1,
  APPLICATION_COMMAND: 2,
  MESSAGE_COMPONENT: 3,
};

const RESPONSE_TYPE = {
  PONG: 1,
  CHANNEL_MESSAGE_WITH_SOURCE: 4,
  UPDATE_MESSAGE: 7,
};

const EPHEMERAL_FLAG = 64;
const MANUAL_CRAWL_COOLDOWN_SECONDS = 60;
const textEncoder = new TextEncoder();

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

function replyMessage(content, { ephemeral = true, embeds = undefined, components = undefined } = {}) {
  const data = {};
  if (content !== undefined && content !== null) data.content = content;
  if (embeds !== undefined) data.embeds = embeds;
  if (components !== undefined) data.components = components;
  if (ephemeral) data.flags = EPHEMERAL_FLAG;
  return jsonResponse({
    type: RESPONSE_TYPE.CHANNEL_MESSAGE_WITH_SOURCE,
    data,
  });
}

function updateComponentMessage(content, components = []) {
  return jsonResponse({
    type: RESPONSE_TYPE.UPDATE_MESSAGE,
    data: { content, components },
  });
}

function hexToUint8Array(hex) {
  if (!hex || typeof hex !== "string" || hex.length % 2 !== 0 || !/^[0-9a-fA-F]+$/.test(hex)) {
    return null;
  }
  const bytes = new Uint8Array(hex.length / 2);
  for (let i = 0; i < hex.length; i += 2) {
    bytes[i / 2] = parseInt(hex.slice(i, i + 2), 16);
  }
  return bytes;
}

export async function verifyDiscordSignature(request, rawBody, publicKeyHex) {
  const signatureHex = request.headers.get("x-signature-ed25519");
  const timestamp = request.headers.get("x-signature-timestamp");

  if (!signatureHex || !timestamp || !publicKeyHex) {
    return false;
  }

  const publicKeyBytes = hexToUint8Array(publicKeyHex.trim());
  const signatureBytes = hexToUint8Array(signatureHex.trim());
  if (!publicKeyBytes || publicKeyBytes.length !== 32 || !signatureBytes || signatureBytes.length !== 64) {
    return false;
  }

  try {
    const cryptoKey = await crypto.subtle.importKey(
      "raw",
      publicKeyBytes,
      { name: "Ed25519" },
      false,
      ["verify"]
    );
    const payloadBytes = textEncoder.encode(`${timestamp}${rawBody}`);
    return await crypto.subtle.verify("Ed25519", cryptoKey, signatureBytes, payloadBytes);
  } catch {
    return false;
  }
}

function normalizeSubscriptions(raw) {
  if (!raw || typeof raw !== "object") {
    return { channel_id: null, users: {} };
  }
  const users = raw.users && typeof raw.users === "object" ? raw.users : {};
  const cleanedUsers = {};

  for (const [uid, info] of Object.entries(users)) {
    if (!info || typeof info !== "object") continue;
    const seen = new Set();
    const subs = [];
    for (const item of Array.isArray(info.subs) ? info.subs : []) {
      const kw = (typeof item === "object" && item !== null ? String(item.keyword || "") : String(item || "")).trim();
      if (kw && !seen.has(kw)) {
        seen.add(kw);
        subs.push(kw);
      }
    }
    cleanedUsers[String(uid)] = {
      name: String(info.name || "成員"),
      subs,
    };
  }

  return {
    channel_id: raw.channel_id ? String(raw.channel_id) : null,
    users: cleanedUsers,
  };
}

async function loadSubscriptions(env) {
  if (!env.BOT_KV) {
    throw new Error("尚未在 Cloudflare Worker 綁定 BOT_KV 命名空間。");
  }
  const raw = await env.BOT_KV.get("subscriptions", "json");
  return normalizeSubscriptions(raw);
}

async function saveSubscriptions(env, subs) {
  await env.BOT_KV.put("subscriptions", JSON.stringify(subs));
}

function getActor(interaction) {
  const userObj = interaction.member?.user || interaction.user || {};
  const userId = String(userObj.id || "");
  const displayName = String(
    interaction.member?.nick ||
      userObj.global_name ||
      userObj.username ||
      "成員"
  );
  return { userId, displayName };
}

function getOptionValue(interaction, name) {
  const options = interaction.data?.options;
  if (!Array.isArray(options)) return null;
  const found = options.find((opt) => opt.name === name);
  return found ? String(found.value || "") : null;
}

function splitKeywords(rawInput) {
  return String(rawInput || "")
    .trim()
    .split(/[,，、;；\n]+/)
    .map((k) => k.trim())
    .filter(Boolean);
}

function buildUnsubscribeComponents(keywords) {
  const limited = keywords.slice(0, 20);
  const rows = [];

  for (let i = 0; i < limited.length; i += 5) {
    const chunk = limited.slice(i, i + 5);
    rows.push({
      type: 1,
      components: chunk.map((kw) => ({
        type: 2,
        style: 2,
        label: kw.slice(0, 80),
        emoji: { name: "❌" },
        custom_id: `unsub_rm:${kw}`.slice(0, 100),
      })),
    });
  }

  rows.push({
    type: 1,
    components: [
      {
        type: 2,
        style: 4,
        label: "💥 一鍵清空全部",
        custom_id: "unsub_clear_all",
      },
      {
        type: 2,
        style: 2,
        label: "關閉選單",
        custom_id: "unsub_close",
      },
    ],
  });

  return rows;
}

async function handleSetChannel(interaction, env) {
  const subs = await loadSubscriptions(env);
  subs.channel_id = String(interaction.channel_id);
  await saveSubscriptions(env, subs);

  return replyMessage(
    "📢 **通知頻道設定完成！**\n未來只要在社團發現成員關注的二手商品，機器人都會在此頻道發送貼文並 **@標記** 想買的人！",
    { ephemeral: false }
  );
}

async function handleSubscribe(interaction, env) {
  const rawList = getOptionValue(interaction, "商品清單") || "";
  const newKeywords = splitKeywords(rawList);

  if (newKeywords.length === 0) {
    return replyMessage(
      "❌ 請輸入想找的商品名稱！若要一次關注多樣，請用頓號或逗號隔開（例如：`螢幕、滑鼠、便當`）。",
      { ephemeral: true }
    );
  }

  const tooLong = newKeywords.filter((k) => k.length > 80);
  if (tooLong.length > 0) {
    return replyMessage("❌ 單一關鍵字請勿超過 80 個字元。", { ephemeral: true });
  }

  const { userId, displayName } = getActor(interaction);
  const subs = await loadSubscriptions(env);
  if (!subs.users[userId]) {
    subs.users[userId] = { name: displayName, subs: [] };
  }
  const userInfo = subs.users[userId];
  userInfo.name = displayName;

  const addedList = [];
  const alreadyList = [];
  for (const kw of newKeywords) {
    if (!userInfo.subs.includes(kw)) {
      userInfo.subs.push(kw);
      addedList.push(kw);
    } else {
      alreadyList.push(kw);
    }
  }

  await saveSubscriptions(env, subs);

  const lines = [];
  if (addedList.length > 0) {
    lines.push(
      `✅ **成功新增關注（共 ${addedList.length} 項）：** ` +
        addedList.map((k) => `\`${k}\``).join("、")
    );
  }
  if (alreadyList.length > 0) {
    lines.push(
      "ℹ️ **已在關注清單中：** " + alreadyList.map((k) => `\`${k}\``).join("、")
    );
  }
  lines.push("\n💡 *小秘訣：想要同時關注多種商品，直接用「頓號、」或「逗號,」分隔即可一次全部加入！*");
  lines.push("只要社團有人發布上述物品，機器人就會立刻在此 `@標記你` 喔！");

  return replyMessage(lines.join("\n"), { ephemeral: true });
}

async function handleUnsubscribe(interaction, env) {
  const { userId } = getActor(interaction);
  const subs = await loadSubscriptions(env);
  const userInfo = subs.users[userId];
  const currentKeywords = userInfo ? userInfo.subs : [];

  if (currentKeywords.length === 0) {
    return replyMessage("你目前尚未關注任何商品喔！可以使用 `/關注` 來新增。", {
      ephemeral: true,
    });
  }

  const targetInput = getOptionValue(interaction, "欲取消商品");
  if (targetInput && targetInput.trim()) {
    const targets = new Set(splitKeywords(targetInput).map((k) => k.toLowerCase()));
    const deleted = [];
    const remaining = [];

    for (const kw of currentKeywords) {
      if (targets.has(kw.toLowerCase())) {
        deleted.push(kw);
      } else {
        remaining.push(kw);
      }
    }

    if (deleted.length > 0) {
      userInfo.subs = remaining;
      await saveSubscriptions(env, subs);
      const delStr = deleted.map((k) => `\`${k}\``).join("、");
      return replyMessage(`✅ 已成功為你取消關注 **${deleted.length}** 個商品：${delStr}！`, {
        ephemeral: true,
      });
    }
    return replyMessage("找不到符合你輸入的商品，請使用 `/我的清單` 查看目前已關注的物品。", {
      ephemeral: true,
    });
  }

  return replyMessage(
    "👇 **請直接點擊下方想取消的商品按鈕（點一下立刻刪除，可連續點擊）：**",
    {
      ephemeral: true,
      components: buildUnsubscribeComponents(currentKeywords),
    }
  );
}

async function handleMyList(interaction, env) {
  const { userId, displayName } = getActor(interaction);
  const subs = await loadSubscriptions(env);
  const userInfo = subs.users[userId];
  const currentSubs = userInfo ? userInfo.subs : [];

  if (currentSubs.length === 0) {
    return replyMessage(
      "你目前還沒有關注任何商品！\n快使用 `/關注` 輸入想找的物品（多個可用頓號「、」或逗號「,」分隔）～",
      { ephemeral: true }
    );
  }

  const lines = currentSubs.map((kw, idx) => `**${idx + 1}.** \`${kw}\``);
  return replyMessage(null, {
    ephemeral: true,
    embeds: [
      {
        title: `📋 ${displayName} 的關注清單（共 ${currentSubs.length} 項）`,
        description: lines.join("\n"),
        color: 0x3498db,
        footer: {
          text: "提示：使用 /關注 可用頓號一次新增多項，使用 /取消關注 可直接點擊按鈕刪除",
        },
      },
    ],
  });
}

async function handleCheckNow(interaction, env) {
  if (!env.GITHUB_OWNER || !env.GITHUB_REPO || !env.GITHUB_TOKEN) {
    return replyMessage(
      "❌ 尚未在 Cloudflare Worker 設定 GitHub 觸發參數（`GITHUB_OWNER`、`GITHUB_REPO`、`GITHUB_TOKEN`）。",
      { ephemeral: true }
    );
  }

  const subs = await loadSubscriptions(env);
  if (!subs.channel_id) {
    return replyMessage("📢 尚未設定通知頻道，請先在目標頻道輸入 `/設定通知頻道`！", {
      ephemeral: false,
    });
  }

  const nowSec = Math.floor(Date.now() / 1000);
  const lastTriggerRaw = await env.BOT_KV.get("manual_trigger_ts");
  const lastTriggerSec = lastTriggerRaw ? Number(lastTriggerRaw) : 0;

  if (Number.isFinite(lastTriggerSec) && nowSec - lastTriggerSec < MANUAL_CRAWL_COOLDOWN_SECONDS) {
    const waitSec = MANUAL_CRAWL_COOLDOWN_SECONDS - (nowSec - lastTriggerSec);
    return replyMessage(`⏳ 剛剛才觸發過巡邏，請稍候 **${waitSec} 秒**後再試！`, {
      ephemeral: true,
    });
  }

  const workflowFile = env.GITHUB_WORKFLOW_FILE || "crawl.yml";
  const branchRef = env.GITHUB_REF || "main";
  const dispatchUrl = `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/actions/workflows/${workflowFile}/dispatches`;

  const ghResp = await fetch(dispatchUrl, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "fb-discord-bot-worker",
      "X-GitHub-Api-Version": "2022-11-28",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      ref: branchRef,
      inputs: { trigger_source: "discord" },
    }),
  });

  if (ghResp.status !== 204) {
    return replyMessage(
      `❌ 觸發 GitHub Actions 巡邏失敗（HTTP ${ghResp.status}），請檢查 Worker 的 \`GITHUB_TOKEN\` 權限與 Repo 設定。`,
      { ephemeral: false }
    );
  }

  await env.BOT_KV.put("manual_trigger_ts", String(nowSec));
  return replyMessage(
    "🚀 **已成功觸發雲端巡邏！**\nGitHub Actions 正在啟動無頭瀏覽器掃描社團（約需 20～40 秒），若有符合成員關注的商品將會自動推播到此頻道。",
    { ephemeral: false }
  );
}

function handleHelp() {
  return replyMessage(null, {
    ephemeral: true,
    embeds: [
      {
        title: "🤖 【清交二手拍拍賣小幫手】使用指南",
        description:
          "自動巡邏 Facebook 二手社團，只要有人發布你想要的物品，就會在伺服器標記通知你！",
        color: 0xf1c40f,
        fields: [
          {
            name: "📌 核心指令說明",
            value: [
              "• `/關注 <商品清單>`：設定想找的物品，**可用「頓號、」或「逗號,」分隔**（例如 `/關注 商品清單:螢幕、滑鼠、便當`）",
              "• `/取消關注`：**彈出商品獨立按鈕**，直接點一下即刪除，亦提供「一鍵清空」",
              "• `/我的清單`：查看目前關注的所有物品列表",
              "• `/設定通知頻道`：設定商品推播廣播頻道（由管理員設定）",
              "• `/立即巡邏`：立刻到社團抓取一次最新貼文",
            ].join("\n"),
            inline: false,
          },
        ],
        footer: { text: "支援多人各自設定，社團新貼文自動 @標記 想買的人！" },
      },
    ],
  });
}

async function handleComponentInteraction(interaction, env) {
  const customId = String(interaction.data?.custom_id || "");

  if (customId === "unsub_close") {
    return updateComponentMessage("已關閉選單。", []);
  }

  const { userId } = getActor(interaction);
  const subs = await loadSubscriptions(env);
  const userInfo = subs.users[userId];

  if (customId === "unsub_clear_all") {
    if (userInfo) {
      userInfo.subs = [];
      await saveSubscriptions(env, subs);
    }
    return updateComponentMessage("💥 **已成功清空所有關注的商品！** 目前關注清單為空。", []);
  }

  if (customId.startsWith("unsub_rm:")) {
    const targetKw = customId.slice("unsub_rm:".length);
    let remaining = userInfo ? userInfo.subs : [];

    if (userInfo) {
      remaining = userInfo.subs.filter((k) => k !== targetKw);
      userInfo.subs = remaining;
      await saveSubscriptions(env, subs);
    }

    if (remaining.length > 0) {
      return updateComponentMessage(
        `🗑️ 已取消關注「**${targetKw}**」！\n若還要刪除其他商品，可直接點擊下方剩餘按鈕：`,
        buildUnsubscribeComponents(remaining)
      );
    }
    return updateComponentMessage(
      `🗑️ 已取消關注「**${targetKw}**」！\n✅ **目前你的關注清單已經全部清空囉！**`,
      []
    );
  }

  return updateComponentMessage("未知的操作按鈕。", []);
}

export default {
  async fetch(request, env) {
    if (request.method === "GET") {
      return new Response("FB Discord Bot Worker is running.", {
        status: 200,
        headers: { "content-type": "text/plain; charset=utf-8" },
      });
    }

    if (request.method !== "POST") {
      return new Response("Method Not Allowed", { status: 405 });
    }

    const rawBody = await request.text();
    const isValid = await verifyDiscordSignature(request, rawBody, env.DISCORD_PUBLIC_KEY);
    if (!isValid) {
      return new Response("Invalid request signature", { status: 401 });
    }

    let interaction;
    try {
      interaction = JSON.parse(rawBody);
    } catch {
      return new Response("Invalid JSON", { status: 400 });
    }

    if (interaction.type === INTERACTION_TYPE.PING) {
      return jsonResponse({ type: RESPONSE_TYPE.PONG });
    }

    if (interaction.type === INTERACTION_TYPE.APPLICATION_COMMAND) {
      const commandName = interaction.data?.name;
      switch (commandName) {
        case "設定通知頻道":
          return await handleSetChannel(interaction, env);
        case "關注":
          return await handleSubscribe(interaction, env);
        case "取消關注":
          return await handleUnsubscribe(interaction, env);
        case "我的清單":
          return await handleMyList(interaction, env);
        case "立即巡邏":
          return await handleCheckNow(interaction, env);
        case "幫助":
          return handleHelp();
        default:
          return replyMessage(`未知指令：${commandName}`, { ephemeral: true });
      }
    }

    if (interaction.type === INTERACTION_TYPE.MESSAGE_COMPONENT) {
      return await handleComponentInteraction(interaction, env);
    }

    return new Response("Unsupported interaction type", { status: 400 });
  },
};
