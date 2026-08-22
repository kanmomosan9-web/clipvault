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

## 動画が再生できないとき

Activity 内は CSP により**同一オリジン以外のメディアを読めない**。
そのため動画には2つの経路がある。

| 経路 | URL | VM の帯域 |
|---|---|---|
| 1. CDN プロキシ | `/.proxy/cdn/attachments/...` | 使わない |
| 2. VM 中継 | `/media/{id}?stream=1` | 使う |

既定は 1。URL Mappings に `/cdn -> cdn.discordapp.com` が要る。
**失敗すると自動で 2 に切り替わる**ので、マッピングが無くても再生はできる。

Console でどちらを使っているか分かる。

```
[clipvault] media = cdn プロキシ経由
```

```
[clipvault] CDN プロキシに失敗。VM 中継へ切り替えます
```

後者が出るなら URL Mappings の `/cdn` を見直す。動作はするが**望ましくない**:

- 動画がVMを往復するので遅く、シークのたびに待たされる
- Tailscale Funnel はメディア配信向けではない
- 2 OCPU の ARM で複数人が同時視聴すると詰まる
- 自動で切り替わるため、壊れていることに気づけない

### 切り分け

まず VM 側で CDN が正常かを確かめる。

```bash
.venv/bin/python diagnose_media.py
```

登録すべき Target ホストと、Activity が実際に叩く URL が表示される。
**`cdn.discordapp.com` 以外が出た場合は、そのホストを登録する。**

次にフォールバックを止めて、CDN 経路の失敗をそのまま見る。
Activity の URL に `nofallback=1` は付けられないので、ブラウザ版で
`?nofallback=1` を付けて確認するか、Console の以下を読む。

```
[clipvault] media src = /.proxy/cdn/attachments/...
[clipvault] 再生失敗 code= 4 message= ... src= ...
```

`code=4`（SRC_NOT_SUPPORTED）は URL が読めていない。Network タブで
その URL のステータスを見る。404 ならマッピング未一致、401/403 なら
署名クエリが落ちている。

中継はシーク用に Range リクエストを通す。`selftest_media.py` で検証できる。

## デスクトップ版だけ再生できないとき

モバイルやブラウザでは再生できるのにデスクトップ版 Discord だけ駄目、
という場合はクライアント側の要因が濃厚。デスクトップ版は Electron で動いており、
メディア再生の実装がブラウザと異なる。

### 開発者ツールが開けない場合の診断

再生に失敗すると、**画面に診断が表示され、同じ内容がサーバーにも送られる**。

```bash
journalctl -u clipvault-bot -f | grep '\[client\]'
```

```
WARNING [client] つるかん / media / {'phase': 'stream', 'errorCode': 4,
  'probeStatus': 206, 'codecs': {'mp4_h264_aac': 'no', ...}, 'ua': '...'}
```

読み方:

| 見るところ | 意味 |
|---|---|
| `probeStatus` が 200/206 | **データは取れている**。再生できないのはデコード側 |
| `probeStatus` が 401/403/404 | URL が読めていない。マッピングか認証の問題 |
| `mp4_h264_aac` が `no` | **その環境が H.264/AAC を再生できない**。クライアント側の制約 |
| `errorCode` 4 | SRC_NOT_SUPPORTED。形式かURLが受け付けられていない |

`probeStatus` が 206 なのに `errorCode` が 4 なら、**通信ではなくコーデックの問題**。
こちらのコードでは解決できない。

### kUnsupportedConfig が出る場合

```
errorMessage: PipelineStatus::DECODER_ERROR_NOT_SUPPORTED:
  video decoder initialization failed with DecoderStatus::Codes::kUnsupportedConfig
probeStatus: 206 video/mp4
mp4_h264_aac: probably
```

これは**通信もコーデック対応も問題なく、デコーダの初期化だけが失敗**している状態。
`canPlayType` は大まかな対応しか答えないので、実際のプロファイルやレベルまでは
保証しない。

ゲームのキャプチャは H.264 でも 4:4:4 クロマや高いレベルで録られることがあり、
Electron のデコーダが受け付けないことがある。

クリップの中身を調べる。

```bash
.venv/bin/python inspect_codecs.py --all
```

```
clip 413: League of Legends 2026.08.20 - 21.34.mp4
  映像 : h264 / High 4:4:4 Predictive / Level 5.1 / yuv444p / 2560x1440
  !!   プロファイルが特殊: High 4:4:4 Predictive
  !!   クロマ形式が特殊: yuv444p（4:2:0 でない）
```

`!!` が付いたら再エンコードで解決できる。

```bash
ffmpeg -i in.mp4 -c:v libx264 -profile:v high -level 4.1 \
       -pix_fmt yuv420p -c:a aac -b:a 128k out.mp4
```

**ブラウザ版やモバイル版では再生できる**ことが多いので、当面はそちらを使うのも手。

### デスクトップ版で開発者ツールを開く

どうしても Console を見たい場合、設定ファイルで有効にできる。

1. Discord を完全終了（タスクトレイからも終了）
2. `%AppData%\discord\settings.json` をメモ帳で開く
3. 次の行を追加（末尾のカンマに注意）

```json
"DANGEROUS_ENABLE_DEVTOOLS_ONLY_ENABLE_IF_YOU_KNOW_WHAT_YOURE_DOING": true
```

4. Discord を起動し、Activity 上で `Ctrl+Shift+I`

確認が済んだら `false` に戻しておくのが無難。

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
