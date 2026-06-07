#!/bin/bash
set -e
echo "🗑️  Uninstalling Typefree..."
echo ""

for svc in typefree ydotoold; do
    systemctl --user stop $svc.service 2>/dev/null || true
    systemctl --user disable $svc.service 2>/dev/null || true
    rm -f ~/.config/systemd/user/$svc.service
done
systemctl --user daemon-reload

rm -rf ~/.local/share/typefree

echo ""
echo "✅ Uninstalled."
echo ""
echo "Kept (remove manually if you want):"
echo "  • config:        ~/.config/typefree"
echo "  • udev rule:     sudo rm /etc/udev/rules.d/99-typefree-uinput.rules"
echo "  • 'input' group: sudo gpasswd -d $USER input"
echo "  • python deps:   pip3 uninstall --break-system-packages -y openai-whisper evdev sounddevice"
