# Recording the demo GIF

A good README demo is ~15–25s: open a text field, **hold Right Ctrl**, say a
sentence, release, and let the viewer watch the words appear at the cursor.

## Wayland (KDE/GNOME) — record a region, convert to GIF

```bash
# 1. install once
sudo apt install -y wf-recorder      # screen capture on Wayland
cargo install gifski 2>/dev/null || sudo apt install -y gifski   # mp4/png -> gif

# 2. record a screen region to mp4 (Ctrl+C to stop)
wf-recorder -g "$(slurp)" -f /tmp/demo.mp4     # `slurp` lets you drag a box

# 3. convert to a crisp, small GIF
ffmpeg -i /tmp/demo.mp4 -vf "fps=15,scale=900:-1:flags=lanczos" /tmp/frames_%04d.png
gifski -o docs/demo.gif --fps 15 --width 900 /tmp/frames_*.png
```

## X11 alternative

```bash
sudo apt install -y peek      # point-and-shoot GIF recorder with a GUI
```

## Then

1. Save the file as `docs/demo.gif`.
2. In the top-level `README.md`, uncomment the `![Typefree in action](docs/demo.gif)`
   line and delete the "📹 A short screen recording goes here" note.
3. Commit: `git add docs/demo.gif README.md && git commit -m "Add demo GIF"`.

Keep it under ~5 MB so it loads fast on the GitHub page (lower `--width` or `--fps`
if needed).
