#!/usr/bin/env bash
set -euo pipefail

APP=/home/ubuntu/clipvault

echo "== packages =="
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip ffmpeg fonts-noto-cjk git sqlite3

echo "== virtualenv =="
cd "$APP"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -r requirements-worker.txt

echo "== services =="
sudo cp deploy/clipvault-bot.service /etc/systemd/system/
sudo cp deploy/clipvault-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now clipvault-bot
sudo systemctl enable --now clipvault-worker

echo
echo "done. verify networking with:"
echo "  bash deploy/check-network.sh"
echo
echo "check services with:"
echo "  sudo systemctl status clipvault-bot"
echo "  journalctl -u clipvault-bot -f"
