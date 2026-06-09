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
# Detect repository directory
if [ -f "bin/meshbot" ] && [ -d "core" ]; then
  REPO_DIR=$(pwd)
elif [ -d "${HOME}/Meshcore-bot" ] && [ -f "${HOME}/Meshcore-bot/bin/meshbot" ]; then
  REPO_DIR="${HOME}/Meshcore-bot"
else
  REPO_DIR=""
fi

if [ -n "$REPO_DIR" ]; then
  VENV_DIR="${REPO_DIR}/venv"
  if [ -d "$VENV_DIR" ]; then
    echo "[Uninstall] Removing python virtual environment..."
    rm -rf "$VENV_DIR"
  fi
fi

# 5. Clean up process lockfiles
if [ -n "$REPO_DIR" ]; then
  PID_FILE="${REPO_DIR}/config/meshbot.pid"
  if [ -f "$PID_FILE" ]; then
    echo "[Uninstall] Cleaning process lockfiles..."
    rm -f "$PID_FILE"
  fi
fi

# 6. Prompt to clean up configurations
if [ -n "$REPO_DIR" ]; then
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
fi

# 7. Optionally remove repo directory itself
if [ -n "$REPO_DIR" ] && [ "$REPO_DIR" = "${HOME}/Meshcore-bot" ]; then
  echo "--------------------------------------------------"
  read -p "Do you want to completely remove the repository directory ${REPO_DIR}? [y/N]: " -n 1 -r
  echo
  if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "[Uninstall] Removing repository directory..."
    rm -rf "$REPO_DIR"
    echo "Repository directory removed."
  fi
fi

echo "=================================================="
echo "MeshCore-bot has been successfully uninstalled."
echo "=================================================="
