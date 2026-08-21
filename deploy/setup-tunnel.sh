#!/usr/bin/env bash
# Cloudflare Tunnel を入れて ClipVault の Web ライブラリを HTTPS 公開する。
# ポートは一切開けない。トンネルが内側から Cloudflare に繋ぎに行く。
set -euo pipefail

ARCH=$(dpkg --print-architecture)
URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${ARCH}"

echo "== cloudflared =="
curl -fsSL "$URL" -o /tmp/cloudflared
sudo install -m 755 /tmp/cloudflared /usr/local/bin/cloudflared
cloudflared --version

cat <<'MSG'

次の手順:

 1. https://one.dash.cloudflare.com/ → Networks → Tunnels → Create a tunnel
 2. Cloudflared を選び、名前を clipvault にして保存
 3. 表示されたトークン（eyJ... で始まる長い文字列）をコピー
 4. Public hostname を追加:
      Subdomain : clips
      Domain    : （持っているドメイン。無ければ手順下部を参照）
      Service   : HTTP  ->  localhost:8787
 5. このVMで:
      sudo cp deploy/cloudflared.service /etc/systemd/system/
      sudo sed -i "s|REPLACE_WITH_TUNNEL_TOKEN|<コピーしたトークン>|" \
        /etc/systemd/system/cloudflared.service
      sudo systemctl daemon-reload && sudo systemctl enable --now cloudflared
 6. .env に公開URLを設定して bot を再起動:
      PUBLIC_URL=https://clips.example.com
      sudo systemctl restart clipvault-bot

ドメインを持っていない場合、まず動作確認だけなら使い捨てのトンネルが使える:

      cloudflared tunnel --url http://localhost:8787

  実行すると https://xxxx-yyyy.trycloudflare.com が払い出される。
  そのURLを PUBLIC_URL に設定すれば即座に試せる。
  ただし再起動のたびにURLが変わるため、常用には向かない。

MSG
