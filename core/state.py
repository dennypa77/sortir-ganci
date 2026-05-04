"""
State manager untuk Stasiun Sortir.

Tanggung jawab:

* **Slot management** — assign nomor slot ke resi (sequential 1, 2, 3, …),
  release slot saat resi sudah lengkap & diambil packer, recycle slot ke
  resi baru.
* **Completion tracking** — track sisa charm per resi → mudah cek apakah
  resi sudah lengkap tanpa loop seluruh demand.
* **Persistence** — save snapshot ke ``.scan_state.json`` (gitignored)
  per perubahan, supaya kalau aplikasi crash / restart, packer bisa
  resume dari titik terakhir.

State machine slot:

    [Idle]  ──register──▶  [Active]  ──semua charm scan──▶  [Complete]
                              ▲                                  │
                              └────────release (kosongkan)───────┘

Saat slot di-release, dia kembali ke pool ``released_slots`` dan menjadi
kandidat utama saat assign berikutnya — supaya nomor slot tetap kompak
(packer tidak loncat ke nomor besar selama masih ada slot kecil tersedia).
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Set


_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(_BASE_DIR, '.scan_state.json')


@dataclass
class SortirState:
    """In-memory snapshot dari sesi sortir."""

    # Identitas pesanan — dipakai untuk validasi resume.
    pesanan_file: str = ''
    pesanan_mtime: float = 0.0

    # Mode UI: 'setup' (scan resi → assign slot) atau 'sort' (scan charm).
    mode: str = 'setup'

    # demand: full_sku → [{resi, sku_pesanan_asli, remaining}, …]. FCFS-safe:
    # entri urutannya = urutan munculnya resi di pesanan.
    demand: Dict[str, List[dict]] = field(default_factory=dict)

    # Ringkasan per-resi yang dibekukan dari pesanan (initial total, breakdown).
    resi_summary: Dict[str, dict] = field(default_factory=dict)

    # Slot assignment — slot ditentukan oleh Setup mode, bukan otomatis.
    slot_map: Dict[str, int] = field(default_factory=dict)        # resi → slot
    released_slots: Set[int] = field(default_factory=set)         # slot bebas dipakai ulang
    next_slot_number: int = 1                                     # slot baru kalau pool kosong

    # Per-resi remaining charm — dipakai untuk deteksi completion cepat tanpa
    # loop demand. Saat 0 → resi lengkap → tile slot jadi hijau.
    resi_remaining: Dict[str, int] = field(default_factory=dict)

    # Resi yang slot-nya sudah di-release (alias: resi sudah dikemas).
    archived_resi: List[dict] = field(default_factory=list)

    # History scan terbatas — supaya state file tidak membengkak.
    history: List[dict] = field(default_factory=list)
    history_limit: int = 50

    # Counter agregat — derived dari demand tapi di-cache untuk progress bar.
    scanned_count: int = 0
    total_charm: int = 0

    # ──────────────────────────────────────────────────────────
    # Slot operations
    # ──────────────────────────────────────────────────────────
    def assign_slot(self, resi: str) -> int:
        """
        Assign nomor slot ke ``resi``. Kalau resi sudah punya slot, return
        slot existing. Pakai slot dari pool released dulu (supaya nomor
        kompak), baru fallback ke ``next_slot_number``.
        """
        if resi in self.slot_map:
            return self.slot_map[resi]
        if self.released_slots:
            slot = min(self.released_slots)
            self.released_slots.discard(slot)
        else:
            slot = self.next_slot_number
            self.next_slot_number += 1
        self.slot_map[resi] = slot
        return slot

    def release_slot(self, slot: int) -> Optional[str]:
        """
        Kosongkan ``slot`` — kembalikan ke pool. Resi yang sebelumnya di
        slot tsb di-archive (untuk record). Return resi yang dilepas, atau
        ``None`` kalau slot tidak ada di slot_map.
        """
        resi = next((r for r, s in self.slot_map.items() if s == slot), None)
        if resi is None:
            return None
        del self.slot_map[resi]
        self.released_slots.add(slot)

        # Catat di archive utk audit. Sertakan total charm & timestamp release.
        info = self.resi_summary.get(resi, {})
        self.archived_resi.append({
            'resi':         resi,
            'slot':         slot,
            'total_charm':  info.get('total_charm', 0),
            'released_at':  datetime.now().isoformat(timespec='seconds'),
            'completed':    self.is_resi_complete(resi),
        })
        return resi

    def slot_of(self, resi: str) -> Optional[int]:
        return self.slot_map.get(resi)

    def resi_at_slot(self, slot: int) -> Optional[str]:
        return next((r for r, s in self.slot_map.items() if s == slot), None)

    def all_active_slots(self) -> List[int]:
        """Slot yang sedang ter-assign ke resi (urutan menaik)."""
        return sorted(self.slot_map.values())

    def all_known_slots(self) -> List[int]:
        """Semua nomor slot yang pernah dipakai (active + released), untuk grid."""
        active = set(self.slot_map.values())
        return sorted(active | self.released_slots)

    # ──────────────────────────────────────────────────────────
    # Completion tracking
    # ──────────────────────────────────────────────────────────
    def is_resi_complete(self, resi: str) -> bool:
        """True kalau semua charm yang dipesan resi ini sudah ter-scan."""
        return self.resi_remaining.get(resi, 0) <= 0

    def decrement_resi(self, resi: str) -> int:
        """Kurangi remaining charm utk resi. Return sisa setelah decrement."""
        if resi in self.resi_remaining:
            self.resi_remaining[resi] = max(0, self.resi_remaining[resi] - 1)
        return self.resi_remaining.get(resi, 0)

    def progress_of(self, resi: str) -> tuple:
        """Return (sudah, total) charm utk resi tsb."""
        total = self.resi_summary.get(resi, {}).get('total_charm', 0)
        sisa = self.resi_remaining.get(resi, 0)
        return total - sisa, total

    # ──────────────────────────────────────────────────────────
    # Persistence
    # ──────────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        """Serialisasi state ke dict siap di-JSON-kan."""
        return {
            'pesanan_file':     self.pesanan_file,
            'pesanan_mtime':    self.pesanan_mtime,
            'mode':             self.mode,
            'demand':           self.demand,
            'resi_summary':     self.resi_summary,
            'slot_map':         self.slot_map,
            'released_slots':   sorted(self.released_slots),
            'next_slot_number': self.next_slot_number,
            'resi_remaining':   self.resi_remaining,
            'archived_resi':    self.archived_resi,
            'history':          self.history[-self.history_limit:],
            'scanned_count':    self.scanned_count,
            'total_charm':      self.total_charm,
            'saved_at':         datetime.now().isoformat(timespec='seconds'),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SortirState":
        s = cls()
        s.pesanan_file     = d.get('pesanan_file', '')
        s.pesanan_mtime    = float(d.get('pesanan_mtime', 0.0))
        s.mode             = d.get('mode', 'setup')
        s.demand           = d.get('demand', {})
        s.resi_summary     = d.get('resi_summary', {})
        s.slot_map         = {k: int(v) for k, v in d.get('slot_map', {}).items()}
        s.released_slots   = set(d.get('released_slots', []))
        s.next_slot_number = int(d.get('next_slot_number', 1))
        s.resi_remaining   = {k: int(v) for k, v in d.get('resi_remaining', {}).items()}
        s.archived_resi    = list(d.get('archived_resi', []))
        s.history          = list(d.get('history', []))
        s.scanned_count    = int(d.get('scanned_count', 0))
        s.total_charm      = int(d.get('total_charm', 0))
        return s

    def save(self) -> None:
        """Tulis state ke disk. Tidak raise — kegagalan I/O tidak boleh ganggu UI."""
        try:
            tmp = STATE_FILE + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
            os.replace(tmp, STATE_FILE)
        except OSError:
            pass

    @staticmethod
    def load() -> Optional["SortirState"]:
        """Baca state dari disk. Return None kalau tidak ada / corrupt."""
        if not os.path.exists(STATE_FILE):
            return None
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                d = json.load(f)
            return SortirState.from_dict(d)
        except (OSError, json.JSONDecodeError):
            return None

    @staticmethod
    def clear() -> None:
        """Hapus state file (mis. saat user klik Reset)."""
        try:
            if os.path.exists(STATE_FILE):
                os.remove(STATE_FILE)
        except OSError:
            pass

    def matches_pesanan(self, file_path: str) -> bool:
        """Cek apakah state ini cocok dgn file pesanan tertentu (path + mtime)."""
        if not file_path or not os.path.exists(file_path):
            return False
        try:
            mtime = os.path.getmtime(file_path)
        except OSError:
            return False
        return self.pesanan_file == file_path and abs(self.pesanan_mtime - mtime) < 1.0


# ──────────────────────────────────────────────────────────────────
# Helper: build state dari hasil load_pesanan_demand
# ──────────────────────────────────────────────────────────────────
def build_initial_state(
    pesanan_file: str,
    demand: Dict[str, List[dict]],
    resi_summary: Dict[str, dict],
) -> SortirState:
    """Bangun ``SortirState`` dari output :func:`core.pesanan.load_pesanan_demand`."""
    s = SortirState()
    s.pesanan_file = pesanan_file
    try:
        s.pesanan_mtime = os.path.getmtime(pesanan_file) if pesanan_file else 0.0
    except OSError:
        s.pesanan_mtime = 0.0
    s.mode = 'setup'
    s.demand = {k: [dict(e) for e in entries] for k, entries in demand.items()}  # deep copy
    s.resi_summary = {
        r: {
            'total_charm':   info['total_charm'],
            'sku_breakdown': dict(info.get('sku_breakdown', {})),
        }
        for r, info in resi_summary.items()
    }
    s.resi_remaining = {r: info['total_charm'] for r, info in resi_summary.items()}
    s.total_charm = sum(info['total_charm'] for info in resi_summary.values())
    s.scanned_count = 0
    return s
