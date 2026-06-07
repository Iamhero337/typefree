#!/bin/bash

set -e

echo "🗑️  Uninstalling Speech-to-Text Hotkey..."
echo ""

# Stop and disable the service
echo "Stopping daemon..."
systemctl --user stop speech2text.service || true
systemctl --user disable speech2text.service || true

# Remove service file
echo "Removing service..."
rm -f ~/.config/systemd/user/speech2text.service
systemctl --user daemon-reload

# Remove daemon files
echo "Removing application files..."
rm -rf ~/.local/share/speech-to-text-hotkey

echo ""
echo "✅ Uninstall complete!"
echo ""
echo "To also remove Python packages, run:"
echo "   pip3 uninstall -y openai-whisper pynput sounddevice scipy numpy"
