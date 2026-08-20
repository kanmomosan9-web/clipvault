# ネットワーク設定

## 方針：開けるのは22番だけ

ClipVaultのジョブAPIは `127.0.0.1:8787` にバインドしています。**インターネットからは到達できません。** 自宅PCからはSSHトンネルで繋ぐため、追加のポート開放は一切不要です。

```
自宅PC                          Oracle Cloud VM
┌──────────────┐                ┌──────────────────────┐
│ worker.py    │                │  bot.py              │
│   ↓          │                │    ↓                 │
│ 127.0.0.1:8787│══ SSH :22 ════▶│  127.0.0.1:8787      │
│ (トンネル入口) │  暗号化済み     │  (APIはローカルのみ)  │
└──────────────┘                └──────────────────────┘
```

**開放ポート = 22/TCP のみ。** これがネットワーク設定の全てです。攻撃対象が最小になり、TLS証明書も不要になります。

---

## 1. インスタンス作成時の「Networking」欄

Create instance 画面の Networking セクションで設定する項目です。

| 項目 | 設定値 | 意味 |
|---|---|---|
| Primary network | **Create new virtual cloud network** | 初回はこれ。2台目以降は既存VCNを選ぶ |
| New virtual cloud network name | `vcn-clipvault` | 任意 |
| Subnet | **Create new public subnet** | パブリックサブネット。プライベートを選ぶとSSHできない |
| New subnet name | `subnet-public` | 任意 |
| Private IPv4 address | Automatically assign | 既定のまま |
| **Assign a public IPv4 address** | **Yes** | ここが No だと外部から一切繋がらない |
| Assign a private IPv6 address | No | 不要 |

この選択で、Oracleが裏側に以下を自動作成します。

| リソース | 内容 |
|---|---|
| VCN | `10.0.0.0/16` |
| パブリックサブネット | `10.0.0.0/24` |
| インターネットゲートウェイ | VCNを外部に繋ぐ出入口 |
| ルートテーブル | `0.0.0.0/0` → インターネットゲートウェイ |
| セキュリティリスト | 下記の既定ルール |
| DHCPオプション | 既定のDNS設定 |

**この自動作成された内容が、そのまま今回の要件を満たしています。** 手を加える必要はありません。

## 2. セキュリティリストの既定値を確認する

念のため中身を確認しておきます。

コンソール左上メニュー → **Networking** → **Virtual cloud networks** → `vcn-clipvault` → **Security Lists** → `Default Security List for vcn-clipvault`

### イングレス（外部 → VM）

| Source | Protocol | Dest. Port | 用途 |
|---|---|---|---|
| `0.0.0.0/0` | TCP | **22** | SSH。**これだけが必要** |
| `0.0.0.0/0` | ICMP type 3, code 4 | — | Path MTU Discovery。**消さないこと** |
| `10.0.0.0/16` | ICMP type 3 | — | VCN内部の到達不能通知 |

**ICMPの2行を消さないでください。** 「ICMPは危険」と思って消すと、経路上のMTU不一致でSSHが途中でフリーズする、大きなファイルの転送だけ止まる、といった原因の掴みにくい障害が起きます。

### エグレス（VM → 外部）

| Destination | Protocol | 用途 |
|---|---|---|
| `0.0.0.0/0` | All | 全許可 |

**全許可のままにしてください。** VMからは以下への外向き通信が必要です。

- Discord Gateway（WebSocket / 443）
- Discord CDN（動画・添付の取得 / 443）
- apt リポジトリ、PyPI、Hugging Face（Whisperモデルのダウンロード / 443）

絞るなら宛先IPの管理が必要になりますが、Discordのアドレスは頻繁に変わるため実務的ではありません。

### 追加すべきルール

**ありません。** 8787番を開ける必要はなく、開けてはいけません。もし開けてしまうと、`WORKER_TOKEN` の総当たり攻撃を受ける対象がインターネット全体に広がります。

## 3. インスタンス内部のファイアウォール

Oracleの Ubuntu イメージは、**VCNのセキュリティリストとは別に、インスタンス内にも iptables ルールを持っています**。両方を通らないと通信できません。ここが最大の落とし穴です。

```
外部 ──▶ [セキュリティリスト] ──▶ [インスタンス内 iptables] ──▶ プロセス
           コンソールで設定           OS内で設定
```

現状を確認します。

```bash
sudo iptables -L INPUT -n --line-numbers
```

22番のACCEPTが入っていればOKです。**今回は22番しか使わないので、何も変更しません。**

### ufw を有効にしないこと

```bash
sudo ufw enable   # ← これをやると閉め出される可能性が高い
```

Ubuntuに慣れているとつい `ufw` を使いたくなりますが、Oracleのイメージでは既存のiptablesルールと競合し、**SSHごと遮断してPCから二度と入れなくなる**事故が頻発します。使う場合は必ず先に `sudo ufw allow 22/tcp` を実行してください。

### 閉め出された場合の復旧

コンソールから**シリアルコンソール**で入れます。

インスタンス詳細 → **Resources** → **Console connection** → **Launch Cloud Shell connection**

ネットワークを経由しないため、ファイアウォールを壊しても復旧できます。**この存在を知っているだけで、ネットワーク設定を触る心理的なハードルが下がります。**

## 4. SSH を 0.0.0.0/0 のままにする判断

22番を自宅のIPだけに制限したくなりますが、今回は**全開放のまま鍵認証とfail2banで守ることを推奨**します。

理由は、日本の家庭用回線はIPアドレスが固定でないことが多く、**IPが変わった瞬間に自分が閉め出される**からです。復旧はシリアルコンソール経由で可能ですが、ワーカーのトンネルもその間切れます。

代わりに以下で守ります。

```bash
sudo apt install -y fail2ban
sudo systemctl enable --now fail2ban
sudo sshd -T | grep -E "passwordauthentication|permitrootlogin"
```

`passwordauthentication no` であれば、パスワード総当たりは原理的に成立しません。ログは汚れますが実害はありません。

固定IP契約がある、あるいはVPN経由で固定できるなら、イングレスの `0.0.0.0/0` を自分のIP `/32` に絞ってください。そのほうが当然安全です。

## 5. パブリックIPの扱い

既定で割り当てられるのは**エフェメラルIP**です。VNICが存在する限り保持されるので、再起動やアイドル停止では変わりません。ただしインスタンスを削除して作り直すと別のIPになります。

IPを固定したい場合は**予約済みパブリックIP**に変換できます。

インスタンス詳細 → **Resources** → **Attached VNICs** → プライマリVNICをクリック → **IPv4 Addresses** → 該当行の「⋮」 → **Edit** → Public IP Type を **Reserved public IP** → **Create a new reserved IP**

> 予約済みIPを**インスタンスから外したまま放置すると課金対象になる可能性があります**。使わなくなったら必ず解放してください。

IPが変わっても平気にしたいなら、後述のTailscaleのほうが確実です。

## 6. 接続の検証

### VM側

APIが外部に露出していないことを確認します。

```bash
ss -tlnp | grep 8787
```

```
LISTEN 0 128 127.0.0.1:8787 0.0.0.0:*
```

**`127.0.0.1:8787` になっていることが重要です。** `0.0.0.0:8787` と表示されたら `.env` の `API_HOST` が間違っています。

ローカルからは応答することを確認します。

```bash
curl -s http://127.0.0.1:8787/health
```

```json
{"status":"ok","bot":"ClipVault#1234","clips":47,"queue":{"pending":2}}
```

### 外部から到達できないことの確認

自宅PCから、トンネル**なし**で叩いてみます。

```powershell
curl http://<パブリックIP>:8787/health
```

タイムアウトまたは接続拒否になれば正しい状態です。応答が返ってきたら設定が間違っています。

### トンネル経由

```powershell
ssh -i $env:USERPROFILE\.ssh\oracle_vm -N -L 8787:127.0.0.1:8787 ubuntu@<パブリックIP>
```

別ウィンドウで、

```powershell
curl http://127.0.0.1:8787/health
```

VM上と同じJSONが返れば完成です。

## 7. トンネルのオプション解説

`deploy/tunnel.bat` で使っているオプションの意味です。

| オプション | 効果 |
|---|---|
| `-N` | リモートコマンドを実行しない（ポート転送のみ） |
| `-L 8787:127.0.0.1:8787` | ローカル8787をVMの127.0.0.1:8787へ転送 |
| `-o ServerAliveInterval=30` | 30秒ごとに死活確認。NAT越しの無通信切断を防ぐ |
| `-o ExitOnForwardFailure=yes` | **転送に失敗したら即終了**。これが無いと「繋がっているのに転送されていない」状態に気づけない |

`-L` の左の `8787` はローカルPCのポートなので、自宅で既に使っていれば `18787:127.0.0.1:8787` のように変えて、ワーカーの `WORKER_API_URL` も合わせてください。

---

## 代替案：Tailscale

SSHトンネルを常時維持するのが煩わしい場合、Tailscaleを使うとより安定します。

```bash
# VM側
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

自宅PCにもTailscaleを入れて同じアカウントでログインすると、両者が仮想的な同一ネットワークに入ります。

VM側の `.env` を変更します。

```
API_HOST=100.x.x.x     # tailscale ip -4 で確認したVMのTailscale IP
```

自宅PC側は、

```
WORKER_API_URL=http://100.x.x.x:8787
```

**この場合もセキュリティリストは変更不要です。** TailscaleはWireGuardで穴を開けずにP2P接続を確立するため、22番以外を開ける必要がありません。

| | SSHトンネル | Tailscale |
|---|---|---|
| 追加ソフト | 不要 | 両方に必要 |
| 再接続 | batでループ | 自動 |
| IPが変わったとき | 手動修正 | 影響なし |
| 依存先 | 無し | Tailscaleの認証サーバー |

**まずSSHトンネルで動かし、面倒になったらTailscaleに移行**するのが無難です。どちらもコード変更は不要で、`.env` の1行だけの違いです。

---

## トラブルシューティング

| 症状 | 原因と対処 |
|---|---|
| SSHがタイムアウト | セキュリティリストに22番のイングレスが無い、またはパブリックIP未割り当て |
| SSHが `Connection refused` | インスタンスは起動中だがsshdが未起動。数分待つ |
| SSHが `Permission denied (publickey)` | ユーザー名が違う（Ubuntuは `ubuntu`、Oracle Linuxは `opc`） |
| SSHが途中で固まる、大きい転送だけ失敗 | ICMP type 3 code 4 のルールを消している。復活させる |
| トンネルは繋がるがcurlが失敗 | VM側でbotが停止中。`sudo systemctl status clipvault-bot` |
| ワーカーが401 | `WORKER_TOKEN` がVMと自宅PCで不一致 |
| `bind: Address already in use` | ローカルの8787が使用中。別ポートに変える |
| 全部設定したのに繋がらない | セキュリティリストとインスタンス内iptablesの**両方**を確認したか |
