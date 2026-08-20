# GitHub のセットアップ

ローカルPC → GitHub → Oracle Cloud VM の流れを通しで作る手順です。GitHubを使うのが初めてでも進められるように書いています。

---

## なぜGitHubを経由するのか

VMにファイルを送る手段としては `scp` でも構いませんが、GitHubを挟むと以下が得られます。

- 更新が `git pull` の1コマンドで済む
- 自宅PC（GPUワーカー）とVMに同じコードを配れる
- 変更履歴が残り、壊したときに戻せる
- 就活のポートフォリオとして提示できる

---

## Step 1. Git をインストールする（Windows）

```powershell
git --version
```

バージョンが出れば済んでいます。出なければインストールします。

```powershell
winget install Git.Git
```

インストール後、**PowerShellを開き直して**から再確認してください。

初回だけ名前とメールを設定します（コミットに記録される情報です）。

```powershell
git config --global user.name "あなたの名前"
git config --global user.email "GitHubに登録するメールアドレス"
```

## Step 2. GitHub アカウントを作る

https://github.com/signup

ユーザー名・メール・パスワードを入力し、メール認証を済ませます。二要素認証（2FA）は現在必須なので、案内に従って設定してください。認証アプリ（Google Authenticator等）かSMSを選べます。

**リカバリコードは必ず保存してください。** スマホを失くすとアカウントに入れなくなります。

## Step 3. リポジトリを作る

https://github.com/new

| 項目 | 値 |
|---|---|
| Repository name | `clipvault` |
| Description | `Discord clip manager with tags, search and transcription`（任意） |
| Public / Private | **Private** を選択 |
| Add a README file | **チェックしない** |
| Add .gitignore | **None**（既に用意済み） |
| Choose a license | None（後から追加可） |

**Private を推奨します。** コードに秘密情報は含まれない設計ですが、後述のとおり事故は起こりうるので、最初は非公開にしておくのが安全です。公開に切り替えるのは後からいつでもできます。

「Create repository」を押すと、コマンドの案内が出ます。次のステップで使うので画面は開いたままにしてください。

## Step 4. ローカルをリポジトリにする

ダウンロードした `clipvault` フォルダでPowerShellを開きます。

```powershell
cd C:\Users\<あなた>\clipvault
git init
```

### コミット前に必ず確認する

**これが一番重要な手順です。**

```powershell
git add -A
git status
```

出てきた一覧に **`.env` が含まれていないこと**を必ず目視で確認してください。`.env.example` はあってOKですが、`.env` があったら止めてください。

`.env` が出てきた場合、`.gitignore` が効いていません。

```powershell
git rm --cached .env
```

で取り消してから、`.gitignore` に `.env` の行があるか確認します。

含まれるべきファイルは以下の25個です。

```
.env.example  .gitignore
README.md  DEPLOY.md  ORACLE.md  NETWORK.md
bot.py  worker.py  api.py  db.py  dc.py  config.py  grid.py  media.py  doctor.py
selftest.py  selftest_list.py  selftest_api.py
requirements.txt  requirements-worker.txt
deploy/setup-vm.sh  deploy/check-network.sh
deploy/clipvault-bot.service  deploy/clipvault-worker.service
deploy/tunnel.bat  deploy/start-worker.bat
```

`data/` や `__pycache__/` が出ていたら `.gitignore` を確認してください。

### コミットする

```powershell
git commit -m "Initial commit: ClipVault"
```

## Step 5. GitHub に送る

Step 3の画面に表示されているURLを使います。

```powershell
git branch -M main
git remote add origin https://github.com/<あなたのユーザー名>/clipvault.git
git push -u origin main
```

初回のpushでブラウザが開き、GitHubへのログインを求められます（Git Credential Managerが自動で処理します）。承認すると以降は自動でログイン状態が保たれます。

> **パスワードを聞かれた場合**、GitHubのアカウントパスワードは使えません。2021年から廃止されています。ブラウザ認証が出ないときは、https://github.com/settings/tokens で Personal Access Token（classic）を発行し、`repo` スコープにチェックを入れて、パスワード欄にそのトークンを貼ってください。

pushが終わったら、ブラウザでリポジトリを開いてファイルが並んでいることを確認します。

**`.env` が無いことをここでもう一度確認してください。** もし上がってしまっていたら、次項の対応が必要です。

### 万一 .env を上げてしまったら

**トークンを再発行してください。** ファイルを消すだけでは不十分です。GitHubの履歴からは消えても、すでに公開された秘密は漏れたものとして扱うのが原則です。

1. Discord Developer Portal → Bot → **Reset Token**
2. `WORKER_TOKEN` を `openssl rand -hex 32` で作り直す
3. リポジトリ自体を削除して作り直すのが最も確実

## Step 6. VM にクローンする

VMからプライベートリポジトリを読むには認証が要ります。**デプロイキー**を使うのが最も安全です。読み取り専用で、そのリポジトリだけに権限が限定されます。

VMにSSHで入って、鍵を作ります。

```bash
ssh-keygen -t ed25519 -f ~/.ssh/github -N ""
cat ~/.ssh/github.pub
```

出てきた `ssh-ed25519 AAAA...` を丸ごとコピーします。

GitHubのリポジトリページで、

**Settings** → 左メニュー **Deploy keys** → **Add deploy key**

| 項目 | 値 |
|---|---|
| Title | `oracle-vm` |
| Key | コピーした公開鍵 |
| Allow write access | **チェックしない**（読み取り専用にする） |

VMに戻り、この鍵を使う設定を書きます。

```bash
cat >> ~/.ssh/config <<'EOF'
Host github.com
  IdentityFile ~/.ssh/github
  IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config
```

接続テストをします。

```bash
ssh -T git@github.com
```

`Hi <ユーザー名>/clipvault! You've successfully authenticated...` と出ればOKです（`shell access` が提供されないという文言が続きますが正常です）。

クローンします。**HTTPSではなくSSH形式のURL**を使う点に注意してください。

```bash
git clone git@github.com:<あなたのユーザー名>/clipvault.git
cd clipvault
```

以降は既定の手順どおりです。

```bash
cp .env.example .env
nano .env          # トークン類を記入
bash deploy/setup-vm.sh
bash deploy/check-network.sh
```

## Step 7. 自宅PC（GPUワーカー）にも配る

自宅PCは既にファイルを持っているので、同じフォルダをそのまま使えます。`.env` の中身だけVM用と変えてください（`WORKER_API_URL` と `WORKER_TOKEN` を設定し、`DISCORD_TOKEN` は書かない）。

別のPCに入れる場合は、Step 6と同様にデプロイキーを作るか、`gh` CLI でログインしてクローンします。

---

## 日常の更新フロー

### ローカルで直した内容をVMに反映する

```powershell
# ローカルPC
git add -A
git status                    # .env が無いか毎回確認
git commit -m "何を変えたか"
git push
```

```bash
# VM
cd ~/clipvault
git pull
sudo systemctl restart clipvault-bot clipvault-worker
```

### VM上で直接直してしまった場合

VM側の変更は `git pull` で衝突します。VMでは編集しないのが原則ですが、緊急で直した場合は、

```bash
git stash          # 変更を退避
git pull
git stash pop      # 戻す（衝突したら手で解決）
```

もしくはVMの変更を捨てる場合は、

```bash
git checkout -- .
git pull
```

### コミットメッセージの書き方

厳密なルールは不要ですが、後から探しやすくなります。

```
リスト表示のサムネイルを大きくした
リアクション集計のバグを修正
文字起こしワーカーを追加
```

---

## よくあるつまずき

| 症状 | 対処 |
|---|---|
| `git: command not found` | Gitが未インストール、またはターミナルの再起動忘れ |
| push時に `Authentication failed` | パスワードではなくPersonal Access Tokenを使う |
| `Permission denied (publickey)` (VM) | デプロイキーの登録漏れ、または `~/.ssh/config` の記述ミス |
| `Repository not found` (VM) | HTTPSのURLでクローンしている。`git@github.com:` 形式に変える |
| `.env` を上げてしまった | トークンを再発行する。消すだけでは不十分 |
| `data/` が上がっている | `.gitignore` を確認し、`git rm -r --cached data` で除外 |

---

## 補足：公開リポジトリにする場合

就活で見せたい場合、Publicに切り替えられます。その前に必ず確認してください。

```powershell
git log --all --full-history -- .env
```

何も出力されなければ、`.env` は一度もコミットされていません。安全に公開できます。

公開する場合は README に構成図や工夫した点（URL失効対策、ジョブキューによる二段構えのワーカー、Components V2のコンポーネント数制約への対応など）を書いておくと、設計判断が見える資料になります。
