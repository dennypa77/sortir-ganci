"""
Utilitas SKU yang di-share antara `sortir_desain.py` dan `stasiun_sortir.py`.

Diekstrak dari `sortir_desain.proses_data()` agar logika normalisasi & resolusi
bundle bisa di-reuse oleh aplikasi stasiun sortir scan-to-sort tanpa perlu
menyentuh atau memuat aplikasi pengumpul desain produksi.

Semua fungsi di sini PURE (tidak side-effect, tidak menulis log) sehingga aman
dipanggil dari konteks UI manapun. Caller bertanggung jawab atas logging.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional


# ── Konstanta domain ───────────────────────────────────────────────────────
ATURAN_BUNDLE = {
    'S': 5,
    'M': 5,
    'BS': 10,
}

UKURAN_VALID = ('L', 'S', 'M', 'BS')
UKURAN_BUNDLE_CAPABLE = ('S', 'M', 'BS')


# ── Helpers pure: parsing & normalisasi SKU ────────────────────────────────
def parse_dynamic_sku(sku_bundle_dasar: str) -> List[str]:
    """
    Baca bundle SKU dengan urutan angka dinamis.

    Mendukung format lama (`GK-ANM-SET-81-90`) dan format baru (`ANM-81-90`).
    Mengembalikan list SKU dasar (tanpa suffix ukuran) ber-prefix `GK-` dengan
    angka di-pad 7 digit. Return list kosong jika pola tidak cocok.
    """
    pattern = r'(?:GK-)?(.*?)-(\d+)-(\d+)$'
    match = re.search(pattern, sku_bundle_dasar, re.IGNORECASE)
    if match:
        kategori_mentah = match.group(1)
        kategori = re.sub(r'-?SET-?', '', kategori_mentah, flags=re.IGNORECASE).strip('-')
        start = int(match.group(2))
        end = int(match.group(3))
        return [f"GK-{kategori}-{str(i).zfill(7)}" for i in range(start, end + 1)]
    return []


def normalize_sku(sku_str: str) -> str:
    """
    Seragamkan SKU agar kebal terhadap variasi format:
      1. Ada/tidaknya prefix `GK-`.
      2. Angka dengan/tanpa nol di awalan (mis. `0010750` ≡ `10750`).

    Contoh: `GK-ATM-0010750-L` & `ATM-10750-L` keduanya → `ATM-10750-L`.
    """
    s = str(sku_str).strip().upper()
    s = s.replace('GK-', '')
    s = re.sub(r'(^|-)0+(\d+)', r'\g<1>\g<2>', s)
    return s


def to_ultra_short_sku(sku_str: str) -> str:
    """
    Ubah SKU panjang → format Ultra-Short.

    Contoh:
      `GK-ATM-SET-5021-5030-BS` → `5021BS`
      `GK-ANM-0000487-L`        → `487L`
    """
    s = str(sku_str).strip().upper()
    s = s.replace('GK-', '')
    s = re.sub(r'^(ATM|ANM)-?', '', s)
    s = re.sub(r'-?SET-?', '-', s)

    parts = [p for p in s.split('-') if p]
    if not parts:
        return ""

    ukuran = ""
    if parts[-1].isalpha():
        ukuran = parts[-1]
        parts = parts[:-1]

    if not parts:
        return ukuran

    angka = parts[0].lstrip('0') or '0'
    return f"{angka}{ukuran}"


def pad_sku_unit(sku_pesanan: str) -> str:
    """
    Pad angka jadi 7 digit HANYA untuk SKU tunggal ber-suffix `-L/-S/-M/-BS`.

    Contoh: `GK-ANM-487-L` → `GK-ANM-0000487-L`.

    Format bundle/SET (mis. `GK-ANM-SET-3851-3860-BS`) sengaja TIDAK di-pad,
    karena part `[-3]` adalah angka (`3851`) → diabaikan oleh heuristik.
    """
    parts = str(sku_pesanan).split('-')
    if len(parts) >= 3 and parts[-1].upper() in UKURAN_VALID:
        angka = parts[-2]
        prev = parts[-3] if len(parts) >= 4 else ''
        if angka.isdigit() and len(angka) < 7 and not prev.isdigit():
            parts[-2] = angka.zfill(7)
            return '-'.join(parts)
    return sku_pesanan


def extract_ukuran(sku_pesanan: str) -> str:
    """Ambil suffix ukuran (uppercase) dari SKU. Return '' bila bukan L/S/M/BS."""
    ukuran = str(sku_pesanan).split('-')[-1].upper()
    return ukuran if ukuran in UKURAN_VALID else ''


def alias_atm_anm(sku_str: str) -> str:
    """
    Hasilkan alias ATM↔ANM dari SKU yang diberikan.

    Mengembalikan string SKU dengan prefix kategori ditukar; mis.
    `GK-ATM-487-L` → `GK-ANM-487-L`. Bila tidak mengandung token `GK-ATM-`
    atau `GK-ANM-`, return string kosong.

    Catatan: di proyek ini ATM dan ANM adalah dua varian penamaan yang sama
    secara desain (file di folder master kadang dibuat dengan prefix yang
    berbeda dari yang dipakai di pesanan).
    """
    s = str(sku_str)
    if 'GK-ATM-' in s:
        return s.replace('GK-ATM-', 'GK-ANM-', 1)
    if 'GK-ANM-' in s:
        return s.replace('GK-ANM-', 'GK-ATM-', 1)
    return ''


# ── Resolusi bundle (ekstraksi dari proses_data) ───────────────────────────
@dataclass
class BundleResolution:
    """
    Hasil resolusi `sku_pesanan` → daftar SKU dasar (tanpa suffix ukuran).

    Attribut flag (`matched_set_keyword`, `used_database`, `used_dynamic`)
    sengaja diekspos agar caller bisa mereproduksi logging diagnostik
    seperti versi inline sebelum refactor.
    """
    is_bundle: bool
    sku_dasar_list: List[str] = field(default_factory=list)
    sku_bundle_dasar: str = ''
    matched_set_keyword: bool = False
    used_database: bool = False
    used_dynamic: bool = False


def _lookup_database_bundle(sku_bundle_dasar: str, ukuran: str, df_database) -> List[str]:
    """Lookup `sku_bundling_base` / `sku_bundling_base_bs` di DataFrame database."""
    if df_database is None:
        return []
    kolom_cari = 'sku_bundling_base_bs' if ukuran == 'BS' else 'sku_bundling_base'
    if kolom_cari not in df_database.columns:
        return []
    return df_database[df_database[kolom_cari] == sku_bundle_dasar]['sku_individu_base'].tolist()


def resolve_bundle(sku_pesanan: str, ukuran: str, df_database=None) -> BundleResolution:
    """
    Tentukan apakah SKU adalah bundle dan kembalikan daftar SKU dasarnya.

    Args:
        sku_pesanan: SKU lengkap dengan suffix ukuran (mis. `GK-ANM-SET-81-90-S`).
        ukuran: Salah satu dari `'L'`, `'S'`, `'M'`, `'BS'`.
        df_database: DataFrame opsional dengan kolom
            `sku_bundling_base`, `sku_bundling_base_bs`, `sku_individu_base`.
            Boleh `None` (mis. saat dipanggil dari stasiun_sortir tanpa DB).

    Urutan resolusi (kompatibel dgn `proses_data` versi inline):
      1. Jika token `-SET-` muncul → bundle. Komponen di-resolve via DB →
         lalu `parse_dynamic_sku` sebagai fallback.
      2. Else jika ukuran ∈ {S, M, BS} → coba match DB bundle. Jika gagal,
         coba `parse_dynamic_sku`. Jika keduanya gagal → **bukan bundle**
         (item tunggal S/M/BS).
      3. Lainnya (mis. ukuran=L) → item tunggal.

    Untuk kasus bundle, list dipotong sesuai `ATURAN_BUNDLE[ukuran]` jika
    ukuran tersebut diatur (S=5, M=5, BS=10).

    Returns:
        BundleResolution. Untuk bundle yang gagal di-resolve komponennya,
        `is_bundle=True` tapi `sku_dasar_list=[]` — caller boleh mencatat
        sebagai resep gagal.
    """
    sku_bundle_dasar = sku_pesanan.rsplit('-', 1)[0]
    res = BundleResolution(is_bundle=False, sku_bundle_dasar=sku_bundle_dasar)

    has_set = "-SET-" in sku_pesanan.upper()

    if has_set:
        res.is_bundle = True
        res.matched_set_keyword = True
    elif ukuran in UKURAN_BUNDLE_CAPABLE:
        db_list = _lookup_database_bundle(sku_bundle_dasar, ukuran, df_database)
        if db_list:
            res.is_bundle = True
            res.sku_dasar_list = db_list
            res.used_database = True
        else:
            dyn_list = parse_dynamic_sku(sku_bundle_dasar)
            if dyn_list:
                res.is_bundle = True
                res.sku_dasar_list = dyn_list
                res.used_dynamic = True

    # Path bundle yg komponennya belum terisi (terutama dari cabang SET).
    if res.is_bundle and not res.sku_dasar_list:
        db_list = _lookup_database_bundle(sku_bundle_dasar, ukuran, df_database)
        if db_list:
            res.sku_dasar_list = db_list
            res.used_database = True
        else:
            dyn_list = parse_dynamic_sku(sku_bundle_dasar)
            if dyn_list:
                res.sku_dasar_list = dyn_list
                res.used_dynamic = True

    if res.is_bundle:
        if res.sku_dasar_list and ukuran in ATURAN_BUNDLE:
            res.sku_dasar_list = res.sku_dasar_list[:ATURAN_BUNDLE[ukuran]]
    else:
        # Item tunggal (termasuk S/M/BS yang bukan bundle & tidak match dynamic).
        res.sku_dasar_list = [sku_bundle_dasar]

    return res
