#!/bin/bash
echo "🔍 Typefree status"
echo "========================================"
for svc in ydotoold typefree; do
    if systemctl --user is-active --quiet $svc.service; then
        echo "✅ $svc: running"
    else
        echo "❌ $svc: stopped"
    fi
done
echo ""
if id -nG | grep -qw input; then
    echo "✅ 'input' group active (keyboard readable)"
else
    echo "⚠️  not in active 'input' group — log out/in if you just installed"
fi
echo ""
systemctl --user status typefree.service --no-pager 2>/dev/null | head -n 12 || true
