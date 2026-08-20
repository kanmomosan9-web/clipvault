# デプロイ手順（Oracle Cloud 無料VM）

## 構成

```
┌─ Oracle Cloud VM（常時稼働・無料）──────────┐
│  clipvault-bot      … Discord bot 本体      │
│                       ジョブAPI :8787       │
│  clipvault-worker   … CPU フォールバック    │
│                       small / 10分待ってから │
│  data/clipvault.db  … 本物のディスクに永続   │
└──────────────────────────────────────────┘
                    ↑ SSHトンネル :8787
┌─ 自宅PC（RTX 3080・電源が入っているときだけ）┐
│  worker.py (remote) … large-v3 / 即座に奪う  │
│                       空き時間に低品質を再処理│
└──────────────────────────────────────────┘
```

自宅PCが起動していれば数秒でGPUが処理する。落ちていれば10分後にVMが `small` で処理し、次にPCが起動したときGPUが自動で取り直す。

---

## 1. VMを作る

Oracle Cloud にサインアップし、**Always Free** の Ampere A1 インスタンスを作成する。

| 項目 | 値 |
|---|---|
| Shape | VM.Standard.A1.Flex（Ampere / ARM） |
| Home region | Japan Central (Osaka) 推奨・**後から変更不可** |
| OCPU / RAM | **2 OCPU / 12 GB**（2026年6月に半減。これが上限） |
| Image | Canonical Ubuntu 24.04 |
| SSHキー | 公開鍵を登録。秘密鍵は自宅PCの `~/.ssh/oracle_vm` に保存 |

作成後、パブリックIPを控える。

> **アカウント作成からインスタンス作成までの詳細は `ORACLE.md` を参照。** 無料枠の変更、アイドル回収ポリシー、Out of capacity の回避策など、詰まりやすい箇所をまとめてある。

## 2. 接続して配置

GitHub をまだ用意していない場合は先に `GITHUB.md` を実施する（リポジトリ作成・push・デプロイキー登録）。

```bash
ssh -i ~/.ssh/oracle_vm ubuntu@<パブリックIP>

git clone git@github.com:<あなた>/clipvault.git
cd clipvault
cp .env.example .env
nano .env      # トークン類を記入
```

`WORKER_TOKEN` は長いランダム文字列にする。

```bash
openssl rand -hex 32
```

## 3. セットアップ

```bash
bash deploy/setup-vm.sh
```

ffmpeg・日本語フォント（fonts-noto-cjk）・venv・依存パッケージを入れ、systemdサービスを2つ登録して起動する。

```bash
sudo systemctl status clipvault-bot
journalctl -u clipvault-bot -f
```

`BUILD 2026-08-21a` と `job API listening on 127.0.0.1:8787` が出れば成功。

**ポートは開けない。** APIは `127.0.0.1` にのみバインドしており、外部からは到達できない。自宅PCとはSSHトンネルで繋ぐ。設定の詳細と検証方法は `NETWORK.md`、確認は `bash deploy/check-network.sh`。

## 4. 自宅PCをGPUワーカーにする

同じリポジトリを自宅PCにも置く。`.env` は**別内容**にする。

```
WORKER_API_URL=http://127.0.0.1:8787
WORKER_TOKEN=VMと同じ文字列
WORKER_TIER=gpu
WHISPER_MODEL=large-v3
WHISPER_DEVICE=cuda
WHISPER_COMPUTE=float16
AUTO_UPGRADE=1
```

**`DISCORD_TOKEN` は書かない。** リモートモードではジョブと一緒に新鮮な添付URLがAPIから渡されるため、自宅PC側にbotトークンを置く必要がない。

```powershell
pip install -r requirements.txt
pip install -r requirements-worker.txt
```

`deploy/tunnel.bat` の `VMHOST` を自分のIPに書き換えて実行し、別ウィンドウでワーカーを起動する。

```
deploy\tunnel.bat        ← 開いたままにする（切れても自動再接続）
deploy\start-worker.bat
```

両方をスタートアップ（`Win+R` → `shell:startup`）に入れておけば、PC起動時に自動で参加する。PCを切っても、VM側が10分後に引き継ぐだけで何も壊れない。

## 5. 動作確認

Discordで `/jobs` を実行すると、どのモデルが何件処理したかが見える。

```
文字起こし済み  42 / 47 件
キュー          待機中: 5
使用モデル      large-v3: 38 件
                small: 4 件
再処理待ち      4 件が large-v3 以外で処理済み
```

`small` の分は、自宅PCが起動していれば自動で `large-v3` に置き換わっていく。手動で促す場合は `/transcribe target:低品質モデルの分を再処理`。

---

## 運用

### バックアップ

`data/clipvault.db` だけが再生成不可能な資産。VMのcronで定期的に固めておく。

```bash
crontab -e
```

```
0 4 * * * cd /home/ubuntu/clipvault && sqlite3 data/clipvault.db ".backup /home/ubuntu/backup-$(date +\%u).db"
```

曜日ごとに7世代を回す。自宅PCへ落とすなら `scp` で。

```powershell
scp -i %USERPROFILE%\.ssh\oracle_vm ubuntu@<IP>:/home/ubuntu/backup-*.db .
```

### 更新

```bash
cd ~/clipvault
git pull
sudo systemctl restart clipvault-bot clipvault-worker
```

### VMのCPUワーカーを止めたいとき

「遅い文字起こしなら無いほうがいい」なら止められる。キューは溜まったまま残り、自宅PCが起動したときに処理される。

```bash
sudo systemctl disable --now clipvault-worker
```

**ただしアイドル回収の対策を兼ねている点に注意。** Always Free インスタンスは7日間 CPU・ネットワーク・メモリの全てが10%未満だと停止対象になる。CPUワーカーは `--preload` でWhisperモデルを常駐させるため、メモリ使用率だけで条件を外せる。止める場合は `ORACLE.md` の代替手段を参照。

### 待ち時間の調整

VMが引き継ぐまでの猶予は `clipvault-worker.service` の `WORKER_GRACE_SECONDS`（既定600秒）で変える。短くするとPCが一瞬落ちただけでVMが拾ってしまうので、10分程度が無難。

```bash
sudo systemctl edit --full clipvault-worker
sudo systemctl restart clipvault-worker
```

---

## トラブルシューティング

- **トンネルが繋がらない** → VMのIP・秘密鍵のパスを確認。`ssh -i ... ubuntu@<IP>` が単体で通るか先に試す
- **ワーカーが 401** → VMと自宅PCの `WORKER_TOKEN` が不一致
- **`claim failed` が繰り返される** → トンネルが落ちている。`tunnel.bat` は自動再接続するが、VM側でbotが停止している可能性も確認する
- **VMで日本語が豆腐** → `fonts-noto-cjk` が入っていない。`setup-vm.sh` を再実行
- **ARM で faster-whisper が入らない** → `pip install ctranslate2` が aarch64 ホイールを見つけられない場合がある。CPUワーカーを諦めて `clipvault-worker` を無効化し、自宅PC専用にする
