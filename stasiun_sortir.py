"""
Stasiun Sortir Ganci — aplikasi scan-to-sort untuk packer.

Workflow yang di-solve:
  1. Setelah laser cutting, 50-80 charm akrilik dari banyak order tumpah ke
     wadah campur.
  2. Packer ambil charm random → scan barcode SKU → aplikasi tunjuk slot
     resi mana yang butuh charm itu → packer drop ke slot tsb.
  3. Repeat sampai semua charm masuk slot resinya.

Aplikasi punya **dua mode**:

* **Setup Slot** — scan stiker resi → aplikasi assign nomor slot berurutan
  (1, 2, 3, …). Packer tulis nomor itu di kotak fisik & taruh di rak.
  Slot yang sudah pernah di-release bisa dipakai ulang.
* **Sortir Charm** — scan barcode SKU → aplikasi tunjuk slot tujuan
  (resi mana) berdasarkan urutan FCFS demand. Saat semua charm utk satu
  resi sudah ter-scan, slot-nya berubah hijau — packer bisa pickup dan
  klik tile slot untuk **kosongkan** sehingga slot bisa diisi resi baru.

State (slot map, demand counter, history) auto-save ke ``.scan_state.json``
per scan; kalau aplikasi crash/ditutup, tinggal buka ulang → dialog
"Resume sesi terakhir?" muncul.
"""

from __future__ import annotations

import os
import sys
import threading
from datetime import datetime
from tkinter import filedialog, messagebox

import customtkinter as ctk

from core import animations, voice
from core.config import load_config, save_config
from core.pesanan import load_pesanan_demand, total_charm_initial
from core.sku_utils import alias_atm_anm, normalize_sku, pad_sku_unit
from core.state import SortirState, build_initial_state
from core.updater import consume_update_flag
from core.version import get_version

# Folder berisi `slot_NN.mp3` di-bundle bareng app. Resolve dari lokasi file
# ini supaya tetap ketemu bila app di-launch dari direktori berbeda.
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
_AUDIO_DIR = os.path.join(_APP_DIR, "audio")

# ── Sound (Windows). Optional — gracefully no-op kalau tidak tersedia. ─────
try:
    import winsound  # type: ignore[import]
    _HAS_BEEP = True
except ImportError:
    _HAS_BEEP = False


# ── Tema warna stasiun ─────────────────────────────────────────────────────
COLOR_OK       = "#23c78e"   # Match / complete
COLOR_WARN     = "#f5a623"   # Overflow / warning
COLOR_ERR      = "#f26c6c"   # Unknown / error
COLOR_ACTIVE   = "#7c6af7"   # Slot aktif (sedang menerima charm)
COLOR_IDLE     = "#404060"   # Slot kosong / idle
COLOR_DIM      = "#9090b0"
COLOR_TEXT     = "#e2e2f0"
COLOR_PANEL    = "#2a2a3e"
COLOR_BG       = "#1e1e2e"
COLOR_ACCENT2  = "#56cfcf"

HISTORY_DISPLAY = 10  # baris di textbox riwayat


def _beep(kind: str) -> None:
    """Suara feedback singkat. ``kind`` ∈ {match, overflow, unknown, register}."""
    if not _HAS_BEEP:
        return
    try:
        if kind == "match":
            winsound.Beep(880, 80)
        elif kind == "overflow":
            winsound.Beep(440, 180)
        elif kind == "unknown":
            winsound.Beep(220, 90)
            winsound.Beep(220, 90)
        elif kind == "register":
            winsound.Beep(660, 60)
            winsound.Beep(990, 60)
        elif kind == "release":
            winsound.Beep(540, 100)
    except RuntimeError:
        pass


class StasiunSortirApp(ctk.CTk):

    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.app_version = get_version()
        self.title(f"📦 Stasiun Sortir Ganci  v{self.app_version}")
        self.geometry("1280x880")
        self.minsize(1100, 760)
        self.configure(fg_color=COLOR_BG)

        # State utama
        self.session: SortirState = SortirState()
        self.tile_widgets: dict = {}  # slot_number → frame widget di grid

        # Voice playback (slot number TTS) — gracefully no-op kalau folder
        # audio tidak ada (mis. user belum run generate_audio.py).
        voice.set_audio_dir(_AUDIO_DIR)

        self._build_ui()
        self.after(400, self._show_update_notification)
        # Tawarkan resume sesi sebelumnya (kalau ada)
        self.after(500, self._maybe_resume_session)

    # ==================================================================
    # UI builder
    # ==================================================================
    def _build_ui(self) -> None:
        # Strip header
        ctk.CTkFrame(self, height=4, fg_color=COLOR_ACTIVE, corner_radius=0).pack(fill="x")

        # Title bar
        bar = ctk.CTkFrame(self, fg_color=COLOR_BG)
        bar.pack(fill="x", padx=24, pady=(14, 6))
        ctk.CTkLabel(
            bar, text=f"📦  Stasiun Sortir Ganci  ·  v{self.app_version}",
            font=("Segoe UI", 18, "bold"), text_color=COLOR_ACCENT2,
        ).pack(side="left")

        ctk.CTkButton(
            bar, text="🔄  Reset", command=self._reset, width=110, height=34,
            fg_color="#3d3d56", hover_color="#505070",
        ).pack(side="right", padx=(8, 0))

        ctk.CTkButton(
            bar, text="📂  Load Pesanan", command=self._load_pesanan_dialog,
            width=160, height=34,
            fg_color="#23c78e", hover_color="#1da87a",
        ).pack(side="right")

        self.btn_mode = ctk.CTkButton(
            bar, text="📋  Mode: Setup Slot", command=self._toggle_mode,
            width=200, height=34,
            fg_color=COLOR_ACTIVE, hover_color="#5d4dd4",
        )
        self.btn_mode.pack(side="right", padx=(0, 16))

        # Status row
        status_card = ctk.CTkFrame(self, fg_color=COLOR_PANEL, corner_radius=8)
        status_card.pack(fill="x", padx=24, pady=4)
        status_inner = ctk.CTkFrame(status_card, fg_color=COLOR_PANEL)
        status_inner.pack(fill="x", padx=14, pady=10)

        self.lbl_setup    = ctk.CTkLabel(status_inner, text="Setup: -",
                                         font=("Segoe UI", 11), text_color=COLOR_TEXT)
        self.lbl_charm    = ctk.CTkLabel(status_inner, text="Charm: -",
                                         font=("Segoe UI", 11), text_color=COLOR_TEXT)
        self.lbl_complete = ctk.CTkLabel(status_inner, text="Resi Lengkap: -",
                                         font=("Segoe UI", 11), text_color=COLOR_TEXT)
        self.lbl_archive  = ctk.CTkLabel(status_inner, text="Sudah Diambil: -",
                                         font=("Segoe UI", 11), text_color=COLOR_TEXT)
        self.lbl_setup.pack(side="left", padx=(0, 24))
        self.lbl_charm.pack(side="left", padx=(0, 24))
        self.lbl_complete.pack(side="left", padx=(0, 24))
        self.lbl_archive.pack(side="left", padx=(0, 24))

        self.progress = ctk.CTkProgressBar(status_inner, height=12, progress_color=COLOR_ACTIVE)
        self.progress.pack(side="right", fill="x", expand=True, padx=(20, 0))
        self.progress.set(0)

        # Scan input
        scan_card = ctk.CTkFrame(self, fg_color=COLOR_PANEL, corner_radius=8)
        scan_card.pack(fill="x", padx=24, pady=8)
        scan_inner = ctk.CTkFrame(scan_card, fg_color=COLOR_PANEL)
        scan_inner.pack(fill="x", padx=14, pady=10)
        self.scan_label = ctk.CTkLabel(
            scan_inner, text="📋  Scan stiker resi:",
            font=("Segoe UI", 12, "bold"), text_color=COLOR_ACTIVE,
        )
        self.scan_label.pack(side="left", padx=(0, 12))

        self.scan_var = ctk.StringVar()
        self.scan_entry = ctk.CTkEntry(
            scan_inner, textvariable=self.scan_var,
            font=("Consolas", 16),
            height=40, border_width=2, border_color=COLOR_ACTIVE,
            placeholder_text="(klik di sini, lalu scan)",
        )
        self.scan_entry.pack(side="left", fill="x", expand=True)
        self.scan_entry.bind("<Return>", self._on_scan)
        self.scan_entry.bind("<Escape>", lambda e: self.scan_var.set(""))
        self.scan_entry.configure(state="disabled")

        # Main split horizontal: result panel kiri (sidebar) + slot grid kanan.
        # Sidebar sempit & stack vertikal supaya slot grid dapat ruang vertikal
        # yang luas — area utama yang dilihat packer.
        main_split = ctk.CTkFrame(self, fg_color=COLOR_BG)
        main_split.pack(fill="both", expand=True, padx=24, pady=(0, 8))

        # Result sidebar — kolom kiri, fixed width.
        result_card = ctk.CTkFrame(
            main_split, fg_color=COLOR_PANEL, corner_radius=10,
            width=300,
        )
        result_card.pack(side="left", fill="y", padx=(0, 12))
        result_card.pack_propagate(False)
        result_inner = ctk.CTkFrame(result_card, fg_color=COLOR_PANEL)
        result_inner.pack(fill="x", padx=14, pady=14)

        # Kotak nomor slot — di atas, full width sidebar.
        slot_box = ctk.CTkFrame(
            result_inner, fg_color=COLOR_IDLE, corner_radius=10,
            height=140,
        )
        slot_box.pack(fill="x")
        slot_box.pack_propagate(False)
        # CTkFont shared object — bisa di-configure(size=...) untuk bounce anim
        # tanpa recreate font setiap frame.
        self._big_font_base_size = 64
        self._big_font = ctk.CTkFont(
            family="Segoe UI", size=self._big_font_base_size, weight="bold",
        )
        self.result_big_label = ctk.CTkLabel(
            slot_box, text="—",
            font=self._big_font, text_color="white",
        )
        self.result_big_label.pack(expand=True)
        self._result_slot_box = slot_box  # untuk ubah warna background

        # Info di bawah slot box — stack vertikal (kind, resi, sku, sisa).
        info = ctk.CTkFrame(result_inner, fg_color=COLOR_PANEL)
        info.pack(fill="x", pady=(14, 0))

        self.result_kind_label = ctk.CTkLabel(
            info, text="BELUM LOAD PESANAN",
            font=("Segoe UI", 14, "bold"), text_color=COLOR_DIM,
            anchor="w", justify="left", wraplength=270,
        )
        self.result_kind_label.pack(fill="x", pady=(0, 6))

        self.result_resi_label = ctk.CTkLabel(
            info, text="", font=("Consolas", 13, "bold"),
            text_color=COLOR_TEXT, anchor="w", justify="left", wraplength=270,
        )
        self.result_resi_label.pack(fill="x", pady=(0, 4))

        self.result_sku_label = ctk.CTkLabel(
            info, text="", font=("Consolas", 11),
            text_color=COLOR_DIM, anchor="w", justify="left", wraplength=270,
        )
        self.result_sku_label.pack(fill="x", pady=(0, 4))

        self.result_sisa_label = ctk.CTkLabel(
            info, text="", font=("Segoe UI", 11, "italic"),
            text_color=COLOR_DIM, anchor="w", justify="left", wraplength=270,
        )
        self.result_sisa_label.pack(fill="x")

        # Slot grid — area utama di kanan main_split, mengisi sisa ruang.
        grid_card = ctk.CTkFrame(main_split, fg_color=COLOR_PANEL, corner_radius=10)
        grid_card.pack(side="left", fill="both", expand=True)

        grid_header = ctk.CTkFrame(grid_card, fg_color=COLOR_PANEL)
        grid_header.pack(fill="x", padx=14, pady=(10, 4))
        ctk.CTkLabel(
            grid_header, text="📋  Layout Slot",
            font=("Segoe UI", 14, "bold"), text_color=COLOR_ACCENT2,
            anchor="w",
        ).pack(side="left")
        ctk.CTkLabel(
            grid_header,
            text="Klik tile hijau (✓ LENGKAP) untuk kosongkan slot & ambil kotak",
            font=("Segoe UI", 10, "italic"), text_color=COLOR_DIM,
            anchor="e",
        ).pack(side="right")

        self.slot_grid_scroll = ctk.CTkScrollableFrame(
            grid_card, fg_color="#1a1a2e", corner_radius=6,
        )
        self.slot_grid_scroll.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # 5 kolom dgn weight sama supaya 1-5 sebaris, 6-10 baris berikutnya.
        for c in range(self.GRID_COLS):
            self.slot_grid_scroll.grid_columnconfigure(c, weight=1, uniform="slot_col")

        # History panel — compact di bawah
        hist_card = ctk.CTkFrame(self, fg_color=COLOR_PANEL, corner_radius=8)
        hist_card.pack(fill="x", padx=24, pady=(0, 14))
        ctk.CTkLabel(
            hist_card, text="📜  Riwayat Scan (10 terakhir)",
            font=("Segoe UI", 10, "bold"), text_color=COLOR_ACCENT2,
            anchor="w",
        ).pack(fill="x", padx=14, pady=(6, 2))

        self.history_box = ctk.CTkTextbox(
            hist_card, height=95, font=("Consolas", 10),
            fg_color="#12121f", text_color=COLOR_TEXT, wrap="none",
        )
        self.history_box.pack(fill="x", padx=14, pady=(0, 8))
        self.history_box.configure(state="disabled")

    # ==================================================================
    # Update notif (dari core.updater)
    # ==================================================================
    def _show_update_notification(self) -> None:
        flag = consume_update_flag()
        if not flag:
            return
        old = flag.get("old_version", "?")
        new = flag.get("new_version", "?")
        ts = flag.get("timestamp", "")
        messagebox.showinfo(
            "✅ Aplikasi Telah Diupdate",
            f"Aplikasi otomatis di-update ke versi terbaru.\n\n"
            f"Versi sebelumnya : {old}\n"
            f"Versi sekarang   : {new}\n"
            f"Waktu update     : {ts}",
        )

    # ==================================================================
    # Resume sesi terakhir (dialog saat startup)
    # ==================================================================
    def _maybe_resume_session(self) -> None:
        prev = SortirState.load()
        if prev is None or not prev.pesanan_file:
            return

        if not os.path.exists(prev.pesanan_file):
            # File pesanan sudah hilang → diam-diam buang state lama.
            SortirState.clear()
            return

        if not prev.matches_pesanan(prev.pesanan_file):
            # File berubah sejak save terakhir → tawarkan reset.
            if messagebox.askyesno(
                "🔄 File pesanan berubah",
                f"File pesanan ({os.path.basename(prev.pesanan_file)}) sudah dimodifikasi "
                f"sejak sesi terakhir. State lama tidak cocok lagi.\n\n"
                f"Hapus state lama & mulai sesi baru?",
            ):
                SortirState.clear()
            return

        n_setup = len(prev.slot_map)
        n_total_resi = len(prev.resi_summary)
        n_archived = len(prev.archived_resi)

        if not messagebox.askyesno(
            "📂 Resume sesi terakhir?",
            f"Ditemukan sesi sortir tersimpan untuk pesanan:\n"
            f"  {os.path.basename(prev.pesanan_file)}\n\n"
            f"Status terakhir:\n"
            f"  Setup    : {n_setup}/{n_total_resi} resi ter-register\n"
            f"  Sortir   : {prev.scanned_count}/{prev.total_charm} charm\n"
            f"  Diambil  : {n_archived} kotak\n"
            f"  Mode     : {prev.mode.upper()}\n\n"
            f"Lanjutkan dari sesi ini?",
        ):
            SortirState.clear()
            return

        self.session = prev
        self._refresh_all_ui()
        self.scan_entry.configure(state="normal")
        self.scan_entry.focus_set()
        self._set_idle_message(
            f"✅ Sesi sebelumnya di-resume — {prev.mode.upper()} mode",
            COLOR_OK,
        )

    # ==================================================================
    # Mode toggle
    # ==================================================================
    def _toggle_mode(self) -> None:
        if not self.session.demand:
            messagebox.showinfo("Belum siap", "Load pesanan dulu sebelum ganti mode.")
            return
        self.session.mode = "sort" if self.session.mode == "setup" else "setup"
        self._apply_mode_ui()
        self.session.save()
        self.scan_entry.focus_set()

    def _apply_mode_ui(self) -> None:
        if self.session.mode == "setup":
            self.btn_mode.configure(text="📋  Mode: Setup Slot", fg_color=COLOR_ACTIVE)
            self.scan_label.configure(text="📋  Scan stiker resi:", text_color=COLOR_ACTIVE)
            self.scan_entry.configure(border_color=COLOR_ACTIVE)
            self._set_idle_message("📋 Mode SETUP — scan stiker resi untuk assign slot", COLOR_ACTIVE)
        else:
            self.btn_mode.configure(text="📦  Mode: Sortir Charm", fg_color="#23c78e")
            self.scan_label.configure(text="📦  Scan barcode charm:", text_color="#23c78e")
            self.scan_entry.configure(border_color="#23c78e")
            self._set_idle_message("📦 Mode SORTIR — scan barcode charm", COLOR_OK)

    # ==================================================================
    # Load pesanan
    # ==================================================================
    def _load_pesanan_dialog(self) -> None:
        cfg = load_config()
        initial_pesanan = cfg.get("file_pesanan", "") or os.getcwd()
        path_pesanan = filedialog.askopenfilename(
            title="Pilih file pesanan harian (.xlsx)",
            initialdir=os.path.dirname(initial_pesanan) if os.path.exists(os.path.dirname(initial_pesanan)) else os.getcwd(),
            initialfile=os.path.basename(initial_pesanan) if initial_pesanan else "",
            filetypes=[("Excel files", "*.xlsx *.xls")],
        )
        if not path_pesanan:
            return

        path_database = cfg.get("file_database", "")
        if not (path_database and os.path.exists(path_database)):
            path_database = filedialog.askopenfilename(
                title="Pilih file database SKU (untuk resolve bundle)",
                initialdir=os.getcwd(),
                filetypes=[("Excel files", "*.xlsx *.xls")],
            )
            if not path_database:
                if not messagebox.askyesno(
                    "Database SKU tidak dipilih",
                    "Bundle yang butuh DB lookup akan di-skip. Lanjut?",
                ):
                    return
                path_database = ""

        cfg["file_pesanan"] = path_pesanan
        if path_database:
            cfg["file_database"] = path_database
        save_config(cfg)

        self._set_idle_message("⏳  Membaca pesanan...", COLOR_DIM)
        threading.Thread(
            target=self._build_demand_thread,
            args=(path_pesanan, path_database),
            daemon=True,
        ).start()

    def _build_demand_thread(self, path_pesanan: str, path_database: str) -> None:
        try:
            demand, summary, skipped = load_pesanan_demand(path_pesanan, path_database)
        except Exception as e:
            self.after(0, lambda: messagebox.showerror(
                "Gagal membaca pesanan",
                f"Tidak bisa membaca file pesanan:\n\n{e}",
            ))
            self.after(0, self._reset_ui_idle)
            return

        def _apply():
            new_state = build_initial_state(path_pesanan, demand, summary)
            self.session = new_state
            self.session.save()
            self._refresh_all_ui()
            self.scan_entry.configure(state="normal")
            self.scan_entry.focus_set()
            self._apply_mode_ui()

            if skipped:
                preview = "\n".join(skipped[:6])
                if len(skipped) > 6:
                    preview += f"\n…(+{len(skipped) - 6} baris lagi)"
                messagebox.showwarning(
                    f"⚠️  {len(skipped)} baris di-skip",
                    f"Beberapa baris pesanan tidak bisa diproses:\n\n{preview}",
                )

        self.after(0, _apply)

    def _reset_ui_idle(self) -> None:
        self._set_idle_message("BELUM LOAD PESANAN", COLOR_DIM)
        self.scan_entry.configure(state="disabled")
        for w in self.tile_widgets.values():
            try:
                w.destroy()
            except Exception:
                pass
        self.tile_widgets.clear()
        self._refresh_status()

    # ==================================================================
    # Reset
    # ==================================================================
    def _reset(self) -> None:
        if not self.session.demand:
            return
        if not messagebox.askyesno(
            "Reset stasiun",
            "Reset progress sortir? Semua scan dan slot map akan hilang.\n"
            "(Data pesanan akan dimuat ulang dari file.)",
        ):
            return
        cfg = load_config()
        path_pesanan = cfg.get("file_pesanan", "")
        path_database = cfg.get("file_database", "")
        SortirState.clear()
        if not (path_pesanan and os.path.exists(path_pesanan)):
            self.session = SortirState()
            self._reset_ui_idle()
            return
        self._set_idle_message("⏳  Memuat ulang...", COLOR_DIM)
        threading.Thread(
            target=self._build_demand_thread,
            args=(path_pesanan, path_database),
            daemon=True,
        ).start()

    # ==================================================================
    # Scan handler — dispatch ke setup / sort
    # ==================================================================
    def _on_scan(self, _event=None) -> None:
        raw = self.scan_var.get().strip()
        self.scan_var.set("")
        if not raw or not self.session.demand:
            return

        if self.session.mode == "setup":
            self._handle_setup_scan(raw)
        else:
            self._handle_sort_scan(raw)
        self.session.save()

    # ──────── SETUP MODE ────────
    def _handle_setup_scan(self, raw: str) -> None:
        """
        Scan di mode setup = scan stiker resi → assign slot.
        Edge case:
          - Resi sudah ter-register   → tampil slot existing.
          - Resi tidak di pesanan     → warning merah.
          - Resi tidak case-sensitive (uppercase comparison).
        """
        # Cari resi di pesanan (case-insensitive). Resi disimpan apa adanya
        # di state.resi_summary, jadi kita cocokkan dgn upper().
        target = self._find_resi_in_pesanan(raw)
        if not target:
            self._render_unknown_resi(raw)
            _beep("unknown")
            self._add_history("setup_unknown", raw, "", None)
            return

        existing_slot = self.session.slot_of(target)
        if existing_slot is not None:
            self._render_setup_existing(target, existing_slot)
            _beep("overflow")
            self._add_history("setup_dup", target, target, existing_slot)
            return

        slot = self.session.assign_slot(target)
        # Buat tile dulu sebelum render result agar pulse animation bisa
        # menemukan tile target di slot grid.
        self._add_or_update_tile(slot, target)
        self._render_setup_assign(target, slot)
        _beep("register")
        self._add_history("setup_new", "", target, slot)
        self._refresh_status()

    def _find_resi_in_pesanan(self, scanned: str) -> str:
        """Match resi tanpa case (umumnya scanner output uppercase)."""
        s = scanned.strip().upper()
        if not s:
            return ""
        for r in self.session.resi_summary.keys():
            if r.upper() == s:
                return r
        return ""

    # ──────── SORT MODE ────────
    def _handle_sort_scan(self, raw: str) -> None:
        full_sku = self._lookup_sku(raw)
        if not full_sku:
            self._render_unknown(raw)
            _beep("unknown")
            self._add_history("sort_unknown", raw, "", None)
            return

        entries = self.session.demand[full_sku]
        # Filter ke entry yang resinya SUDAH ter-register; resi yang belum
        # punya slot di-block sampai operator setup dulu.
        available = next(
            (e for e in entries
             if e['remaining'] > 0 and e['resi'] in self.session.slot_map),
            None,
        )
        if available is None:
            # Cek apakah masih ada resi yang butuh tapi belum register.
            unregistered = [
                e['resi'] for e in entries
                if e['remaining'] > 0 and e['resi'] not in self.session.slot_map
            ]
            if unregistered:
                self._render_unregistered(full_sku, unregistered[0])
                _beep("overflow")
                self._add_history("sort_unregistered", full_sku, unregistered[0], None)
                return
            # Tidak ada yang remaining > 0 → benar-benar overflow.
            self._render_overflow(full_sku)
            _beep("overflow")
            self._add_history("sort_overflow", full_sku, "", None)
            return

        # Match: alokasikan ke resi (FCFS — first entry with remaining > 0).
        available["remaining"] -= 1
        self.session.scanned_count += 1
        resi = available["resi"]
        slot = self.session.slot_map[resi]
        sisa_di_resi = self.session.decrement_resi(resi)
        sudah_total, total_total = self.session.progress_of(resi)

        # Update tile slot DULU — kalau resi baru complete, fade ungu→hijau
        # mulai sekarang dan akan tampil utuh (pulse di _render_match conditional
        # supaya tidak menimpa fade saat completion).
        self._add_or_update_tile(slot, resi)
        self._render_match(slot, resi, full_sku, sudah_total, total_total)
        _beep("match")
        self._add_history("sort_match", full_sku, resi, slot)
        self._refresh_status()

    def _lookup_sku(self, scanned: str) -> str:
        """Cari SKU di demand dgn beberapa varian (padding, alias ATM↔ANM)."""
        candidates = []

        def add(s):
            s = (s or "").strip().upper()
            if s and s not in candidates:
                candidates.append(s)

        add(scanned)
        add(pad_sku_unit(scanned))
        add(alias_atm_anm(scanned))
        add(pad_sku_unit(alias_atm_anm(scanned)))

        for c in candidates:
            if c in self.session.demand:
                return c
        cnorms = {normalize_sku(c) for c in candidates if c}
        for key in self.session.demand:
            if normalize_sku(key) in cnorms:
                return key
        return ""

    # ==================================================================
    # Render BIG result
    # ==================================================================
    def _set_slot_box(self, text: str, bg_color: str) -> None:
        """
        Update kotak nomor slot di sidebar (warna BG + teks).

        Memicu bounce animation pada angka kalau text berubah dari nilai
        sebelumnya — pop visual saat slot baru di-tunjuk supaya packer
        langsung sadar update.
        """
        prev_text = self.result_big_label.cget("text")
        self._result_slot_box.configure(fg_color=bg_color)
        self.result_big_label.configure(text=text, fg_color=bg_color, text_color="white")
        if text != prev_text and text not in ("", "—"):
            animations.bounce_font(
                self.result_big_label,
                self._big_font,
                base_size=self._big_font_base_size,
                peak_size=self._big_font_base_size + 10,
                duration_ms=180,
            )

    def _render_match(self, slot, resi, sku, sudah, total):
        self._set_slot_box(str(slot) if slot is not None else "?", COLOR_OK)
        if slot is not None:
            voice.play_slot_voice(slot)
            # Kalau scan ini melengkapi resi, fade ungu→hijau di tile yang
            # sudah di-trigger _update_tile yang jadi feedback completion.
            # Pulse hanya saat resi belum complete agar tidak menimpa fade.
            if sudah < total:
                self._pulse_tile_target(slot)
        self.result_kind_label.configure(
            text="✅  DROP CHARM DI SLOT INI",
            text_color=COLOR_OK,
        )
        self.result_resi_label.configure(text=f"Resi : {resi}", text_color=COLOR_TEXT)
        self.result_sku_label.configure(text=f"SKU  : {sku}", text_color=COLOR_DIM)
        self.result_sisa_label.configure(
            text=f"Progress slot ini: {sudah}/{total}"
                 + ("  ✓ LENGKAP — kotak siap diambil" if sudah == total else ""),
            text_color=COLOR_OK if sudah == total else COLOR_DIM,
        )

    def _render_overflow(self, sku):
        self._set_slot_box("✕", COLOR_WARN)
        self.result_kind_label.configure(
            text="⚠️  SUDAH LENGKAP — charm ini kelebihan",
            text_color=COLOR_WARN,
        )
        self.result_resi_label.configure(text="(simpan ke kotak sisa)", text_color=COLOR_WARN)
        self.result_sku_label.configure(text=f"SKU  : {sku}", text_color=COLOR_DIM)
        self.result_sisa_label.configure(
            text="Semua resi yang butuh SKU ini sudah lengkap",
            text_color=COLOR_DIM,
        )

    def _render_unknown(self, raw):
        self._set_slot_box("?", COLOR_ERR)
        self.result_kind_label.configure(
            text="❌  SKU TIDAK TERDAFTAR",
            text_color=COLOR_ERR,
        )
        self.result_resi_label.configure(text="(cek ulang barcode / pesanan)", text_color=COLOR_ERR)
        self.result_sku_label.configure(text=f"Discan: {raw}", text_color=COLOR_DIM)
        self.result_sisa_label.configure(text="", text_color=COLOR_DIM)

    def _render_unregistered(self, sku, resi):
        self._set_slot_box("!", COLOR_WARN)
        self.result_kind_label.configure(
            text="⚠️  RESI BELUM TER-REGISTER — kembali ke Mode SETUP",
            text_color=COLOR_WARN,
        )
        self.result_resi_label.configure(text=f"Resi : {resi}", text_color=COLOR_WARN)
        self.result_sku_label.configure(text=f"SKU  : {sku}", text_color=COLOR_DIM)
        self.result_sisa_label.configure(
            text="Klik tombol Mode di header → scan stiker resi ini dulu",
            text_color=COLOR_DIM,
        )

    def _render_setup_assign(self, resi, slot):
        self._set_slot_box(str(slot), COLOR_OK)
        voice.play_slot_voice(slot)
        self._pulse_tile_target(slot)
        self.result_kind_label.configure(
            text="✅  RESI TER-REGISTER",
            text_color=COLOR_OK,
        )
        self.result_resi_label.configure(text=f"Resi : {resi}", text_color=COLOR_TEXT)
        info = self.session.resi_summary.get(resi, {})
        self.result_sku_label.configure(
            text=f"Total charm di slot ini: {info.get('total_charm', 0)}",
            text_color=COLOR_DIM,
        )
        self.result_sisa_label.configure(
            text=f"Tulis '{slot}' di kotak fisik → taruh di rak",
            text_color=COLOR_DIM,
        )

    def _render_setup_existing(self, resi, slot):
        self._set_slot_box(str(slot), COLOR_WARN)
        self.result_kind_label.configure(
            text="ℹ️  RESI SUDAH TER-REGISTER",
            text_color=COLOR_WARN,
        )
        self.result_resi_label.configure(text=f"Resi : {resi}", text_color=COLOR_TEXT)
        self.result_sku_label.configure(text="(scan duplikat)", text_color=COLOR_DIM)
        self.result_sisa_label.configure(text="", text_color=COLOR_DIM)

    def _render_unknown_resi(self, raw):
        self._set_slot_box("?", COLOR_ERR)
        self.result_kind_label.configure(
            text="❌  RESI TIDAK ADA DI PESANAN HARI INI",
            text_color=COLOR_ERR,
        )
        self.result_resi_label.configure(text=f"Discan: {raw}", text_color=COLOR_ERR)
        self.result_sku_label.configure(text="(cek ulang stiker / file pesanan)", text_color=COLOR_DIM)
        self.result_sisa_label.configure(text="", text_color=COLOR_DIM)

    def _set_idle_message(self, text: str, color: str):
        self._set_slot_box("—", COLOR_IDLE)
        self.result_kind_label.configure(text=text, text_color=color)
        self.result_resi_label.configure(text="", text_color=COLOR_TEXT)
        self.result_sku_label.configure(
            text=(f"{len(self.session.demand)} SKU unik · "
                  f"{len(self.session.resi_summary)} resi · "
                  f"{self.session.total_charm} charm"
                  if self.session.demand else ""),
            text_color=COLOR_DIM,
        )
        self.result_sisa_label.configure(text="", text_color=COLOR_DIM)

    # ==================================================================
    # Status & history
    # ==================================================================
    def _refresh_status(self):
        n_setup = len(self.session.slot_map)
        n_total_resi = len(self.session.resi_summary)
        n_complete = sum(
            1 for r in self.session.slot_map
            if self.session.is_resi_complete(r)
        )
        n_archived = len(self.session.archived_resi)

        self.lbl_setup.configure(text=f"Setup: {n_setup}/{n_total_resi} resi")
        self.lbl_charm.configure(
            text=f"Charm: {self.session.scanned_count}/{self.session.total_charm}"
        )
        self.lbl_complete.configure(text=f"Resi Lengkap: {n_complete}")
        self.lbl_archive.configure(text=f"Sudah Diambil: {n_archived}")

        if self.session.total_charm > 0:
            self.progress.set(self.session.scanned_count / self.session.total_charm)
        else:
            self.progress.set(0)

    def _add_history(self, kind: str, sku_or_raw: str, resi: str, slot):
        self.session.history.append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "kind": kind,
            "sku":  sku_or_raw,
            "resi": resi,
            "slot": slot,
        })
        if len(self.session.history) > self.session.history_limit:
            self.session.history = self.session.history[-self.session.history_limit:]
        self._render_history()

    def _render_history(self):
        # Symbol & label per kind
        labels = {
            "sort_match":         ("✓",  COLOR_OK),
            "sort_overflow":      ("!",  COLOR_WARN),
            "sort_unknown":       ("✕",  COLOR_ERR),
            "sort_unregistered":  ("⚠",  COLOR_WARN),
            "setup_new":          ("➕", COLOR_ACTIVE),
            "setup_dup":          ("•",  COLOR_DIM),
            "setup_unknown":      ("✕",  COLOR_ERR),
            "release":            ("⬆",  COLOR_DIM),
        }
        self.history_box.configure(state="normal")
        self.history_box.delete("1.0", "end")
        recent = self.session.history[-HISTORY_DISPLAY:]
        for h in reversed(recent):
            sym, _color = labels.get(h["kind"], ("·", COLOR_DIM))
            slot_str = f"slot {h['slot']:>3d}" if isinstance(h["slot"], int) else "—".ljust(8)
            sku_str = (h.get("sku") or "")[:30]
            line = f"[{h['time']}] {sym} {h['kind']:<18s} {sku_str:<30s} -> {slot_str}"
            if h.get("resi"):
                line += f"  ({h['resi']})"
            self.history_box.insert("end", line + "\n")
        self.history_box.configure(state="disabled")

    # ==================================================================
    # Slot grid panel (kanan)
    # ==================================================================
    def _refresh_all_ui(self):
        # Hapus tile lama
        for w in self.tile_widgets.values():
            try:
                w.destroy()
            except Exception:
                pass
        self.tile_widgets.clear()

        # Buat ulang tile sesuai slot_map
        for slot in self.session.all_known_slots():
            resi = self.session.resi_at_slot(slot)
            self._add_or_update_tile(slot, resi)

        self._refresh_status()
        self._render_history()
        self._apply_mode_ui()

    def _add_or_update_tile(self, slot: int, resi=None) -> None:
        """
        Buat / refresh tile slot di grid kanan.

        Status warna:
          * idle (slot dilepas, resi=None)        → abu-abu
          * active (resi assigned, belum lengkap) → ungu/biru
          * complete (resi lengkap)               → hijau, klik untuk kosongkan
        """
        # Tentukan status & warna tile
        if resi is None:
            status, fg = "idle", COLOR_IDLE
            sub_text = "(kosong)"
        elif self.session.is_resi_complete(resi):
            status, fg = "complete", COLOR_OK
            sub_text = "✓ LENGKAP — klik untuk ambil"
        else:
            status, fg = "active", COLOR_ACTIVE
            sudah, total = self.session.progress_of(resi)
            sub_text = f"{sudah}/{total} charm"

        # Recreate kalau belum ada widget atau status berubah
        existing = self.tile_widgets.get(slot)
        if existing is None:
            tile = self._create_tile(slot, fg, status, resi, sub_text)
            self.tile_widgets[slot] = tile
        else:
            self._update_tile(existing, slot, fg, status, resi, sub_text)

    # Grid = 5 kolom (cocok ratio modern monitor & tile lebar 170px).
    GRID_COLS = 5

    def _create_tile(self, slot, fg, status, resi, sub_text):
        all_slots = sorted(self.tile_widgets.keys() | {slot})
        idx = all_slots.index(slot)
        row, col = divmod(idx, self.GRID_COLS)

        tile = ctk.CTkFrame(
            self.slot_grid_scroll, fg_color=fg, corner_radius=10,
            width=180, height=140,
        )
        tile.grid(row=row, column=col, padx=6, pady=6, sticky="nsew")
        tile.grid_propagate(False)

        def _on_click(_e=None, slot_local=slot):
            self._maybe_release_slot(slot_local)
        tile.bind("<Button-1>", _on_click)

        # Nomor slot — besar, prominent
        lbl_num = ctk.CTkLabel(
            tile, text=str(slot), font=("Segoe UI", 38, "bold"),
            text_color="white", fg_color=fg,
        )
        lbl_num.pack(pady=(6, 0))
        lbl_num.bind("<Button-1>", _on_click)

        # Resi (truncated 16 char terakhir biar muat di tile lebar)
        resi_txt = (resi[-16:] if resi else "—")
        lbl_resi = ctk.CTkLabel(
            tile, text=resi_txt, font=("Consolas", 11, "bold"),
            text_color="white", fg_color=fg,
        )
        lbl_resi.pack(pady=(0, 2))
        lbl_resi.bind("<Button-1>", _on_click)

        # Sub text (progress / status) — diperbesar agar "X/Y charm" mudah dibaca
        lbl_sub = ctk.CTkLabel(
            tile, text=sub_text, font=("Segoe UI", 14, "bold"),
            text_color="white", fg_color=fg,
        )
        lbl_sub.pack(pady=(0, 8))
        lbl_sub.bind("<Button-1>", _on_click)

        tile._lbl_num = lbl_num
        tile._lbl_resi = lbl_resi
        tile._lbl_sub = lbl_sub
        tile._status = status
        tile._current_fg = fg  # warna fg "settled", dipakai sebagai base anim
        return tile

    def _apply_tile_fg(self, tile, color: str) -> None:
        """Apply ``color`` ke tile frame + ketiga child labels-nya."""
        try:
            tile.configure(fg_color=color)
            tile._lbl_num.configure(fg_color=color)
            tile._lbl_resi.configure(fg_color=color)
            tile._lbl_sub.configure(fg_color=color)
        except Exception:
            pass  # widget bisa saja sudah di-destroy mid-animation

    def _update_tile(self, tile, slot, fg, status, resi, sub_text):
        if tile._status != status:
            prev_fg = getattr(tile, "_current_fg", fg)
            tile._status = status
            tile._current_fg = fg
            # Color fade smooth saat transition (ungu→hijau saat lengkap, dll).
            animations.animate_color(
                tile, prev_fg, fg, duration_ms=280,
                set_fn=lambda c, t=tile: self._apply_tile_fg(t, c),
            )
        tile._lbl_num.configure(text=str(slot))
        tile._lbl_resi.configure(text=(resi[-14:] if resi else "—"))
        tile._lbl_sub.configure(text=sub_text)

    def _pulse_tile_target(self, slot: int) -> None:
        """
        Pulse glow tile slot tujuan saat scan match, supaya mata packer
        langsung tertarik ke slot yang dimaksud di grid.
        """
        tile = self.tile_widgets.get(slot)
        if tile is None:
            return
        base = getattr(tile, "_current_fg", COLOR_OK)
        # Peak = base di-mix 55% ke putih → cerah tapi masih kelihatan asalnya.
        peak = animations.interpolate_color(base, "#ffffff", 0.55)
        animations.pulse_color(
            tile, base_hex=base, peak_hex=peak, duration_ms=420,
            set_fn=lambda c, t=tile: self._apply_tile_fg(t, c),
        )

    def _maybe_release_slot(self, slot: int) -> None:
        """Kosongkan slot kalau sudah complete (klik handler dari tile)."""
        resi = self.session.resi_at_slot(slot)
        if resi is None:
            return  # slot kosong/idle — nothing to do

        is_complete = self.session.is_resi_complete(resi)
        if not is_complete:
            sudah, total = self.session.progress_of(resi)
            messagebox.showinfo(
                "Slot belum lengkap",
                f"Resi {resi} di slot {slot} baru {sudah}/{total} charm.\n\n"
                f"Slot hanya bisa dikosongkan setelah semua charm terkumpul "
                f"(tile berubah hijau).",
            )
            return

        if not messagebox.askyesno(
            "Kosongkan slot?",
            f"Slot {slot} (resi {resi}) sudah lengkap.\n\n"
            f"Pickup kotaknya, lalu kosongkan slot agar bisa "
            f"dipakai resi lain?",
        ):
            return

        released_resi = self.session.release_slot(slot)
        if released_resi is None:
            return
        # Update tile menjadi idle
        self._add_or_update_tile(slot, None)
        _beep("release")
        self._add_history("release", "", released_resi, slot)
        self._refresh_status()
        self.session.save()


# ──────────────────────────────────────────────────────────────────
def main() -> int:
    app = StasiunSortirApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
