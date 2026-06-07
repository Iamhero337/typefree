#!/bin/bash

set -e

echo "========================================"
echo "🎤 Speech-to-Text Hotkey Installer"
echo "========================================"
echo ""

# Check if running on Linux
if [[ ! "$OSTYPE" == "linux-gnu"* ]]; then
    echo "❌ Error: This tool only works on Linux"
    exit 1
fi

# Check if pip3 is installed
if ! command -v pip3 &> /dev/null; then
    echo "❌ Error: Python 3 and pip3 are required"
    echo "Install with: sudo apt-get install python3-pip"
    exit 1
fi

echo "📦 Installing system dependencies..."
sudo apt-get update
sudo apt-get install -y python3-pip xclip alsa-utils pulseaudio 2>/dev/null || {
    echo "⚠️  Could not install all system packages automatically"
    echo "Please ensure xclip, alsa-utils, and pulseaudio are installed"
}

echo "🐍 Installing Python packages..."
pip3 install -q -r requirements.txt

echo "📁 Setting up daemon..."
mkdir -p ~/.local/share/speech-to-text-hotkey
cp speech2text.py ~/.local/share/speech-to-text-hotkey/
chmod +x ~/.local/share/speech-to-text-hotkey/speech2text.py

echo "⚙️  Setting up systemd service..."
mkdir -p ~/.config/systemd/user
cp speech2text.service ~/.config/systemd/user/
systemctl --user daemon-reload

echo "🚀 Starting daemon..."
systemctl --user enable --now speech2text.service

# Wait a moment and check status
sleep 2

if systemctl --user is-active --quiet speech2text.service; then
    echo ""
    echo "========================================"
    echo "✅ Installation complete!"
    echo "========================================"
    echo ""
    echo "🎙️  Press and hold Alt+Z to record"
    echo ""
    echo "📝 Useful commands:"
    echo "   uninstall.sh         - Remove the daemon"
    echo "   ./status.sh          - Check daemon status"
    echo "   ./logs.sh            - View live logs"
    echo ""
else
    echo ""
    echo "❌ Failed to start daemon"
    echo "View logs with: journalctl --user -u speech2text.service -n 50"
    exit 1
fi
