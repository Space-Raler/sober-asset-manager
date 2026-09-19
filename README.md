# Sober Asset Overlay Manager

A desktop app for installing custom cursors, fonts, and other
replaceable assets into **Sober**, the Linux Roblox client by
[VinegarHQ](https://vinegarhq.org) — featuring automatic backups, per-change
undo, preset management, and a one-click panic button if a mod breaks something.

![Sober Asset Overlay Manager Screenshot](./screenshot.png)

## Why

Sober lets you override game assets using its [asset overlay
system](https://vinegarhq.org/Sober/Configuration/TipsAndTricks.html), but
it means recreating a specific nested folder structure and knowing the
exact filenames Roblox expects — and there's no built-in way to undo a
change if it breaks something. This tool handles the folder/file mechanics
and adds safety nets on top.

## Features

- **Cursors** — install a PNG as any of Sober's known cursor slots (arrow,
  far arrow, I-beam) or a custom filename
- **Fonts** — install custom fonts (`.ttf`, `.otf`) with automatic full font family JSON mapping
- **Presets Management** — save, load, and delete complete configurations of your active asset overlays
- **Custom path (advanced)** — replace *any* overlay-able asset by typing
  its exact path relative to `asset_overlay/` or using the built-in Asset Finder
- **Automatic backups** — whatever was previously in that slot (if
  anything) is backed up before being overwritten
- **Undo** — revert your last change, or any specific past change, from
  the history list
- **Panic button** — instantly disables *every* active override at once by
  moving the whole overlay folder aside, in case a mod softlocks the game
  or breaks something. Nothing is deleted — one click brings it all back
- Warns you if it can't find a Sober installation, instead of failing
  silently
- Dark UI, no external dependencies beyond Python + tkinter (+ optional
  Pillow for image previews)

## Requirements

- Linux (Sober is Linux-only, distributed via Flatpak)
- [Sober](https://vinegarhq.org) installed and launched at least once
- Python 3
- `tkinter`
- `Pillow` (optional, only used for the image preview thumbnail)

## Install & Run

```bash
git clone [https://github.com/Space-Raler/sober-asset-manager.git](https://github.com/Space-Raler/sober-asset-manager.git)
cd sober-asset-manager

# tkinter (if not already installed)
sudo apt install python3-tk        # Debian/Ubuntu
sudo dnf install python3-tkinter  # Fedora
sudo pacman -S tk                 # Arch

# optional, for image preview
pip install --user -r requirements.txt

python3 sober_asset_manager.py