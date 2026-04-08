"""
Full simulation test — mensimulasikan persis logika aplikasi
dengan file pesanan_harian.xlsx dan folder master sebenarnya.
"""
import os, json, re, time
import pandas as pd
from collections import defaultdict

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
    cfg = json.load(f)

FILE_PESANAN   = cfg['file_pesanan']
FILE_DATABASE  = cfg['file_database']
FOLDER_MASTER  = cfg['folder_master']

print("=" * 65)
print("FULL SIMULATION TEST")
print("=" * 65)
print(f"  Pesanan : {FILE_PESANAN}")
print(f"  Database: {FILE_DATABASE}")
print(f"  Master  : {FOLDER_MASTER}")
print()

# ── 1. Baca file pesanan ──────────────────────────────────────
print("[1] Membaca file pesanan...")
df_pes = pd.read_excel(FILE_PESANAN)
print(f"    Kolom: {list(df_pes.columns)}")
print(f"    Total baris: {len(df_pes)}")
print(f"    Sample 5 baris:")
print(df_pes.head().to_string(index=False))
print()

# ── 2. Baca database ──────────────────────────────────────────
print("[2] Membaca database SKU...")
df_db = pd.read_excel(FILE_DATABASE)
print(f"    Kolom: {list(df_db.columns)}")
print(f"    Total baris: {len(df_db)}")
print()

# ── 3. Scan folder master ─────────────────────────────────────
print("[3] Scanning folder master...")
t0 = time.perf_counter()
file_index = {}
for root, dirs, files in os.walk(FOLDER_MASTER):
    for fname in files:
        file_index[fname] = os.path.join(root, fname)
elapsed = time.perf_counter() - t0
print(f"    Total file: {len(file_index):,} dalam {elapsed:.1f}s")

# Cek ekstensi
from collections import Counter
exts = Counter(os.path.splitext(f)[1].lower() for f in file_index)
print(f"    Ekstensi: {dict(exts.most_common(8))}")

cdr_files = [f for f in file_index if f.lower().endswith('.cdr')]
print(f"    File .cdr: {len(cdr_files):,}")
if cdr_files:
    print(f"    Contoh .cdr (5 pertama): {cdr_files[:5]}")

# Case-insensitive index
file_index_ci = {k.lower(): v for k, v in file_index.items()}
print()

# ── 4. Normalisasi SKU (fungsi baru) ──────────────────────────
def normalkan_sku(sku_raw):
    sku = sku_raw.strip()
    sku = sku.replace('ATM-', 'ANM-')
    _parts = sku.split('-')
    if len(_parts) >= 3 and _parts[-1].upper() in ['L', 'S', 'M', 'BS']:
        _angka = _parts[-2]
        _prev  = _parts[-3] if len(_parts) >= 4 else ''
        if _angka.isdigit() and len(_angka) < 7 and not _prev.isdigit():
            _parts[-2] = _angka.zfill(7)
            sku = '-'.join(_parts)
    return sku

# ── 5. Simulasi pencarian per baris pesanan ───────────────────
print("[4] Simulasi pencarian file (semua baris pesanan)...")
print("-" * 65)

found = 0
not_found = 0
not_found_list = []
skipped = 0

ATURAN_BUNDLE = {'S': 5, 'M': 5, 'BS': 10}

def parse_dynamic_sku(sku_bundle_dasar):
    pattern = r'(?:GK-)?(.*?)-(\d+)-(\d+)$'
    match = re.search(pattern, sku_bundle_dasar, re.IGNORECASE)
    if match:
        kategori_mentah = match.group(1)
        kategori = re.sub(r'-?SET-?', '', kategori_mentah, flags=re.IGNORECASE).strip('-')
        start = int(match.group(2))
        end   = int(match.group(3))
        return [f"GK-{kategori}-{str(i).zfill(7)}" for i in range(start, end + 1)]
    return []

for idx, row in df_pes.iterrows():
    try:
        sku_raw = str(row['sku']).strip()
        sku = normalkan_sku(sku_raw)
        ukuran = sku.split('-')[-1].upper()

        if ukuran not in ['L', 'S', 'M', 'BS']:
            print(f"  [SKIP] Baris {idx+2}: ukuran '{ukuran}' tidak valid — {sku!r}")
            skipped += 1
            continue

        sku_bundle_dasar = sku.rsplit('-', 1)[0]

        # Tentukan mode
        is_bundle = False
        sku_dasar_list = []

        if '-SET-' in sku.upper():
            is_bundle = True
        elif ukuran in ['S', 'M', 'BS']:
            kolom = 'sku_bundling_base_bs' if ukuran == 'BS' else 'sku_bundling_base'
            if kolom in df_db.columns:
                matched = df_db[df_db[kolom] == sku_bundle_dasar]['sku_individu_base'].tolist()
                if matched:
                    is_bundle = True
                    sku_dasar_list = matched
            if not is_bundle:
                dyn = parse_dynamic_sku(sku_bundle_dasar)
                if dyn:
                    is_bundle = True
                    sku_dasar_list = dyn

        if is_bundle:
            if not sku_dasar_list:
                kolom = 'sku_bundling_base_bs' if ukuran == 'BS' else 'sku_bundling_base'
                if kolom in df_db.columns:
                    sku_dasar_list = df_db[df_db[kolom] == sku_bundle_dasar]['sku_individu_base'].tolist()
                if not sku_dasar_list:
                    sku_dasar_list = parse_dynamic_sku(sku_bundle_dasar)
            if ukuran in ATURAN_BUNDLE:
                sku_dasar_list = sku_dasar_list[:ATURAN_BUNDLE[ukuran]]
        else:
            sku_dasar_list = [sku_bundle_dasar]

        # Cari file
        for sku_dasar in sku_dasar_list:
            nama_cari = f"{sku_dasar}-{ukuran}.cdr"
            nama_cari_l = nama_cari.lower()

            exact = nama_cari in file_index
            ci    = nama_cari_l in file_index_ci

            if exact:
                found += 1
            else:
                not_found += 1
                not_found_list.append({
                    'sku_raw': sku_raw,
                    'sku_norm': sku,
                    'nama_cari': nama_cari,
                    'ci_match': ci,
                    'ci_actual': os.path.basename(file_index_ci[nama_cari_l]) if ci else None,
                })

    except Exception as e:
        print(f"  [ERR] Baris {idx+2}: {e}")
        skipped += 1

print(f"    Ditemukan (exact)      : {found}")
print(f"    Tidak ditemukan        : {not_found}")
print(f"    Dilewati (format salah): {skipped}")
print()

# ── 6. Analisis yang tidak ditemukan ──────────────────────────
if not_found_list:
    ci_matches    = [x for x in not_found_list if x['ci_match']]
    no_ci_matches = [x for x in not_found_list if not x['ci_match']]

    if ci_matches:
        print(f"[!] {len(ci_matches)} file BEDA HURUF BESAR/KECIL (case mismatch):")
        for x in ci_matches[:5]:
            print(f"    Cari  : {x['nama_cari']!r}")
            print(f"    Aktual: {x['ci_actual']!r}")
        print()

    print(f"[!] {len(no_ci_matches)} file benar-benar tidak ada di folder master.")
    if no_ci_matches:
        print(f"    Contoh 10 pertama yang tidak ditemukan:")
        for x in no_ci_matches[:10]:
            print(f"    [{x['sku_raw']!r}] → cari: {x['nama_cari']!r}")
        print()

    # Cek pola nama file CDR yang tersedia vs yang dicari
    print("[5] Perbandingan pola nama file:")
    print(f"    Contoh file .cdr aktual di folder master:")
    for f in cdr_files[:8]:
        print(f"      {f!r}")
    print(f"    Contoh nama file yang DICARI tapi tidak ada:")
    for x in no_ci_matches[:8]:
        print(f"      {x['nama_cari']!r}")

print()
print("=" * 65)
print("SELESAI")
