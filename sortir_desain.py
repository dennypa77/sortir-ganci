import pandas as pd
import os
import shutil
from datetime import datetime
import re
import time
import json
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
import threading
from collections import defaultdict

# ── Utilitas SKU di-share dengan stasiun_sortir.py ──────────────────────────
from core.sku_utils import (
    ATURAN_BUNDLE,
    parse_dynamic_sku,
    normalize_sku,
    to_ultra_short_sku,
    pad_sku_unit,
    resolve_bundle,
)
from core.version import get_version
from core.updater import consume_update_flag
from core.config import (
    load_config,
    save_config,
    DEFAULT_CONFIG,
    TEMPLATE_CONFIG,
    USER_CONFIG_FILE,
)

# ── Google Sheets (opsional, install: pip install gspread google-auth) ──────
try:
    import gspread
    from google.oauth2.service_account import Credentials as GSCredentials
    GSPREAD_AVAILABLE = True
except ImportError:
    GSPREAD_AVAILABLE = False

# Config loading di-share dengan stasiun_sortir.py via core.config (lihat di-import).

def build_file_index(folder_akar, log_callback, status_callback=None):
    """
    Scan folder master 1x saja, simpan semua file ke dict.
    Index juga menyimpan alias ATM <-> ANM agar pencarian tetap
    berhasil meski nama file di folder berbeda dengan format pesanan.
    """
    log_callback("🔍 Membangun index file dari Folder Master Desain...")
    if status_callback:
        status_callback("🔍 Memulai indexing file master...")
        
    t0 = time.perf_counter()
    index = {}
    count_files = 0
    
    for root, dirs, files in os.walk(folder_akar):
        for fname in files:
            count_files += 1
            if count_files % 3000 == 0 and status_callback:
                status_callback(f"🔍 Mengindeks file... ({count_files:,} file ditemukan)")
                
            index[fname] = os.path.join(root, fname)
            # Tambah alias ATM <-> ANM supaya pencarian tidak gagal
            # karena perbedaan prefix antara file & pesanan
            if 'GK-ATM-' in fname:
                alias = fname.replace('GK-ATM-', 'GK-ANM-', 1)
                if alias not in index:   # jangan timpa file yang benar-benar ANM
                    index[alias] = os.path.join(root, fname)
            elif 'GK-ANM-' in fname:
                alias = fname.replace('GK-ANM-', 'GK-ATM-', 1)
                if alias not in index:
                    index[alias] = os.path.join(root, fname)
    elapsed = time.perf_counter() - t0
    file_asli = sum(1 for v in index.values() if os.path.basename(v) in index)
    log_callback(f"✅ Index selesai: {len(index):,} entri (termasuk alias ATM/ANM) dalam {elapsed:.1f} detik.\n")
    return index


def sync_to_google_sheets(spreadsheet_id, json_key_path, pesanan_grouped, log_callback):
    """
    Sinkronisasi data pesanan ke Google Sheets (append-only).
    Dipanggil SETELAH seluruh proses file dan log Excel selesai 100%.
    Return: True jika sukses atau sengaja dilewati, False jika error koneksi/API.
    """
    if not GSPREAD_AVAILABLE:
        log_callback(
            "⚠️  [GSheets] Library 'gspread' belum terpasang.\n"
            "   Jalankan: pip install gspread google-auth",
            tag="warn"
        )
        return False

    # Belum dikonfigurasi → skip dengan diam-diam (bukan error)
    if not spreadsheet_id or not json_key_path:
        return True

    if not os.path.exists(json_key_path):
        log_callback(
            f"⚠️  [GSheets] File JSON Key tidak ditemukan:\n   {json_key_path}",
            tag="warn"
        )
        return False

    try:
        log_callback("\n" + "─" * 55)
        log_callback("☁️  Mengirim data ke Google Sheets...")

        SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
        creds  = GSCredentials.from_service_account_file(json_key_path, scopes=SCOPES)
        client = gspread.authorize(creds)

        spreadsheet = client.open_by_key(spreadsheet_id)

        # Buka sheet 'Data_Sales', buat otomatis jika belum ada
        try:
            sheet = spreadsheet.worksheet('Data_Sales')
        except gspread.exceptions.WorksheetNotFound:
            sheet = spreadsheet.add_worksheet(title='Data_Sales', rows=1000, cols=5)
            sheet.append_row(
                ['Tanggal', 'Resi', 'SKU', 'Jumlah', 'Ukuran'],
                value_input_option='USER_ENTERED'
            )
            log_callback("☁️  Sheet 'Data_Sales' baru berhasil dibuat.")

        # Siapkan baris data dari pesanan_grouped
        tanggal = datetime.now().strftime('%Y-%m-%d')
        rows_to_append = []
        for resi, sku_dict in pesanan_grouped.items():
            for sku_pesanan, jumlah in sku_dict.items():
                ukuran = sku_pesanan.split('-')[-1].upper()
                rows_to_append.append([tanggal, resi, sku_pesanan, jumlah, ukuran])

        if rows_to_append:
            expected = len(rows_to_append)
            response = sheet.append_rows(rows_to_append, value_input_option='USER_ENTERED')

            # Verifikasi jumlah baris yang benar-benar terupload via response API.
            # Response dari Sheets API: {'updates': {'updatedRows': N, ...}, ...}
            # Tujuan: deteksi partial upload akibat rate limit service account.
            updated = None
            try:
                updated = int(response.get('updates', {}).get('updatedRows'))
            except (AttributeError, TypeError, ValueError):
                updated = None

            if updated is None:
                log_callback(
                    f"☁️  ✅ {expected} baris dikirim ke Google Sheets "
                    f"(persentase tidak bisa diverifikasi — response API tidak standar).",
                    tag="warn"
                )
            elif updated == expected:
                log_callback(
                    f"☁️  ✅ {updated}/{expected} baris berhasil dikirim ke Google Sheets "
                    f"(100% terupload).",
                    tag="success"
                )
            elif updated > 0:
                pct = (updated / expected) * 100
                missing = expected - updated
                log_callback(
                    f"☁️  ⚠️  PARTIAL UPLOAD: {updated}/{expected} baris terupload "
                    f"({pct:.1f}%) — {missing} baris TIDAK terkirim.\n"
                    f"   Kemungkinan kena limit service account "
                    f"(quota Google Sheets API: 60 write/menit, 300 write/menit/project).\n"
                    f"   Cek manual sheet 'Data_Sales' & upload ulang baris yang hilang bila perlu.",
                    tag="warn"
                )
            else:
                log_callback(
                    f"☁️  ❌ 0/{expected} baris terupload (0%) — semua baris GAGAL.\n"
                    f"   Kemungkinan kena limit service account atau koneksi bermasalah.",
                    tag="error"
                )
                return False
        else:
            log_callback("☁️  Tidak ada data baru untuk dikirim.", tag="warn")

        return True

    except Exception as e:
        log_callback(f"☁️  ❌ Gagal sync ke Google Sheets: {e}", tag="error")
        return False


def load_stok_database(spreadsheet_id, json_key_path, log_callback):
    """
    Membaca tab 'DATABASE_PRODUK' dari Google Sheets SATU KALI,
    mengembalikan dictionary {sku_master: {'stok': int, 'nama_produk': str}}
    untuk pengecekan stok yang cepat tanpa bolak-balik panggil API.
    Return None jika tidak dikonfigurasi atau gagal.
    """
    if not GSPREAD_AVAILABLE:
        return None
    if not spreadsheet_id or not json_key_path:
        return None
    if not os.path.exists(json_key_path):
        return None

    try:
        log_callback("\n" + "─" * 55)
        log_callback("📦 Membaca database stok dari Google Sheets...")

        SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
        creds  = GSCredentials.from_service_account_file(json_key_path, scopes=SCOPES)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(spreadsheet_id)

        try:
            ws = spreadsheet.worksheet('DATABASE_PRODUK')
        except gspread.exceptions.WorksheetNotFound:
            log_callback("⚠️  [Stok] Tab 'DATABASE_PRODUK' tidak ditemukan di spreadsheet.", tag="warn")
            return None

        # Baca semua nilai sekaligus (1 API call)
        all_values = ws.get_all_values()
        if len(all_values) < 2:
            log_callback("⚠️  [Stok] Tab 'DATABASE_PRODUK' kosong atau hanya berisi header.", tag="warn")
            return None

        stok_dict = {}
        header = [str(h).strip() for h in all_values[0]]

        # Cari indeks kolom berdasarkan nama header (Kolom A=0, C=2, I=8)
        # Fallback ke indeks posisi jika header tidak ditemukan
        def col_idx(names, fallback):
            for n in names:
                if n in header:
                    return header.index(n)
            return fallback

        idx_sku   = col_idx(['SKU Master', 'sku_master', 'SKU'], 0)      # Kolom A
        idx_sku_pendek = col_idx(['SKU Pendek', 'sku_pendek'], 1)        # Kolom B
        idx_nama  = col_idx(['Nama Produk', 'nama_produk', 'Nama'], 2)   # Kolom C
        idx_stok  = col_idx(['Stok Saat Ini', 'stok_saat_ini', 'Stok'], 8)  # Kolom I

        for row in all_values[1:]:
            if not row:
                continue
            sku = str(row[idx_sku]).strip() if idx_sku < len(row) else ''
            if not sku:
                continue
            sku_pendek = str(row[idx_sku_pendek]).strip() if idx_sku_pendek < len(row) else ''
            nama = str(row[idx_nama]).strip() if idx_nama < len(row) else ''
            stok_raw = str(row[idx_stok]).strip() if idx_stok < len(row) else '0'
            try:
                stok = int(float(stok_raw)) if stok_raw else 0
            except ValueError:
                stok = 0
            
            sku_norm = normalize_sku(sku)
            data_stok = {
                'stok': stok, 
                'nama_produk': nama, 
                'sku_asli': sku, 
                'sku_pendek': sku_pendek
            }
            # Simpan berdasarkan format Ultra-Short sebagai target utama pencarian jika tersedia
            if sku_pendek:
                stok_dict[sku_pendek.upper()] = data_stok
            # Simpan juga berdasarkan sku_norm sebagai fallback
            stok_dict[sku_norm] = data_stok

        log_callback(
            f"✅ Database stok berhasil dimuat: {len(stok_dict):,} produk unik.",
            tag="success"
        )
        log_callback("─" * 55)
        return stok_dict

    except Exception as e:
        log_callback(f"⚠️  [Stok] Gagal membaca DATABASE_PRODUK: {repr(e)}", tag="warn")
        return None


def log_keluar_gudang(spreadsheet_id, json_key_path, ambil_gudang_log, log_callback):
    """
    Catat semua pengambilan dari gudang ke tab 'LOG_KELUAR'.
    ambil_gudang_log: list of dict {sku_master, jumlah, nama_produk}
    """
    if not GSPREAD_AVAILABLE:
        return
    if not spreadsheet_id or not json_key_path:
        return
    if not ambil_gudang_log:
        return
    if not os.path.exists(json_key_path):
        return

    try:
        log_callback("\n" + "─" * 55)
        log_callback("📝 Mencatat pengambilan gudang ke LOG_KELUAR...")

        SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
        creds  = GSCredentials.from_service_account_file(json_key_path, scopes=SCOPES)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(spreadsheet_id)

        try:
            ws_log = spreadsheet.worksheet('LOG_KELUAR')
        except gspread.exceptions.WorksheetNotFound:
            ws_log = spreadsheet.add_worksheet(title='LOG_KELUAR', rows=1000, cols=5)
            ws_log.append_row(
                ['Tanggal', 'SKU Pendek', 'SKU Master', 'Jumlah Keluar', 'Keterangan'],
                value_input_option='USER_ENTERED'
            )
            log_callback("📝 Tab 'LOG_KELUAR' baru berhasil dibuat.")

        tanggal = datetime.now().strftime('%Y-%m-%d')
        rows = []
        for item in ambil_gudang_log:
            rows.append([
                tanggal,
                item.get('sku_pendek', ''),
                None,  # Gunakan None kembali agar Google Sheets TIDAK menimpa ARRAYFORMULA Anda!
                item['jumlah'],
                'Ambil dari Gudang'
            ])

        ws_log.append_rows(rows, value_input_option='USER_ENTERED')
        log_callback(
            f"✅ {len(rows)} baris log gudang berhasil dicatat ke LOG_KELUAR.",
            tag="success"
        )

    except Exception as e:
        log_callback(f"⚠️  [LOG_KELUAR] Gagal mencatat log gudang: {repr(e)}", tag="warn")


def proses_data(file_pesanan, file_database, folder_master_desain, folder_output,
                log_callback, progress_callback, finish_callback,
                status_callback=None, cloud_warn_callback=None,
                spreadsheet_id='', json_key_path='', mode=1,
                cek_stok_aktif=True, gudang_log_callback=None,
                tulis_log_keluar=False):
    """
    mode=1 : Layout Masal — semua file dalam 1 folder flat, tidak ada subfolder bundle.
    mode=2 : Sortir per Resi — file dikelompokkan dalam subfolder per nomor resi.
    """
    waktu_mulai = time.perf_counter()
    log_callback("─" * 60)
    log_callback("  🚀  MEMULAI PROSES PENGUMPULAN DESAIN")
    log_callback("─" * 60)

    def set_status(txt):
        if status_callback:
            status_callback(txt)

    nama_folder_harian = datetime.now().strftime('%Y-%m-%d')

    if mode == 1:
        path_output_utama = os.path.join(folder_output, f"{nama_folder_harian}_LAYOUT_MASAL")
        label_mode = "Layout Masal"
    else:
        path_output_utama = os.path.join(folder_output, f"{nama_folder_harian}_SORTIR_PER_RESI")
        label_mode = "Sortir per Resi"

    log_callback(f"   Mode Output    : {label_mode}")
    log_callback(f"   Folder Output  : {os.path.basename(path_output_utama)}")

    set_status("🗑  Membersihkan folder output lama...")
    if os.path.exists(path_output_utama):
        log_callback(f"♻️  Menghapus folder lama: {os.path.basename(path_output_utama)} (harap tunggu, folder di cloud bisa lambat...)")
        shutil.rmtree(path_output_utama, ignore_errors=True)
        log_callback(f"   ✅ Selesai dihapus: {os.path.basename(path_output_utama)}")

    set_status("📂  Menyiapkan folder output...")
    log_callback("📂 Membuat folder output baru...")
    os.makedirs(path_output_utama, exist_ok=True)
    log_callback(f"✅ Folder output siap.")

    log_data = []
    label_data = []
    custom_data = []  # {resi, sku, jumlah} untuk SKU custom
    nama_file_log = os.path.join(folder_output, f"log_{nama_folder_harian}.xlsx")
    nama_file_label = os.path.join(folder_output, f"label_untuk_cetak_{nama_folder_harian}.xlsx")

    set_status("📖  Membaca file Excel...")
    log_callback("📖 Membaca file Excel pesanan & database SKU...")
    try:
        df_pesanan = pd.read_excel(file_pesanan)
        df_database = pd.read_excel(file_database)
        log_callback("✅ File pesanan dan database SKU berhasil dibaca.\n")
    except FileNotFoundError as e:
        log_callback(f"❌ ERROR: File tidak ditemukan! Pastikan Anda telah memilih file yang benar:\n   {e.filename}", tag="error")
        finish_callback(False)
        return
    except Exception as e:
        log_callback(f"❌ ERROR membaca file Excel: {e}", tag="error")
        finish_callback(False)
        return

    # ── Bangun index file 1x saja ──────────────────────────────
    file_index = build_file_index(folder_master_desain, log_callback, status_callback)

    # ── Baca database stok dari Google Sheets 1x saja ──────────
    set_status("📦  Membaca database stok gudang...")
    stok_db = {}
    if cek_stok_aktif and spreadsheet_id and json_key_path:
        stok_db = load_stok_database(spreadsheet_id, json_key_path, log_callback) or {}
    elif not spreadsheet_id or not json_key_path:
        pass  # Belum dikonfigurasi, skip pengecekan stok

    # Akumulasi log pengambilan dari gudang
    ambil_gudang_log = []  # list of {sku_master, jumlah, nama_produk}

    # Helper lookup stok 3-tier (urutan dipertahankan dari versi inline):
    #   1. format Ultra-Short (mis. '481M')
    #   2. fallback sku_norm dgn ukuran
    #   3. fallback sku_norm tanpa ukuran (DB kadang simpan tanpa '-L' dsb)
    def cari_info_stok(sku_master_key, sku_dasar):
        info = stok_db.get(to_ultra_short_sku(sku_master_key))
        if info is None:
            info = stok_db.get(normalize_sku(sku_master_key))
        if info is None:
            info = stok_db.get(normalize_sku(sku_dasar))
        return info

    # =========================================================
    # FIX BUG: Gabungkan baris dengan Resi + SKU yang SAMA
    # Sebelumnya: tiap baris diproses terpisah, file saling timpa
    # Sekarang: groupby Resi lalu akumulasi jumlah per SKU unik
    # =========================================================
    pesanan_grouped = defaultdict(lambda: defaultdict(int))  # {resi: {sku: total_jumlah}}
    pesanan_order = []  # Menjaga urutan resi agar log rapi

    for index, row in df_pesanan.iterrows():
        try:
            resi = str(row['resi']).strip()
            sku_pesanan = str(row['sku']).strip()
            
            # Pad SKU tunggal -L/-S/-M/-BS jadi 7 digit (lihat core.sku_utils.pad_sku_unit).
            sku_pesanan = pad_sku_unit(sku_pesanan)

            jumlah = int(row['jumlah'])
            ukuran = sku_pesanan.split('-')[-1].upper()

            if ukuran not in ['L', 'S', 'M', 'BS']:
                log_callback(f"⚠️  SKIP Baris {index+2}: SKU '{sku_pesanan}' ukuran '{ukuran}' tidak valid.", tag="warn")
                continue

            # Akumulasi jumlah untuk resi+sku yang sama
            if resi not in pesanan_grouped or sku_pesanan not in pesanan_grouped[resi]:
                pesanan_order.append((resi, sku_pesanan))
            pesanan_grouped[resi][sku_pesanan] += jumlah

        except Exception as e:
            log_callback(f"⚠️  SKIP Baris {index+2} format salah: {e}", tag="warn")
            continue

    total_resi_unik = len(pesanan_grouped)
    log_callback(f"📋 Total Resi Unik  : {total_resi_unik}")
    log_callback(f"📋 Total Baris Excel: {len(df_pesanan)}\n")

    # Proses berdasarkan resi unik, bukan baris excel
    resi_list = list(pesanan_grouped.keys())
    
    for idx_resi, resi in enumerate(resi_list):
        progress_callback(idx_resi + 1, total_resi_unik)
        log_callback(f"\n{'━'*55}")
        log_callback(f"📦 RESI: {resi}  ({idx_resi+1}/{total_resi_unik})")
        log_callback(f"{'━'*55}")

        path_folder_resi = None
        if mode == 2:
            path_folder_resi = os.path.join(path_output_utama, resi)
            os.makedirs(path_folder_resi, exist_ok=True)

        # Counter per-file dalam resi ini
        file_counter_resi = defaultdict(int)

        for sku_pesanan, jumlah in pesanan_grouped[resi].items():
            ukuran = sku_pesanan.split('-')[-1].upper()

            log_callback(f"\n  ➤ SKU: {sku_pesanan}  |  Qty: {jumlah}")

            # ── Deteksi SKU custom → skip pencarian file ────────
            if sku_pesanan.upper().startswith('CUSTOM'):
                log_callback(f"  🎨 [CUSTOM] Desain custom — tidak dicari di master.", tag="warn")
                for qty_i in range(1, jumlah + 1):
                    custom_data.append({
                        'Resi': resi,
                        'SKU Custom': sku_pesanan,
                        'Qty Unit': qty_i,
                        'Ukuran': ukuran,
                    })
                continue

            # Resolusi bundle/single via core.sku_utils.resolve_bundle.
            # Caller (di sini) tetap bertanggung jawab atas log diagnostik
            # supaya stream log identik dengan versi inline sebelum refactor.
            res = resolve_bundle(sku_pesanan, ukuran, df_database)
            is_bundle = res.is_bundle
            sku_dasar_list = list(res.sku_dasar_list)
            sku_bundle_dasar = res.sku_bundle_dasar

            if is_bundle:
                # Cabang S/M/BS yang dapat komponen via parse_dynamic_sku.
                if res.used_dynamic and not res.matched_set_keyword:
                    log_callback(f"  🔍 [Dinamis] Range terdeteksi dari: {sku_bundle_dasar}")
                # Cabang SET yang melewati lookup DB (silent jika DB hit; pesan
                # ini muncul saat DB miss → fallback ke parse_dynamic_sku).
                elif res.matched_set_keyword and not res.used_database:
                    log_callback(f"  🔍 [Dinamis] Membaca range angka dari: {sku_bundle_dasar}")

                if not sku_dasar_list:
                    log_callback(f"  ❌ Gagal membuat resep bundle '{sku_bundle_dasar}'.", tag="error")
                    log_data.append({
                        'Waktu': datetime.now().strftime('%H:%M:%S'),
                        'Resi': resi, 'SKU Pesanan': sku_pesanan,
                        'File Dicari': '-', 'Status': 'GAGAL',
                        'Keterangan': 'Resep tidak ada di database dan format tidak bisa diparse.'
                    })
                    continue

                if ukuran in ATURAN_BUNDLE:
                    log_callback(f"  📐 Bundle ukuran '{ukuran}' → ambil {len(sku_dasar_list)} item.")
            else:
                # Item tunggal — termasuk ukuran S/M/BS yang bukan bundle.
                if ukuran in ['S', 'M', 'BS']:
                    log_callback(f"  🏷️  SKU Tunggal ukuran {ukuran} terdeteksi (bukan bundle).")
                else:
                    log_callback(f"  🏷️  SKU Tunggal (-L) terdeteksi.")

            # =====================================================
            # Cek Stok Gudang (SEBELUM loop QTY)
            #
            # Bundle (S/M/BS) disimpan di gudang sebagai SATU set di bawah
            # SKU perwakilan (lead) — ultra-short SET == ultra-short komponen
            # pertama, mis. 'GK-ATM-SET-481-485-M' → '481M'. Karena itu untuk
            # bundle stok dicek SEKALI: kalau 1 set ready, SEMUA komponen
            # ditandai ambil-gudang & stok dikurangi 1× per bundle. Bundle
            # tidak boleh diambil sebagian — kalau set kurang, semua dicetak.
            # Item tunggal (L, atau S/M/BS non-bundle) tetap dicek per-SKU.
            # =====================================================
            gudang_flag = {}

            if is_bundle:
                # Kunci perwakilan = SKU set apa adanya (ultra-short identik
                # dgn komponen pertama); komponen pertama dipakai utk fallback.
                sku_perwakilan = f"{sku_dasar_list[0]}-{ukuran}"
                info_stok = cari_info_stok(sku_pesanan, sku_dasar_list[0])
                if info_stok is None:
                    info_stok = cari_info_stok(sku_perwakilan, sku_dasar_list[0])

                stok_tersedia = info_stok['stok'] if info_stok else 0
                nama_produk   = info_stok['nama_produk'] if info_stok else ''
                sku_asli      = info_stok['sku_asli'] if info_stok else sku_pesanan
                sku_pendek    = info_stok['sku_pendek'] if info_stok else to_ultra_short_sku(sku_pesanan)

                if stok_db and info_stok and stok_tersedia >= jumlah:
                    # 1 bundle = 1 unit stok → seluruh komponen ambil dari gudang.
                    for sku_dasar in sku_dasar_list:
                        gudang_flag[sku_dasar] = True
                    info_stok['stok'] -= jumlah  # Kurangi stok memory agar tidak bentrok dgn resi berikutnya

                    msg_gudang = (f"    ✨ [GUDANG] Bundle Ready: {stok_tersedia} set | {sku_pendek} | {nama_produk}.")
                    log_callback(msg_gudang, tag="gudang")

                    if gudang_log_callback:
                        gudang_log_callback(f"📦 Resi {resi} | 🟢 {jumlah} set | {sku_pendek} | {nama_produk} ({sku_asli}) -> Sisa Gudang: {info_stok['stok']}", tag="gudang")

                    ambil_gudang_log.append({
                        'sku_master': sku_asli,
                        'sku_pendek': sku_pendek,
                        'jumlah':     jumlah
                    })
                else:
                    for sku_dasar in sku_dasar_list:
                        gudang_flag[sku_dasar] = False
            else:
                # Item tunggal — cek stok per SKU dasar (perilaku lama).
                for sku_dasar in sku_dasar_list:
                    sku_master_key = f"{sku_dasar}-{ukuran}"
                    info_stok = cari_info_stok(sku_master_key, sku_dasar)

                    stok_tersedia = info_stok['stok'] if info_stok else 0
                    nama_produk   = info_stok['nama_produk'] if info_stok else ''
                    sku_asli      = info_stok['sku_asli'] if info_stok else sku_master_key
                    sku_pendek    = info_stok['sku_pendek'] if info_stok else to_ultra_short_sku(sku_master_key)

                    # Jika stok di memori cukup untuk jumlah pesanan, kurangi & tandai ambil dari gudang
                    if stok_db and info_stok and stok_tersedia >= jumlah:
                        gudang_flag[sku_dasar] = True
                        info_stok['stok'] -= jumlah  # Kurangi stok memory agar tidak bentrok dgn resi berikutnya

                        msg_gudang = (f"    ✨ [GUDANG] Stok Ready: {stok_tersedia} pcs | {sku_pendek} | {nama_produk}.")
                        log_callback(msg_gudang, tag="gudang")

                        if gudang_log_callback:
                            gudang_log_callback(f"📦 Resi {resi} | 🟢 {jumlah} pcs | {sku_pendek} | {nama_produk} ({sku_asli}) -> Sisa Gudang: {info_stok['stok']}", tag="gudang")

                        ambil_gudang_log.append({
                            'sku_master': sku_asli,
                            'sku_pendek': sku_pendek,
                            'jumlah':     jumlah
                        })
                    else:
                        gudang_flag[sku_dasar] = False


            # =====================================================
            # Loop qty: setiap unit mendapat folder/file terpisah
            # =====================================================
            for qty_i in range(1, jumlah + 1):
                if not sku_dasar_list:
                    continue

                for sku_dasar in sku_dasar_list:
                    nama_file_dicari = f"{sku_dasar}-{ukuran}.cdr"
                    path_sumber_file = file_index.get(nama_file_dicari)

                    file_counter_resi[nama_file_dicari] += 1
                    urutan = file_counter_resi[nama_file_dicari]

                    # Jika sudah ditandai ambil dari gudang
                    if gudang_flag.get(sku_dasar):
                        log_data.append({
                            'Waktu': datetime.now().strftime('%H:%M:%S'),
                            'Resi': resi, 'SKU Pesanan': sku_pesanan,
                            'File Dicari': nama_file_dicari,
                            'Status': 'GUDANG', 'Keterangan': 'Diambil dari gudang'
                        })
                        continue  # Lanjut ke item berikutnya tanpa copy fisik

                    status_log = 'GAGAL'
                    ket = "File tidak ditemukan di Master Desain"


                    if path_sumber_file:
                        status_log = 'SUKSES'
                        ket = 'Berhasil disalin'
                        try:
                            if mode == 1:
                                # ── Mode 1: Layout Masal — semua file flat dalam 1 folder ──
                                nama_tujuan = f"{sku_dasar}-{ukuran}_{resi}_{urutan}.cdr"
                                shutil.copy(path_sumber_file,
                                            os.path.join(path_output_utama, nama_tujuan))
                                log_callback(f"    ✅ [OK] {nama_tujuan}", tag="success")
                            else:
                                # ── Mode 2: Sortir per Resi — folder per resi ──
                                nama_audit = f"{sku_dasar}-{ukuran}_{urutan}.cdr"
                                shutil.copy(path_sumber_file,
                                            os.path.join(path_folder_resi, nama_audit))
                                log_callback(f"    ✅ [OK] {nama_audit}", tag="success")

                            label_data.append({'sku_untuk_label': f"{sku_dasar}-{ukuran}"})
                        except Exception as e:
                            status_log = 'GAGAL'
                            ket = f"Error saat copy: {e}"
                            log_callback(f"    ❌ [ERR] {nama_file_dicari}: {e}", tag="error")
                    else:
                        log_callback(f"    ❌ [TIDAK DITEMUKAN] {nama_file_dicari}", tag="error")

                    log_data.append({
                        'Waktu': datetime.now().strftime('%H:%M:%S'),
                        'Resi': resi, 'SKU Pesanan': sku_pesanan,
                        'File Dicari': nama_file_dicari,
                        'Status': status_log, 'Keterangan': ket
                    })

    if log_data:
        log_gudang = [x for x in log_data if x['Status'] == 'GUDANG']
        with pd.ExcelWriter(nama_file_log) as writer:
            pd.DataFrame(log_data).to_excel(writer, sheet_name='Semua Proses', index=False)
            if log_gudang:
                pd.DataFrame(log_gudang).to_excel(writer, sheet_name='Ambil Gudang', index=False)
                
        log_callback(f"\n💾 Log disimpan: {os.path.basename(nama_file_log)} (Termasuk tab 'Ambil Gudang')")
        
    if label_data:
        pd.DataFrame(label_data).to_excel(nama_file_label, index=False)
        log_callback(f"🏷️  Label disimpan: {os.path.basename(nama_file_label)}")

    total    = len(log_data)
    berhasil = sum(1 for item in log_data if item['Status'] == 'SUKSES')
    gudang   = sum(1 for item in log_data if item['Status'] == 'GUDANG')
    gagal    = total - berhasil - gudang

    if gagal > 0:
        log_callback("\n" + "─" * 55, tag="error")
        log_callback("❌  DESAIN YANG GAGAL DITEMUKAN:", tag="error")
        log_callback("─" * 55, tag="error")
        for log in log_data:
            if log['Status'] == 'GAGAL':
                log_callback(f"   • {log['File Dicari']}  (Resi: {log['Resi']})", tag="error")

    log_callback("\n" + "═" * 55)
    log_callback("📊  RINGKASAN HASIL")
    log_callback("═" * 55)
    log_callback(f"   Total File Di-request  : {total}")
    log_callback(f"   Berhasil Diproses      : {berhasil}", tag="success")
    log_callback(f"   ✨ Diambil dari Gudang  : {gudang} pcs", tag="gudang")
    log_callback(f"   Gagal / Tidak Ditemukan: {gagal}", tag="error" if gagal > 0 else "success")
    log_callback(f"   🎨 Desain Custom       : {len(custom_data)} pcs", tag="warn")
    log_callback("═" * 55)

    durasi_total = time.perf_counter() - waktu_mulai
    menit = int(durasi_total // 60)
    detik = int(durasi_total % 60)
    if menit > 0:
        durasi_str = f"{menit} menit {detik} detik"
    else:
        durasi_str = f"{detik} detik"
    log_callback(f"⏱️  Durasi Total           : {durasi_str}", tag="success")
    log_callback("═" * 55)
    log_callback("\n✅  SEMUA PESANAN TELAH SELESAI DIPROSES ✅", tag="success")

    # ── Catat log gudang ke LOG_KELUAR (opsional via toggle) ────
    if ambil_gudang_log:
        if tulis_log_keluar:
            set_status("📝  Mencatat log pengambilan gudang...")
            log_keluar_gudang(
                spreadsheet_id, json_key_path, ambil_gudang_log, log_callback
            )
        else:
            log_callback(
                f"\n⏭️  [LOG_KELUAR] Dilewati — opsi 'Tulis di Log Keluar' tidak aktif. "
                f"{len(ambil_gudang_log)} pengambilan gudang TIDAK dicatat (stok sheet tidak dikurangi).",
                tag="warn"
            )

    # ── Sync ke Google Sheets (setelah semua file & log Excel selesai) ──────
    sync_ok = sync_to_google_sheets(
        spreadsheet_id, json_key_path, pesanan_grouped, log_callback
    )
    if not sync_ok and (spreadsheet_id or json_key_path):
        # Ada konfigurasi tapi gagal → tampilkan notifikasi, tidak crash
        if cloud_warn_callback:
            cloud_warn_callback()

    finish_callback(True)


# =============================================================
#  UI — Tampilan Modern dengan Tkinter
# =============================================================

# ── Tema Light (terang) ────────────────────────────────────────────────────
DARK_BG     = "#eef1f6"   # Latar belakang utama (abu sangat terang)
PANEL_BG    = "#ffffff"   # Latar panel / card (putih)
INPUT_BG    = "#f5f7fa"   # Latar field input
ACCENT      = "#6c5ce7"   # Ungu accent
ACCENT2     = "#5648c9"   # Ungu gelap (teks header — kontras di latar terang)
BTN_GREEN   = "#16a34a"   # Tombol run (hijau)
BTN_GREEN_H = "#15803d"   # Hover tombol run
BTN_GREY    = "#e2e8f0"   # Tombol sekunder (abu terang)
BTN_GREY_H  = "#cbd5e1"
TEXT_MAIN   = "#1f2937"   # Teks utama (gelap)
TEXT_DIM    = "#6b7280"   # Teks redup / subtitle
COLOR_OK    = "#16a34a"   # Warna sukses
COLOR_ERR   = "#dc2626"   # Warna error
COLOR_WARN  = "#b45309"   # Warna peringatan
COLOR_GUDANG = "#0e7490"  # Warna info gudang (teal gelap, terbaca di putih)
LOG_BG      = "#fbfcfe"   # Background log (putih keabu)
BORDER      = "#d1d5db"   # Warna border

class SortirDesainApp:
    def __init__(self, root):
        self.root = root
        self.app_version = get_version()
        self.root.title(f"🤖 Robot Sortir Desain — Ganci v{self.app_version}")
        self.root.geometry("920x860")
        self.root.minsize(820, 700)
        self.root.configure(bg=DARK_BG)

        cfg = load_config()
        self.file_pesanan_path        = tk.StringVar(value=cfg['file_pesanan'])
        self.file_database_path       = tk.StringVar(value=cfg['file_database'])
        self.folder_master_desain_path = tk.StringVar(value=cfg['folder_master'])
        self.folder_output_path       = tk.StringVar(value=cfg['folder_output'])
        self.spreadsheet_id_var       = tk.StringVar(value=cfg.get('spreadsheet_id', ''))
        self.json_key_path_var        = tk.StringVar(value=cfg.get('json_key_path', ''))
        self.mode_var                 = tk.IntVar(value=cfg.get('mode', 1))
        # Toggle "Tulis di Log Keluar" — default OFF tiap sesi (tidak dipersist).
        self.tulis_log_keluar_var     = tk.BooleanVar(value=False)

        self._build_ui()
        # Tampilkan notifikasi update setelah mainloop sempat render UI dasar.
        self.root.after(400, self._show_update_notification)

    # ----------------------------------------------------------
    # Builder UI
    # ----------------------------------------------------------
    def _build_ui(self):
        # ── Header strip ──────────────────────────────────────
        tk.Frame(self.root, bg=ACCENT, height=4).pack(fill=tk.X)

        # ── Title bar ─────────────────────────────────────────
        title_bar = tk.Frame(self.root, bg=DARK_BG, pady=12)
        title_bar.pack(fill=tk.X, padx=22)

        tk.Label(
            title_bar, text=f"⚙️  Robot Sortir Desain Otomatis  v{self.app_version}",
            font=("Segoe UI", 16, "bold"),
            bg=DARK_BG, fg=ACCENT2
        ).pack(side=tk.LEFT)

        tk.Label(
            title_bar, text="by Ganci Project",
            font=("Segoe UI", 9),
            bg=DARK_BG, fg=TEXT_DIM
        ).pack(side=tk.RIGHT, anchor="s", pady=4)

        # ── Styles (light) ─────────────────────────────────────
        style = ttk.Style()
        style.theme_use("clam")
        # Notebook utama: tab besar
        style.configure("Main.TNotebook", background=DARK_BG, borderwidth=0, tabmargins=[2, 4, 2, 0])
        style.configure(
            "Main.TNotebook.Tab",
            background="#dde3ec", foreground=TEXT_DIM,
            font=("Segoe UI", 12, "bold"),
            padding=[28, 12], borderwidth=0
        )
        style.map(
            "Main.TNotebook.Tab",
            background=[("selected", PANEL_BG), ("active", "#e8edf4")],
            foreground=[("selected", ACCENT2), ("active", TEXT_MAIN)],
        )
        # Notebook log: tab kecil
        style.configure("Sub.TNotebook", background=PANEL_BG, borderwidth=0)
        style.configure(
            "Sub.TNotebook.Tab",
            background="#eceff4", foreground=TEXT_DIM,
            font=("Segoe UI", 9, "bold"),
            padding=[16, 6], borderwidth=0
        )
        style.map(
            "Sub.TNotebook.Tab",
            background=[("selected", LOG_BG), ("active", "#e2e8f0")],
            foreground=[("selected", ACCENT2), ("active", TEXT_MAIN)],
        )
        style.configure(
            "Custom.Horizontal.TProgressbar",
            troughcolor="#e2e8f0", background=ACCENT,
            lightcolor=ACCENT, darkcolor=ACCENT, bordercolor=BORDER,
            thickness=14
        )

        # ── Notebook utama: 2 tab (Pengaturan & Proses) ────────
        main_nb = ttk.Notebook(self.root, style="Main.TNotebook")
        main_nb.pack(fill=tk.BOTH, expand=True, padx=18, pady=(0, 14))

        tab_setting = tk.Frame(main_nb, bg=DARK_BG)
        tab_proses  = tk.Frame(main_nb, bg=DARK_BG)
        main_nb.add(tab_setting, text="  ⚙️  Pengaturan  ")
        main_nb.add(tab_proses,  text="  ▶  Proses  ")

        self._build_tab_setting(tab_setting)
        self._build_tab_proses(tab_proses)

    # ----------------------------------------------------------
    # Tab Pengaturan — file/folder + integrasi Google Sheets
    # ----------------------------------------------------------
    def _build_tab_setting(self, parent):
        wrap = tk.Frame(parent, bg=DARK_BG, padx=4, pady=10)
        wrap.pack(fill=tk.BOTH, expand=True)

        # ── Card: File & Folder ───────────────────────────────
        card_file = self._make_card(wrap, "📁  File & Folder")
        inner = tk.Frame(card_file, bg=PANEL_BG, padx=18, pady=12)
        inner.pack(fill=tk.X)
        inner.columnconfigure(1, weight=1)

        rows = [
            ("Data Pesanan :",  self.file_pesanan_path,         self._browse_pesanan),
            ("Database SKU :",  self.file_database_path,        self._browse_database),
            ("Folder Master :", self.folder_master_desain_path, self._browse_folder_master),
            ("Folder Output :", self.folder_output_path,        self._browse_folder_output),
        ]
        for r, (label, var, cmd) in enumerate(rows):
            tk.Label(inner, text=label, bg=PANEL_BG, fg=TEXT_DIM,
                     font=("Segoe UI", 10), anchor="w", width=14).grid(row=r, column=0, sticky="w", pady=6)
            self._make_entry(inner, var).grid(row=r, column=1, sticky="ew", padx=(8, 8), pady=6, ipady=6)
            self._make_btn(inner, "📂 Pilih", cmd, small=True).grid(row=r, column=2, pady=6)

        # ── Card: Integrasi Google Sheets ─────────────────────
        card_gs = self._make_card(wrap, "☁️  Integrasi Google Sheets")
        gs_inner = tk.Frame(card_gs, bg=PANEL_BG, padx=18, pady=12)
        gs_inner.pack(fill=tk.X)
        gs_inner.columnconfigure(1, weight=1)

        tk.Label(gs_inner, text="Spreadsheet ID :", bg=PANEL_BG, fg=TEXT_DIM,
                 font=("Segoe UI", 10), anchor="w", width=16).grid(row=0, column=0, sticky="w", pady=6)
        self._make_entry(gs_inner, self.spreadsheet_id_var).grid(
            row=0, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=6, ipady=6)

        tk.Label(gs_inner, text="File JSON Key :", bg=PANEL_BG, fg=TEXT_DIM,
                 font=("Segoe UI", 10), anchor="w", width=16).grid(row=1, column=0, sticky="w", pady=6)
        self._make_entry(gs_inner, self.json_key_path_var).grid(
            row=1, column=1, sticky="ew", padx=(8, 8), pady=6, ipady=6)
        self._make_btn(gs_inner, "📂 Pilih", self._browse_json_key, small=True).grid(row=1, column=2, pady=6)

        tk.Label(gs_inner,
                 text="ⓘ  Pastikan email 'client_email' dari JSON sudah jadi Editor di G-Sheets Anda.\n"
                      "    Kosongkan kedua field di atas jika tidak memakai Google Sheets.",
                 bg=PANEL_BG, fg=TEXT_DIM, justify="left",
                 font=("Segoe UI", 8, "italic")).grid(
                 row=2, column=0, columnspan=3, sticky="w", pady=(4, 4))

    # ----------------------------------------------------------
    # Tab Proses — mode, tombol aksi, progress, LOG besar
    # ----------------------------------------------------------
    def _build_tab_proses(self, parent):
        wrap = tk.Frame(parent, bg=DARK_BG, padx=4, pady=10)
        wrap.pack(fill=tk.BOTH, expand=True)

        # ── Baris atas: Mode + tombol aksi ────────────────────
        top = tk.Frame(wrap, bg=PANEL_BG, bd=0, highlightthickness=1, highlightbackground=BORDER)
        top.pack(fill=tk.X)
        top_inner = tk.Frame(top, bg=PANEL_BG, padx=18, pady=12)
        top_inner.pack(fill=tk.X)

        tk.Label(top_inner, text="📤  Mode Output",
                 bg=PANEL_BG, fg=ACCENT2, font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 6))

        radio_row = tk.Frame(top_inner, bg=PANEL_BG)
        radio_row.pack(fill=tk.X)

        def _make_radio(parent_, text, val, desc):
            f = tk.Frame(parent_, bg=PANEL_BG)
            f.pack(side=tk.LEFT, padx=(0, 28))
            tk.Radiobutton(
                f, text=text, variable=self.mode_var, value=val,
                bg=PANEL_BG, fg=TEXT_MAIN, activebackground=PANEL_BG,
                activeforeground=ACCENT2, selectcolor="#ffffff",
                font=("Segoe UI", 10, "bold"), relief="flat", cursor="hand2"
            ).pack(anchor="w")
            tk.Label(f, text=desc, bg=PANEL_BG, fg=TEXT_DIM,
                     font=("Segoe UI", 8, "italic")).pack(anchor="w", padx=(22, 0))

        _make_radio(radio_row, "⭐ Layout Masal (Default)", 1,
                    "Semua file dalam 1 folder flat, tanpa subfolder")
        _make_radio(radio_row, "📂 Sortir per Resi", 2,
                    "File dikelompokkan dalam subfolder per nomor resi")

        # Tombol aksi
        btn_row = tk.Frame(top_inner, bg=PANEL_BG)
        btn_row.pack(fill=tk.X, pady=(14, 0))

        self.btn_start = self._make_btn(
            btn_row, "▶   Mulai Pemrosesan", self._start_thread,
            color=BTN_GREEN, hover=BTN_GREEN_H, fg="white", width=22, big=True
        )
        self.btn_start.pack(side=tk.LEFT, padx=(0, 10))

        self.btn_folder = self._make_btn(
            btn_row, "📂  Buka Folder Output", self._open_output_folder,
            color=BTN_GREY, hover=BTN_GREY_H, width=22, big=True
        )
        self.btn_folder.pack(side=tk.LEFT)

        # Toggle: tulis pengambilan gudang ke LOG_KELUAR (kurangi stok sheet)
        tk.Checkbutton(
            btn_row, text="  📝 Tulis di Log Keluar (kurangi stok)",
            variable=self.tulis_log_keluar_var,
            bg=PANEL_BG, fg=TEXT_MAIN, activebackground=PANEL_BG,
            activeforeground=ACCENT2, selectcolor="#ffffff",
            font=("Segoe UI", 9, "bold"), relief="flat", cursor="hand2",
            anchor="w"
        ).pack(side=tk.LEFT, padx=(16, 0))

        # ── Progress ──────────────────────────────────────────
        prog_inner = tk.Frame(top_inner, bg=PANEL_BG)
        prog_inner.pack(fill=tk.X, pady=(14, 0))

        self.lbl_status = tk.Label(
            prog_inner, text="⏸  Menunggu aksi pengguna...",
            bg=PANEL_BG, fg=TEXT_DIM, font=("Segoe UI", 9, "italic"), anchor="w"
        )
        self.lbl_status.pack(fill=tk.X, pady=(0, 6))

        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(
            prog_inner, variable=self.progress_var,
            maximum=100, style="Custom.Horizontal.TProgressbar"
        )
        self.progress_bar.pack(fill=tk.X)

        # ── Log area (expand utama — LOG besar) ────────────────
        log_card = tk.Frame(wrap, bg=PANEL_BG, bd=0, highlightthickness=1, highlightbackground=BORDER)
        log_card.pack(fill=tk.BOTH, expand=True, pady=(12, 0))

        log_title_bar = tk.Frame(log_card, bg=PANEL_BG)
        log_title_bar.pack(fill=tk.X)
        tk.Label(
            log_title_bar, text="🖥️  Log Proses",
            bg=PANEL_BG, fg=ACCENT2,
            font=("Segoe UI", 11, "bold"),
            padx=16, pady=9, anchor="w"
        ).pack(side=tk.LEFT)
        self._make_btn(log_title_bar, "🗑  Hapus Log", self._clear_log,
                       color=BTN_GREY, hover=BTN_GREY_H, small=True).pack(side=tk.RIGHT, padx=12, pady=6)

        log_inner = tk.Frame(log_card, bg=PANEL_BG)
        log_inner.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))

        self.log_notebook = ttk.Notebook(log_inner, style="Sub.TNotebook")
        self.log_notebook.pack(fill=tk.BOTH, expand=True)

        tab_semua = tk.Frame(self.log_notebook, bg=LOG_BG)
        tab_gudang = tk.Frame(self.log_notebook, bg=LOG_BG)
        self.log_notebook.add(tab_semua, text="  🖥️ Semua Proses  ")
        self.log_notebook.add(tab_gudang, text="  📦 Log Gudang  ")

        self.text_log = scrolledtext.ScrolledText(
            tab_semua, state="disabled", wrap="word",
            bg=LOG_BG, fg=TEXT_MAIN,
            font=("Consolas", 11),
            relief="flat", bd=0, padx=14, pady=12,
            selectbackground=ACCENT, selectforeground="white"
        )
        self.text_log.pack(fill=tk.BOTH, expand=True)

        self.text_log_gudang = scrolledtext.ScrolledText(
            tab_gudang, state="disabled", wrap="word",
            bg=LOG_BG, fg=TEXT_MAIN,
            font=("Consolas", 11),
            relief="flat", bd=0, padx=14, pady=12,
            selectbackground=ACCENT, selectforeground="white"
        )
        self.text_log_gudang.pack(fill=tk.BOTH, expand=True)

        for txt_widget in (self.text_log, self.text_log_gudang):
            txt_widget.tag_config("error",   foreground=COLOR_ERR,   font=("Consolas", 11, "bold"))
            txt_widget.tag_config("success", foreground=COLOR_OK,    font=("Consolas", 11, "bold"))
            txt_widget.tag_config("warn",    foreground=COLOR_WARN,  font=("Consolas", 11))
            txt_widget.tag_config("header",  foreground=ACCENT2,     font=("Consolas", 11, "bold"))
            txt_widget.tag_config("gudang",  foreground=COLOR_GUDANG, font=("Consolas", 11, "bold"))

    # ----------------------------------------------------------
    # Widget Helpers
    # ----------------------------------------------------------
    def _make_card(self, parent, title):
        """Card putih dengan judul accent. Return frame body untuk diisi caller."""
        card = tk.Frame(parent, bg=PANEL_BG, bd=0, highlightthickness=1, highlightbackground=BORDER)
        card.pack(fill=tk.X, pady=(0, 12))
        tk.Label(
            card, text=title,
            bg=PANEL_BG, fg=ACCENT2,
            font=("Segoe UI", 11, "bold"),
            padx=16, pady=9, anchor="w"
        ).pack(fill=tk.X)
        tk.Frame(card, bg=BORDER, height=1).pack(fill=tk.X)
        return card

    def _make_entry(self, parent, var):
        """Entry bergaya light mode."""
        return tk.Entry(
            parent, textvariable=var, bg=INPUT_BG, fg=TEXT_MAIN,
            insertbackground=TEXT_MAIN, relief="flat",
            font=("Segoe UI", 10), bd=0, highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=ACCENT
        )

    def _make_btn(self, parent, text, command, color=BTN_GREY, hover=BTN_GREY_H,
                  width=None, small=False, big=False, fg=TEXT_MAIN):
        font = ("Segoe UI", 9, "bold") if not big else ("Segoe UI", 11, "bold")
        pady = 3 if small else 8
        btn = tk.Button(
            parent, text=text, command=command,
            bg=color, fg=fg, activebackground=hover, activeforeground=fg,
            font=font, relief="flat", bd=0, cursor="hand2",
            padx=12, pady=pady
        )
        if width:
            btn.config(width=width)
        btn.bind("<Enter>", lambda e: btn.config(bg=hover))
        btn.bind("<Leave>", lambda e: btn.config(bg=color))
        return btn

    # ----------------------------------------------------------
    # Browse / File Dialogs
    # ----------------------------------------------------------
    def _browse_pesanan(self):
        fn = filedialog.askopenfilename(
            title="Pilih File Pesanan Harian",
            initialdir=os.getcwd(),
            filetypes=[("Excel files", "*.xlsx *.xls")]
        )
        if fn:
            self.file_pesanan_path.set(fn)

    def _browse_database(self):
        fn = filedialog.askopenfilename(
            title="Pilih Database SKU",
            initialdir=os.getcwd(),
            filetypes=[("Excel files", "*.xlsx *.xls")]
        )
        if fn:
            self.file_database_path.set(fn)

    def _browse_folder_master(self):
        fd = filedialog.askdirectory(
            title="Pilih Folder Master Desain",
            initialdir=os.getcwd()
        )
        if fd:
            self.folder_master_desain_path.set(fd)

    def _browse_folder_output(self):
        fd = filedialog.askdirectory(
            title="Pilih Folder Output (tempat hasil sortir disimpan)",
            initialdir=os.getcwd()
        )
        if fd:
            self.folder_output_path.set(fd)

    def _browse_json_key(self):
        fn = filedialog.askopenfilename(
            title="Pilih File JSON Service Account Google",
            initialdir=os.getcwd(),
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if fn:
            self.json_key_path_var.set(fn)

    def _show_update_notification(self):
        """
        Tampilkan info dialog kalau updater (dipanggil dari run.bat) baru saja
        sukses memperbarui kode. Dipakai sekali — flag dihapus oleh
        ``consume_update_flag()`` setelah dibaca.
        """
        flag = consume_update_flag()
        if not flag:
            return
        old = flag.get('old_version', '?')
        new = flag.get('new_version', '?')
        ts = flag.get('timestamp', '')
        messagebox.showinfo(
            "✅ Aplikasi Telah Diupdate",
            f"Aplikasi otomatis di-update ke versi terbaru.\n\n"
            f"Versi sebelumnya : {old}\n"
            f"Versi sekarang   : {new}\n"
            f"Waktu update     : {ts}"
        )

    def show_cloud_warning(self):
        """Callback untuk notifikasi gagal sync cloud (dipanggil via root.after)."""
        def _warn():
            messagebox.showwarning(
                "☁️  Sinkronisasi Cloud",
                "Sortir Selesai, tapi gagal update ke Cloud\n"
                "(Cek Koneksi Internet atau konfigurasi Google Sheets)"
            )
        self.root.after(0, _warn)

    # ----------------------------------------------------------
    # Actions
    # ----------------------------------------------------------
    def _open_output_folder(self):
        folder = self.folder_output_path.get()
        if folder and os.path.exists(folder):
            os.startfile(folder)
        else:
            messagebox.showwarning("Peringatan", "Folder output belum ada atau belum dipilih.\nSilakan pilih Folder Output terlebih dahulu.")

    def _clear_log(self):
        for w in (self.text_log, self.text_log_gudang):
            w.config(state="normal")
            w.delete(1.0, tk.END)
            w.config(state="disabled")

    def log_message(self, message, tag=None):
        def _append():
            self.text_log.config(state="normal")
            if tag:
                self.text_log.insert(tk.END, message + "\n", tag)
            else:
                self.text_log.insert(tk.END, message + "\n")
            self.text_log.see(tk.END)
            self.text_log.config(state="disabled")
        self.root.after(0, _append)

    def log_gudang_message(self, message, tag=None):
        def _append():
            self.text_log_gudang.config(state="normal")
            if tag:
                self.text_log_gudang.insert(tk.END, message + "\n", tag)
            else:
                self.text_log_gudang.insert(tk.END, message + "\n")
            self.text_log_gudang.see(tk.END)
            self.text_log_gudang.config(state="disabled")
        self.root.after(0, _append)

    def update_progress(self, current, total):
        def _update():
            p = (current / total) * 100
            self.progress_var.set(p)
            self.lbl_status.config(
                text=f"⏳  Memproses Resi {current} dari {total}  ({int(p)}%)",
                fg=ACCENT2
            )
        self.root.after(0, _update)

    def process_finished(self, success):
        def _done():
            self.btn_start.config(state="normal", bg=BTN_GREEN)
            self.btn_start.bind("<Enter>", lambda e: self.btn_start.config(bg=BTN_GREEN_H))
            self.btn_start.bind("<Leave>", lambda e: self.btn_start.config(bg=BTN_GREEN))
            if success:
                self.lbl_status.config(
                    text="✅  Selesai dengan Sukses! Silakan periksa folder Output.",
                    fg=COLOR_OK
                )
                messagebox.showinfo(
                    "✅ Proses Selesai",
                    "Semua pesanan berhasil disortir!\n\nSilakan periksa folder Output Anda."
                )
            else:
                self.lbl_status.config(text="❌  Proses dihentikan karena error.", fg=COLOR_ERR)
                messagebox.showerror("❌ Error", "Proses dihentikan paksa karena terjadi kesalahan.\nSilakan lihat Log.")
        self.root.after(0, _done)

    def update_status(self, text):
        def _update():
            self.lbl_status.config(text=text, fg=ACCENT2)
        self.root.after(0, _update)

    def _start_thread(self):
        file_pes       = self.file_pesanan_path.get().strip()
        file_db        = self.file_database_path.get().strip()
        folder_master  = self.folder_master_desain_path.get().strip()
        folder_output  = self.folder_output_path.get().strip()
        spreadsheet_id = self.spreadsheet_id_var.get().strip()
        json_key_path  = self.json_key_path_var.get().strip()

        # Validasi sebelum mulai
        if not file_pes or not file_db or not folder_master or not folder_output:
            messagebox.showwarning(
                "⚠️ Pengaturan Belum Lengkap",
                "Harap lengkapi semua field:\n• Data Pesanan\n• Database SKU\n• Folder Master Desain\n• Folder Output"
            )
            return

        mode = self.mode_var.get()

        # Simpan config agar diingat sesi berikutnya
        save_config({
            'file_pesanan':    file_pes,
            'file_database':   file_db,
            'folder_master':   folder_master,
            'folder_output':   folder_output,
            'spreadsheet_id':  spreadsheet_id,
            'json_key_path':   json_key_path,
            'mode':            mode,
        })

        self.btn_start.config(state="disabled", bg="#9ca3af")
        self._clear_log()
        self.progress_var.set(0)
        self.lbl_status.config(text="⏳  Memulai...", fg=ACCENT2)

        t = threading.Thread(
            target=proses_data,
            args=(file_pes, file_db, folder_master, folder_output,
                  self.log_message, self.update_progress, self.process_finished,
                  self.update_status, self.show_cloud_warning,
                  spreadsheet_id, json_key_path, mode, True, self.log_gudang_message,
                  self.tulis_log_keluar_var.get())
        )
        t.daemon = True
        t.start()


# --- ENTRY POINT ---
if __name__ == "__main__":
    app_root = tk.Tk()
    try:
        app_root.iconbitmap("icon.ico")
    except Exception:
        pass
    app = SortirDesainApp(app_root)
    app_root.mainloop()
