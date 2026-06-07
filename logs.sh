#!/bin/bash

echo "📋 Speech-to-Text Hotkey Logs (live)"
echo "Press Ctrl+C to exit"
echo "========================================"

journalctl --user -u speech2text.service -f
