"""
Bangun *demand map* dari ``pesanan_harian.xlsx`` untuk stasiun sortir.

Demand map menjawab pertanyaan stasiun sortir: **"charm dengan SKU X dipesan
oleh resi mana saja, dan masing-masing butuh berapa unit?"**. Logika ini
mengikuti pola yang sama dengan :func:`sortir_desain.proses_data` — bundle
SET di-expand jadi SKU individu, ukuran S/M/BS dipotong sesuai
``ATURAN_BUNDLE`` — sehingga jumlah charm fisik yang harus di-scan packer
identik dengan jumlah file desain yang di-output oleh aplikasi sortir desain.

Output utama :func:`load_pesanan_demand`:

* ``demand[full_sku] = [{resi, sku_pesanan_asli, remaining}, …]``
* ``resi_summary[resi] = {total_charm, sku_breakdown: {full_sku: qty}}``
* ``skipped`` daftar baris di-skip + alasan

Slot assignment **tidak** dihasilkan di sini — slot ditentukan oleh
operator lewat Setup mode di stasiun_sortir (scan stiker resi → assign
slot berikutnya). Lihat :mod:`core.state` untuk slot management.

CUSTOM SKU di-skip karena tidak punya barcode generic (operator pakai
desain custom secara manual, di luar workflow scan).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Tuple

import pandas as pd

from core.sku_utils import (
    extract_ukuran,
    pad_sku_unit,
    resolve_bundle,
)


def load_pesanan_demand(
    file_pesanan: str,
    file_database: str = '',
) -> Tuple[Dict[str, List[dict]], Dict[str, dict], List[str]]:
    """
    Baca pesanan + database SKU → demand map untuk stasiun sortir.

    Args:
        file_pesanan: path .xlsx dgn kolom ``resi``, ``sku``, ``jumlah``.
        file_database: path .xlsx dgn kolom ``sku_bundling_base``,
            ``sku_bundling_base_bs``, ``sku_individu_base``. Kosong → bundle
            yang butuh DB lookup tidak akan ter-resolve (akan masuk
            ``skipped``); bundle SET dgn range numerik tetap bisa.

    Returns:
        Tuple ``(demand, resi_summary, skipped)``. Lihat docstring modul
        untuk struktur masing-masing. Slot assignment dilakukan di luar
        sini (lihat :mod:`core.state`).

    Raises:
        FileNotFoundError jika ``file_pesanan`` tidak ada (file_database
        opsional).
    """
    df_pesanan = pd.read_excel(file_pesanan)

    df_database = None
    if file_database:
        try:
            df_database = pd.read_excel(file_database)
        except FileNotFoundError:
            df_database = None  # boleh tidak ada — bundle DB lookup di-skip

    demand: Dict[str, List[dict]] = defaultdict(list)
    resi_summary: Dict[str, dict] = defaultdict(
        lambda: {'total_charm': 0, 'sku_breakdown': defaultdict(int)}
    )
    skipped: List[str] = []

    for index, row in df_pesanan.iterrows():
        try:
            resi = str(row['resi']).strip()
            sku_pesanan = str(row['sku']).strip()
            sku_pesanan = pad_sku_unit(sku_pesanan)
            jumlah = int(row['jumlah'])
        except Exception as e:
            skipped.append(f"Baris {index + 2}: format salah ({e})")
            continue

        ukuran = extract_ukuran(sku_pesanan)
        if not ukuran:
            skipped.append(
                f"Baris {index + 2}: SKU '{sku_pesanan}' tidak punya ukuran valid (L/S/M/BS)"
            )
            continue

        if sku_pesanan.upper().startswith('CUSTOM'):
            # Desain custom dikerjakan manual — tidak ada barcode di stok untuk di-scan.
            skipped.append(f"Baris {index + 2}: SKU CUSTOM ({sku_pesanan}) — skip dari scan station")
            continue

        res = resolve_bundle(sku_pesanan, ukuran, df_database)
        if not res.sku_dasar_list:
            skipped.append(
                f"Baris {index + 2}: gagal resolve bundle '{sku_pesanan}' "
                f"(resep tidak ada di database & format range tidak terbaca)"
            )
            continue

        for sku_dasar in res.sku_dasar_list:
            full_sku = f"{sku_dasar}-{ukuran}"

            existing = next(
                (e for e in demand[full_sku] if e['resi'] == resi),
                None,
            )
            if existing:
                existing['remaining'] += jumlah
            else:
                demand[full_sku].append({
                    'resi':              resi,
                    'sku_pesanan_asli':  sku_pesanan,
                    'remaining':         jumlah,
                })

            resi_summary[resi]['total_charm'] += jumlah
            resi_summary[resi]['sku_breakdown'][full_sku] += jumlah

    # Konversi nested defaultdict ke dict biasa supaya hasil "dingin" & predictable.
    resi_summary_clean = {
        r: {
            'total_charm':   info['total_charm'],
            'sku_breakdown': dict(info['sku_breakdown']),
        }
        for r, info in resi_summary.items()
    }

    return dict(demand), resi_summary_clean, skipped


def total_charm_outstanding(demand: Dict[str, List[dict]]) -> int:
    """Total unit charm yang masih harus di-scan (sum remaining > 0)."""
    return sum(e['remaining'] for entries in demand.values() for e in entries if e['remaining'] > 0)


def total_charm_initial(resi_summary: Dict[str, dict]) -> int:
    """Total unit charm yang DI-EXPECT di awal (acuan untuk progress bar)."""
    return sum(info['total_charm'] for info in resi_summary.values())
