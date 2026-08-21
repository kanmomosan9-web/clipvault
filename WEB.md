# Web ライブラリ

ブラウザで開く YouTube 風のクリップライブラリ。サムネイル一覧、検索、
再生、タグ編集、時刻付きコメント、リアクションが使える。

bot は残したまま併用する。取り込み・文字起こしは bot、閲覧と編集は Web、という分担。

---

## 何ができるか

| | bot | Web |
|---|---|---|
| クリップの取り込み | ○ 自動 | — |
| 一覧 | 3件ずつ | **グリッドで一覧** |
| 再生 | ジャンプのみ | **その場で再生** |
| 文字起こしから頭出し | ✕ 不可 | **行クリックでシーク** |
| タグ編集 | モーダル | **その場で追加・削除** |
| コメント | ✕ | **時刻付きコメント** |
| リアクション | Discord のみ | **誰が押したか分かる** |

再生位置に紐づいたコメントはシークバーに印が出る。文字起こしの印（水色）と
コメントの印（クリーム）で、どこで何が起きたかがバーだけで分かる。

## 仕組み

```
ブラウザ ──HTTPS──▶ Cloudflare ──トンネル──▶ VM:8787 (aiohttp)
                                                  │
                                     ┌────────────┼────────────┐
                                     │            │            │
                                 SQLite      data/thumbs   Discord CDN
                                （メタ情報）  （サムネ）     （動画本体）
```

**動画は VM を経由しない。** `/media/{id}` は署名付きURLを取り直して 302 で
CDN へ飛ばすだけなので、VM の帯域も CPU も消費しない。URL の失効対策は
bot と同じ仕組みをそのまま使っている。

## 認証

Discord で `/library` を実行すると、**本人専用のリンク**が ephemeral で返る。

```
https://clips.example.com/?t=<ランダム32文字>
```

トークンは Discord のユーザーID と紐づいて DB に保存される。だから
コメントやリアクションが誰のものか分かる。OAuth を組まずに済ませている。

- 有効期限は既定 30 日（`WEB_TOKEN_DAYS`）
- 期限切れなら `/library` を打ち直すだけ
- 1人1トークン。再発行すると古いものは無効になる
- 初回アクセスで Cookie に入るので、以降は素のURLで開ける

**リンクを他人に渡すとその人として書き込める。** 身内サーバー前提の割り切り。
より厳密にするなら Discord OAuth に差し替える。

## セットアップ

### 1. HTTPS で公開する

ポートは開けない。Cloudflare Tunnel が内側から繋ぎに行く。

```bash
bash deploy/setup-tunnel.sh
```

表示される手順に従う。ドメインを持っていなければ、まず使い捨てで試せる。

```bash
cloudflared tunnel --url http://localhost:8787
```

`https://xxxx-yyyy.trycloudflare.com` が払い出される。**再起動のたびに
変わる**ので動作確認用。

### 2. 公開URLを設定

```bash
nano ~/clipvault/.env
```

```
WEB_ENABLED=1
PUBLIC_URL=https://clips.example.com
WEB_TOKEN_DAYS=30
```

`PUBLIC_URL` に末尾のスラッシュは不要。

```bash
sudo systemctl restart clipvault-bot
```

### 3. 開く

Discord で `/library` → 「ライブラリを開く」ボタン。

## API

すべて `X-ClipVault-Token` ヘッダーか `?t=` か Cookie で認証する。

| メソッド | パス | 用途 |
|---|---|---|
| GET | `/api/me` | ログイン中のユーザー |
| GET | `/api/clips` | 一覧・検索（`q` `tag` `game` `by` `sort`） |
| GET | `/api/clips/{id}` | 詳細（文字起こし・コメント・リアクション込み） |
| GET | `/api/facets` | 絞り込み用のゲーム・タグ・投稿者 |
| PUT | `/api/clips/{id}/tags` | タグを差し替え（ゲームタグは保持される） |
| PATCH | `/api/clips/{id}` | タイトル・メモ |
| POST | `/api/clips/{id}/comments` | コメント投稿（`at` で時刻指定） |
| DELETE | `/api/comments/{id}` | 自分のコメントを削除 |
| POST | `/api/clips/{id}/reactions` | リアクションのトグル |
| GET | `/media/{id}` | 動画（CDN へ 302） |
| GET | `/thumb/{id}` | サムネイル |

ワーカー用の `/api/jobs/*` は従来どおり `Authorization: Bearer` を使う。
こちらは Web トークンでは通らない。

## リアクションの二系統について

Discord API には「ユーザーの代理でリアクションを付ける」手段が無い。
そのため次の二系統になる。

- **Discord 側のリアクション** … `/resync` で数だけ読み取る。誰が押したかは持たない
- **Web 側のリアクション** … 誰がどの絵文字を押したか DB に記録する

詳細画面では Web 側を絵文字チップとして表示し、Discord 側は `★N` として
横に並べる。カードの `★` は両方の合計。

## 検索の対象

タイトル・タグ・メモ・文字起こし・**コメント本文**。

コメントだけは FTS5 に入れず LIKE で照合している。コメントは頻繁に増減するので、
そのたびに全文検索インデックスを作り直すのを避けるため。

## 動作確認

Discord なしで API を通しで検証できる。

```bash
python3 selftest_web.py
```

認証、一覧、検索、タグ更新、リアクションのトグル、コメント、動画リダイレクト、
静的配信までを実際の HTTP サーバーに対して叩く。

## つまずきやすい点

- **リンクを開くと「有効期限が切れています」** → `/library` を打ち直す
- **サムネイルが出ない** → `data/thumbs` が空。`/scan` を実行する
- **再生が始まらない** → 元メッセージが削除されている。`/media/{id}` が 410 を返す
- **`/library` が「PUBLIC_URL が未設定」と言う** → `.env` に設定して bot を再起動
- **トンネルは繋がるが 502** → bot が落ちている。`systemctl status clipvault-bot`

## この先

- コメントを Discord のスレッドへ Webhook で同期（本人の名前とアイコンで投稿できる）
- Embedded App SDK を組み込んで Discord Activity 化（Discord 内で完結する）

どちらも今のバックエンドをそのまま使える。
