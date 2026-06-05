"""
Test fokus: stok gudang untuk BUNDLE harus dianggap 1 set.

Menjalankan `proses_data` SUNGGUHAN dengan data sampel in-memory dan
mock `stok_db` yang meniru log nyata (hanya SKU lead `481M` yang punya
stok). Memverifikasi bahwa SELURUH komponen bundle (481-485) diambil
dari gudang — bukan cuma 481 yang ready lalu 482-485 ikut dicetak.

Tidak butuh Google Sheets / service-account: `load_stok_database`
di-monkeypatch.
"""
import os
import sys
import tempfile

import pandas as pd

import sortir_desain


def _siapkan_master(folder, sku_files):
    """Buat file .cdr dummy di folder master."""
    for nama in sku_files:
        with open(os.path.join(folder, nama), 'w', encoding='utf-8') as f:
            f.write('dummy cdr')


def jalankan(nama_kasus, df_pesanan, mock_stok, master_files):
    """Jalankan proses_data 1x dan kembalikan (log_lines, copied_files)."""
    tmp = tempfile.mkdtemp(prefix='ganci_test_')
    folder_master = os.path.join(tmp, 'master')
    folder_output = os.path.join(tmp, 'output')
    os.makedirs(folder_master, exist_ok=True)
    os.makedirs(folder_output, exist_ok=True)
    _siapkan_master(folder_master, master_files)

    file_pesanan = os.path.join(tmp, 'pesanan.xlsx')
    file_database = os.path.join(tmp, 'database.xlsx')
    df_pesanan.to_excel(file_pesanan, index=False)
    # df_database kosong tapi punya kolom yg diharapkan resolver bundle.
    pd.DataFrame(columns=['sku_bundling_base', 'sku_bundling_base_bs', 'sku_individu_base']).to_excel(
        file_database, index=False
    )

    # Monkeypatch loader stok → kembalikan mock (hindari Google Sheets).
    sortir_desain.load_stok_database = lambda *a, **k: dict(mock_stok)

    log_lines = []
    proses_selesai = {}

    def log_cb(msg, tag=None):
        log_lines.append(msg)

    def progress_cb(cur, total):
        pass

    def finish_cb(ok):
        proses_selesai['ok'] = ok

    sortir_desain.proses_data(
        file_pesanan=file_pesanan,
        file_database=file_database,
        folder_master_desain=folder_master,
        folder_output=folder_output,
        log_callback=log_cb,
        progress_callback=progress_cb,
        finish_callback=finish_cb,
        spreadsheet_id='DUMMY',     # non-empty → loader (mock) dipanggil
        json_key_path='DUMMY',
        mode=1,
        cek_stok_aktif=True,
    )

    # Kumpulkan file .cdr yg benar-benar disalin ke output (mode 1 = flat).
    out_dir = os.path.join(folder_output, [d for d in os.listdir(folder_output) if 'LAYOUT_MASAL' in d][0])
    copied = sorted(f for f in os.listdir(out_dir) if f.lower().endswith('.cdr'))

    return log_lines, copied


def main():
    gagal = 0

    # ── KASUS 1: Bundle M, stok set tersedia (mirip log user) ──────────
    # Gudang hanya punya entri lead '481M' = 4 set. Komponen 482-485 TIDAK
    # ada entri stok. Master desain punya semua file 481-485.
    mock_stok = {
        '481M': {
            'stok': 4,
            'nama_produk': 'Gantungan Kunci Keychain Acrylic IDOL KPOP CORTIS PREMIUM 3MM',
            'sku_asli': 'GK-ATM-0000481-M',
            'sku_pendek': '481M',
        }
    }
    df_pes = pd.DataFrame([{'resi': 'RESI-A', 'sku': 'GK-ATM-SET-481-485-M', 'jumlah': 1}])
    master = [f'GK-ATM-000048{n}-M.cdr' for n in range(1, 6)]  # 481..485
    log, copied = jalankan('bundle-ready', df_pes, mock_stok, master)

    print('=' * 60)
    print('KASUS 1 — Bundle M, 1 set ready di gudang (481M=4)')
    print('=' * 60)
    bundle_ready = any('Bundle Ready' in l for l in log)
    ada_copy = len(copied) > 0
    print(f'  Log "Bundle Ready"        : {bundle_ready}  (harus True)')
    print(f'  File .cdr disalin ke output: {copied}  (harus [] — semua dari gudang)')
    if not bundle_ready:
        print('  ❌ GAGAL: bundle tidak terdeteksi ready'); gagal += 1
    elif ada_copy:
        print('  ❌ GAGAL: ada file dicetak padahal bundle harusnya full-gudang'); gagal += 1
    else:
        print('  ✅ LULUS: seluruh set diambil dari gudang, tidak ada cetak')

    # ── KASUS 2: Bundle M, stok set TIDAK cukup → semua dicetak ────────
    mock_stok2 = {
        '481M': {'stok': 0, 'nama_produk': 'X', 'sku_asli': 'GK-ATM-0000481-M', 'sku_pendek': '481M'}
    }
    log2, copied2 = jalankan('bundle-habis', df_pes, mock_stok2, master)
    print()
    print('=' * 60)
    print('KASUS 2 — Bundle M, stok set habis (481M=0)')
    print('=' * 60)
    print(f'  File .cdr disalin ke output: {sorted(copied2)}  (harus 5 file 481-485)')
    if len(copied2) == 5:
        print('  ✅ LULUS: bundle tak cukup → seluruh set dicetak')
    else:
        print('  ❌ GAGAL: jumlah file cetak tidak 5'); gagal += 1

    # ── KASUS 3: Item tunggal L tidak terpengaruh ──────────────────────
    mock_stok3 = {
        '500L': {'stok': 3, 'nama_produk': 'Single L', 'sku_asli': 'GK-ATM-0000500-L', 'sku_pendek': '500L'}
    }
    df_pes3 = pd.DataFrame([{'resi': 'RESI-C', 'sku': 'GK-ATM-0000500-L', 'jumlah': 1}])
    master3 = ['GK-ATM-0000500-L.cdr']
    log3, copied3 = jalankan('single-L', df_pes3, mock_stok3, master3)
    print()
    print('=' * 60)
    print('KASUS 3 — Item tunggal L, stok ready (500L=3)')
    print('=' * 60)
    stok_ready = any('Stok Ready' in l for l in log3)
    print(f'  Log "Stok Ready" (pcs)     : {stok_ready}  (harus True)')
    print(f'  File .cdr disalin ke output : {copied3}  (harus [] — dari gudang)')
    if stok_ready and not copied3:
        print('  ✅ LULUS: item tunggal tetap per-SKU seperti semula')
    else:
        print('  ❌ GAGAL: perilaku item tunggal berubah'); gagal += 1

    print()
    print('=' * 60)
    if gagal == 0:
        print('🎉 SEMUA KASUS LULUS')
    else:
        print(f'💥 {gagal} KASUS GAGAL')
    print('=' * 60)
    return 1 if gagal else 0


if __name__ == '__main__':
    sys.exit(main())
