#!/bin/bash
echo "📋 Typefree logs (live) — Ctrl+C to exit"
echo "========================================"
journalctl --user -u typefree.service -f
