#!/usr/bin/env bash
# uninstall.sh: Linux Uninstaller for MeshCore-bot Central Hub

set -e # Exit immediately on error

echo "=================================================="
echo "Starting MeshCore-bot Linux Uninstallation Script"
echo "=================================================="

# 1. Stop and disable systemd service
if systemctl is-active --quiet meshcore-bot.service; then
  echo "[Uninstall] Stopping systemd service..."
  sudo systemctl stop meshcore-bot.service || true
fi

if systemctl is-enabled --quiet meshcore-bot.service; then
  echo "[Uninstall] Disabling systemd service..."
  sudo systemctl disable meshcore-bot.service || true
fi

# 2. Remove systemd service file
SERVICE_PATH="/etc/systemd/system/meshcore-bot.service"
if [ -f "$SERVICE_PATH" ]; then
  echo "[Uninstall] Removing systemd service unit file..."
  sudo rm -f "$SERVICE_PATH"
  sudo systemctl daemon-reload
fi

# 3. Remove global wrapper
WRAPPER_PATH="/usr/local/bin/meshbot"
if [ -f "$WRAPPER_PATH" ]; then
  echo "[Uninstall] Removing global CLI wrapper runner..."
  sudo rm -f "$WRAPPER_PATH"
fi

# 4. Remove project virtual environment
REPO_DIR=$(pwd)
VENV_DIR="${REPO_DIR}/venv"
if [ -d "$VENV_DIR" ]; then
  echo "[Uninstall] Removing python virtual environment..."
  rm -rf "$VENV_DIR"
fi

# 5. Clean up process lockfiles
PID_FILE="${REPO_DIR}/config/meshbot.pid"
if [ -f "$PID_FILE" ]; then
  echo "[Uninstall] Cleaning process lockfiles..."
  rm -f "$PID_FILE"
fi

# 6. Prompt to clean up configurations
echo "--------------------------------------------------"
read -p "Do you want to clean up your config.json and bot configurations? [y/N]: " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
  echo "[Uninstall] Cleaning up configurations..."
  rm -f "${REPO_DIR}/config/config.json"
  echo "Configuration files removed."
else
  echo "[Uninstall] Configuration files preserved."
fi

echo "=================================================="
echo "MeshCore-bot has been successfully uninstalled."
echo "=================================================="
