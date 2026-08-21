#!/usr/bin/env bash
# Tailscale Funnel で ClipVault に固定の HTTPS アドレスを与える。
# ポートは開けない。Funnel が内側から Tailscale に繋ぎに行く。
set -euo pipefail

if ! command -v tailscale >/dev/null; then
  echo "== tailscale =="
  curl -fsSL https://tailscale.com/install.sh | sh
fi

echo
echo "== ログイン =="
echo "表示される URL をブラウザで開いて承認してください。"
sudo tailscale up --hostname=clipvault

echo
echo "== Funnel を有効化 =="
echo "初回は管理画面で Funnel の利用許可が必要です。"
echo "エラーが出たら https://login.tailscale.com/admin/dns で"
echo "  Settings -> Funnel を有効にしてから再実行してください。"
sudo tailscale funnel --bg 8787

echo
echo "== 現在の状態 =="
sudo tailscale funnel status
echo
URL=$(sudo tailscale funnel status 2>/dev/null | grep -oE 'https://[a-z0-9.-]+\.ts\.net' | head -1)
if [ -n "$URL" ]; then
  echo "公開URL: $URL"
  echo
  echo "次の手順:"
  echo "  1. .env に設定"
  echo "       PUBLIC_URL=$URL"
  echo "  2. bot を再起動"
  echo "       sudo systemctl restart clipvault-bot"
  echo "  3. 自宅PC の .env を書き換え（SSHトンネルは不要になります）"
  echo "       WORKER_API_URL=$URL"
  echo "  4. Discord Developer Portal -> Activities -> URL Mappings"
  echo "       /            ->  ${URL#https://}"
  echo "       /.proxy/cdn  ->  cdn.discordapp.com"
else
  echo "URL を取得できませんでした。'sudo tailscale funnel status' を確認してください。"
fi
