#!/bin/bash

echo "🔍 Speech-to-Text Hotkey Status"
echo "========================================"

if systemctl --user is-active --quiet speech2text.service; then
    echo "✅ Status: Running"
else
    echo "❌ Status: Stopped"
fi

echo ""
systemctl --user status speech2text.service --no-pager || true
