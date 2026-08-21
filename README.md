# ClipVault

Discordをクリップ用の外部ストレージとして扱い、Netflix風グリッド・タグ・全文検索で管理するbot。

## 動作の要点

**添付ファイルのURLはDBに保存しない。** Discordの添付URLは署名付きで約24時間で失効するため、保存するのは `message_id` / `attachment_id` のみ。再生・DL時に毎回メッセージを取得して新鮮なURLを得る（`ClipView._embed()`）。

グリッドはPillowで1枚のPNGに合成してからEmbedに貼る。タイル上の番号がそのままボタン番号に対応する。

## セットアップ

### 1. ffmpeg

サムネ抽出と尺の取得に必要。`ffmpeg` と `ffprobe` にPATHが通っていること。

```powershell
winget install Gyan.FFmpeg
```

インストール後にターミナルを開き直し、`ffmpeg -version` が通るか確認する。

### 2. botの作成

1. https://discord.com/developers/applications で New Application
2. **Bot** タブ → Reset Token でトークンを取得
3. **Bot** タブ → Privileged Gateway Intents で **MESSAGE CONTENT INTENT を ON**（添付ファイルの取得に必須）
4. **OAuth2 → URL Generator** で `bot` と `applications.commands` にチェック、権限は
   `View Channels` / `Send Messages` / `Read Message History` / `Add Reactions` / `Attach Files`
5. 生成URLでサーバーに招待

### 3. 設定

```bash
pip install -r requirements.txt
cp .env.example .env
```

`.env` を編集する。IDはDiscordの設定 → 詳細設定 → 開発者モードをONにして、右クリック → IDをコピー。

```
DISCORD_TOKEN=...
GUILD_ID=123456789012345678
STORAGE_CHANNEL_IDS=987654321098765432
```

保管チャンネルは、**bot以外が発言できない設定**にしておくと埋もれない。クリップ投稿だけを許可し、会話は別チャンネルに分ける。

### 4. 起動

```bash
python bot.py
```

`GUILD_ID` を設定していればスラッシュコマンドは即座に反映される（グローバル同期は最大1時間かかる）。

## 使い方

| コマンド | 動作 |
|---|---|
| `/clips` | グリッド一覧。番号ボタンで詳細、セレクトで絞り込み・並び替え |
| `/clips view:リスト` | リスト表示。各クリップに専用の再生・編集ボタンが付く |
| `/clips tag:LoL` | タグ指定で開く（autocomplete対応） |
| `/search ペンタ` | タイトル・タグ・メモ・文字起こしを全文検索 |
| `/scan limit:500` | 保管チャンネルの過去ログを遡って一括取り込み |
| `/library` | **ブラウザ版ライブラリのリンクを発行** |
| `/stats` | 件数・総時間・総容量・人気タグ |
| `/resync` | 全クリップのリアクション数を数え直す |
| `/transcribe` | 文字起こしジョブをキューに積む |
| `/jobs` | 文字起こしの進捗と失敗理由を見る |
| `/alias ボット ADC` | 表記ゆれを既存タグに寄せる |

保管チャンネルに動画を投稿すると自動で取り込まれ、bot が 📼 を付ける。ファイル名から**ゲーム名と録画日時を自動判定**する（ShadowPlay / Medal / Outplayed の命名規則に対応）。本文で誰かを `@mention` すると、その人が `player` タグとして付く。

クリップに付けたリアクション数が★評価になり、「人気順」で並び替えできる。

タイトルとタグは詳細画面の `🏷 編集` から。タグはカンマ区切り、`/alias` で正規化される。

## 動作確認（Discordなしで）

```bash
python selftest.py       # DB・検索・グリッド描画
python selftest_list.py  # Components V2の組み立てとコンポーネント数
```

ダミーデータを投入してDB・検索・グリッド描画を検証し、`preview_grid.png` を出力する。フォントが見つかっているかもここで確認できる（見つからないと日本語が豆腐になる）。

## ファイル構成

```
config.py    設定・パス・ゲーム名エイリアス・フォント探索順
db.py        SQLiteスキーマ、タグ正規化、FTS5検索
media.py     ffprobe/ffmpeg、ファイル名パース、表示整形
grid.py      Pillowによるグリッド合成
bot.py       取り込み、GridView / ListView / ClipView / EditModal、スラッシュコマンド
worker.py    文字起こしワーカー（別プロセスで起動）
api.py       ジョブAPI（リモートワーカー用のHTTPエンドポイント）
webui.py     Web ライブラリの REST API と静的配信
web/         ブラウザ版 UI（index.html 単体）
dc.py        添付URLの再取得ヘルパー
doctor.py    環境とファイル版の診断
deploy/      systemdユニット、VMセットアップ、ネットワーク検証、SSHトンネル
```

## 表示モードの切り替え

- **リスト（既定）** … 1件ずつパネル幅いっぱいの大きなサムネと専用ボタン。1ページ3件
- **グリッド** … 3×3で9件。合成PNGなので一覧性が高い。操作は番号ボタン

既定の表示形式は `config.py` の `DEFAULT_VIEW`（`"list"` / `"grid"`）で変更できる。

グリッド画面の `📋 リスト表示` / リスト画面の `🔲 グリッド表示` で相互に切り替わる。切り替えは**新しいパネルを開く**動作になる（Discordの `IS_COMPONENTS_V2` フラグは一度付けると外せないため、同じメッセージを書き換えられない）。前のパネルは「メッセージを非表示」で消せる。

リストは1ページ4件が上限（`LIST_PAGE_SIZE`）。Discordの1メッセージ40コンポーネント制限に対し、文字起こしのあるクリップは1件で8個を消費するため、4件で39個、5件だと47個で超過する。`selftest_list.py` で実際の個数を検証できる。

文字起こしは既定で折りたたまれ、`🎙 文字起こし (N)` ボタンで展開する。**検索中は折りたたんでいても該当行だけ表示される**（ヒットした理由が分かるようにするため）。展開時の行数は `LIST_TRANSCRIPT_LINES`（既定8）。

サムネの表示サイズはDiscord側が決めるため直接は指定できないが、`MediaGallery` はパネル幅いっぱいに表示される。書き出し解像度は `LIST_THUMB_WIDTH`（既定768px）で調整する。**過去に取り込んだクリップのサムネ元画像は640px幅**なので、大きく表示すると少し甘い。ffmpegの抽出解像度は1280pxに変更済みなので、`/scan` し直すか手動で `data/thumbs/` を消すと再生成される。

## 文字起こし（セリフ検索）

音声をローカルGPUで文字起こしし、`/search` の対象に加える。**「あのとき叫んでたやつ」をタグ無しで引ける**ようになる。

### 仕組み

botとは**別プロセス**で動く。gatewayを数十秒ブロックするとbotが無応答になるため、必ず分離する。

```
取り込み → jobs テーブルに pending で積む
                ↓
        worker.py（別プロセス）
        1. message_id から新鮮なURLを取得
        2. ffmpeg で 16kHz モノラル wav を抽出（動画全体はDLしない）
        3. faster-whisper で認識
        4. clips.transcript / clips.segments に保存 → FTS5 に投入
```

### ワーカーは2種類

同じ `worker.py` が2モードで動く。

| モード | 条件 | 用途 |
|---|---|---|
| ローカル | `WORKER_API_URL` 未設定 | DBに直接アクセス。bot と同じマシンで動かす |
| リモート | `WORKER_API_URL` 設定 | HTTP経由。別マシン（自宅GPU）から参加する |

`WORKER_GRACE_SECONDS` 秒だけ待ってからジョブを取るように設定できるので、**GPUマシンを即時・CPUマシンを遅延にすれば自動でフェイルオーバーする**。GPU側は空き時間に低品質モデルで処理された分を自動で取り直す（`AUTO_UPGRADE=1`）。

クラウド構成は `DEPLOY.md`、Oracle Cloud は `ORACLE.md`、ネットワークは `NETWORK.md`、GitHub は `GITHUB.md`、**ブラウザ版ライブラリは `WEB.md`** を参照。

### 導入

```bash
pip install -r requirements-worker.txt
```

GPUを使う場合は CUDA 対応の cuDNN が別途必要。うまく初期化できなければ自動でCPU/int8にフォールバックする（遅いが動く）。

```bash
python worker.py            # 通常起動
python worker.py --preload  # 起動時にモデルを読み込む
```

botとworkerは**同時に起動しておく**。workerはキューを10秒ごとに見に行く。

### 設定（`.env` で上書き可）

| 変数 | 既定 | 備考 |
|---|---|---|
| `AUTO_TRANSCRIBE` | `1` | 取り込み時に自動でキューへ。`0` で手動のみ |
| `WHISPER_MODEL` | `large-v3` | VRAM 10GB なら large-v3 が載る。軽くするなら `medium` |
| `WHISPER_DEVICE` | `cuda` | GPUが無ければ `cpu` |
| `WHISPER_COMPUTE` | `float16` | CPU時は `int8` |
| `WHISPER_LANG` | `ja` | 自動判別に任せると短いクリップで英語誤判定が起きるため固定 |

### 精度を上げている工夫

- **`vad_filter=True`** … BGMやSEを無音として除去する。これが無いと「ご視聴ありがとうございました」のような幻覚テキストを大量に吐く
- **ゲーム別の語彙プロンプト** … `config.GAME_PROMPTS` に定義。`#LoL` が付いたクリップには「バロン、ガンク、ウルト…」を `initial_prompt` として渡すので、ゲーム特有語の認識率が上がる。新しいゲームを足すときはここに追記する
- **`condition_on_previous_text=False`** … 短いクリップで前文脈に引きずられて同じ文を繰り返す現象を防ぐ

### 既存クリップへの適用

```
/transcribe                    # 未処理のクリップすべてをキューに積む
/transcribe target:全クリップ force:True   # やり直し
/jobs                          # 進捗確認
```

処理速度の目安は、RTX 3080 + large-v3 で **30秒のクリップが数秒**。100本でも十数分で終わる。

### 検索結果での見え方

`/search バロン` でヒットしたクリップを開くと、詳細画面の「🔍 該当箇所」に**その語が出てくる時刻**が並ぶ。

```
🔍 該当箇所
`0:14` バロン取られた
`0:22` やばいって
```

Discordの動画プレーヤーは時刻指定リンクに対応していないため、表示された時刻を見て手動でシークする形になる。

## つまずきやすい点

- **日本語が豆腐になる** → `config.FONT_CANDIDATES` にフォントのパスを追加する。確実なのは Noto Sans JP をDLして `fonts/NotoSansJP-Bold.ttf` に置くこと
- **サムネが出ない** → ffmpegがPATHにない、または動画が壊れている。起動時ログに警告が出る
- **コマンドが出ない** → `GUILD_ID` が正しいか、招待時に `applications.commands` スコープを付けたか確認
- **取り込まれない** → MESSAGE CONTENT INTENT がOFF、または `STORAGE_CHANNEL_IDS` が違う
- **📼 が付かない** → botに Add Reactions 権限がない。起動ログに警告が出る
- **★が全部0** → `/scan` で取り込んだ既存分は `/resync` を一度実行する
- **文字起こしが進まない** → `worker.py` が起動していない。`/jobs` で待機中の件数を確認
- **文字起こしが失敗する** → `/jobs` に理由が出る。`could not refresh attachment URL` なら元メッセージが削除済み
- **変な文章が量産される** → BGMが大きい。`config.WHISPER_MODEL` を上げるか、Demucsでボーカル分離を挟む

## 次に足すもの

- **投稿前の自動圧縮**: 無料枠10MBに収めるため、720p / CRF28 へ再エンコードしてから投稿
- **Components V2化**: 表示層は `GridView.show()` と `ClipView.show()` に閉じているので、そこだけ `LayoutView` に差し替えれば移行できる
