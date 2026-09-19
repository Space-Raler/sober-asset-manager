#!/usr/bin/env python3
"""
Sober Asset Overlay Manager
----------------------------
A small GUI tool for installing custom cursors, sounds, or any other
replaceable asset into Sober (the Linux Roblox client by VinegarHQ),
using its "asset overlay" system.

Includes:
  - Cursor and sound replacement (with known common filenames)
  - A "Custom path" mode for any other overlay-able asset
  - Automatic backups + per-change undo
  - A panic button to instantly disable ALL mods if something breaks
    (e.g. the game fails to load / softlocks), and re-enable them later

Requires: python3, tkinter, and Pillow (optional, for image preview)
    Install tkinter (Debian/Ubuntu):  sudo apt install python3-tk
    Install tkinter (Fedora):         sudo dnf install python3-tkinter
    Install tkinter (Arch):           sudo pacman -S tk
    Install Pillow (optional):        pip install --user Pillow

Run with:  python3 sober_asset_manager.py

Notes on sound filenames: Roblox doesn't publish an official list of its
internal asset filenames, and they can change between client updates.
The ones listed here are commonly referenced by the community (e.g. in
VinegarHQ's own docs) but aren't guaranteed to be current. For anything
not listed, you'll need to find the exact filename/path yourself by
opening the Roblox APK in an archive manager and matching it under
packages/com.roblox.client/base.apk/assets/content/... — the overlay
must mirror that exact structure.
"""

import os
import json
import shutil
import zipfile
import datetime
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

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
SOUND_EXTS = (".mp3", ".ogg", ".wav")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".tga")

APP_DATA_DIR = os.path.expanduser("~/.local/share/sober-asset-manager")
BACKUPS_DIR = os.path.join(APP_DATA_DIR, "backups")
HISTORY_FILE = os.path.join(APP_DATA_DIR, "history.json")

# ---------------------------------------------------------------------
# Known asset categories
# label -> relative path from asset_overlay/, or "" if user must type
# the file name themselves (custom mode)
# ---------------------------------------------------------------------

CURSOR_BASE = "content/textures/Cursors/KeyboardMouse"
SOUND_BASE = "content/sounds"

CURSOR_KNOWN = {
    "Arrow (default pointer)": "ArrowCursor.png",
    "Arrow, far (zoomed out)": "ArrowFarCursor.png",
    "I-Beam (text fields)": "IBeamCursor.png",
    "Custom filename...": "",
}

# Only one filename here is confirmed (from VinegarHQ's own docs). Everything
# else varies by client version/platform — use "Find asset..." to look up
# real current filenames from your own installed Roblox files instead of
# guessing.
SOUND_KNOWN = {
    "Death/damage sound (ouch.ogg)": "ouch.ogg",
    "Custom filename...": "",
}

CATEGORIES = {
    "Cursor": {"base": CURSOR_BASE, "known": CURSOR_KNOWN, "ext": ".png"},
    "Sound": {"base": SOUND_BASE, "known": SOUND_KNOWN, "ext": ""},
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
    """Yield (display_path, source) for every asset file packed inside
    Sober's downloaded Roblox .apk files (APKs are ZIP archives), with
    the path shown relative to 'assets/' — matching the structure
    asset_overlay expects. 'source' is (apk_path, entry_name) in case a
    future version wants to extract the original file."""
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
    """Lets the user search Sober's actual downloaded Roblox files to find
    a real, current asset filename/path instead of guessing one."""

    def __init__(self, master, on_pick, ext_filter=None):
        super().__init__(master)
        self.title("Find asset in your Roblox files")
        self.geometry("560x460")
        self.configure(bg=BG)
        self.on_pick = on_pick
        self.ext_filter = ext_filter  # tuple of extensions or None for all
        self._all_results = None

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

        self.status = tk.Label(self, text="", bg=BG, fg=FG_MUTED,
                                 font=(FONT_FAMILY, 8))
        self.status.pack(fill="x", padx=14)

        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(fill="x", padx=14, pady=12)
        RoundedButton(btn_row, text="Use Selected", command=self._pick,
                       bg=ACCENT, hover=ACCENT_HOVER).pack(side="right")
        RoundedButton(btn_row, text="Cancel", command=self.destroy,
                       bg=BG_PANEL_ALT, hover=BORDER, fg=FG).pack(side="right", padx=(0, 8))

        self._load_all()
        self._refresh()

    def _load_all(self):
        if not os.path.isdir(PACKAGES_DIR):
            self.status.config(
                text="No downloaded Roblox files found yet — launch a game "
                     "in Sober at least once first.", fg=DANGER,
            )
            self._all_results = []
            return
        results = list(iter_installed_assets())
        if self.ext_filter:
            results = [r for r in results if r[0].lower().endswith(self.ext_filter)]
        self._all_results = results
        self.status.config(text=f"{len(results)} files found.")

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
        self.geometry("600x760")
        self.minsize(560, 700)
        self.configure(bg=BG)

        os.makedirs(APP_DATA_DIR, exist_ok=True)
        os.makedirs(BACKUPS_DIR, exist_ok=True)

        self.selected_file = None
        self.preview_img = None
        self.history = self._load_history()

        self._setup_style()
        self._build_ui()
        self._on_category_change()
        self._refresh_history_list()
        self.after(150, self._check_sober_installed)

    # -----------------------------------------------------------------
    # Styling
    # -----------------------------------------------------------------

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

    # -----------------------------------------------------------------
    # UI
    # -----------------------------------------------------------------

    def _build_ui(self):
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=18, pady=(20, 6))
        tk.Label(header, text="🛠  Sober Asset Overlay Manager", bg=BG, fg=FG,
                  font=(FONT_FAMILY, 16, "bold")).pack(anchor="w")
        tk.Label(header, text="Replace cursors, sounds, or other assets in Sober",
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

        # ---- Step 1: category ----
        body1 = self._card(self, "1 · What are you replacing?")
        self.category_var = tk.StringVar(value=list(CATEGORIES.keys())[0])
        self.category_combo = ttk.Combobox(
            body1, textvariable=self.category_var, values=list(CATEGORIES.keys()),
            state="readonly", font=(FONT_FAMILY, 10),
        )
        self.category_combo.pack(fill="x")
        self.category_combo.bind("<<ComboboxSelected>>", self._on_category_change)

        self.known_var = tk.StringVar()
        self.known_combo = ttk.Combobox(
            body1, textvariable=self.known_var, state="readonly",
            font=(FONT_FAMILY, 10),
        )
        self.known_combo.bind("<<ComboboxSelected>>", self._on_known_change)

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
            text="Path relative to asset_overlay/, e.g. content/sounds/ouch.ogg",
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
            list_frame, height=7, bg=BG_PANEL_ALT, fg=FG,
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

    # -----------------------------------------------------------------
    # Category / field behaviour
    # -----------------------------------------------------------------

    def _on_category_change(self, event=None):
        cat = CATEGORIES[self.category_var.get()]
        self.known_combo.pack_forget()
        self.custom_path_row.pack_forget()
        self.custom_path_hint.pack_forget()

        if cat["known"]:
            self.known_combo["values"] = list(cat["known"].keys())
            self.known_var.set(list(cat["known"].keys())[0])
            self.known_combo.pack(fill="x", pady=(8, 0))
            self._on_known_change()
        else:
            self.custom_path_hint.config(
                text="Path relative to asset_overlay/, e.g. content/sounds/ouch.ogg"
            )
            self.custom_path_row.pack(fill="x", pady=(8, 2))
            self.custom_path_hint.pack(fill="x")

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
        if cat is CATEGORIES["Sound"]:
            ext_filter = SOUND_EXTS
        elif cat is CATEGORIES["Cursor"]:
            ext_filter = IMAGE_EXTS

        def handle_pick(display_path):
            if not cat["known"] or self.known_var.get() == "Custom filename...":
                # full "content/..." path goes straight into the field
                self.custom_path_var.set(display_path)
            else:
                # known category: just take the filename part
                self.custom_path_var.set(os.path.basename(display_path))

        AssetFinderDialog(self, handle_pick, ext_filter=ext_filter)

    def _target_relative_path(self):
        """Returns the path relative to ASSET_OVERLAY_DIR, or None if invalid."""
        cat_name = self.category_var.get()
        cat = CATEGORIES[cat_name]

        if not cat["known"]:  # Custom path (advanced)
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

    # -----------------------------------------------------------------
    # File picking / preview
    # -----------------------------------------------------------------

    def browse_file(self):
        path = filedialog.askopenfilename(title="Choose a replacement file")
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
        icon = "🔊" if path.lower().endswith((".ogg", ".wav", ".mp3")) else "📄"
        self.preview_canvas.config(text=icon, image="")

    # -----------------------------------------------------------------
    # Install / backup / undo
    # -----------------------------------------------------------------

    def install_asset(self):
        if not self.selected_file:
            self._set_status("Please choose a replacement file first.", DANGER)
            return

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
        dest = os.path.join(ASSET_OVERLAY_DIR, entry["rel_path"])
        try:
            if entry["backup_path"] and os.path.isfile(entry["backup_path"]):
                shutil.copyfile(entry["backup_path"], dest)
                msg = f"Restored previous version of {entry['rel_path']}."
            else:
                if os.path.isfile(dest):
                    os.remove(dest)
                msg = f"Removed override for {entry['rel_path']} (back to Roblox default)."
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
        # listbox is reversed, so map back to the real index
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

    # -----------------------------------------------------------------
    # Panic button: disable / re-enable ALL mods at once
    # -----------------------------------------------------------------

    def _disabled_dirs(self):
        """Any previously-disabled overlay folders sitting next to asset_overlay."""
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
            "This instantly disables every asset override (cursors, sounds, "
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

    # -----------------------------------------------------------------
    # Startup check
    # -----------------------------------------------------------------

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
