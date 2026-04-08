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

# ── Google Sheets (opsional, install: pip install gspread google-auth) ──────
try:
    import gspread
    from google.oauth2.service_account import Credentials as GSCredentials
    GSPREAD_AVAILABLE = True
except ImportError:
    GSPREAD_AVAILABLE = False

# --- ATURAN BUNDLE (tidak berubah) ---
ATURAN_BUNDLE = {
    'S': 5,
    'M': 5,
    'BS': 10,
}

# --- CONFIG (disimpan otomatis ke config.json) ---
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
DEFAULT_CONFIG = {
    'file_pesanan':    'pesanan_harian.xlsx',
    'file_database':   'database_sku.xlsx',
    'folder_master':   '',
    'folder_output':   '',
    'spreadsheet_id':  '',       # ID Google Sheets (dari URL)
    'json_key_path':   '',       # Path ke file JSON Service Account
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            # merge supaya key baru dari DEFAULT_CONFIG tetap ada
            return {**DEFAULT_CONFIG, **data}
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()

def save_config(cfg: dict):
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

def build_file_index(folder_akar, log_callback):
    """
    Scan folder master 1x saja, simpan semua file ke dict.
    Index juga menyimpan alias ATM <-> ANM agar pencarian tetap
    berhasil meski nama file di folder berbeda dengan format pesanan.
    """
    log_callback("🔍 Membangun index file dari Folder Master Desain...")
    t0 = time.perf_counter()
    index = {}
    for root, dirs, files in os.walk(folder_akar):
        for fname in files:
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
            sheet.append_rows(rows_to_append, value_input_option='USER_ENTERED')
            log_callback(
                f"☁️  ✅ {len(rows_to_append)} baris berhasil dikirim ke Google Sheets!",
                tag="success"
            )
        else:
            log_callback("☁️  Tidak ada data baru untuk dikirim.", tag="warn")

        return True

    except Exception as e:
        log_callback(f"☁️  ❌ Gagal sync ke Google Sheets: {e}", tag="error")
        return False


def parse_dynamic_sku(sku_bundle_dasar):
    """
    Fungsi untuk membaca SKU bundle secara dinamis dari urutan angkanya.
    Mendukung format lama (contoh: GK-ANM-SET-81-90) dan format baru (contoh: ANM-81-90).
    """
    pattern = r'(?:GK-)?(.*?)-(\d+)-(\d+)$'
    match = re.search(pattern, sku_bundle_dasar, re.IGNORECASE)
    if match:
        kategori_mentah = match.group(1)
        kategori = re.sub(r'-?SET-?', '', kategori_mentah, flags=re.IGNORECASE).strip('-')
        start = int(match.group(2))
        end = int(match.group(3))
        
        sku_list = []
        for i in range(start, end + 1):
            sku_list.append(f"GK-{kategori}-{str(i).zfill(7)}")
        return sku_list
    return []

def proses_data(file_pesanan, file_database, folder_master_desain, folder_output,
                log_callback, progress_callback, finish_callback,
                status_callback=None, cloud_warn_callback=None,
                spreadsheet_id='', json_key_path='', mode=1):
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
    file_index = build_file_index(folder_master_desain, log_callback)

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
            
            # --- Normalisasi Format SKU ---
            # 1. Ganti typo ATM ke ANM
            sku_pesanan = sku_pesanan.replace('ATM-', 'ANM-')

            # 2. Pad angka jadi 7 digit HANYA untuk SKU tunggal: GK-ANM-487-L → GK-ANM-0000487-L
            #    Jangan pad format bundle/SET seperti: GK-ANM-SET-3851-3860-BS
            #    Cara aman: split per '-', jika part[-2] adalah angka pendek DAN part[-3] BUKAN angka → pad
            _parts = sku_pesanan.split('-')
            if len(_parts) >= 3 and _parts[-1].upper() in ['L', 'S', 'M', 'BS']:
                _angka = _parts[-2]
                _prev  = _parts[-3] if len(_parts) >= 4 else ''
                if _angka.isdigit() and len(_angka) < 7 and not _prev.isdigit():
                    _parts[-2] = _angka.zfill(7)
                    sku_pesanan = '-'.join(_parts)

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

            sku_dasar_list = []
            is_bundle = False
            sku_bundle_dasar = sku_pesanan.rsplit('-', 1)[0]

            # Cek apakah benar-benar bundle (SET) atau item S/M/BS yang ada di db bundle
            if "-SET-" in sku_pesanan.upper():
                is_bundle = True
            elif ukuran in ['S', 'M', 'BS']:
                kolom_cari = 'sku_bundling_base_bs' if ukuran == 'BS' else 'sku_bundling_base'
                if kolom_cari in df_database.columns:
                    filter_bundle = df_database[kolom_cari] == sku_bundle_dasar
                    matched = df_database[filter_bundle]['sku_individu_base'].tolist()
                    if matched:
                        is_bundle = True
                        sku_dasar_list = matched

                # Fallback: coba parse dinamis (misal GK-ANM-SET-81-90)
                if not is_bundle:
                    sku_dasar_list = parse_dynamic_sku(sku_bundle_dasar)
                    if sku_dasar_list:
                        is_bundle = True
                        log_callback(f"  🔍 [Dinamis] Range terdeteksi dari: {sku_bundle_dasar}")

            if is_bundle:
                if not sku_dasar_list:
                    # Belum terisi (dari SET path), coba database dulu
                    kolom_cari = 'sku_bundling_base_bs' if ukuran == 'BS' else 'sku_bundling_base'
                    if kolom_cari in df_database.columns:
                        filter_bundle = df_database[kolom_cari] == sku_bundle_dasar
                        sku_dasar_list = df_database[filter_bundle]['sku_individu_base'].tolist()
                    if not sku_dasar_list:
                        log_callback(f"  🔍 [Dinamis] Membaca range angka dari: {sku_bundle_dasar}")
                        sku_dasar_list = parse_dynamic_sku(sku_bundle_dasar)

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
                    batas = ATURAN_BUNDLE[ukuran]
                    sku_dasar_list = sku_dasar_list[:batas]
                    log_callback(f"  📐 Bundle ukuran '{ukuran}' → ambil {len(sku_dasar_list)} item.")
            else:
                # Item tunggal — termasuk ukuran S/M/BS yang bukan bundle
                sku_dasar = sku_bundle_dasar
                sku_dasar_list.append(sku_dasar)
                if ukuran in ['S', 'M', 'BS']:
                    log_callback(f"  🏷️  SKU Tunggal ukuran {ukuran} terdeteksi (bukan bundle).")
                else:
                    log_callback(f"  🏷️  SKU Tunggal (-L) terdeteksi.")

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
        pd.DataFrame(log_data).to_excel(nama_file_log, index=False)
        log_callback(f"\n💾 Log disimpan: {os.path.basename(nama_file_log)}")
    if label_data:
        pd.DataFrame(label_data).to_excel(nama_file_label, index=False)
        log_callback(f"🏷️  Label disimpan: {os.path.basename(nama_file_label)}")

    total = len(log_data)
    berhasil = sum(1 for item in log_data if item['Status'] == 'SUKSES')
    gagal = total - berhasil

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

DARK_BG     = "#1e1e2e"   # Latar belakang utama (navy gelap)
PANEL_BG    = "#2a2a3e"   # Latar panel / card
ACCENT      = "#7c6af7"   # Ungu accent
ACCENT2     = "#56cfcf"   # Teal accent (header teks)
BTN_GREEN   = "#23c78e"   # Tombol run
BTN_GREEN_H = "#1da87a"   # Hover tombol run
BTN_GREY    = "#3d3d56"   # Tombol sekunder
BTN_GREY_H  = "#505070"
TEXT_MAIN   = "#e2e2f0"   # Teks utama
TEXT_DIM    = "#9090b0"   # Teks redup / subtitle
COLOR_OK    = "#23c78e"   # Warna sukses
COLOR_ERR   = "#f26c6c"   # Warna error
COLOR_WARN  = "#f5a623"   # Warna peringatan
LOG_BG      = "#12121f"   # Background log hitam
BORDER      = "#3a3a55"   # Warna border

class SortirDesainApp:
    def __init__(self, root):
        self.root = root
        self.root.title("🤖 Robot Sortir Desain — Ganci")
        self.root.geometry("900x820")
        self.root.minsize(800, 680)
        self.root.configure(bg=DARK_BG)

        cfg = load_config()
        self.file_pesanan_path        = tk.StringVar(value=cfg['file_pesanan'])
        self.file_database_path       = tk.StringVar(value=cfg['file_database'])
        self.folder_master_desain_path = tk.StringVar(value=cfg['folder_master'])
        self.folder_output_path       = tk.StringVar(value=cfg['folder_output'])
        self.spreadsheet_id_var       = tk.StringVar(value=cfg.get('spreadsheet_id', ''))
        self.json_key_path_var        = tk.StringVar(value=cfg.get('json_key_path', ''))
        self.mode_var                 = tk.IntVar(value=cfg.get('mode', 1))

        self._build_ui()

    # ----------------------------------------------------------
    # Builder UI
    # ----------------------------------------------------------
    def _build_ui(self):
        # ── Header strip ──────────────────────────────────────
        header = tk.Frame(self.root, bg=ACCENT, height=5)
        header.pack(fill=tk.X)

        # ── Title bar ─────────────────────────────────────────
        title_bar = tk.Frame(self.root, bg=DARK_BG, pady=18)
        title_bar.pack(fill=tk.X, padx=24)

        tk.Label(
            title_bar, text="⚙️  Robot Sortir Desain Otomatis",
            font=("Segoe UI", 17, "bold"),
            bg=DARK_BG, fg=ACCENT2
        ).pack(side=tk.LEFT)

        tk.Label(
            title_bar, text="by Ganci Project",
            font=("Segoe UI", 9),
            bg=DARK_BG, fg=TEXT_DIM
        ).pack(side=tk.RIGHT, anchor="s", pady=4)

        # ── Main content area ──────────────────────────────────
        outer = tk.Frame(self.root, bg=DARK_BG)
        outer.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 16))

        # ── Tab Notebook untuk Pengaturan ─────────────────────
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Dark.TNotebook", background=DARK_BG, borderwidth=0, tabmargins=[0, 0, 0, 0])
        style.configure(
            "Dark.TNotebook.Tab",
            background=BTN_GREY, foreground=TEXT_DIM,
            font=("Segoe UI", 9, "bold"),
            padding=[14, 6], borderwidth=0
        )
        style.map(
            "Dark.TNotebook.Tab",
            background=[("selected", PANEL_BG), ("active", BTN_GREY_H)],
            foreground=[("selected", ACCENT2), ("active", TEXT_MAIN)],
        )
        style.configure(
            "Custom.Horizontal.TProgressbar",
            troughcolor="#1a1a2e", background=ACCENT,
            lightcolor=ACCENT, darkcolor=ACCENT, bordercolor=BORDER,
            thickness=12
        )

        notebook = ttk.Notebook(outer, style="Dark.TNotebook")
        notebook.pack(fill=tk.X, pady=(0, 10))

        # ── Tab 1: Pengaturan File & Folder ───────────────────
        tab_file = tk.Frame(notebook, bg=PANEL_BG, pady=2)
        notebook.add(tab_file, text="  📁  Pengaturan File & Folder  ")

        # Garis dekoratif atas
        tk.Frame(tab_file, bg=ACCENT, height=2).pack(fill=tk.X)

        inner = tk.Frame(tab_file, bg=PANEL_BG, padx=16, pady=10)
        inner.pack(fill=tk.X)
        inner.columnconfigure(1, weight=1)

        rows = [
            ("Data Pesanan :",  self.file_pesanan_path,         self._browse_pesanan,       False),
            ("Database SKU :",  self.file_database_path,        self._browse_database,      False),
            ("Folder Master :", self.folder_master_desain_path, self._browse_folder_master, True),
            ("Folder Output :", self.folder_output_path,        self._browse_folder_output, True),
        ]
        for r, (label, var, cmd, is_folder) in enumerate(rows):
            tk.Label(inner, text=label, bg=PANEL_BG, fg=TEXT_DIM,
                     font=("Segoe UI", 9), anchor="w", width=14).grid(row=r, column=0, sticky="w", pady=5)
            ent = tk.Entry(inner, textvariable=var, bg="#1a1a2e", fg=TEXT_MAIN,
                           insertbackground=TEXT_MAIN, relief="flat",
                           font=("Segoe UI", 9), bd=0, highlightthickness=1,
                           highlightbackground=BORDER, highlightcolor=ACCENT)
            ent.grid(row=r, column=1, sticky="ew", padx=(6, 6), pady=5, ipady=6)
            self._make_btn(inner, "📂 Pilih", cmd, small=True).grid(row=r, column=2, pady=5)

        # ── Tab 2: Integrasi Google Sheets ────────────────────
        tab_gs = tk.Frame(notebook, bg=PANEL_BG, pady=2)
        notebook.add(tab_gs, text="  ☁️  Integrasi Google Sheets  ")

        tk.Frame(tab_gs, bg=ACCENT, height=2).pack(fill=tk.X)

        gs_inner = tk.Frame(tab_gs, bg=PANEL_BG, padx=16, pady=10)
        gs_inner.pack(fill=tk.X)
        gs_inner.columnconfigure(1, weight=1)

        tk.Label(gs_inner, text="Spreadsheet ID :", bg=PANEL_BG, fg=TEXT_DIM,
                 font=("Segoe UI", 9), anchor="w", width=16).grid(
                 row=0, column=0, sticky="w", pady=5)
        tk.Entry(gs_inner, textvariable=self.spreadsheet_id_var,
                 bg="#1a1a2e", fg=TEXT_MAIN, insertbackground=TEXT_MAIN,
                 relief="flat", font=("Segoe UI", 9), bd=0,
                 highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=ACCENT).grid(
                 row=0, column=1, columnspan=2, sticky="ew",
                 padx=(6, 0), pady=5, ipady=6)

        tk.Label(gs_inner, text="File JSON Key :", bg=PANEL_BG, fg=TEXT_DIM,
                 font=("Segoe UI", 9), anchor="w", width=16).grid(
                 row=1, column=0, sticky="w", pady=5)
        tk.Entry(gs_inner, textvariable=self.json_key_path_var,
                 bg="#1a1a2e", fg=TEXT_MAIN, insertbackground=TEXT_MAIN,
                 relief="flat", font=("Segoe UI", 9), bd=0,
                 highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=ACCENT).grid(
                 row=1, column=1, sticky="ew", padx=(6, 6), pady=5, ipady=6)
        self._make_btn(gs_inner, "📂 Pilih", self._browse_json_key,
                       small=True).grid(row=1, column=2, pady=5)

        tk.Label(gs_inner,
                 text="  ⓘ  Kosongkan kedua field di atas jika tidak menggunakan Google Sheets",
                 bg=PANEL_BG, fg=TEXT_DIM,
                 font=("Segoe UI", 8, "italic")).grid(
                 row=2, column=0, columnspan=3, sticky="w", pady=(2, 6))

        # ── Mode Output (Radio) ─────────────────────────────────
        mode_card = tk.Frame(outer, bg=PANEL_BG, bd=0, highlightthickness=1, highlightbackground=BORDER)
        mode_card.pack(fill=tk.X, pady=(0, 8))
        tk.Frame(mode_card, bg=ACCENT, height=2).pack(fill=tk.X)
        mode_inner = tk.Frame(mode_card, bg=PANEL_BG, padx=16, pady=8)
        mode_inner.pack(fill=tk.X)

        tk.Label(mode_inner, text="📤  Mode Output :",
                 bg=PANEL_BG, fg=ACCENT2, font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(0, 18))

        def _make_radio(parent, text, val, desc):
            f = tk.Frame(parent, bg=PANEL_BG)
            f.pack(side=tk.LEFT, padx=(0, 24))
            rb = tk.Radiobutton(
                f, text=text, variable=self.mode_var, value=val,
                bg=PANEL_BG, fg=TEXT_MAIN, activebackground=PANEL_BG,
                activeforeground=ACCENT2, selectcolor=DARK_BG,
                font=("Segoe UI", 9, "bold"), relief="flat", cursor="hand2"
            )
            rb.pack(anchor="w")
            tk.Label(f, text=desc, bg=PANEL_BG, fg=TEXT_DIM,
                     font=("Segoe UI", 8, "italic")).pack(anchor="w", padx=(20, 0))

        _make_radio(mode_inner,
                    "⭐ Layout Masal (Default)", 1,
                    "Semua file dalam 1 folder flat, tanpa subfolder")
        _make_radio(mode_inner,
                    "📂 Sortir per Resi", 2,
                    "File dikelompokkan dalam subfolder per nomor resi")

        # ── Action buttons ────────────────────────────────────
        btn_row = tk.Frame(outer, bg=DARK_BG)
        btn_row.pack(fill=tk.X, pady=(0, 8))

        self.btn_start = self._make_btn(
            btn_row, "▶   Mulai Pemrosesan", self._start_thread,
            color=BTN_GREEN, hover=BTN_GREEN_H, width=22, big=True
        )
        self.btn_start.pack(side=tk.LEFT, padx=(0, 10))

        self.btn_folder = self._make_btn(
            btn_row, "📂  Buka Folder Output", self._open_output_folder,
            color=BTN_GREY, hover=BTN_GREY_H, width=22, big=True
        )
        self.btn_folder.pack(side=tk.LEFT)

        # ── Progress ──────────────────────────────────────────
        prog_card = tk.Frame(outer, bg=PANEL_BG, bd=0, highlightthickness=1, highlightbackground=BORDER)
        prog_card.pack(fill=tk.X, pady=(0, 8))
        prog_inner = tk.Frame(prog_card, bg=PANEL_BG, padx=16, pady=8)
        prog_inner.pack(fill=tk.X)

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

        # ── Log area (expand utama) ────────────────────────────
        log_card = tk.Frame(outer, bg=PANEL_BG, bd=0, highlightthickness=1, highlightbackground=BORDER)
        log_card.pack(fill=tk.BOTH, expand=True)

        # Header log dengan tombol hapus di sebelah kanan
        log_header_row = tk.Frame(log_card, bg=PANEL_BG)
        log_header_row.pack(fill=tk.X)
        tk.Frame(log_header_row, bg=ACCENT, height=2).pack(fill=tk.X, side=tk.TOP)
        log_title_bar = tk.Frame(log_header_row, bg=PANEL_BG)
        log_title_bar.pack(fill=tk.X)
        tk.Label(
            log_title_bar, text="🖥️  Log Proses",
            bg=PANEL_BG, fg=ACCENT2,
            font=("Segoe UI", 10, "bold"),
            padx=14, pady=7, anchor="w"
        ).pack(side=tk.LEFT)
        self._make_btn(log_title_bar, "🗑  Hapus Log", self._clear_log,
                       color=BTN_GREY, hover=BTN_GREY_H, small=True).pack(side=tk.RIGHT, padx=12, pady=4)

        log_inner = tk.Frame(log_card, bg=LOG_BG)
        log_inner.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))

        self.text_log = scrolledtext.ScrolledText(
            log_inner, state="disabled", wrap="word",
            bg=LOG_BG, fg=TEXT_MAIN,
            font=("Consolas", 10),
            relief="flat", bd=0, padx=12, pady=10,
            selectbackground=ACCENT
        )
        self.text_log.pack(fill=tk.BOTH, expand=True)

        # Tag warna log
        self.text_log.tag_config("error",   foreground=COLOR_ERR,  font=("Consolas", 10, "bold"))
        self.text_log.tag_config("success", foreground=COLOR_OK,   font=("Consolas", 10, "bold"))
        self.text_log.tag_config("warn",    foreground=COLOR_WARN, font=("Consolas", 10))
        self.text_log.tag_config("header",  foreground=ACCENT2,    font=("Consolas", 10, "bold"))

    # ----------------------------------------------------------
    # Widget Helpers
    # ----------------------------------------------------------
    def _card_header(self, parent, text):
        h = tk.Frame(parent, bg=ACCENT, height=2)
        h.pack(fill=tk.X)
        tk.Label(
            parent, text=text,
            bg=PANEL_BG, fg=ACCENT2,
            font=("Segoe UI", 10, "bold"),
            padx=14, pady=8, anchor="w"
        ).pack(fill=tk.X)

    def _make_btn(self, parent, text, command, color=BTN_GREY, hover=BTN_GREY_H,
                  width=None, small=False, big=False):
        font = ("Segoe UI", 9, "bold") if not big else ("Segoe UI", 10, "bold")
        pady = 3 if small else 7
        btn = tk.Button(
            parent, text=text, command=command,
            bg=color, fg="white", activebackground=hover, activeforeground="white",
            font=font, relief="flat", bd=0, cursor="hand2",
            padx=10, pady=pady
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
        self.text_log.config(state="normal")
        self.text_log.delete(1.0, tk.END)
        self.text_log.config(state="disabled")

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

        self.btn_start.config(state="disabled", bg="#555570")
        self._clear_log()
        self.progress_var.set(0)
        self.lbl_status.config(text="⏳  Memulai...", fg=ACCENT2)

        t = threading.Thread(
            target=proses_data,
            args=(file_pes, file_db, folder_master, folder_output,
                  self.log_message, self.update_progress, self.process_finished,
                  self.update_status, self.show_cloud_warning,
                  spreadsheet_id, json_key_path, mode)
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
