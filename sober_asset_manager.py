#!/usr/bin/env python3
"""
Sober Asset Overlay Manager
----------------------------
A small GUI tool for installing custom cursors, fonts, or any other
replaceable asset into Sober (the Linux Roblox client by VinegarHQ),
using its "asset overlay" system.

Includes:
  - Cursor and font replacement (with known common filenames + full font family patching)
  - A "Custom path" mode for any other overlay-able asset
  - Preset management (save, load, and delete custom configurations)
  - Automatic backups + per-change undo
  - A panic button to instantly disable ALL mods if something breaks
    (e.g. the game fails to load / softlocks), and re-enable them later

Requires: python3, tkinter, and Pillow (optional, for image preview)
    Install tkinter (Debian/Ubuntu):  sudo apt install python3-tk
    Install tkinter (Fedora):         sudo dnf install python3-tkinter
    Install tkinter (Arch):           sudo pacman -S tk
    Install Pillow (optional):        pip install --user Pillow

Run with:  python3 sober_asset_manager.py
"""

import os
import re
import json
import shutil
import zipfile
import datetime
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, simpledialog
from pathlib import Path

try:
    from PIL import Image, ImageTk
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False

# ---------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------

SOBER_APP_DIR = os.path.expanduser("~/.var/app/org.vinegarhq.Sober")
SOBER_DATA_DIR = os.path.join(SOBER_APP_DIR, "data", "sober")
ASSET_OVERLAY_DIR = os.path.join(SOBER_DATA_DIR, "asset_overlay")
PACKAGES_DIR = os.path.join(SOBER_DATA_DIR, "packages")

# Extensions grouped for the asset finder's filter dropdown
FONT_EXTS = (".ttf", ".otf")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".tga")

APP_DATA_DIR = os.path.expanduser("~/.local/share/sober-asset-manager")
BACKUPS_DIR = os.path.join(APP_DATA_DIR, "backups")
PRESETS_DIR = os.path.join(APP_DATA_DIR, "presets")
HISTORY_FILE = os.path.join(APP_DATA_DIR, "history.json")

# ---------------------------------------------------------------------
# Known asset categories
# ---------------------------------------------------------------------

CURSOR_BASE = "content/textures/Cursors/KeyboardMouse"
FONT_BASE = "content/fonts"

CURSOR_KNOWN = {
    "Arrow (default pointer)": "ArrowCursor.png",
    "Arrow, far (zoomed out)": "ArrowFarCursor.png",
    "I-Beam (text fields)": "IBeamCursor.png",
    "Custom filename...": "",
}

FONT_KNOWN = {
    "Source Sans Pro Regular (SourceSansPro-Regular.ttf)": "SourceSansPro-Regular.ttf",
    "Builder Sans Regular (BuilderSans-Regular.ttf)": "BuilderSans-Regular.ttf",
    "Custom filename...": "",
}

CATEGORIES = {
    "Font": {"base": FONT_BASE, "known": FONT_KNOWN, "ext": ".ttf"},
    "Cursor": {"base": CURSOR_BASE, "known": CURSOR_KNOWN, "ext": ".png"},
    "Custom path (advanced)": {"base": "", "known": {}, "ext": ""},
}

# ---------------------------------------------------------------------
# Color palette (dark theme)
# ---------------------------------------------------------------------

BG = "#181a1f"
BG_PANEL = "#20232b"
BG_PANEL_ALT = "#262a33"
BORDER = "#31363f"
FG = "#e6e6e6"
FG_MUTED = "#8b93a1"
ACCENT = "#5b8cff"
ACCENT_HOVER = "#4574e6"
SUCCESS = "#3ddc84"
DANGER = "#ff6161"
DANGER_HOVER = "#e04b4b"
WARN = "#f5a623"
WARN_HOVER = "#d6900f"

FONT_FAMILY = "Sans"


def find_base_apk():
    if not os.path.isdir(PACKAGES_DIR):
        return None
    for root, _dirs, files in os.walk(PACKAGES_DIR):
        for f in files:
            if f == "base.apk" and "com.roblox.client" in root:
                return os.path.join(root, f)
    return None


class RoundedButton(tk.Button):
    def __init__(self, master, bg=ACCENT, hover=ACCENT_HOVER, fg="#ffffff", **kwargs):
        super().__init__(
            master, bg=bg, fg=fg, activebackground=hover, activeforeground=fg,
            relief="flat", bd=0, padx=14, pady=8,
            font=(FONT_FAMILY, 10, "bold"), cursor="hand2",
            highlightthickness=0, **kwargs,
        )
        self._bg, self._hover = bg, hover
        self.bind("<Enter>", lambda e: self.config(bg=self._hover))
        self.bind("<Leave>", lambda e: self.config(bg=self._bg))


def iter_installed_assets():
    if not os.path.isdir(PACKAGES_DIR):
        return
    for root, _dirs, files in os.walk(PACKAGES_DIR):
        for fname in files:
            if not fname.lower().endswith(".apk"):
                continue
            apk_path = os.path.join(root, fname)
            try:
                with zipfile.ZipFile(apk_path) as zf:
                    for entry in zf.namelist():
                        if entry.endswith("/"):
                            continue
                        if "assets/" not in entry:
                            continue
                        rel = entry.split("assets/", 1)[1]
                        if not rel:
                            continue
                        yield rel, (apk_path, entry)
            except zipfile.BadZipFile:
                continue
            except (PermissionError, OSError):
                continue


class AssetFinderDialog(tk.Toplevel):
    def __init__(self, master, on_pick, ext_filter=None):
        super().__init__(master)
        self.title("Find asset in your Roblox files")
        self.geometry("560x460")
        self.configure(bg=BG)
        self.on_pick = on_pick
        self.ext_filter = ext_filter
        self._all_results = []

        tk.Label(
            self, text="Search files Sober has already downloaded for this "
                       "game, to find the exact current filename/path.",
            bg=BG, fg=FG_MUTED, font=(FONT_FAMILY, 8), wraplength=520,
            justify="left",
        ).pack(fill="x", padx=14, pady=(14, 6))

        search_row = tk.Frame(self, bg=BG)
        search_row.pack(fill="x", padx=14)
        self.query_var = tk.StringVar()
        entry = tk.Entry(
            search_row, textvariable=self.query_var, bg=BG_PANEL_ALT, fg=FG,
            insertbackground=FG, relief="flat", font=(FONT_FAMILY, 10),
        )
        entry.pack(fill="x", ipady=4)
        entry.bind("<KeyRelease>", lambda e: self._refresh())
        entry.focus_set()

        list_frame = tk.Frame(self, bg=BG_PANEL_ALT, highlightbackground=BORDER,
                                highlightthickness=1)
        list_frame.pack(fill="both", expand=True, padx=14, pady=10)
        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side="right", fill="y")
        self.listbox = tk.Listbox(
            list_frame, bg=BG_PANEL_ALT, fg=FG, selectbackground=ACCENT,
            selectforeground="#ffffff", relief="flat", font=(FONT_FAMILY, 9),
            highlightthickness=0, bd=0, yscrollcommand=scrollbar.set,
        )
        self.listbox.pack(side="left", fill="both", expand=True, padx=4, pady=4)
        scrollbar.config(command=self.listbox.yview)
        self.listbox.bind("<Double-Button-1>", lambda e: self._pick())

        self.status = tk.Label(self, text="Scanning installed Roblox files (please wait)...", bg=BG, fg=WARN,
                                 font=(FONT_FAMILY, 8))
        self.status.pack(fill="x", padx=14)

        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(fill="x", padx=14, pady=12)
        RoundedButton(btn_row, text="Use Selected", command=self._pick,
                       bg=ACCENT, hover=ACCENT_HOVER).pack(side="right")
        RoundedButton(btn_row, text="Cancel", command=self.destroy,
                       bg=BG_PANEL_ALT, hover=BORDER, fg=FG).pack(side="right", padx=(0, 8))

        threading.Thread(target=self._load_all_async, daemon=True).start()

    def _load_all_async(self):
        if not os.path.isdir(PACKAGES_DIR):
            self.after(0, lambda: self.status.config(
                text="No downloaded Roblox files found yet — launch a game in Sober at least once first.", fg=DANGER
            ))
            self._all_results = []
            return
        results = list(iter_installed_assets())
        if self.ext_filter:
            results = [r for r in results if r[0].lower().endswith(self.ext_filter)]
        self._all_results = results
        self.after(0, self._on_load_complete)

    def _on_load_complete(self):
        self.status.config(text=f"{len(self._all_results)} files found.", fg=SUCCESS)
        self._refresh()

    def _refresh(self):
        if self._all_results is None:
            return
        q = self.query_var.get().strip().lower()
        self.listbox.delete(0, tk.END)
        shown = 0
        for display, _abs in self._all_results:
            if q and q not in display.lower():
                continue
            self.listbox.insert(tk.END, display)
            shown += 1
            if shown >= 300:
                self.listbox.insert(tk.END, "  ...more results, refine your search")
                break

    def _pick(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        display = self.listbox.get(sel[0]).strip()
        if display.startswith("...") or "more results" in display:
            return
        self.on_pick(display)
        self.destroy()


class AssetManagerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Sober Asset Overlay Manager")
        self.geometry("600x900")
        self.minsize(560, 780)
        self.configure(bg=BG)

        os.makedirs(APP_DATA_DIR, exist_ok=True)
        os.makedirs(BACKUPS_DIR, exist_ok=True)
        os.makedirs(PRESETS_DIR, exist_ok=True)

        self.selected_file = None
        self.preview_img = None
        self.history = self._load_history()

        self._setup_style()
        self._build_ui()
        self._on_category_change()
        self._refresh_history_list()
        self._refresh_presets_list()
        self.after(150, self._check_sober_installed)

    def _setup_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "TCombobox", fieldbackground=BG_PANEL_ALT, background=BG_PANEL_ALT,
            foreground=FG, arrowcolor=FG, bordercolor=BORDER,
            lightcolor=BG_PANEL_ALT, darkcolor=BG_PANEL_ALT, padding=6,
        )
        style.map("TCombobox", fieldbackground=[("readonly", BG_PANEL_ALT)],
                  foreground=[("readonly", FG)])
        self.option_add("*TCombobox*Listbox.background", BG_PANEL_ALT)
        self.option_add("*TCombobox*Listbox.foreground", FG)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.option_add("*TCombobox*Listbox.font", (FONT_FAMILY, 10))

    def _card(self, parent, title=None, subtitle=None):
        outer = tk.Frame(parent, bg=BG_PANEL, highlightbackground=BORDER,
                          highlightthickness=1, bd=0)
        outer.pack(fill="x", padx=18, pady=(0, 14))
        if title:
            head = tk.Frame(outer, bg=BG_PANEL)
            head.pack(fill="x", padx=16, pady=(14, 2))
            tk.Label(head, text=title, bg=BG_PANEL, fg=FG,
                      font=(FONT_FAMILY, 11, "bold"), anchor="w").pack(side="left")
        if subtitle:
            tk.Label(outer, text=subtitle, bg=BG_PANEL, fg=FG_MUTED,
                      font=(FONT_FAMILY, 8), anchor="w", justify="left",
                      wraplength=500).pack(fill="x", padx=16, pady=(0, 6))
        body = tk.Frame(outer, bg=BG_PANEL)
        body.pack(fill="x", padx=16, pady=(4, 16))
        return body

    def _build_ui(self):
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=18, pady=(20, 6))
        tk.Label(header, text="🛠  Sober Asset Overlay Manager", bg=BG, fg=FG,
                  font=(FONT_FAMILY, 16, "bold")).pack(anchor="w")
        tk.Label(header, text="Replace cursors, fonts, or other assets in Sober",
                  bg=BG, fg=FG_MUTED, font=(FONT_FAMILY, 9)).pack(anchor="w", pady=(2, 0))

        # ---- Panic button row ----
        panic_row = tk.Frame(self, bg=BG)
        panic_row.pack(fill="x", padx=18, pady=(0, 10))

        self.panic_btn = RoundedButton(
            panic_row, text="⛔ Disable ALL mods (panic button)",
            command=self.panic_disable, bg=DANGER, hover=DANGER_HOVER,
        )
        self.panic_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.restore_btn = RoundedButton(
            panic_row, text="↩ Re-enable my mods",
            command=self.panic_restore, bg=WARN, hover=WARN_HOVER,
        )
        self.restore_btn.pack(side="left", fill="x", expand=True, padx=(6, 0))

        self.panic_status = tk.Label(
            self, text="", bg=BG, fg=FG_MUTED, font=(FONT_FAMILY, 8),
            wraplength=560, justify="left",
        )
        self.panic_status.pack(fill="x", padx=20, pady=(0, 8), anchor="w")

        # ---- Presets Card ----
        body_presets = self._card(self, "Presets Management", subtitle="Save, load, or delete configurations of your active asset overlays.")
        preset_row = tk.Frame(body_presets, bg=BG_PANEL)
        preset_row.pack(fill="x", pady=(0, 2))

        self.preset_var = tk.StringVar()
        self.preset_combo = ttk.Combobox(
            preset_row, textvariable=self.preset_var, state="readonly",
            font=(FONT_FAMILY, 10),
        )
        self.preset_combo.pack(side="left", fill="x", expand=True, padx=(0, 8))

        RoundedButton(preset_row, text="Save Current...", command=self.save_preset,
                       bg=ACCENT, hover=ACCENT_HOVER).pack(side="left", padx=(0, 6))
        RoundedButton(preset_row, text="Load", command=self.load_preset,
                       bg=BG_PANEL_ALT, hover=BORDER, fg=FG).pack(side="left", padx=(0, 6))
        RoundedButton(preset_row, text="Delete", command=self.delete_preset,
                       bg=DANGER, hover=DANGER_HOVER).pack(side="left")

        # ---- Step 1: category ----
        body1 = self._card(self, "1 · What are you replacing?")
        self.category_var = tk.StringVar(value=list(CATEGORIES.keys())[0])
        self.category_combo = ttk.Combobox(
            body1, textvariable=self.category_var, values=list(CATEGORIES.keys()),
            state="readonly", font=(FONT_FAMILY, 10),
        )
        self.category_combo.pack(fill="x")
        self.category_combo.bind("<<ComboboxSelected>>", self._on_category_change)

        self.known_combo = ttk.Combobox(body1, state="readonly", font=(FONT_FAMILY, 10))
        self.known_var = tk.StringVar()
        self.known_combo.config(textvariable=self.known_var)
        self.known_combo.bind("<<ComboboxSelected>>", self._on_known_change)

        # Font-specific options frame
        self.font_options_frame = tk.Frame(body1, bg=BG_PANEL)
        self.patch_families_var = tk.BooleanVar(value=True)
        self.patch_checkbox = tk.Checkbutton(
            self.font_options_frame, text="Patch all font family JSON maps automatically (Recommended)",
            variable=self.patch_families_var, bg=BG_PANEL, fg=FG, selectcolor=BG_PANEL_ALT,
            activebackground=BG_PANEL, activeforeground=FG, font=(FONT_FAMILY, 9),
        )
        self.patch_checkbox.pack(anchor="w", pady=(6, 0))

        self.custom_path_row = tk.Frame(body1, bg=BG_PANEL)
        self.custom_path_var = tk.StringVar()
        self.custom_path_entry = tk.Entry(
            self.custom_path_row, textvariable=self.custom_path_var,
            bg=BG_PANEL_ALT, fg=FG,
            insertbackground=FG, relief="flat", font=(FONT_FAMILY, 10),
        )
        self.custom_path_entry.pack(side="left", fill="x", expand=True, ipady=3)
        find_btn = RoundedButton(
            self.custom_path_row, text="🔍 Find asset...",
            command=self._open_asset_finder,
            bg=BG_PANEL_ALT, hover=BORDER, fg=FG,
        )
        find_btn.config(highlightbackground=BORDER, highlightthickness=1)
        find_btn.pack(side="right", padx=(8, 0))

        self.custom_path_hint = tk.Label(
            body1,
            text="Path relative to asset_overlay/, e.g. content/fonts/BuilderSans-Regular.ttf",
            bg=BG_PANEL, fg=FG_MUTED, font=(FONT_FAMILY, 8),
        )

        # ---- Step 2: file ----
        body2 = self._card(self, "2 · Pick your replacement file")

        drop_row = tk.Frame(body2, bg=BG_PANEL_ALT, highlightbackground=BORDER,
                             highlightthickness=1)
        drop_row.pack(fill="x", pady=(4, 10))

        self.preview_canvas = tk.Label(
            drop_row, text="📄", bg=BG_PANEL_ALT, fg=FG_MUTED,
            font=(FONT_FAMILY, 22), width=4, height=2,
        )
        self.preview_canvas.pack(side="left", padx=10, pady=10)

        info_col = tk.Frame(drop_row, bg=BG_PANEL_ALT)
        info_col.pack(side="left", fill="both", expand=True, pady=10)
        self.file_label = tk.Label(info_col, text="No file selected", bg=BG_PANEL_ALT,
                                     fg=FG, font=(FONT_FAMILY, 10), anchor="w")
        self.file_label.pack(fill="x", anchor="w")
        self.file_path_label = tk.Label(info_col, text="", bg=BG_PANEL_ALT, fg=FG_MUTED,
                                          font=(FONT_FAMILY, 8), anchor="w",
                                          wraplength=320, justify="left")
        self.file_path_label.pack(fill="x", anchor="w")

        browse_btn = RoundedButton(drop_row, text="Browse...", command=self.browse_file,
                                     bg=BG_PANEL_ALT, hover=BORDER, fg=FG)
        browse_btn.config(highlightbackground=BORDER, highlightthickness=1)
        browse_btn.pack(side="right", padx=10)

        # ---- Step 3: install ----
        body3 = self._card(self, "3 · Install",
                             subtitle="The file currently in that slot (if any) is "
                                      "backed up automatically, so you can undo this.")
        install_btn = RoundedButton(body3, text="⬇  Install", command=self.install_asset,
                                      bg=ACCENT, hover=ACCENT_HOVER)
        install_btn.pack(fill="x")
        self.status_label = tk.Label(body3, text="", bg=BG_PANEL, fg=FG_MUTED,
                                       font=(FONT_FAMILY, 8), anchor="w",
                                       justify="left", wraplength=500)
        self.status_label.pack(fill="x", pady=(8, 0))

        # ---- History / undo ----
        body4 = self._card(self, "Recent changes")
        list_row = tk.Frame(body4, bg=BG_PANEL)
        list_row.pack(fill="both", expand=True)

        list_frame = tk.Frame(list_row, bg=BG_PANEL_ALT, highlightbackground=BORDER,
                                highlightthickness=1)
        list_frame.pack(side="left", fill="both", expand=True)
        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side="right", fill="y")
        self.history_list = tk.Listbox(
            list_frame, height=5, bg=BG_PANEL_ALT, fg=FG,
            selectbackground=ACCENT, selectforeground="#ffffff",
            relief="flat", font=(FONT_FAMILY, 9), highlightthickness=0, bd=0,
            yscrollcommand=scrollbar.set,
        )
        self.history_list.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        scrollbar.config(command=self.history_list.yview)

        btn_col = tk.Frame(list_row, bg=BG_PANEL)
        btn_col.pack(side="right", fill="y", padx=(10, 0))
        undo_btn = RoundedButton(btn_col, text="Undo Selected", command=self.undo_selected,
                                   bg=BG_PANEL_ALT, hover=BORDER, fg=FG)
        undo_btn.pack(fill="x", pady=(0, 6))
        undo_last_btn = RoundedButton(btn_col, text="Undo Last", command=self.undo_last,
                                        bg=WARN, hover=WARN_HOVER)
        undo_last_btn.pack(fill="x")

        footer = tk.Label(
            self, text=f"Overlay folder: {ASSET_OVERLAY_DIR}",
            bg=BG, fg=FG_MUTED, font=(FONT_FAMILY, 7), wraplength=560, justify="left",
        )
        footer.pack(fill="x", padx=20, pady=(0, 14), anchor="w")

    def _get_available_presets(self):
        if not os.path.isdir(PRESETS_DIR):
            return []
        return sorted([d for d in os.listdir(PRESETS_DIR) if os.path.isdir(os.path.join(PRESETS_DIR, d))])

    def _refresh_presets_list(self):
        presets = self._get_available_presets()
        self.preset_combo["values"] = presets
        if presets:
            if self.preset_var.get() not in presets:
                self.preset_var.set(presets[0])
        else:
            self.preset_var.set("")

    def save_preset(self):
        if not os.path.isdir(ASSET_OVERLAY_DIR) or not os.listdir(ASSET_OVERLAY_DIR):
            messagebox.showwarning("Empty Overlay", "There are no active asset overrides to save as a preset.")
            return
        
        name = simpledialog.askstring("Save Preset", "Enter a name for this preset:", parent=self)
        if not name:
            return
        
        name = "".join(c for c in name if c.isalnum() or c in (' ', '_', '-')).strip()
        if not name:
            messagebox.showerror("Invalid Name", "Please enter a valid preset name.")
            return

        target_dir = os.path.join(PRESETS_DIR, name)
        if os.path.exists(target_dir):
            if not messagebox.askyesno("Overwrite Preset", f"Preset '{name}' already exists. Overwrite it?"):
                return
            shutil.rmtree(target_dir)

        try:
            shutil.copytree(ASSET_OVERLAY_DIR, target_dir)
            self._refresh_presets_list()
            self.preset_var.set(name)
            self._set_status(f"✓ Preset '{name}' saved successfully.", SUCCESS)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save preset:\n{e}")

    def load_preset(self):
        name = self.preset_var.get().strip()
        if not name:
            messagebox.showwarning("No Preset Selected", "Please select a preset to load.")
            return

        preset_path = os.path.join(PRESETS_DIR, name)
        if not os.path.isdir(preset_path):
            messagebox.showerror("Error", f"Preset '{name}' does not exist.")
            self._refresh_presets_list()
            return

        if not messagebox.askyesno("Load Preset", f"Load preset '{name}'? This will merge/apply the preset files into your active asset overlay."):
            return

        try:
            os.makedirs(ASSET_OVERLAY_DIR, exist_ok=True)
            for root, _dirs, files in os.walk(preset_path):
                rel_root = os.path.relpath(root, preset_path)
                dest_root = ASSET_OVERLAY_DIR if rel_root == "." else os.path.join(ASSET_OVERLAY_DIR, rel_root)
                os.makedirs(dest_root, exist_ok=True)
                for file in files:
                    src_file = os.path.join(root, file)
                    dest_file = os.path.join(dest_root, file)
                    shutil.copyfile(src_file, dest_file)
            
            self._set_status(f"✓ Preset '{name}' loaded. Restart Sober to apply changes.", SUCCESS)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load preset:\n{e}")

    def delete_preset(self):
        name = self.preset_var.get().strip()
        if not name:
            messagebox.showwarning("No Preset Selected", "Please select a preset to delete.")
            return

        preset_path = os.path.join(PRESETS_DIR, name)
        if not os.path.isdir(preset_path):
            messagebox.showerror("Error", f"Preset '{name}' does not exist.")
            self._refresh_presets_list()
            return

        if not messagebox.askyesno("Delete Preset", f"Are you sure you want to delete the preset '{name}'?"):
            return

        try:
            shutil.rmtree(preset_path)
            self._refresh_presets_list()
            self._set_status(f"🗑 Preset '{name}' deleted.", WARN)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to delete preset:\n{e}")

    def _on_category_change(self, event=None):
        cat_name = self.category_var.get()
        cat = CATEGORIES[cat_name]
        self.known_combo.pack_forget()
        self.font_options_frame.pack_forget()
        self.custom_path_row.pack_forget()
        self.custom_path_hint.pack_forget()

        if cat["known"]:
            self.known_combo["values"] = list(cat["known"].keys())
            self.known_var.set(list(cat["known"].keys())[0])
            self.known_combo.pack(fill="x", pady=(8, 0))
            self._on_known_change()
        else:
            self.custom_path_hint.config(
                text="Path relative to asset_overlay/, e.g. content/fonts/BuilderSans-Regular.ttf"
            )
            self.custom_path_row.pack(fill="x", pady=(8, 2))
            self.custom_path_hint.pack(fill="x")

        if cat_name == "Font":
            self.font_options_frame.pack(fill="x", pady=(6, 0))

    def _on_known_change(self, event=None):
        cat = CATEGORIES[self.category_var.get()]
        label = self.known_var.get()
        if label == "Custom filename...":
            self.custom_path_hint.config(text=f"Filename only, placed in {cat['base']}/")
            self.custom_path_row.pack(fill="x", pady=(8, 2))
            self.custom_path_hint.pack(fill="x")
        else:
            self.custom_path_row.pack_forget()
            self.custom_path_hint.pack_forget()

    def _open_asset_finder(self):
        cat = CATEGORIES[self.category_var.get()]
        ext_filter = None
        if cat is CATEGORIES["Font"]:
            ext_filter = FONT_EXTS
        elif cat is CATEGORIES["Cursor"]:
            ext_filter = IMAGE_EXTS

        def handle_pick(display_path):
            if not cat["known"] or self.known_var.get() == "Custom filename...":
                self.custom_path_var.set(display_path)
            else:
                self.custom_path_var.set(os.path.basename(display_path))

        AssetFinderDialog(self, handle_pick, ext_filter=ext_filter)

    def _target_relative_path(self):
        cat_name = self.category_var.get()
        cat = CATEGORIES[cat_name]

        if not cat["known"]:
            rel = self.custom_path_var.get().strip().lstrip("/")
            return rel or None

        label = self.known_var.get()
        if label == "Custom filename...":
            fname = self.custom_path_var.get().strip()
        else:
            fname = cat["known"].get(label, "")
        if not fname:
            return None
        if cat["ext"] and not fname.lower().endswith(cat["ext"]):
            fname += cat["ext"]
        return f"{cat['base']}/{fname}"

    def browse_file(self):
        cat_name = self.category_var.get()
        filetypes = [("All Files", "*.*")]
        if cat_name == "Font":
            filetypes = [("Font Files", "*.ttf *.otf *.ttc"), ("All Files", "*.*")]
        elif cat_name == "Cursor":
            filetypes = [("Image Files", "*.png *.jpg *.jpeg *.bmp"), ("All Files", "*.*")]

        path = filedialog.askopenfilename(
            title="Choose a replacement file",
            filetypes=filetypes
        )
        if not path:
            return
        self.selected_file = path
        self.file_label.config(text=os.path.basename(path))
        self.file_path_label.config(text=path)
        self._show_preview(path)

    def _show_preview(self, path):
        if HAVE_PIL and path.lower().endswith((".png", ".jpg", ".jpeg", ".bmp", ".gif")):
            try:
                img = Image.open(path).convert("RGBA")
                img.thumbnail((48, 48))
                self.preview_img = ImageTk.PhotoImage(img)
                self.preview_canvas.config(image=self.preview_img, text="")
                return
            except Exception:
                pass
        icon = "🔤" if path.lower().endswith((".ttf", ".otf", ".ttc")) else "📄"
        self.preview_canvas.config(text=icon, image="")

    def install_asset(self):
        if not self.selected_file:
            self._set_status("Please choose a replacement file first.", DANGER)
            return

        cat_name = self.category_var.get()

        # Handle Full Font Family Patching if Font category & checkbox enabled
        if cat_name == "Font" and self.patch_families_var.get():
            apk_path = find_base_apk()
            if not apk_path or not os.path.isfile(apk_path):
                messagebox.showerror(
                    "Roblox APK Not Found",
                    "Could not locate base.apk. Make sure Sober/Roblox has been launched at least once."
                )
                return

            font_filename = os.path.basename(self.selected_file)
            ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_path = os.path.join(BACKUPS_DIR, f"{ts}__font_patch_backup")
            os.makedirs(backup_path, exist_ok=True)

            overlay_fonts_dir = os.path.join(ASSET_OVERLAY_DIR, "content", "fonts")
            overlay_families_dir = os.path.join(overlay_fonts_dir, "families")
            alt_fonts_dir = os.path.join(ASSET_OVERLAY_DIR, "fonts")

            try:
                if os.path.isdir(overlay_fonts_dir):
                    shutil.copytree(overlay_fonts_dir, os.path.join(backup_path, "content_fonts"), dirs_exist_ok=True)
                if os.path.isdir(alt_fonts_dir):
                    shutil.copytree(alt_fonts_dir, os.path.join(backup_path, "alt_fonts"), dirs_exist_ok=True)

                os.makedirs(overlay_families_dir, exist_ok=True)
                os.makedirs(alt_fonts_dir, exist_ok=True)

                # Extract fresh font-family JSON maps from base.apk
                with zipfile.ZipFile(apk_path, 'r') as zf:
                    for member in zf.namelist():
                        if member.startswith("assets/content/fonts/families/") and member.endswith(".json"):
                            fname = os.path.basename(member)
                            if fname:
                                with zf.open(member) as src, open(os.path.join(overlay_families_dir, fname), "wb") as dst:
                                    dst.write(src.read())

                # Copy font files into overlay locations
                shutil.copyfile(self.selected_file, os.path.join(overlay_fonts_dir, font_filename))
                shutil.copyfile(self.selected_file, os.path.join(alt_fonts_dir, font_filename))

                # Patch non-emoji font-family JSON files via regex
                asset_id_regex = re.compile(r'("assetId"\s*:\s*")[^"]*(")')
                changed = 0
                for json_path in Path(overlay_families_dir).glob("*.json"):
                    lower_name = json_path.name.lower()
                    if "emoji" in lower_name or "twemoji" in lower_name:
                        continue
                    text = json_path.read_text(encoding="utf-8")
                    new_text, count = asset_id_regex.subn(
                        lambda m: f'{m.group(1)}rbxasset://fonts/{font_filename}{m.group(2)}',
                        text
                    )
                    if count > 0:
                        json_path.write_text(new_text, encoding="utf-8")
                        changed += 1

                self._add_history_entry("content/fonts/families (Full Font Patch)", backup_path)
                self._set_status(f"✓ Installed font & patched {changed} family files. Fully restart Sober!", SUCCESS)
                return

            except Exception as e:
                messagebox.showerror("Error", f"Failed to patch font families:\n{e}")
                return

        # Standard asset overlay installation flow
        rel_path = self._target_relative_path()
        if not rel_path:
            self._set_status("Please choose or type a target filename/path.", DANGER)
            return

        dest = os.path.join(ASSET_OVERLAY_DIR, rel_path)
        backup_path = None

        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)

            if os.path.isfile(dest):
                ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                safe_name = rel_path.replace("/", "__")
                backup_path = os.path.join(BACKUPS_DIR, f"{ts}__{safe_name}")
                shutil.copyfile(dest, backup_path)

            shutil.copyfile(self.selected_file, dest)
        except PermissionError:
            messagebox.showerror(
                "Permission denied",
                f"Could not write to:\n{dest}\n\n"
                "Check that the asset_overlay folder is owned by your user.",
            )
            return
        except Exception as e:
            messagebox.showerror("Error", f"Failed to install asset:\n{e}")
            return

        self._add_history_entry(rel_path, backup_path)
        self._set_status(
            f"✓ Installed to {rel_path}. Fully quit and reopen Sober to see it.",
            SUCCESS,
        )

    def _add_history_entry(self, rel_path, backup_path):
        entry = {
            "time": datetime.datetime.now().isoformat(timespec="seconds"),
            "rel_path": rel_path,
            "backup_path": backup_path,
        }
        self.history.append(entry)
        self._save_history()
        self._refresh_history_list()

    def _load_history(self):
        if os.path.isfile(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    def _save_history(self):
        try:
            with open(HISTORY_FILE, "w") as f:
                json.dump(self.history, f, indent=2)
        except Exception:
            pass

    def _refresh_history_list(self):
        self.history_list.delete(0, tk.END)
        for entry in reversed(self.history):
            ts = entry["time"].replace("T", " ")
            had_backup = "restorable" if entry["backup_path"] else "new"
            self.history_list.insert(tk.END, f"  {ts}  ·  {entry['rel_path']}  ({had_backup})")

    def _undo_entry(self, entry):
        rel_path = entry["rel_path"]
        if rel_path == "content/fonts/families (Full Font Patch)":
            # Restore full font backup directory if available
            backup_dir = entry["backup_path"]
            try:
                overlay_fonts_dir = os.path.join(ASSET_OVERLAY_DIR, "content", "fonts")
                alt_fonts_dir = os.path.join(ASSET_OVERLAY_DIR, "fonts")
                
                # Clear current font overlay items
                if os.path.isdir(overlay_fonts_dir):
                    shutil.rmtree(overlay_fonts_dir)
                if os.path.isdir(alt_fonts_dir):
                    shutil.rmtree(alt_fonts_dir)

                # Restore backup contents if present
                if backup_dir and os.path.isdir(backup_dir):
                    cf_backup = os.path.join(backup_dir, "content_fonts")
                    af_backup = os.path.join(backup_dir, "alt_fonts")
                    if os.path.isdir(cf_backup):
                        shutil.copytree(cf_backup, overlay_fonts_dir)
                    if os.path.isdir(af_backup):
                        shutil.copytree(af_backup, alt_fonts_dir)
                
                self._set_status("↩ Reverted full font family patch. Restart Sober.", WARN)
                return True
            except Exception as e:
                messagebox.showerror("Error", f"Failed to undo font patch:\n{e}")
                return False

        dest = os.path.join(ASSET_OVERLAY_DIR, rel_path)
        try:
            if entry["backup_path"] and os.path.isfile(entry["backup_path"]):
                shutil.copyfile(entry["backup_path"], dest)
                msg = f"Restored previous version of {rel_path}."
            else:
                if os.path.isfile(dest):
                    os.remove(dest)
                msg = f"Removed override for {rel_path} (back to Roblox default)."
        except Exception as e:
            messagebox.showerror("Error", f"Failed to undo:\n{e}")
            return False
        self._set_status(f"↩ {msg} Restart Sober to see it.", WARN)
        return True

    def undo_selected(self):
        sel = self.history_list.curselection()
        if not sel:
            self._set_status("Select a change in the list to undo.", FG_MUTED)
            return
        idx = len(self.history) - 1 - sel[0]
        entry = self.history[idx]
        if messagebox.askyesno("Undo change", f"Undo change to {entry['rel_path']}?"):
            if self._undo_entry(entry):
                del self.history[idx]
                self._save_history()
                self._refresh_history_list()

    def undo_last(self):
        if not self.history:
            self._set_status("No changes to undo.", FG_MUTED)
            return
        entry = self.history[-1]
        if messagebox.askyesno("Undo last change", f"Undo change to {entry['rel_path']}?"):
            if self._undo_entry(entry):
                self.history.pop()
                self._save_history()
                self._refresh_history_list()

    def _set_status(self, text, color):
        self.status_label.config(text=text, fg=color)

    def _disabled_dirs(self):
        if not os.path.isdir(SOBER_DATA_DIR):
            return []
        return sorted(
            d for d in os.listdir(SOBER_DATA_DIR)
            if d.startswith("asset_overlay.disabled-")
        )

    def panic_disable(self):
        if not os.path.isdir(ASSET_OVERLAY_DIR):
            self.panic_status.config(
                text="No active overlay folder found — nothing to disable.",
                fg=FG_MUTED,
            )
            return
        if not messagebox.askyesno(
            "Disable all mods",
            "This instantly disables every asset override (cursors, fonts, "
            "anything else) by moving the whole overlay folder aside.\n\n"
            "Nothing is deleted — you can bring it all back with "
            "'Re-enable my mods'.\n\nFully restart Sober afterward. Continue?",
        ):
            return
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        disabled_path = os.path.join(SOBER_DATA_DIR, f"asset_overlay.disabled-{ts}")
        try:
            shutil.move(ASSET_OVERLAY_DIR, disabled_path)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to disable mods:\n{e}")
            return
        self.panic_status.config(
            text="⛔ All mods disabled. Fully quit and reopen Sober — it will "
                 "load with stock assets.",
            fg=DANGER,
        )

    def panic_restore(self):
        disabled = self._disabled_dirs()
        if not disabled:
            self.panic_status.config(
                text="No disabled mods found to restore.", fg=FG_MUTED
            )
            return
        latest = disabled[-1]
        latest_path = os.path.join(SOBER_DATA_DIR, latest)

        if os.path.isdir(ASSET_OVERLAY_DIR):
            messagebox.showwarning(
                "Overlay already active",
                "There's already an active overlay folder — remove or rename it "
                "manually first if you want to restore the disabled one instead.",
            )
            return

        try:
            shutil.move(latest_path, ASSET_OVERLAY_DIR)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to re-enable mods:\n{e}")
            return
        self.panic_status.config(
            text="↩ Mods re-enabled. Fully quit and reopen Sober to see them again.",
            fg=SUCCESS,
        )

    def _check_sober_installed(self):
        if os.path.isdir(SOBER_APP_DIR):
            return
        messagebox.showwarning(
            "Sober not found",
            "This tool couldn't find a Sober installation at:\n\n"
            f"{SOBER_APP_DIR}\n\n"
            "Sober only runs on Linux, installed via Flatpak. If you haven't "
            "launched Sober at least once yet, do that first so it can create "
            "its data folder, then reopen this tool.",
        )


if __name__ == "__main__":
    app = AssetManagerApp()
    app.mainloop()