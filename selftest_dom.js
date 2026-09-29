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
const PULSE = { seq: 10, changes: [] };
const NOTIFS = [
  { id: 1, kind: "comment", read: false, actor: "たいち", actors: ["たいち"], clip_id: 1,
    clip_title: "ペンタキル", thread_id: null, thread_title: "", body: "神", at: 3,
    created_at: new Date().toISOString() },
  { id: 2, kind: "reply", read: false, actor: "やしき", actors: ["やしき"], clip_id: null,
    clip_title: "", thread_id: 5, thread_title: "神プレイ集", body: "次は俺",
    created_at: new Date().toISOString() },
];
const THREADS = [{ id: 5, title: "神プレイ集", creator: "つるかん", creator_id: "4242",
  updated_at: new Date().toISOString(), posts: 2, videos: 1, cover: 1,
  last: { user: "たいち", text: "エグい" }, unread: 2 }];
const THREAD = { thread: { id: 5, title: "神プレイ集", creator: "つるかん", creator_id: "4242",
  mine: true, participants: 2 }, posts: [
  { id: 1, thread_id: 5, user: "つるかん", user_id: "4242", body: "貼ってこ",
    created_at: new Date().toISOString(), edited_at: null,
    clip: { id: 1, title: "ペンタキル", by: "つるかん", duration: 47, game: "LoL", stars: 3 },
    video: null },
  { id: 2, thread_id: 5, user: "たいち", user_id: "77", body: "エグい",
    created_at: new Date().toISOString(), edited_at: null, clip: null,
    video: { src: "/board/media/2", name: "a.mp4" } }] };
let UNAUTH = false;

function fakeFetch(url, init) {
  const u = String(url);
  calls.push((init && init.method ? init.method : "GET") + " " + u);
  const json = (o) => Promise.resolve({
    ok: true, status: 200, json: () => Promise.resolve(o),
    text: () => Promise.resolve(JSON.stringify(o)),
  });
  if (init && init.headers && init.headers.Range) {
    // 動画のプローブ。データは届いているがデコードできない状況を再現する
    return Promise.resolve({
      ok: true, status: 206,
      headers: { get: (k) => (k === "Content-Type" ? "video/mp4" : null) },
    });
  }
  if (u.includes("/api/me") && UNAUTH) {
    return Promise.resolve({ ok: false, status: 401, json: () => Promise.resolve({}) });
  }
  if (u.includes("/api/me")) return json({ id: "4242", name: "つるかん",
    browser_url: "https://clipvault.example.ts.net/?t=TOK" });
  if (u.includes("/api/log")) return json({ ok: true });
  if (u.includes("/api/version")) return json({ v: "999" });
  if (u.includes("/api/pulse")) return json({ notifications: 2, board: 3, v: "100", seq: PULSE.seq,
    changes: PULSE.changes });
  if (u.includes("/api/notifications/read")) return json({ unread: 0 });
  if (u.includes("/api/notifications")) return json({ unread: 2, dm: false, items: NOTIFS });
  if (u.includes("/api/settings")) return json({ dm: true });
  if (/\/api\/board\/threads\/\d+\/posts/.test(u)) return json({ post: { id: 99, thread_id: 5,
    user: "つるかん", user_id: "4242", body: "送った", created_at: new Date().toISOString(),
    edited_at: null, clip: null, video: null } });
  if (/\/api\/board\/threads\/\d+/.test(u)) return json(THREAD);
  if (u.includes("/api/board/threads") && init && init.method === "POST") return json({ id: 5, post_id: 1 });
  if (u.includes("/api/board/posts/") && init && init.method === "DELETE") return json({ ok: true });
  if (u.includes("/api/board/threads")) return json({ items: THREADS });
  if (u.includes("/api/session/")) return json({ host_id: null, host_name: "",
    is_host: false, view: {}, rev: 0, tick: 0, elapsed: 0, watchers: ["つるかん"] });
  if (u.includes("/api/facets")) return json(FACETS);
  if (u.includes("/favorite")) return json({ favorited: true, count: 2 });
  if (u.includes("/reactions")) return json({ added: true, reactions: DETAIL.reactions });
  if (u.includes("/seen")) return json({ ok: true });
  if (u.includes("/comments")) return json({ id: 99 });
  const m = u.match(/\/api\/clips\/(\d+)$/);
  if (m) {
    const base = CLIPS.find((c) => c.id === Number(m[1])) || CLIPS[0];
    return json({ ...DETAIL, ...base, id: Number(m[1]),
      comments: DETAIL.comments, segments: DETAIL.segments,
      reactions: DETAIL.reactions });
  }
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

async function boot(activity, url, opts) {
  opts = opts || {};
  let html = fs.readFileSync(path.join(__dirname, "web", "index.html"), "utf8");
  html = html.replace("%%CLIENT_ID%%", "123").replace("%%CDN_PREFIX%%", "/.proxy/cdn")
             .replace("%%UI_VERSION%%", "100");
  // boot.js はモジュールなので jsdom では読ませず、ブラウザ相当で起動する
  html = html.replace(/<script type="module"[^>]*><\/script>/, "");

  const dom = new JSDOM(html, { runScripts: "dangerously", pretendToBeVisual: true,
                                url: url || "https://clipvault.example.ts.net/",
                                beforeParse(win) {
                                  if (!opts.width) return;
                                  // 画面幅だけを見る簡易版の matchMedia
                                  win.matchMedia = (q) => {
                                    const mx = q.match(/max-width:\s*(\d+)px/);
                                    const coarse = q.includes("pointer:coarse");
                                    if (q.includes("display-mode")) {
                                      return { matches: !!opts.standalone, media: q,
                                               addEventListener() {}, addListener() {} };
                                    }
                                    const ok = (!mx || opts.width <= Number(mx[1]))
                                      && (!coarse || !!opts.touch);
                                    return { matches: ok, media: q,
                                             addEventListener() {}, addListener() {} };
                                  };
                                } });
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
  if (typeof w.__start !== "function") {
    throw new Error("window.__start が定義されていない（スクリプトの途中で例外）");
  }
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

  console.log("\n=== ロゴで復帰 ===");
  // 検索・絞り込み・プレーヤーを開いた状態を作る
  // 検索とタグ絞り込みを実際の操作で作る
  $a("q").value = "バロン";
  $a("q").oninput({ target: $a("q") });
  wa.document.querySelector("#games .tag").onclick();
  $a("myrail").querySelector(".tag").onclick();
  await new Promise((r) => setTimeout(r, 300));

  const before = wa.__state();
  console.log("     絞り込み後:", JSON.stringify(
    { query: before.query, game: before.filters.game, fav: before.onlyFav }));
  check(before.query === "バロン", "検索語が入っている");
  check(before.filters.game === "LoL", "タグ絞り込みが効いている");
  check(before.onlyFav === true, "お気に入り絞り込みが効いている");

  wa.document.querySelectorAll(".card")[0].onclick();
  await new Promise((r) => setTimeout(r, 80));
  check($a("player").classList.contains("on"), "プレーヤーが開いている");

  $a("home").onclick();
  await new Promise((r) => setTimeout(r, 120));

  const after = wa.__state();
  check(!$a("player").classList.contains("on"), "プレーヤーが閉じた");
  check($a("q").value === "" && after.query === "", "検索語が消えた");
  check(after.onlyFav === false, "お気に入り絞り込みが解除された");
  check(after.filters.game === null, "タグ絞り込みが解除された");
  check(after.openId === null, "開いているクリップが解除された");
  check(after.loading === false, "読み込みフラグが解除された");
  check(wa.document.querySelectorAll(".card").length === 2, "一覧が再取得された");
  check($a("mediaerr").hidden, "エラーパネルが消えた");

  console.log("\n=== 再生不能時の逃げ道 ===");
  $a("player").classList.add("on");
  wa.document.querySelectorAll(".card")[0].onclick();
  await new Promise((r) => setTimeout(r, 80));
  const v2 = $a("screen").querySelector("video");
  v2.dispatchEvent(new wa.window.Event("error"));   // 中継でも失敗
  await new Promise((r) => setTimeout(r, 120));
  const err = $a("mediaerr");
  check(!err.hidden, "エラーパネルが出た");
  check($a("err-jump") !== null, "Discord で再生ボタン");
  check($a("err-browser") !== null && !$a("err-browser").disabled,
        "ブラウザで開くボタン（有効）");
  check($a("err-dl") !== null && $a("err-dl").href.includes("stream=1"),
        "ダウンロードリンク");
  check(err.textContent.includes("ハードウェアアクセラレーション"),
        "対処の案内が出る");

  // ---------------------------------------------------------- ショート
  console.log("\n=== ショート ===");
  DETAIL.cdn = "";
  const ws = await boot(false);
  const $s = (id) => ws.document.getElementById(id);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  calls.length = 0;
  $s("shortsbtn").onclick();
  await sleep(120);
  let st = ws.__state().shorts;
  check(!$s("shorts").hidden, "ショート画面が開いた");
  check(st.on && st.idx === 0 && st.cur === 1, "1本目が選ばれている");
  const req = calls.find((c) => c.includes("sort=random"));
  check(!!req && /seed=\d+/.test(req) && req.includes("unseen=1"), "ランダム順で取得している");
  const sl = ws.document.querySelectorAll("#s-feed .slide[data-id]");
  check(sl.length === 2, `スライド ${sl.length} 枚`);
  check(ws.document.querySelector("#s-feed .s-end") !== null, "最後に『全部見ました』");
  const v0 = sl[0].querySelector("video");
  check(!!v0 && v0.getAttribute("src") === "/media/1", "1本目の動画");
  check(ws.__state().shorts.preload === "/media/2", "2本目を先読みしている");
  check(sl[0].querySelector(".s-cap") !== null, "字幕の枠");

  ws.document.dispatchEvent(new ws.KeyboardEvent("keydown", { key: "ArrowDown" }));
  await sleep(100);
  st = ws.__state().shorts;
  check(st.idx === 1 && st.cur === 2, "↓キーで次へ");
  check(!sl[0].querySelector("video"), "前の動画は破棄された");
  const v1 = sl[1].querySelector("video");
  check(!!v1, "2本目が再生対象");
  check(v1 === v0, "同じ video 要素を使い回している（音の許可が引き継がれる）");
  check(v1.getAttribute("src") === "/media/2", "src が 2 本目に替わった");

  // ダブルタップでお気に入り
  calls.length = 0;
  const tap = sl[1].querySelector(".s-tap");
  tap.onclick(); tap.onclick();
  await sleep(60);
  check(sl[1].querySelector(".s-burst").classList.contains("go"), "ダブルタップで★の演出");
  check(!calls.some((c) => c.includes("/api/clips/2/favorite")),
        "登録済みはダブルタップで外さない");
  calls.length = 0;
  const tap0 = sl[0].querySelector(".s-tap");
  tap0.onclick(); tap0.onclick();
  await sleep(60);
  check(calls.some((c) => c.includes("/api/clips/1/favorite")), "未登録はダブルタップで登録");

  // コメント欄
  sl[1].querySelector(".s-talk").onclick();
  await sleep(40);
  check($s("s-sheet").classList.contains("on"), "コメント欄が開いた");
  check($s("s-sheet-b").querySelectorAll(".cm").length === 1, "コメントが描画された");
  $s("s-tab-t").onclick();
  check($s("s-sheet-b").querySelectorAll(".line").length === 2, "文字起こしタブ");
  $s("s-sheet-x").onclick();

  // 3 秒で「見た」になる
  await sleep(3100);
  check(calls.some((c) => c.includes("/api/clips/2/seen")), "3秒見たら既読を送る");

  console.log("\n=== ショートの音 ===");
  // 音ありの自動再生が拒否される環境を再現する
  const realPlay = ws.HTMLMediaElement.prototype.play;
  let blocked = true;
  ws.HTMLMediaElement.prototype.play = function () {
    return blocked && !this.muted ? Promise.reject(new Error("NotAllowedError"))
                                  : Promise.resolve();
  };
  ws.document.dispatchEvent(new ws.KeyboardEvent("keydown", { key: "ArrowUp" }));
  await sleep(80);
  st = ws.__state().shorts;
  check(st.idx === 0 && st.autoMuted && !st.muted, "拒否されたら一時的に消音（自分の消音とは別）");
  check(!$s("s-hint").hidden, "『タップで音声オン』が出る");
  blocked = false;
  sl[0].querySelector(".s-tap").dispatchEvent(new ws.Event("pointerup", { bubbles: true }));
  await sleep(20);
  check(!ws.__state().shorts.autoMuted && !v0.muted, "画面に触れたら音が戻る");
  ws.document.dispatchEvent(new ws.KeyboardEvent("keydown", { key: "ArrowDown" }));
  await sleep(80);
  check(!v0.muted && !ws.__state().shorts.autoMuted, "スワイプ後も音ありのまま");
  sl[1].querySelector(".s-mute").onclick();
  check(v0.muted && ws.__state().shorts.muted, "🔊 ボタンで自分で消音");
  ws.document.dispatchEvent(new ws.KeyboardEvent("keydown", { key: "ArrowUp" }));
  await sleep(80);
  check(v0.muted, "自分で消音にしたら次のクリップも消音");
  sl[0].querySelector(".s-mute").onclick();
  check(!v0.muted, "もう一度押すと音が戻る");
  ws.HTMLMediaElement.prototype.play = realPlay;
  ws.document.dispatchEvent(new ws.KeyboardEvent("keydown", { key: "ArrowDown" }));
  await sleep(60);

  ws.document.dispatchEvent(new ws.KeyboardEvent("keydown", { key: "ArrowDown" }));
  await sleep(60);
  st = ws.__state().shorts;
  check(st.idx === 2 && st.cur === null, "最後まで進むと終わりの画面");

  ws.document.dispatchEvent(new ws.KeyboardEvent("keydown", { key: "Escape" }));
  await sleep(40);
  check($s("shorts").hidden && !ws.__state().shorts.on, "Esc で閉じた");
  check(ws.document.querySelectorAll(".card").length === 2, "一覧は残っている");

  console.log("\n=== /shorts のリンクから直接開く ===");
  calls.length = 0;
  const wu = await boot(false, "https://clipvault.example.ts.net/?t=X&mode=shorts&game=LoL");
  await sleep(120);
  check(wu.__state().shorts.on, "開いた直後にショート");
  check(calls.some((c) => c.includes("sort=random") && c.includes("game=LoL")),
        "ゲームの絞り込みが効いている");

  console.log("\n=== Discord から起動（instance_id 付き） ===");
  const errs = [];
  const wi = await boot(true, "https://123.discordsays.com/?instance_id=i-abc&frame_id=f&platform=desktop")
    .catch((e) => { errs.push(e.message); return null; });
  check(!!wi, "window.__start が定義されている" + (errs.length ? " — " + errs[0] : ""));
  if (wi) {
    check(wi.document.querySelectorAll(".card").length === 2, "一覧が表示された");
    check(!wi.document.getElementById("syncbar").hidden, "みんなで見るバーが出た");
    wi.document.getElementById("shortsbtn").onclick();
    await sleep(120);
    check(wi.__state().shorts.on, "Activity 内でもショートが開く");
    const sv = wi.document.querySelector("#s-feed video");
    check(!!sv && sv.getAttribute("src").startsWith("/.proxy/"), "動画はプロキシ経由");
    wi.close();
  }

  console.log("\n=== ブラウザ版は CDN を直接再生 ===");
  DETAIL.cdn = "https://cdn.discordapp.com/attachments/1/2/c.mp4?ex=A&hm=C";
  const wcd = await boot(false);
  wcd.document.querySelectorAll(".card")[0].onclick();
  await sleep(80);
  let vv = wcd.document.getElementById("screen").querySelector("video");
  check(vv.getAttribute("src") === DETAIL.cdn, "一覧のプレーヤーは CDN の URL を直接使う");
  vv.dispatchEvent(new wcd.Event("error"));
  await sleep(30);
  check(vv.getAttribute("src") === "/media/1?fresh=1", "失敗したら取り直し経路へ");
  wcd.document.getElementById("shortsbtn").onclick();
  await sleep(120);
  vv = wcd.document.querySelector("#s-feed video");
  check(vv && vv.getAttribute("src") === DETAIL.cdn, "ショートも CDN の URL を直接使う");
  vv.dispatchEvent(new wcd.Event("error"));
  await sleep(30);
  check(vv.getAttribute("src").endsWith("?fresh=1"), "ショートも失敗したら取り直し");
  wcd.close();
  DETAIL.cdn = "";

  console.log("\n=== 絞り込みのハイライト ===");
  const wf = await boot(false);
  const $f = (id) => wf.document.getElementById(id);
  check($f("fbar").hidden, "条件が無いときは札を出さない");
  wf.document.querySelector("#games .tag").onclick();
  await sleep(60);
  check(wf.document.querySelector("#games .tag").getAttribute("aria-pressed") === "true",
        "押したらすぐハイライトされる");
  check(!$f("fbar").hidden && $f("fbar").textContent.includes("LoL"), "『絞り込み中』の札が出る");
  check(wf.document.querySelector(".card .chip.hit") !== null, "当たったタグも金色");
  $f("fbar").querySelector(".fchip").onclick();
  await sleep(60);
  check($f("fbar").hidden && wf.__state().filters.game === null, "札の ✕ で外せる");
  const mine = [...$f("myrail").querySelectorAll(".tag")].find((b) => b.textContent.includes("自分の投稿"));
  mine.onclick();
  await sleep(40);
  check(wf.__state().filters.by === "つるかん" &&
        [...$f("myrail").querySelectorAll(".tag")][1].getAttribute("aria-pressed") === "true",
        "『自分の投稿』で絞れる");

  console.log("\n=== 通知と自動更新 ===");
  await sleep(40);
  check($f("nbadge").textContent === "2" && !$f("nbadge").hidden, "ベルに未読数");
  check($f("bbadge").textContent === "3", "掲示板ボタンに未読数");
  PULSE.changes = [{ id: 1, stars: 42, comments: 7 }];
  await wf.pulse();
  const c1 = wf.document.querySelector(".card .star");
  check(c1 && c1.textContent === "★42", "Discord のリアクションが一覧の数字に反映");
  PULSE.changes = [];
  $f("bellbtn").onclick({ stopPropagation() {} });
  await sleep(60);
  check(!$f("npanel").hidden && $f("nlist").querySelectorAll(".ni").length === 2, "通知の一覧");
  check($f("nlist").textContent.includes("あなたのクリップ「ペンタキル」にコメント"), "通知の文面");
  $f("nlist").querySelectorAll(".ni")[1].onclick();
  await sleep(120);
  check(wf.__state().board.on && wf.__state().board.tid === 5, "返信の通知から掲示板のスレッドへ");

  console.log("\n=== 掲示板 ===");
  check(wf.document.body.classList.contains("board-on"), "一覧の代わりに掲示板を表示");
  check($f("blist").querySelectorAll(".ti").length === 1, "スレッド一覧");
  check($f("bposts").querySelectorAll(".bp").length === 2, "書き込みの表示");
  check($f("bposts").querySelector(".ccard") !== null, "クリップはカードで表示");
  check($f("bposts").querySelector("video.bvid") !== null, "直接アップロードの動画は再生できる");
  check($f("bposts").querySelector(".bp .own") !== null, "自分の書き込みに編集・削除");
  check(!!$f("brename"), "立てた人にはタイトル変更・削除");
  $f("btext").value = "送った";
  await wf.sendPost();
  check($f("bposts").querySelectorAll(".bp").length === 3, "送信すると追加される");
  $f("bposts").querySelector(".ccard .cth").onclick();
  await sleep(60);
  check($f("bposts").querySelector(".ccard video") !== null, "カードを押すとその場で再生");
  $f("home").onclick();
  await sleep(80);
  check(!wf.__state().board.on && !wf.document.body.classList.contains("board-on"), "ロゴで一覧に戻る");

  console.log("\n=== 共有 ===");
  check($f("sharebtn").hidden, "クリップを開いていないときは共有ボタンを出さない");
  check(wf.document.querySelector("header").contains($f("sharebtn")), "共有ボタンは掲示板の横（ヘッダー）");
  wf.document.querySelectorAll(".card")[0].onclick();
  await sleep(80);
  check(!$f("sharebtn").hidden, "クリップを開くと共有ボタンが出る");
  $f("sharebtn").onclick();
  await sleep(80);
  check(!$f("modal").hidden && $f("mbox").textContent.includes("新しいスレッドを立てる"), "共有の画面");
  check($f("mbox").querySelector("#mtitle").value === "ペンタキル", "タイトルは動画名が入っている");
  check($f("mbox").querySelectorAll('.opt[data-t]').length === 2, "既存のスレッドを選べる");
  $f("mbox").querySelectorAll(".opt[data-t]")[1].onclick();
  calls.length = 0;
  $f("mbox").querySelector("#mok").onclick();
  await sleep(80);
  check(calls.some((c) => c.startsWith("POST") && c.includes("/api/board/threads/5/posts")),
        "既存のスレッドに投稿");
  check($f("modal").hidden && !$f("toast").hidden, "共有しましたの表示");
  $f("back").onclick();
  await sleep(40);
  check($f("sharebtn").hidden, "閉じると共有ボタンも消える");

  console.log("\n=== 削除の確認（Activity では confirm が使えない） ===");
  wf.confirm = () => { throw new Error("confirm は使わない"); };
  await wf.openBoard(5);
  await sleep(80);
  calls.length = 0;
  $f("bposts").querySelector(".bp .own .dl").onclick();
  await sleep(40);
  check(!$f("modal").hidden && $f("mbox").textContent.includes("削除しますか"), "自前の確認画面が出る");
  $f("mbox").querySelector("#myes").onclick();
  await sleep(60);
  check(calls.some((c) => c.startsWith("DELETE") && c.includes("/api/board/posts/1")), "削除が送られる");
  $f("bdel").onclick();
  await sleep(40);
  $f("mbox").querySelector("#mno").onclick();
  await sleep(40);
  check(!calls.some((c) => c.includes("DELETE") && c.includes("/api/board/threads/5")), "やめるを押すと消さない");
  wf.close();

  console.log("\n=== スマホの配置 ===");
  calls.length = 0;
  const wm = await boot(false, "https://clipvault.example.ts.net/", { width: 390, touch: true });
  const $m = (id) => wm.document.getElementById(id);
  let sm = wm.__state();
  check(sm.compact === true, "狭い画面として判定");
  check(!!$m("bnav") && $m("bnav").contains($m("menubtn")), "操作は下のバーにある");
  check(!wm.document.querySelector("header").contains($m("menubtn")), "上のバーには置かない");
  check($m("mpbody").contains($m("q")) && $m("mpbody").contains($m("sort")),
        "検索窓と並び替えはメニューの中");
  check($m("mpbody").querySelector("aside") !== null, "絞り込みもメニューの中");
  check(!wm.document.querySelector("header").contains($m("q")), "ヘッダーからは消えた");
  check($m("mpanel").hidden, "最初は閉じている");
  check(wm.document.querySelectorAll(".card").length === 2, "一覧は表示される");
  $m("menubtn").onclick();
  check(!$m("mpanel").hidden && $m("menubtn").getAttribute("aria-expanded") === "true",
        "バーを押すと開く");
  wm.document.querySelector("#games .tag").click();
  await sleep(80);
  check($m("mpanel").hidden, "絞り込みを選ぶと閉じる");
  check(!$m("mdot").hidden, "絞り込み中の印が出る");
  $m("nav-fav").onclick();
  await sleep(80);
  check(wm.__state().onlyFav && $m("nav-fav").getAttribute("aria-pressed") === "true",
        "下のバーでお気に入り絞り込み");
  $m("nav-home").onclick();
  await sleep(120);
  check(!wm.__state().onlyFav && wm.__state().filters.game === null, "一覧ボタンで解除");
  $m("menubtn").onclick();
  $m("nav-shorts").onclick();
  await sleep(120);
  check(wm.__state().shorts.on && $m("mpanel").hidden, "下のバーからショートを開ける");
  wm.close();

  console.log("\n=== スマホの Discord ではみんなで見るを出さない ===");
  calls.length = 0;
  const wp = await boot(true,
    "https://123.discordsays.com/?instance_id=i-abc&frame_id=f&platform=mobile",
    { width: 390, touch: true });
  await sleep(60);
  check(wp.__state().phone === true, "スマホとして判定");
  check(wp.document.body.classList.contains("dc-mobile"), "退出ボタンの場所を空ける");
  const hdr = wp.document.querySelector("header");
  check(!hdr.querySelector("button:not(.mark):not(#shortsbtn):not(#boardbtn):not(#bellbtn):not(#sharebtn)"),
        "上のバーはロゴとベルだけ（ショート・掲示板は CSS で非表示）");
  check(wp.document.getElementById("syncbar").hidden, "みんなで見るバーが出ない");
  check(!calls.some((c) => c.includes("/api/session/")), "同時視聴の通信をしない");
  wp.close();

  const wpc = await boot(true,
    "https://123.discordsays.com/?instance_id=i-abc&frame_id=f&platform=desktop",
    { width: 1400 });
  check(!wpc.document.getElementById("syncbar").hidden, "PC では従来どおり出る");
  check(!wpc.__state().compact && wpc.document.querySelector("header").contains(
    wpc.document.getElementById("q")), "PC の配置は変わらない");
  wpc.close();

  console.log("\n=== ブラウザ版のログインとアプリ化 ===");
  const wb = await boot(false, "https://cv.example.ts.net/?t=SECRET&mode=shorts&game=LoL",
                        { width: 390, touch: true });
  await sleep(100);
  check(!wb.location.search.includes("t=SECRET"), "アドレスバーからトークンを消す");
  check(wb.location.search.includes("mode=shorts"), "他の指定は残す");
  check(!wb.document.getElementById("install").hidden, "『ホーム画面に追加』が出る");
  check(!wb.document.getElementById("logout").hidden, "ログアウトが出る");
  wb.document.getElementById("install").onclick();
  check(!wb.document.getElementById("howto").hidden, "追加のしかたを案内する");
  check(!!wb.document.querySelector('link[rel="manifest"]'), "manifest を読み込む");
  check(wb.document.getElementById("update").hidden, "最初は更新のお知らせを出さない");
  await wb.checkUpdate();
  check(!wb.document.getElementById("update").hidden, "サーバー側が新しくなったら知らせる");
  wb.close();

  const wsa = await boot(false, "https://cv.example.ts.net/?source=app",
                         { width: 390, touch: true, standalone: true });
  check(wsa.document.getElementById("install").hidden, "アプリとして開いたら追加ボタンは出さない");
  wsa.close();

  const wact = await boot(true, "https://123.discordsays.com/?instance_id=i&platform=mobile",
                          { width: 390, touch: true });
  check(wact.document.getElementById("install").hidden, "Activity では追加ボタンを出さない");
  check(wact.document.getElementById("logout").hidden, "Activity ではログアウトを出さない");
  wact.close();

  UNAUTH = true;
  const wl = await boot(false, "https://cv.example.ts.net/?mode=shorts&t=OLD");
  await sleep(40);
  const a = wl.document.querySelector("a.login-btn");
  check(!!a, "期限切れなら『Discord でログイン』");
  const href = a ? a.getAttribute("href") : "";
  console.log("     href =", href);
  check(href.startsWith("/login?next=") && href.includes("mode%3Dshorts")
        && !href.includes("OLD"), "戻り先を渡し、古いトークンは渡さない");
  wl.close();
  UNAUTH = false;

  console.log("\n=== ショートの同時視聴（追従側） ===");
  await wu.applyView({ host_id: "9", host_name: "たいち", rev: 50,
                       view: { shorts: 2, playing: true, position: 1 } });
  await sleep(60);
  st = wu.__state().shorts;
  check(st.on && st.follow && st.cur === 2 && st.n === 1, "ホストのクリップ1本を映す");
  await wu.applyView({ host_id: "9", host_name: "たいち", rev: 51, view: { openId: null } });
  await sleep(40);
  check(!wu.__state().shorts.on, "ホストが閉じたら閉じる");

  console.log(`\n${fail.length ? "NG: " + fail.join(" / ") : "DOM OK"}`);
  [w, wa, ws, wu].forEach((x) => { try { x.close(); } catch (e) {} });
  process.exit(fail.length ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
