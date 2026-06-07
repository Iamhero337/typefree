# 🎤 Typefree

**Type with your voice.** Instantly convert speech to text using a global hotkey on Linux. Works everywhere—in your browser, terminal, text editor, or any app where you can type.

**Press and hold `Alt+Z` → Release → Text appears** ✨

## Features

- 🌍 **Works Everywhere** - Use in any app, browser, terminal, or text editor
- 🎙️ **Hold-to-Record** - Press Alt+Z to start, release to stop and transcribe
- 📋 **Dual Output** - Copies to clipboard AND outputs to stdout
- 🚀 **Always Running** - Daemon starts automatically on boot
- 🔒 **Offline** - Uses local Whisper model (no cloud required)
- 🎯 **Accurate** - Powered by OpenAI's Whisper
- ⚙️ **Customizable** - Change hotkey anytime

## Requirements

- Linux (Ubuntu/Debian/Fedora/etc.)
- Python 3.7+
- Microphone
- ~1.4 GB disk space (for Whisper model on first run)

## Quick Start

```bash
# 1. Clone or download this repo
cd typefree

# 2. Install (one command)
bash install.sh

# 3. Done! Press Alt+Z to use
```

That's it! The daemon starts automatically and runs in the background.

## Usage

### Recording

1. **Press and hold** `Alt+Z`
2. **Speak into your microphone**
3. **Release Alt+Z**
4. Your speech converts to text and:
   - ✅ Copies to clipboard (paste with `Ctrl+V`)
   - ✅ Prints to stdout (appears where cursor is)

### Example Use Cases

**In a browser:**
```
[Write an email]
Press Alt+Z → "Hello, this is my message" → Release → Text pasted ✨
```

**In terminal:**
```bash
$ git commit -m "
Press Alt+Z → "Fix login bug in auth module" → Release → Text pasted
```

**In any text editor:**
- Notes, documents, code comments - just press Alt+Z!

## Commands

```bash
# Check if daemon is running
bash status.sh

# View live logs
bash logs.sh

# Restart daemon (if needed)
systemctl --user restart speech2text.service

# Stop daemon temporarily
systemctl --user stop speech2text.service

# Start daemon
systemctl --user start speech2text.service

# Completely uninstall
bash uninstall.sh
```

## Customization

### Change Hotkey

The default is `Alt+Z`. To change it:

```bash
# Edit the service file
nano ~/.config/systemd/user/speech2text.service

# Change this line to use a different key (e.g., 'x' for Alt+X):
# Environment="STT_HOTKEY=z"
# to:
# Environment="STT_HOTKEY=x"

# Reload and restart
systemctl --user daemon-reload
systemctl --user restart speech2text.service
```

### Use Different Language

Edit `speech2text.py` and change:
```python
result = self.model.transcribe(tmp_path, language="en")
```

Replace `"en"` with your language code:
- `"es"` for Spanish
- `"fr"` for French
- `"de"` for German
- `"zh"` for Chinese
- etc.

## Troubleshooting

### Daemon not running?
```bash
# Check status
bash status.sh

# View errors
bash logs.sh
```

### No audio input detected?
```bash
# List audio devices
arecord -l

# If no microphone shows, check with:
pactl list sources
```

### Permission denied on install.sh?
```bash
chmod +x install.sh
bash install.sh
```

### Microphone not working in browser?
Some browsers require extra permissions:
- Firefox/Chrome: Check site permissions for microphone
- Your app must be allowed to access audio

### Text not appearing?

1. **Check if xclip is installed:**
   ```bash
   which xclip
   ```
   If not: `sudo apt-get install xclip`

2. **View logs for errors:**
   ```bash
   bash logs.sh
   ```

3. **Try a longer recording** - Whisper needs a bit of audio to work

## How It Works

1. **Global Hotkey Listener** - Watches for Alt+Z across the system
2. **Audio Recording** - Captures microphone input when key is held
3. **Whisper Transcription** - Converts audio to text (offline, on your machine)
4. **Dual Output**:
   - Copies result to clipboard using `xclip`
   - Prints to stdout (inserts where your cursor is)

## What's Installed

- **speech2text.py** - Main daemon (runs in background)
- **Python packages** - whisper, pynput, sounddevice, scipy, numpy
- **Systemd service** - Auto-starts on boot as a user service

All files stay in `~/.local/share/speech-to-text-hotkey` and `~/.config/systemd/user`

## Uninstall

```bash
bash uninstall.sh
```

This removes the daemon and service. To also remove Python packages:
```bash
pip3 uninstall -y openai-whisper pynput sounddevice scipy numpy
```

## Performance

- **First transcription**: ~5-10 seconds (Whisper model loads)
- **Subsequent transcriptions**: ~2-5 seconds (model cached)
- **Disk space**: ~1.4 GB for base Whisper model

Use `bash status.sh` to confirm it's running smoothly.

## Improvements & Feedback

Want to add features? Here are some ideas:

- [ ] GUI settings panel
- [ ] Multiple hotkey support
- [ ] Auto-correct misspellings
- [ ] Language auto-detection
- [ ] Custom wake words
- [ ] Integration with grammar checkers
- [ ] Support for different Whisper models (tiny, small, medium, large)

Found a bug? Create an issue or submit a PR!

## License

Free to use and modify. Enjoy! 🎉

---

**Need help?** Run `bash status.sh` and `bash logs.sh` to debug issues.
