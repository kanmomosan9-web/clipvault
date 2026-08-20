# Oracle Cloud セットアップ詳細

## 最初に知っておくべき3つの制約

### 1. 無料枠が2026年6月に半減した

Ampere A1（ARM）の Always Free 枠は **4 OCPU / 24 GB → 2 OCPU / 12 GB** に変更されました。公式アナウンスは無く、ドキュメントが書き換わっただけでした。

**2026年8月18日から強制が始まっています。** 古い記事やGitHubのガイドは4/24で書かれているものが大半なので、そのまま真似するとインスタンスが停止・削除されます。

| | 現在のAlways Free枠 |
|---|---|
| Ampere A1 | **2 OCPU / 12 GB**（1台に全部、または1 OCPU×2台） |
| AMD Micro | VM.Standard.E2.1.Micro × 2台（各 1 OCPU / 1 GB） |
| ブロックストレージ | 合計 200 GB |
| 転送量 | 10 TB/月 |

ClipVaultには 2 OCPU / 12 GB で十分です。むしろ**上限ぴったりに設定してください**。超えると止まり、下回ると次の問題に当たります。

### 2. アイドル状態だと回収される

Always Freeのインスタンスは、**7日間の間に以下の3つが全て成立すると停止対象**になります。

- CPU使用率（95パーセンタイル）が10%未満
- ネットワーク使用率が10%未満
- メモリ使用率が10%未満（A1シェイプのみ）

Discord botは普段ほぼ無負荷なので、**放置すると回収されます**。

ただし条件は **AND** です。どれか1つが10%を超えていれば対象外になります。**メモリが一番簡単に超えられます。** 12 GBの10%は1.2 GB。CPUワーカーを `--preload` で起動すればWhisperモデルが常駐し、`small` でも1.5〜2 GB程度を占有し続けます。

つまり**CPUフォールバックワーカーを動かしておくこと自体が、回収対策を兼ねます**。一石二鳥なので、ARM上で faster-whisper が動くなら必ず有効にしてください。

確実性を取るなら Pay As You Go にアップグレードする手もあります。**アップグレードしてもAlways Free枠のリソースは無料のまま**で、回収ポリシーの対象外になります。ただし枠を超えた瞬間に課金されるので、予算アラートの設定が必須です。

### 3. ホームリージョンは後から変えられない

サインアップ時に選んだリージョンが**永久に固定**されます。Always Freeのインスタンスはホームリージョンにしか作れません。

日本からなら **Japan East (Tokyo)** か **Japan Central (Osaka)** です。Tokyoは人気が高く「Out of capacity」に当たりやすいので、**Osakaのほうが取りやすい**傾向があります。レイテンシの差は数msなので、Osakaを推奨します。

---

## Step 1. アカウント作成

https://www.oracle.com/cloud/free/ から「Start for free」。

| 項目 | 注意点 |
|---|---|
| 国 | Japan |
| ホームリージョン | **Japan Central (Osaka)** 推奨。後から変更不可 |
| 電話番号 | SMS認証あり。ハイフン無しで入力 |
| クレジットカード | 本人確認用。数百円の**仮押さえ**が入り、数日で消える。実際の請求は発生しない |

デビットカードやプリペイドは弾かれることがあります。Vプリカ等は避けてください。

**サインアップが審査で止まる・拒否されることがあります。** 明確な理由は開示されません。数日待って別の情報で再挑戦するか、諦めて別のVPS（Hetzner等、月€4程度）を検討することになります。ここは運の要素があります。

作成直後は30日間の無料トライアル（$300クレジット）が付きますが、**トライアルが終わってもAlways Free枠のリソースは動き続けます**。トライアル終了時に枠を超えていると、A1インスタンスは無効化され30日後に削除されるので、**最初から2 OCPU / 12 GBで作っておく**のが安全です。

## Step 2. SSHキーを作る

自宅PC（Windows）で。PowerShellを開いて実行します。

```powershell
ssh-keygen -t ed25519 -f $env:USERPROFILE\.ssh\oracle_vm -C "clipvault"
```

パスフレーズは空でも構いませんが、設定するなら後述のトンネル自動化で `ssh-agent` が必要になります。**まずは空で始めて問題ありません**（秘密鍵ファイル自体がPC内にあるため）。

公開鍵の中身をコピーしておきます。

```powershell
Get-Content $env:USERPROFILE\.ssh\oracle_vm.pub
```

`ssh-ed25519 AAAA...` の1行が出ます。これをまるごと使います。

## Step 3. インスタンスを作る

コンソール左上のメニュー → **Compute** → **Instances** → **Create instance**。

### 名前と配置

| 項目 | 値 |
|---|---|
| Name | `clipvault` |
| Compartment | ルート（既定のまま） |
| Availability domain | AD-1（取れなければAD-2, AD-3を試す） |

### イメージとシェイプ

**Edit** を押して変更します。

| 項目 | 値 |
|---|---|
| Image | **Canonical Ubuntu 24.04**（Oracle Linuxではなく） |
| Shape series | **Ampere** |
| Shape name | **VM.Standard.A1.Flex** |
| OCPUs | **2** |
| Memory (GB) | **12** |

`Always Free-eligible` のラベルが付いていることを必ず確認してください。OCPUを2にすると自動でメモリが12 GBになります。

Ubuntu を選ぶ理由は、`deploy/setup-vm.sh` が `apt` 前提で書かれているためです。Oracle Linux を選ぶと `dnf` に読み替える必要があります。

### ネットワーク

既定のまま「Create new virtual cloud network」で構いません。

| 項目 | 値 |
|---|---|
| Primary network | Create new virtual cloud network |
| Subnet | Create new **public** subnet |
| Assign a public IPv4 address | **Yes** |

**セキュリティリストは触らないでください。** 既定でSSH（22番）だけが開いています。ClipVaultのジョブAPIは `127.0.0.1` にのみバインドし、SSHトンネル経由でアクセスするので、**追加のポート開放は一切不要**です。

各項目の意味、既定ルールの中身、インスタンス内iptablesとの関係、検証方法は **`NETWORK.md`** にまとめてあります。

### SSHキー

**Paste public keys** を選び、Step 2でコピーした `ssh-ed25519 AAAA...` を貼り付けます。

### ブートボリューム

既定の47 GBで足りますが、無料枠は合計200 GBあるので **100 GB** にしておくと余裕が出ます。サムネイル（1枚あたり150 KB程度）とWhisperモデル（`small` で約500 MB、`large-v3` なら約3 GB）を置くためです。

**Create** を押します。

## Step 4. 「Out of capacity」が出たら

Ampere A1は常に取り合いです。以下の順に試します。

1. **Availability domain を変える**（AD-1 → AD-2 → AD-3）
2. **時間帯を変える**。日本時間の深夜〜早朝が取りやすい
3. **1 OCPU / 6 GB で作る**。小さいほうが空きが見つかりやすく、後から2/12に増やせます
4. **Pay As You Go にアップグレードする**。容量の優先度が上がり、体感で大幅に取りやすくなります（Always Free枠内なら請求はゼロ）

手動で何度も押すのが辛い場合は、作成画面で **Create** ではなく **Save as stack** を選ぶとTerraform構成が保存され、Cloud Shellから `terraform apply` をループさせて空きを待つことができます。ただし**過度な連打はアカウント停止のリスク**があるので、間隔は数分以上空けてください。

## Step 5. 接続する

作成後、インスタンス詳細画面の **Public IP address** を控えます。

```powershell
ssh -i $env:USERPROFILE\.ssh\oracle_vm ubuntu@<パブリックIP>
```

初回は fingerprint の確認が出るので `yes`。ユーザー名は Ubuntu イメージなら **`ubuntu`** です（Oracle Linux なら `opc`）。

繋がらない場合の切り分け：

- `Permission denied (publickey)` → ユーザー名が違うか、貼り付けた公開鍵が壊れている
- `Connection timed out` → セキュリティリストで22番が閉じている、またはインスタンスがまだ起動中
- `Connection refused` → インスタンスは生きているがSSHが未起動。数分待つ

## Step 6. 最低限の防御

インスタンスは公開IPを持ち、世界中からSSHを叩かれます。Ubuntuイメージは既定でパスワード認証が無効なので致命的ではありませんが、ログは汚れます。

```bash
sudo apt update && sudo apt install -y fail2ban unattended-upgrades
sudo systemctl enable --now fail2ban
sudo dpkg-reconfigure -plow unattended-upgrades
```

パスワード認証が確実に無効か確認します。

```bash
sudo sshd -T | grep -E "passwordauthentication|permitrootlogin"
```

`passwordauthentication no` と `permitrootlogin no`（または `prohibit-password`）になっていればOKです。

Oracleの Ubuntu イメージは**VCNのセキュリティリストとは別に、インスタンス内部にもiptablesルールを持っています**。今回は22番しか使わないので触る必要はありませんが、将来ポートを開ける場合は「コンソール側のセキュリティリスト」と「インスタンス内のiptables」の**両方**を開ける必要があります。ここで詰まる人が非常に多い箇所です。詳細は `NETWORK.md`。

**`sudo ufw enable` は実行しないでください。** Oracleの既定iptablesと競合し、SSHごと遮断される事故が頻発します。閉め出された場合はコンソールのシリアルコンソールから復旧できます。

## Step 7. ClipVaultを入れる

```bash
git clone https://github.com/<あなた>/clipvault.git
cd clipvault
cp .env.example .env
nano .env
```

`WORKER_TOKEN` を生成して貼ります。

```bash
openssl rand -hex 32
```

あとは自動です。

```bash
bash deploy/setup-vm.sh
bash deploy/check-network.sh
```

ffmpeg・日本語フォント・venv・依存関係・systemdサービス2つを入れて起動します。

```bash
sudo systemctl status clipvault-bot
journalctl -u clipvault-bot -f
```

`BUILD 2026-08-21a` と `job API listening on 127.0.0.1:8787` が出れば完了です。

## Step 8. 回収されないことを確認する

1週間ほど動かしてから、メモリ使用率を確認します。

```bash
free -m
```

`used` が 1200 MB を超えていれば、アイドル判定の対象外です。CPUワーカーがモデルを常駐させているぶんで足りているはずです。

足りない場合（CPUワーカーを無効化した場合など）は、`tmpfs` に常駐データを置くなどの方法もありますが、素直にCPUワーカーを動かすのが一番自然です。どうしても不安なら、Pay As You Go にアップグレードして予算アラートを $1 に設定してください。

---

## ARMでfaster-whisperが入らない場合

`ctranslate2` のaarch64ホイールが見つからずインストールに失敗することがあります。その場合はCPUワーカーを諦めます。

```bash
sudo systemctl disable --now clipvault-worker
```

自宅PC専用構成として問題なく動きます。ただし**アイドル回収対策が消える**ので、代わりの負荷を用意してください。最も手軽なのは、メモリを掴むだけの常駐プロセスです。

```bash
sudo tee /etc/systemd/system/clipvault-ballast.service >/dev/null <<'EOF'
[Unit]
Description=Memory ballast to avoid Always Free idle reclamation

[Service]
Type=simple
ExecStart=/usr/bin/python3 -c "import time; x = bytearray(2*1024*1024*1024); \
[x.__setitem__(slice(0, 4096), b'\\x01'*4096) or time.sleep(300) for _ in iter(int, 1)]"
Restart=always
Nice=19

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl enable --now clipvault-ballast
```

2 GBを確保し、5分ごとに触れてスワップアウトを防ぎます。**あまり品の良い手ではありません**が、Oracleの判定条件に合わせるなら現実的な選択です。CPUワーカーが動くならこちらは不要です。

---

## 費用が発生しないことの確認

コンソール左上メニュー → **Billing & Cost Management** → **Cost Analysis** で、月額が $0.00 であることを確認してください。

Pay As You Go にアップグレードした場合は、**Budgets** で予算を $1 に設定し、アラートを有効にしておきます。Always Free枠内なら発火しませんが、うっかり枠外のリソースを作ったときの保険になります。
