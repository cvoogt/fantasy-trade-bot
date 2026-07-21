#!/usr/bin/env bash
# Uninstall the Fantasy Trade Bot from an LXC/host: stop and remove the systemd
# service, drop the daily value-refresh cron, and (only if you pass --purge)
# delete the install directory including .env and the SQLite DB.
#
# Usage:
#   bash deploy/uninstall.sh            # remove service + cron, keep files
#   bash deploy/uninstall.sh --purge    # also delete /opt/fantasy-trade-bot
set -euo pipefail

SERVICE="fantasy-bot"
UNIT="/etc/systemd/system/${SERVICE}.service"
INSTALL_DIR="/opt/fantasy-trade-bot"
PURGE=0

for arg in "$@"; do
  case "$arg" in
    --purge) PURGE=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "This script manages a systemd unit and needs root — re-run with sudo." >&2
  exit 1
fi

echo "== stopping and disabling ${SERVICE} =="
systemctl disable --now "${SERVICE}" 2>/dev/null || echo "  (service not active/enabled — skipping)"

if [[ -f "${UNIT}" ]]; then
  echo "== removing unit file ${UNIT} =="
  rm -f "${UNIT}"
  systemctl daemon-reload
  systemctl reset-failed "${SERVICE}" 2>/dev/null || true
else
  echo "== no unit file at ${UNIT} — skipping =="
fi

echo "== removing daily value-refresh cron (if present) =="
if crontab -l 2>/dev/null | grep -q "${INSTALL_DIR}.*src.cli init"; then
  crontab -l 2>/dev/null | grep -v "${INSTALL_DIR}.*src.cli init" | crontab -
  echo "  removed."
else
  echo "  none found."
fi

if [[ $PURGE -eq 1 ]]; then
  echo
  echo "!! --purge will delete ${INSTALL_DIR} including .env and trade_bot.db !!"
  read -r -p "Type the install path to confirm deletion: " reply
  if [[ "${reply}" == "${INSTALL_DIR}" ]]; then
    rm -rf "${INSTALL_DIR}"
    echo "== deleted ${INSTALL_DIR} =="
  else
    echo "== path did not match — leaving ${INSTALL_DIR} in place =="
  fi
else
  echo "== left ${INSTALL_DIR} in place (pass --purge to delete it) =="
fi

echo "== done =="
