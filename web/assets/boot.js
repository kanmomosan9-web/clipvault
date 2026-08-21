// Activity 内かどうかを判定して初期化する。
// ブラウザで開いた場合は何もせずアプリを起動するだけ。
const params = new URLSearchParams(location.search);
const frameId = params.get("frame_id");
const CLIENT_ID = window.__CLIENT_ID__ || "";

function show(msg, detail) {
  document.body.innerHTML = `<div style="margin:auto;padding:60px 24px;max-width:460px;text-align:center">
    <div style="font-family:Cinzel,serif;color:#C8AA6E;letter-spacing:.14em;margin-bottom:14px">CLIPVAULT</div>
    <p style="color:#F0E6D2;margin-bottom:8px">${msg}</p>
    <p style="color:#78899A;font-size:12.5px">${detail || ""}</p></div>`;
}

async function startActivity() {
  if (!CLIENT_ID) return show("CLIENT_ID が未設定です", "VM の .env に CLIENT_ID を設定してください。");
  const { DiscordSDK } = await import("/assets/embedded-app-sdk.js");
  const sdk = new DiscordSDK(CLIENT_ID);
  await sdk.ready();

  const { code } = await sdk.commands.authorize({
    client_id: CLIENT_ID,
    response_type: "code",
    state: "",
    prompt: "none",
    scope: ["identify"],
  });

  const r = await fetch("/.proxy/api/token", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code }),
  });
  if (!r.ok) throw new Error("token exchange failed: " + r.status);
  const { access_token, cv_token } = await r.json();

  await sdk.commands.authenticate({ access_token });

  window.CV_PREFIX = "/.proxy";
  window.CV_TOKEN = cv_token;
  window.CV_ACTIVITY = true;
  window.__discordSdk = sdk;
}

(async () => {
  try {
    if (frameId) await startActivity();
    else { window.CV_PREFIX = ""; window.CV_ACTIVITY = false; }
    window.__start();
  } catch (e) {
    console.error(e);
    show("Discord との接続に失敗しました", String(e.message || e));
  }
})();
