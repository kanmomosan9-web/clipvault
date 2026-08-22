/**
 * web/index.html を jsdom で読み込み、API を差し替えて実際に描画させる。
 *
 * innerHTML="" で別の要素を巻き込んで消す、といった DOM 破壊は
 * 構文チェックでは見つからないため、ここで実行して確かめる。
 *
 *   node selftest_dom.js
 */
const fs = require("fs");
const path = require("path");
const { JSDOM } = require(process.env.JSDOM || "/tmp/bundle/node_modules/jsdom");

const CLIPS = [
  { id: 1, title: "ペンタキル", game: "LoL", tags: ["集団戦"], by: "つるかん",
    duration: 47, stars: 12, comments: 2, date: "2026-08-18", favorited: false,
    thumb: "/thumb/1", media: "/media/1", jump: "https://discord.com/x",
    has_transcript: true, preview: { t: 3, text: "バロン取られた" } },
  { id: 2, title: "クラッチ", game: "VALORANT", tags: ["エース"], by: "たいち",
    duration: 29, stars: 5, comments: 0, date: "2026-08-17", favorited: true,
    thumb: "/thumb/2", media: "/media/2", jump: "https://discord.com/y",
    has_transcript: false, preview: null },
];

const DETAIL = {
  ...CLIPS[0], width: 1920, height: 1080, note: "", cdn: "",
  discord_stars: 3, favorited: false,
  reactions: { "🔥": [{ id: "1", name: "たいち" }] },
  segments: [{ start: 3, end: 4, text: "バロン取られた" },
              { start: 8, end: 9, text: "やばい" }],
  comments: [{ id: 1, user: "じんぼ", user_id: "77", at: 3,
               body: "ここが全部", created_at: new Date().toISOString() }],
};

const FACETS = {
  games: [{ name: "LoL", count: 1 }], tags: [{ name: "集団戦", count: 1 }],
  members: [{ name: "つるかん", count: 1 }], favorites: 1,
};

const calls = [];

function fakeFetch(url, init) {
  const u = String(url);
  calls.push((init && init.method ? init.method : "GET") + " " + u);
  const json = (o) => Promise.resolve({
    ok: true, status: 200, json: () => Promise.resolve(o),
    text: () => Promise.resolve(JSON.stringify(o)),
  });
  if (u.includes("/api/me")) return json({ id: "4242", name: "つるかん" });
  if (u.includes("/api/facets")) return json(FACETS);
  if (u.includes("/favorite")) return json({ favorited: true, count: 2 });
  if (u.includes("/reactions")) return json({ added: true, reactions: DETAIL.reactions });
  if (/\/api\/clips\/\d+$/.test(u)) return json(DETAIL);
  if (u.includes("/api/clips")) {
    return json({ total: CLIPS.length, offset: 0, has_more: false, items: CLIPS });
  }
  return json({});
}

const fail = [];
const check = (cond, label) => {
  console.log(`  ${cond ? "OK " : "NG "} ${label}`);
  if (!cond) fail.push(label);
};

async function boot(activity) {
  let html = fs.readFileSync(path.join(__dirname, "web", "index.html"), "utf8");
  html = html.replace("%%CLIENT_ID%%", "123").replace("%%CDN_PREFIX%%", "/.proxy/cdn");
  // boot.js はモジュールなので jsdom では読ませず、ブラウザ相当で起動する
  html = html.replace(/<script type="module"[^>]*><\/script>/, "");

  const dom = new JSDOM(html, { runScripts: "dangerously", pretendToBeVisual: true });
  const w = dom.window;
  w.fetch = fakeFetch;
  // jsdom に無いものを補う
  w.Element.prototype.scrollIntoView = function () {};
  w.HTMLMediaElement.prototype.load = () => {};
  w.HTMLMediaElement.prototype.play = () => Promise.resolve();
  w.HTMLMediaElement.prototype.pause = () => {};
  if (activity) {
    w.CV_PREFIX = "/.proxy"; w.CV_ACTIVITY = true; w.CV_TOKEN = "TOK";
  } else {
    w.CV_PREFIX = ""; w.CV_ACTIVITY = false;
  }

  await new Promise((r) => w.addEventListener("load", r));
  await w.__start();
  await new Promise((r) => setTimeout(r, 60));
  return w;
}

(async () => {
  const w = await boot(false);
  const $ = (id) => w.document.getElementById(id);

  console.log("\n=== 一覧 ===");
  const cards = w.document.querySelectorAll(".card");
  check(cards.length === 2, `カード ${cards.length} 件`);
  check($("n").textContent === "2", "件数表示");
  check(w.document.querySelectorAll(".card .fav").length === 2, "お気に入りボタン");
  check(w.document.querySelector(".card .fav.on") !== null, "★の初期状態が反映");
  check($("myrail").textContent.includes("お気に入り"), "サイドバーのMY欄");

  console.log("\n=== プレーヤーを開く ===");
  cards[0].onclick();
  await new Promise((r) => setTimeout(r, 80));

  const player = $("player");
  check(player.classList.contains("on"), "プレーヤーが表示された");
  check(player.classList.contains("inline"), "グリッド内に展開");
  check(player.parentNode === $("grid"), "グリッドの子として差し込まれた");

  const vid = $("screen").querySelector("video");
  check(vid !== null, "video 要素が生成された");
  check(!!vid && vid.getAttribute("src") === "/media/1", "動画の src");
  check(!!vid && vid.getAttribute("poster") === "/thumb/1", "poster");

  check($("favbtn") !== null, "お気に入りボタンが生存している");
  check($("favbtn").textContent.includes("お気に入り"), "お気に入りボタンの表示");
  check($("reacts").querySelectorAll(".rx").length >= 1, "リアクションが描画された");
  check($("lines").querySelectorAll(".line").length === 2, "文字起こし 2 行");
  check($("cms").querySelectorAll(".cm").length === 1, "コメント 1 件");
  check($("marks").querySelectorAll("u").length >= 2, "シークバーの目盛り");
  check($("tagline").querySelectorAll(".tchip").length >= 1, "タグ表示");

  console.log("\n=== リアクションを押しても壊れない ===");
  $("reacts").querySelector(".rx").onclick();
  await new Promise((r) => setTimeout(r, 60));
  check($("favbtn") !== null, "再描画後もお気に入りボタンが残る");
  check($("lines").querySelectorAll(".line").length === 2, "文字起こしが残る");

  console.log("\n=== 閉じる ===");
  $("back").onclick();
  await new Promise((r) => setTimeout(r, 60));
  check(!player.classList.contains("on"), "プレーヤーが閉じた");
  check(player.parentNode !== $("grid"), "グリッドから外れた");
  check(w.document.querySelectorAll(".card").length === 2, "カードが残っている");

  // ---------------------------------------------------------- Activity
  console.log("\n=== Activity モード ===");
  DETAIL.cdn = "https://cdn.discordapp.com/attachments/1/2/c.mp4?ex=A&is=B&hm=C";
  const wa = await boot(true);
  const $a = (id) => wa.document.getElementById(id);

  wa.document.querySelectorAll(".card")[0].onclick();
  await new Promise((r) => setTimeout(r, 80));

  const va = $a("screen").querySelector("video");
  check(va !== null, "video 要素が生成された");
  const src = va ? va.getAttribute("src") : "";
  console.log("     src =", src);
  check(src.startsWith("/.proxy/cdn/"), "CDN プロキシ経由の src");
  check(src.includes("ex=A") && src.includes("hm=C"), "署名クエリが保持されている");
  check(!src.includes("t=TOK"), "CDN 経路にはトークンを付けない");

  const th = wa.document.querySelector(".card img").getAttribute("src");
  console.log("     thumb =", th);
  check(th.startsWith("/.proxy/thumb/"), "サムネはプレフィックス付き");
  check(th.includes("t=TOK"), "サムネにはトークンが付く");

  check($a("lines").querySelectorAll(".line").length === 2, "文字起こしが描画された");
  check($a("favbtn") !== null, "お気に入りボタンが生存している");

  console.log("\n=== CDN 失敗時のフォールバック ===");
  va.dispatchEvent(new wa.window.Event("error"));
  await new Promise((r) => setTimeout(r, 40));
  const src2 = $a("screen").querySelector("video").getAttribute("src");
  console.log("     src =", src2);
  check(src2.includes("/media/1?stream=1"), "VM 中継へ切り替わる");
  check(src2.includes("t=TOK"), "中継にはトークンが付く");

  console.log(`\n${fail.length ? "NG: " + fail.join(" / ") : "DOM OK"}`);
  process.exit(fail.length ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
