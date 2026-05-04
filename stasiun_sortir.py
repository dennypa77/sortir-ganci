"""
Stasiun Sortir Ganci — aplikasi scan-to-sort untuk packer.

Workflow yang di-solve:
  1. Setelah laser cutting, 50-80 charm akrilik dari banyak order tumpah ke
     wadah campur.
  2. Packer ambil charm random → scan barcode SKU → aplikasi tunjuk slot
     resi mana yang butuh charm itu → packer drop ke slot tsb.
  3. Repeat sampai semua charm sudah masuk slot resinya.

Logika dibalik dari pendekatan manual: bukan packer cari charm untuk resi,
tapi sistem tunjuk resi untuk charm — drastis mengurangi salah-kirim SKU
saat volume tinggi & banyak desain mirip.

Dependencies:
  - customtkinter  (UI modern)
  - core.sku_utils, core.pesanan, core.config, core.version, core.updater

Tidak menyentuh ``sortir_desain.py`` — kedua aplikasi berbagi infrastruktur
core/ tapi independen.
"""

from __future__ import annotations

import os
import sys
import threading
from datetime import datetime
from tkinter import filedialog, messagebox

import customtkinter as ctk

from core.config import load_config, save_config
from core.pesanan import (
    load_pesanan_demand,
    total_charm_initial,
    total_charm_outstanding,
)
from core.sku_utils import alias_atm_anm, normalize_sku, pad_sku_unit
from core.updater import consume_update_flag
from core.version import get_version

# ── Sound (Windows). Optional — gracefully no-op kalau tidak tersedia. ─────
try:
    import winsound  # type: ignore[import]
    _HAS_BEEP = True
except ImportError:
    _HAS_BEEP = False


# ── Tema warna stasiun ─────────────────────────────────────────────────────
COLOR_OK      = "#23c78e"   # Match: drop charm di slot
COLOR_WARN    = "#f5a623"   # Overflow: SKU sudah lengkap
COLOR_ERR     = "#f26c6c"   # Unknown: SKU tidak terdaftar
COLOR_IDLE    = "#404060"   # Idle / placeholder
COLOR_DIM     = "#9090b0"
COLOR_TEXT    = "#e2e2f0"
COLOR_PANEL   = "#2a2a3e"
COLOR_BG      = "#1e1e2e"
COLOR_ACCENT2 = "#56cfcf"

HISTORY_LIMIT = 10


def _beep(kind: str) -> None:
    """Suara feedback singkat. ``kind`` ∈ {match, overflow, unknown}."""
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
    except RuntimeError:
        # winsound.Beep bisa error di environment tanpa audio device — abaikan.
        pass


class StasiunSortirApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.app_version = get_version()
        self.title(f"📦 Stasiun Sortir Ganci  v{self.app_version}")
        self.geometry("1100x820")
        self.minsize(900, 700)
        self.configure(fg_color=COLOR_BG)

        # State scan-to-sort
        self.demand: dict = {}
        self.resi_summary: dict = {}
        self.slot_map: dict = {}
        self.skipped_rows: list = []
        self.scanned_count: int = 0
        self.total_charm: int = 0
        self.history: list = []        # daftar dict {time, sku, resi, slot, kind}

        self._build_ui()
        # Tampilkan notifikasi update setelah render dasar.
        self.after(400, self._show_update_notification)

    # ------------------------------------------------------------------
    # UI Builder
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        # Strip header
        ctk.CTkFrame(self, height=4, fg_color="#7c6af7", corner_radius=0).pack(fill="x")

        # Title + tombol-tombol kanan
        bar = ctk.CTkFrame(self, fg_color=COLOR_BG)
        bar.pack(fill="x", padx=24, pady=(14, 6))
        ctk.CTkLabel(
            bar, text=f"📦  Stasiun Sortir Ganci  ·  v{self.app_version}",
            font=("Segoe UI", 18, "bold"), text_color=COLOR_ACCENT2,
        ).pack(side="left")
        ctk.CTkButton(
            bar, text="🔄  Reset",
            command=self._reset, width=110, height=34,
            fg_color="#3d3d56", hover_color="#505070",
        ).pack(side="right", padx=(8, 0))
        ctk.CTkButton(
            bar, text="📂  Load Pesanan",
            command=self._load_pesanan_dialog, width=160, height=34,
            fg_color="#23c78e", hover_color="#1da87a",
        ).pack(side="right")

        # Status row
        status_card = ctk.CTkFrame(self, fg_color=COLOR_PANEL, corner_radius=8)
        status_card.pack(fill="x", padx=24, pady=4)
        status_inner = ctk.CTkFrame(status_card, fg_color=COLOR_PANEL)
        status_inner.pack(fill="x", padx=14, pady=10)

        self.lbl_total_resi  = ctk.CTkLabel(status_inner, text="Resi: -",
                                            font=("Segoe UI", 11), text_color=COLOR_TEXT)
        self.lbl_total_charm = ctk.CTkLabel(status_inner, text="Charm: -",
                                            font=("Segoe UI", 11), text_color=COLOR_TEXT)
        self.lbl_sortir_done = ctk.CTkLabel(status_inner, text="Sudah Sortir: -",
                                            font=("Segoe UI", 11), text_color=COLOR_TEXT)
        self.lbl_total_resi.pack(side="left", padx=(0, 28))
        self.lbl_total_charm.pack(side="left", padx=(0, 28))
        self.lbl_sortir_done.pack(side="left", padx=(0, 28))

        self.progress = ctk.CTkProgressBar(status_inner, height=12, progress_color="#7c6af7")
        self.progress.pack(side="right", fill="x", expand=True, padx=(20, 0))
        self.progress.set(0)

        # Scan input
        scan_card = ctk.CTkFrame(self, fg_color=COLOR_PANEL, corner_radius=8)
        scan_card.pack(fill="x", padx=24, pady=8)
        scan_inner = ctk.CTkFrame(scan_card, fg_color=COLOR_PANEL)
        scan_inner.pack(fill="x", padx=14, pady=10)
        ctk.CTkLabel(
            scan_inner, text="📷  Scan Barcode:",
            font=("Segoe UI", 12, "bold"), text_color=COLOR_ACCENT2,
        ).pack(side="left", padx=(0, 12))

        self.scan_var = ctk.StringVar()
        self.scan_entry = ctk.CTkEntry(
            scan_inner, textvariable=self.scan_var,
            font=("Consolas", 16),
            height=40, border_width=2, border_color="#7c6af7",
            placeholder_text="(klik di sini, lalu scan barcode...)",
        )
        self.scan_entry.pack(side="left", fill="x", expand=True)
        self.scan_entry.bind("<Return>", self._on_scan)
        self.scan_entry.bind("<Escape>", lambda e: self.scan_var.set(""))
        self.scan_entry.configure(state="disabled")  # locked sampai pesanan di-load

        # Result panel — BIG slot display
        result_card = ctk.CTkFrame(self, fg_color=COLOR_PANEL, corner_radius=10)
        result_card.pack(fill="both", expand=True, padx=24, pady=8)

        self.result_kind_label = ctk.CTkLabel(
            result_card, text="BELUM LOAD PESANAN",
            font=("Segoe UI", 16, "bold"), text_color=COLOR_DIM,
        )
        self.result_kind_label.pack(pady=(20, 6))

        self.result_big_label = ctk.CTkLabel(
            result_card, text="—",
            font=("Segoe UI", 140, "bold"), text_color=COLOR_IDLE,
        )
        self.result_big_label.pack(pady=(4, 4))

        self.result_resi_label = ctk.CTkLabel(
            result_card, text="",
            font=("Consolas", 16, "bold"), text_color=COLOR_TEXT,
        )
        self.result_resi_label.pack(pady=(4, 2))

        self.result_sku_label = ctk.CTkLabel(
            result_card, text="",
            font=("Consolas", 14), text_color=COLOR_DIM,
        )
        self.result_sku_label.pack(pady=(0, 2))

        self.result_sisa_label = ctk.CTkLabel(
            result_card, text="",
            font=("Segoe UI", 12, "italic"), text_color=COLOR_DIM,
        )
        self.result_sisa_label.pack(pady=(0, 18))

        # History panel
        hist_card = ctk.CTkFrame(self, fg_color=COLOR_PANEL, corner_radius=8)
        hist_card.pack(fill="x", padx=24, pady=(0, 16))
        ctk.CTkLabel(
            hist_card, text="📋  Riwayat Scan (10 terakhir)",
            font=("Segoe UI", 11, "bold"), text_color=COLOR_ACCENT2,
            anchor="w",
        ).pack(fill="x", padx=14, pady=(8, 4))

        self.history_box = ctk.CTkTextbox(
            hist_card, height=140, font=("Consolas", 11),
            fg_color="#12121f", text_color=COLOR_TEXT,
            wrap="none",
        )
        self.history_box.pack(fill="x", padx=14, pady=(0, 12))
        self.history_box.configure(state="disabled")

    # ------------------------------------------------------------------
    # Update notification (one-shot dari core.updater)
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Load pesanan
    # ------------------------------------------------------------------
    def _load_pesanan_dialog(self) -> None:
        cfg = load_config()
        # Default ke path config; user bisa override via dialog
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
                    "Database SKU akan di-skip — bundle yang butuh DB lookup "
                    "tidak akan ter-resolve. Lanjut?",
                ):
                    return
                path_database = ""

        # Persist pilihan ke user_config
        cfg["file_pesanan"] = path_pesanan
        if path_database:
            cfg["file_database"] = path_database
        save_config(cfg)

        # Build demand di thread terpisah supaya UI tidak freeze (Excel besar bisa lambat).
        self._set_loading_state(True)
        threading.Thread(
            target=self._build_demand,
            args=(path_pesanan, path_database),
            daemon=True,
        ).start()

    def _set_loading_state(self, loading: bool) -> None:
        if loading:
            self.result_kind_label.configure(text="⏳  MEMBACA PESANAN...", text_color=COLOR_DIM)
            self.result_big_label.configure(text="—", text_color=COLOR_IDLE)
            self.result_resi_label.configure(text="")
            self.result_sku_label.configure(text="")
            self.result_sisa_label.configure(text="")

    def _build_demand(self, path_pesanan: str, path_database: str) -> None:
        try:
            demand, summary, slot_map, skipped = load_pesanan_demand(path_pesanan, path_database)
        except Exception as e:
            self.after(0, lambda: messagebox.showerror(
                "Gagal membaca pesanan",
                f"Tidak bisa membaca file pesanan:\n\n{e}",
            ))
            self.after(0, lambda: self._reset_ui_idle())
            return

        # Apply ke main thread
        def _apply():
            self.demand = demand
            self.resi_summary = summary
            self.slot_map = slot_map
            self.skipped_rows = skipped
            self.scanned_count = 0
            self.total_charm = total_charm_initial(summary)
            self.history.clear()
            self._refresh_status()
            self._render_history()
            self.scan_entry.configure(state="normal")
            self.scan_entry.focus_set()
            self.result_kind_label.configure(
                text="✓ PESANAN DIMUAT — siap scan",
                text_color=COLOR_OK,
            )
            self.result_big_label.configure(text="—", text_color=COLOR_IDLE)
            self.result_resi_label.configure(text="")
            self.result_sku_label.configure(
                text=f"{len(self.demand)} SKU unik · {len(self.resi_summary)} resi · {self.total_charm} charm",
                text_color=COLOR_DIM,
            )
            self.result_sisa_label.configure(text="")

            if skipped:
                # Tampilkan ringkas — full list muncul di history sbg system message
                preview = "\n".join(skipped[:6])
                if len(skipped) > 6:
                    preview += f"\n…(+{len(skipped) - 6} baris lagi)"
                messagebox.showwarning(
                    f"⚠️  {len(skipped)} baris di-skip",
                    f"Beberapa baris pesanan tidak bisa diproses:\n\n{preview}",
                )

        self.after(0, _apply)

    def _reset_ui_idle(self) -> None:
        self.result_kind_label.configure(text="BELUM LOAD PESANAN", text_color=COLOR_DIM)
        self.result_big_label.configure(text="—", text_color=COLOR_IDLE)
        self.result_resi_label.configure(text="")
        self.result_sku_label.configure(text="")
        self.result_sisa_label.configure(text="")
        self.scan_entry.configure(state="disabled")

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------
    def _reset(self) -> None:
        if not self.demand:
            return
        if not messagebox.askyesno(
            "Reset stasiun",
            "Reset progress sortir? Semua scan yang sudah dicatat akan hilang.\n"
            "(Data pesanan akan dimuat ulang dari file.)",
        ):
            return
        cfg = load_config()
        path_pesanan = cfg.get("file_pesanan", "")
        path_database = cfg.get("file_database", "")
        if not (path_pesanan and os.path.exists(path_pesanan)):
            self.demand = {}
            self.resi_summary = {}
            self.slot_map = {}
            self._reset_ui_idle()
            self._refresh_status()
            self._render_history()
            return
        self._set_loading_state(True)
        threading.Thread(
            target=self._build_demand,
            args=(path_pesanan, path_database),
            daemon=True,
        ).start()

    # ------------------------------------------------------------------
    # Scan handler — inti dari scan-to-sort
    # ------------------------------------------------------------------
    def _on_scan(self, _event=None) -> None:
        raw = self.scan_var.get().strip()
        self.scan_var.set("")  # clear segera supaya scan berikutnya siap
        if not raw or not self.demand:
            return

        full_sku = self._lookup_sku(raw)
        if not full_sku:
            self._render_unknown(raw)
            _beep("unknown")
            self._add_history(kind="unknown", sku=raw, resi="", slot=None)
            return

        entries = self.demand[full_sku]
        available = next((e for e in entries if e["remaining"] > 0), None)
        if not available:
            # SKU dikenal tapi semua resi sudah ter-scan → overflow.
            self._render_overflow(full_sku)
            _beep("overflow")
            self._add_history(kind="overflow", sku=full_sku, resi="", slot=None)
            return

        # Match: alokasikan ke resi yang remaining > 0 (first-come-first-serve).
        available["remaining"] -= 1
        self.scanned_count += 1
        resi = available["resi"]
        slot = self.slot_map.get(resi)
        sisa_di_slot_ini = available["remaining"]
        total_qty_di_slot = self.resi_summary[resi]["sku_breakdown"].get(full_sku, 0)
        sudah_di_slot = total_qty_di_slot - sisa_di_slot_ini

        self._render_match(slot, resi, full_sku, sudah_di_slot, total_qty_di_slot)
        _beep("match")
        self._add_history(kind="match", sku=full_sku, resi=resi, slot=slot)
        self._refresh_status()

        if total_charm_outstanding(self.demand) == 0:
            # Semua charm sudah ter-sortir → celebration banner.
            self.after(300, self._render_done)

    # ------------------------------------------------------------------
    # SKU lookup dengan beberapa varian normalisasi
    # ------------------------------------------------------------------
    def _lookup_sku(self, scanned: str) -> str:
        """
        Cari SKU di ``self.demand`` dgn urutan: exact → padded → normalized →
        alias ATM↔ANM. Return key di ``self.demand`` (mis. ``GK-ANM-0000487-L``)
        atau ``""`` bila tidak ada.

        Catatan: tidak peduli apakah entry yg cocok masih punya remaining > 0
        — caller cek itu untuk membedakan match vs overflow.
        """
        candidates = []

        def add(s):
            s = (s or "").strip().upper()
            if s and s not in candidates:
                candidates.append(s)

        add(scanned)
        add(pad_sku_unit(scanned))
        add(alias_atm_anm(scanned))
        add(pad_sku_unit(alias_atm_anm(scanned)))

        # 1) Exact match
        for c in candidates:
            if c in self.demand:
                return c

        # 2) Match via normalize_sku (kebal padding & GK- prefix)
        cand_norms = {normalize_sku(c) for c in candidates if c}
        for key in self.demand:
            if normalize_sku(key) in cand_norms:
                return key
        return ""

    # ------------------------------------------------------------------
    # Render BIG result
    # ------------------------------------------------------------------
    def _render_match(self, slot, resi, sku, sudah, total):
        self.result_kind_label.configure(
            text="✅  DROP CHARM DI SLOT INI",
            text_color=COLOR_OK,
        )
        self.result_big_label.configure(
            text=str(slot) if slot is not None else "?",
            text_color=COLOR_OK,
        )
        self.result_resi_label.configure(
            text=f"Resi : {resi}",
            text_color=COLOR_TEXT,
        )
        self.result_sku_label.configure(
            text=f"SKU  : {sku}",
            text_color=COLOR_DIM,
        )
        self.result_sisa_label.configure(
            text=f"Progress slot ini: {sudah}/{total}",
            text_color=COLOR_DIM,
        )

    def _render_overflow(self, sku):
        self.result_kind_label.configure(
            text="⚠️  SUDAH LENGKAP — charm ini sisa / kelebihan",
            text_color=COLOR_WARN,
        )
        self.result_big_label.configure(text="✕", text_color=COLOR_WARN)
        self.result_resi_label.configure(text="(simpan ke kotak sisa)", text_color=COLOR_WARN)
        self.result_sku_label.configure(text=f"SKU  : {sku}", text_color=COLOR_DIM)
        self.result_sisa_label.configure(
            text="Semua resi yang butuh SKU ini sudah lengkap",
            text_color=COLOR_DIM,
        )

    def _render_unknown(self, raw):
        self.result_kind_label.configure(
            text="❌  SKU TIDAK TERDAFTAR",
            text_color=COLOR_ERR,
        )
        self.result_big_label.configure(text="?", text_color=COLOR_ERR)
        self.result_resi_label.configure(text="(cek ulang barcode / pesanan)", text_color=COLOR_ERR)
        self.result_sku_label.configure(text=f"Discan: {raw}", text_color=COLOR_DIM)
        self.result_sisa_label.configure(text="", text_color=COLOR_DIM)

    def _render_done(self):
        self.result_kind_label.configure(
            text="🎉  SELESAI — semua charm sudah masuk slot resinya!",
            text_color=COLOR_OK,
        )
        self.result_big_label.configure(text="✓", text_color=COLOR_OK)
        self.result_resi_label.configure(text="", text_color=COLOR_TEXT)
        self.result_sku_label.configure(text="", text_color=COLOR_DIM)
        self.result_sisa_label.configure(text="", text_color=COLOR_DIM)

    # ------------------------------------------------------------------
    # Status bar & history
    # ------------------------------------------------------------------
    def _refresh_status(self):
        self.lbl_total_resi.configure(text=f"Resi: {len(self.resi_summary)}")
        self.lbl_total_charm.configure(text=f"Charm: {self.total_charm}")
        self.lbl_sortir_done.configure(
            text=f"Sudah Sortir: {self.scanned_count}/{self.total_charm}"
        )
        if self.total_charm > 0:
            self.progress.set(self.scanned_count / self.total_charm)
        else:
            self.progress.set(0)

    def _add_history(self, kind: str, sku: str, resi: str, slot):
        self.history.append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "kind": kind,
            "sku":  sku,
            "resi": resi,
            "slot": slot,
        })
        if len(self.history) > HISTORY_LIMIT:
            self.history = self.history[-HISTORY_LIMIT:]
        self._render_history()

    def _render_history(self):
        self.history_box.configure(state="normal")
        self.history_box.delete("1.0", "end")
        for h in reversed(self.history):  # terbaru di atas
            mark = {"match": "✓", "overflow": "!", "unknown": "✕"}.get(h["kind"], "·")
            slot = f"slot {h['slot']:>3d}" if isinstance(h["slot"], int) else "—".ljust(8)
            line = f"[{h['time']}] {mark} {h['sku']:<28s}  →  {slot}"
            if h["resi"]:
                line += f"  ({h['resi']})"
            self.history_box.insert("end", line + "\n")
        self.history_box.configure(state="disabled")


def main() -> int:
    app = StasiunSortirApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
