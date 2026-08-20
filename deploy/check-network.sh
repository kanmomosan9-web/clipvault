#!/usr/bin/env bash
# ClipVault のネットワーク設定を検証する。VM 上で実行する。

OK="  OK  "; NG="  NG  "; WARN=" WARN "
fail=0

line() { printf '[%s] %s%s\n' "$1" "$2" "${3:+   $3}"; }

echo
echo "=== ClipVault network check ==="
echo

echo "--- API のバインド先 ---"
listen=$(ss -tlnH 2>/dev/null | awk '$4 ~ /:8787$/ {print $4}' | head -1)
if [ -z "$listen" ]; then
  line "$NG" "8787 で待ち受けていない" "bot が起動していない可能性: systemctl status clipvault-bot"
  fail=1
elif [[ "$listen" == 127.0.0.1:* || "$listen" == "[::1]:"* ]]; then
  line "$OK" "ローカルのみ" "$listen"
else
  line "$NG" "外部に露出している" "$listen  →  .env の API_HOST を 127.0.0.1 に"
  fail=1
fi

echo
echo "--- API の応答 ---"
if body=$(curl -s --max-time 5 http://127.0.0.1:8787/health); then
  line "$OK" "GET /health" "$(echo "$body" | head -c 120)"
else
  line "$NG" "GET /health に失敗" "bot のログを確認: journalctl -u clipvault-bot -n 50"
  fail=1
fi

echo
echo "--- 外向き通信 ---"
for host in discord.com cdn.discordapp.com huggingface.co; do
  if curl -s --max-time 8 -o /dev/null -w '%{http_code}' "https://$host" | grep -qE '^[123]'; then
    line "$OK" "$host"
  else
    line "$NG" "$host に到達できない" "エグレスルールが 0.0.0.0/0 All か確認"
    fail=1
  fi
done

echo
echo "--- インスタンス内ファイアウォール ---"
if sudo iptables -S INPUT 2>/dev/null | grep -qE 'dport 22 .*ACCEPT|--dport 22'; then
  line "$OK" "22/tcp が許可されている"
else
  line "$WARN" "22/tcp の明示的な許可が見つからない" "ポリシーが ACCEPT なら問題なし"
fi
if sudo iptables -S INPUT 2>/dev/null | grep -q '\-\-dport 8787'; then
  line "$NG" "8787 が iptables で開いている" "不要。閉じること"
  fail=1
else
  line "$OK" "8787 は開いていない"
fi
if systemctl is-active --quiet ufw; then
  line "$WARN" "ufw が有効" "Oracle の既定ルールと競合しやすい。22 が許可されているか確認"
else
  line "$OK" "ufw は無効"
fi

echo
echo "--- SSH の設定 ---"
pw=$(sudo sshd -T 2>/dev/null | grep -i '^passwordauthentication' | awk '{print $2}')
[ "$pw" = "no" ] && line "$OK" "パスワード認証" "無効" \
  || { line "$NG" "パスワード認証が有効" "鍵認証のみにすること"; fail=1; }
if systemctl is-active --quiet fail2ban; then
  line "$OK" "fail2ban 稼働中"
else
  line "$WARN" "fail2ban が動いていない" "sudo apt install -y fail2ban && sudo systemctl enable --now fail2ban"
fi

echo
echo "--- アイドル回収の対策 ---"
total=$(free -m | awk '/^Mem:/{print $2}')
used=$(free -m | awk '/^Mem:/{print $3}')
pct=$(( used * 100 / total ))
if [ "$pct" -ge 12 ]; then
  line "$OK" "メモリ使用率 ${pct}%" "${used}MB / ${total}MB — 10% を超えており回収対象外"
else
  line "$WARN" "メモリ使用率 ${pct}%" "10% を下回ると 7 日で停止対象。ORACLE.md を参照"
fi

echo
if [ "$fail" -eq 0 ]; then
  echo "=== 問題なし ==="
  echo "自宅PCから次を実行してトンネルを確認してください:"
  echo "  ssh -N -L 8787:127.0.0.1:8787 ubuntu@<パブリックIP>"
  echo "  curl http://127.0.0.1:8787/health"
else
  echo "=== 要対応の項目があります（上の NG を確認）==="
fi
echo
exit "$fail"
