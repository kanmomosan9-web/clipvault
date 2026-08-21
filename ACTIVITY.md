# Discord Activity 化

Discord の中で iframe として動かす。ブラウザを開かず、リンクも要らない。
**ボイスチャンネルに入る必要はない** — テキストチャンネルや DM からも起動できる。

ブラウザ版はそのまま残る。同じ画面を2つの入口から使う形。

---

## 前提

- サーバーの人数が **25人未満**（未認証 Activity の条件。25人ちょうどは避ける）
- 固定の HTTPS アドレス（Tailscale Funnel で無料で得られる）

## Step 1. 固定 HTTPS アドレスを用意する

使い捨ての `trycloudflare.com` は再起動のたびに URL が変わるので使えない。
URL Mappings に登録したドメインが変わると毎回設定し直しになる。

```bash
bash deploy/setup-tailscale.sh
```

`https://clipvault.tailXXXX.ts.net` のような固定アドレスが得られる。
ポートは開けない。Funnel が内側から繋ぎに行く。

初回は管理画面で Funnel の利用許可が必要なことがある。エラーが出たら
https://login.tailscale.com/admin/dns の Settings → Funnel を有効にして再実行。

**cloudflared はもう不要。** 止めてよい。

```bash
sudo systemctl disable --now cloudflared 2>/dev/null || true
```

## Step 2. 自宅PC の SSH トンネルを廃止する

Funnel の公開URLに直接つなげるので、トンネルが要らなくなる。

自宅PC の `.env`:

```
WORKER_API_URL=https://clipvault.tailXXXX.ts.net
WORKER_TOKEN=VMと同じ値
WORKER_TIER=gpu
WHISPER_MODEL=large-v3
WHISPER_DEVICE=cuda
WHISPER_COMPUTE=float16
AUTO_UPGRADE=1
```

`deploy\tunnel.bat` は不要。スタートアップからも外してよい。
起動は `deploy\start-worker.bat` だけ（落ちても自動で再起動する）。

> ジョブAPIが公開されることになるが、`Authorization: Bearer <WORKER_TOKEN>` で
> 保護されている。トークンは32バイトのランダムで、比較は定数時間。
> 気になるなら Funnel をやめて Tailscale のプライベートIP（`100.x.x.x`）に
> 繋ぐこともできる。その場合は Activity が使えなくなる。

## Step 3. Developer Portal の設定

https://discord.com/developers/applications → 対象のアプリ

### 3-1. CLIENT_ID と CLIENT_SECRET

- **General Information** → Application ID をコピー → `CLIENT_ID`
- **OAuth2** → Client Secret の Reset → コピー → `CLIENT_SECRET`

VM の `.env` に追記する。

```
ACTIVITY_ENABLED=1
CLIENT_ID=123456789012345678
CLIENT_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
PUBLIC_URL=https://clipvault.tailXXXX.ts.net
```

**CLIENT_SECRET はブラウザに渡らない。** コード交換はサーバー側の
`/api/token` だけで行う。

### 3-2. Activities を有効化

**Activities** → **Getting Started** → **Enable**

有効化すると `Launch` という Entry Point コマンドが自動で作られる。
これが App Launcher から起動するための入口になる。

### 3-3. URL Mappings

**Activities** → **URL Mappings** に2件登録する。

| Prefix | Target |
|---|---|
| `/` | `clipvault.tailXXXX.ts.net` |
| `/cdn` | `cdn.discordapp.com` |

**Prefix に `/.proxy` を書いてはいけない。** 「プレフィックスは .proxy を
含むことができません」と弾かれる。`/.proxy` は Discord が自動的に付けるもので、
登録するのは**その先の部分だけ**。

```
ポータルに登録:  /cdn        ->  cdn.discordapp.com
アプリが叩くURL:  /.proxy/cdn/attachments/...
                  ~~~~~~~ Discord が解決する
```

Target に `https://` は付けない。ホスト名だけ。

**2件目が重要。** Activity 内は Discord のプロキシを通るため、外部ドメインは
ここで宣言しないと読めない。これを入れておくと**動画は Discord のプロキシが
直接 CDN から取ってくる**ので、VM の帯域を一切消費しない。

```
Activity ──▶ /.proxy/cdn/attachments/... ──▶ Discord のプロキシ ──▶ CDN
                                              （VM を経由しない）
```

### 3-4. Installation Contexts

**Installation** → Installation Contexts で **Guild Install** を有効に。
DM でも使いたいなら **User Install** も。

## Step 4. 再起動して起動する

```bash
cd ~/clipvault
git pull
sudo systemctl restart clipvault-bot
```

Discord のテキストチャンネルで、入力欄の **＋** → **アプリを使う** →
ClipVault → **Launch**。

パネルが Discord 内で開く。

## 仕組み

```
起動 ──▶ iframe (xxx.discordsays.com)
           │
           │ ① frame_id クエリの有無で Activity かブラウザかを判定
           │ ② DiscordSDK.ready() → authorize() で認可コードを取得
           │ ③ POST /.proxy/api/token でサーバーがコード交換
           │    （client_secret を使うのでブラウザではやらない）
           │ ④ 返ってきたトークンで以降の API を叩く
           ▼
        ブラウザ版と同じ画面
```

`web/assets/boot.js` が判定と認証を担い、アプリ本体（`index.html` 内）は
`window.CV_PREFIX` と `window.CV_TOKEN` を見るだけ。**同じコードが両方で動く。**

SDK は `web/assets/embedded-app-sdk.js` に**バンドル済みで同梱**している。
外部 CDN から読むと URL Mappings が増えて壊れやすいため、自前で配信している。

## 認証の違い

| | ブラウザ版 | Activity |
|---|---|---|
| 本人確認 | `/library` が発行するトークン | Discord OAuth（自動） |
| 期限切れ | `/library` を打ち直す | 起きない |
| リンク共有事故 | ありうる | 起きない |

Activity では `/library` を使わない。起動するだけでログイン済みになる。

## 中身が空のとき

パネルは開くのにクリップが1件も出ない場合、**API に到達できていない**。
`Ctrl+Shift+I` で開発者ツールを開き、Console を見る。

```
[clipvault] activity = true | inFrame = true | search = ?frame_id=...
[clipvault] SDK ready
[clipvault] authorized
[clipvault] token exchanged, prefix = /.proxy  user = つるかん
```

1行目の `activity` が `false` なら、Activity と認識されていない。
その場合はブラウザ用の「リンクの有効期限が切れています」画面になる。

ここまで出ていれば認証は成功している。その先で `/api/clips -> 401` などが
出ていれば原因が特定できる。

VM 側のログにも記録される。

```bash
journalctl -u clipvault-bot -f | grep -E '401|/.proxy'
```

`401 GET /api/clips (token=なし)` ならトークンがヘッダーに乗っていない。
`404` なら URL Mappings のプレフィックスが合っていない。

boot.js は `/.proxy` あり・なしの両方を試して通る方を採用するので、
どちらの流儀でも動く。Console の `prefix =` がどちらになったかで判別できる。

## つまずきやすい点

- **パネルが真っ白** → URL Mappings の1件目が違う。`https://` は付けない
- **動画だけ再生されない** → `/cdn` のマッピング漏れ
- **「プレフィックスは .proxy を含むことができません」** → Prefix は `/cdn`。
  `/.proxy` は書かない
- **Launch ボタンが無い** → Activities を有効化していないか、Entry Point
  コマンドを消してしまっている。Activities を一度無効→有効で再生成される
- **「Discord との接続に失敗」** → `CLIENT_ID` が未設定か誤り。VM の
  `.env` を確認して bot を再起動
- **401 が返る** → `CLIENT_SECRET` が誤り。OAuth2 タブで Reset して入れ直す
- **URL が変わってしまった** → Funnel は固定のはず。`sudo tailscale funnel status` で確認

## 動作確認（Discord なしで）

```bash
python3 selftest_web.py --activity
```

`/.proxy/` 配下のルーティング、`CLIENT_ID` の埋め込み、CDN URL の受け渡し、
コード交換のエラー処理、SDK の配信までを実際の HTTP サーバーに対して検証する。

## この先

Participants API を使うと「いま誰が見ているか」を出せる。さらに再生位置を
共有すれば、ボイスチャットで喋りながら全員で同じクリップを見る
「一緒に見る」モードが作れる。これは Activity でしか作れない体験。
