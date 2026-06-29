# Typefree Project Rules

## Architecture Constraints
- **Do NOT use `dev.grab()` in `typefree.py`**: A previous refactor (`f263b02`) attempted to use `dev.grab()` in the evdev loop. This broke the user's keyboard completely by swallowing all keystrokes and causing the system to lock up or emit random presses. The correct approach is passive listening as implemented in `e77c94d`.

## Stable Anchor
- **The Perfect Stable Build**: Commit `e77c94d` (from June 7, 2026). If anything goes wrong or the app breaks inexplicably during future development, **RESTORE TO `e77c94d`**. This is the known good state.
