// Activity 内かどうかを判定して初期化する。
// ブラウザで開いた場合は何もせずアプリを起動するだけ。
const params = new URLSearchParams(location.search);
const frameId = params.get("frame_id");
const CLIENT_ID = window.__CLIENT_ID__ || "";

// Discord のプロキシは環境によって /.proxy を剥がす場合と残す場合がある。
// どちらでも動くよう、実際に叩いて通る方を採用する。
const CANDIDATES = ["/.proxy", ""];

function panel(msg, detail, hint) {
  document.body.innerHTML = `<div style="margin:auto;padding:56px 24px;max-width:520px;text-align:center">
    <div style="font-family:Cinzel,serif;color:#C8AA6E;letter-spacing:.14em;margin-bottom:16px">CLIPVAULT</div>
    <p style="color:#F0E6D2;margin-bottom:10px;font-size:15px">${msg}</p>
    <p style="color:#78899A;font-size:12.5px;line-height:1.7;word-break:break-word">${detail || ""}</p>
    ${hint ? `<p style="color:#0AC8B9;font-size:12px;margin-top:14px">${hint}</p>` : ""}
  </div>`;
}

async function probe(path, init) {
  let lastErr = "";
  for (const p of CANDIDATES) {
    try {
      const r = await fetch(p + path, init);
      if (r.ok) return { prefix: p, res: r };
      lastErr = `${p || "(なし)"}${path} -> ${r.status}`;
    } catch (e) {
      lastErr = `${p || "(なし)"}${path} -> ${e.message}`;
    }
  }
  throw new Error(lastErr || "到達できません");
}

async function startActivity() {
  if (!CLIENT_ID) {
    panel("CLIENT_ID が未設定です", "VM の .env に CLIENT_ID を設定して bot を再起動してください。");
    return false;
  }

  const { DiscordSDK } = await import("/assets/embedded-app-sdk.js");
  const sdk = new DiscordSDK(CLIENT_ID);
  await sdk.ready();
  console.log("[clipvault] SDK ready");

  const { code } = await sdk.commands.authorize({
    client_id: CLIENT_ID,
    response_type: "code",
    state: "",
    prompt: "none",
    scope: ["identify"],
  });
  console.log("[clipvault] authorized");

  const { prefix, res } = await probe("/api/token", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code }),
  });
  const { access_token, cv_token, name } = await res.json();
  console.log("[clipvault] token exchanged, prefix =", prefix || "(なし)", "user =", name);

  await sdk.commands.authenticate({ access_token });

  // トークン交換が通ったプレフィックスで API も通るか確かめる
  await probe("/api/me", { headers: { "X-ClipVault-Token": cv_token } });

  window.CV_PREFIX = prefix;
  window.CV_TOKEN = cv_token;
  window.CV_ACTIVITY = true;
  window.__discordSdk = sdk;
  return true;
}

(async () => {
  try {
    if (frameId) {
      if (!(await startActivity())) return;
    } else {
      window.CV_PREFIX = "";
      window.CV_ACTIVITY = false;
    }
    await window.__start();
  } catch (e) {
    console.error("[clipvault]", e);
    panel(
      frameId ? "Discord との接続に失敗しました" : "ライブラリを読み込めませんでした",
      String(e && e.message ? e.message : e),
      frameId
        ? "Ctrl+Shift+I で開発者ツールを開くと詳細が見られます。"
        : "Discord で /library を実行してリンクを取り直してください。"
    );
  }
})();
