"""
Script diagnostik — jalankan ini untuk mengecek penyebab 0 file diproses.
"""
import os, json, re, time
import pandas as pd

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')

def normalkan_sku(sku):
    sku = sku.replace('ATM-', 'ANM-')
    _parts = sku.split('-')
    if len(_parts) >= 3 and _parts[-1].upper() in ['L', 'S', 'M', 'BS']:
        _angka = _parts[-2]
        _prev  = _parts[-3] if len(_parts) >= 4 else ''
        if _angka.isdigit() and len(_angka) < 7 and not _prev.isdigit():
            _parts[-2] = _angka.zfill(7)
            sku = '-'.join(_parts)
    return sku

# Test normalisasi
test_cases = [
    ("GK-ATM-SET-3851-3860-BS", "GK-ANM-SET-3851-3860-BS"),   # SET: tidak dipad
    ("GK-ATM-SET-631-640-BS",   "GK-ANM-SET-631-640-BS"),      # SET: tidak dipad
    ("GK-ATM-0002797-L",        "GK-ANM-0002797-L"),           # tunggal: sudah 7 digit
    ("GK-ATM-487-L",            "GK-ANM-0000487-L"),           # tunggal: perlu pad
    ("GK-ANM-0003851-BS",       "GK-ANM-0003851-BS"),          # tunggal BS: tidak dipad (sudah 7 digit)
]
print("=" * 60)
print("TEST NORMALISASI SKU:")
all_ok = True
for sku_in, expected in test_cases:
    result = normalkan_sku(sku_in)
    ok = "✅" if result == expected else "❌"
    if result != expected:
        all_ok = False
    print(f"  {ok} {sku_in!r}")
    print(f"       → {result!r}  (expected: {expected!r})")
print(f"\n  Semua test: {'LULUS ✅' if all_ok else 'ADA YANG GAGAL ❌'}")
print()

print("=" * 60)
print("CONFIG:")
for k, v in cfg.items():
    print(f"  {k}: {v!r}")
print()

# 1. Cek file pesanan
file_pes = cfg['file_pesanan']
print(f"[1] Cek File Pesanan: {file_pes}")
print(f"    Exists: {os.path.exists(file_pes)}")
if os.path.exists(file_pes):
    df = pd.read_excel(file_pes)
    print(f"    Kolom: {list(df.columns)}")
    print(f"    5 baris pertama:")
    print(df.head().to_string())
print()

# 2. Cek folder master
folder_master = cfg['folder_master']
print(f"[2] Cek Folder Master: {folder_master}")
print(f"    Exists: {os.path.exists(folder_master)}")
if os.path.exists(folder_master):
    t0 = time.perf_counter()
    index = {}
    for root, dirs, files in os.walk(folder_master):
        for fname in files:
            index[fname] = os.path.join(root, fname)
    print(f"    Total file terindeks: {len(index)}")
    
    if index:
        # Tampilkan 10 sampel file
        samples = list(index.keys())[:10]
        print(f"    10 sampel nama file:")
        for s in samples:
            print(f"      {s!r}")
        
        # Cek ekstensi
        from collections import Counter
        exts = Counter(os.path.splitext(f)[1].lower() for f in index.keys())
        print(f"    Ekstensi file: {dict(exts.most_common(10))}")
        
        # Coba cari file .cdr
        cdr_files = [f for f in index.keys() if f.lower().endswith('.cdr')]
        print(f"    File .cdr (lower): {len(cdr_files)}")
        if cdr_files:
            print(f"    Contoh .cdr: {cdr_files[:5]}")
    
    print(f"    Waktu scan: {time.perf_counter()-t0:.1f}s")
print()

# 3. Simulasi pencarian SKU pertama dari pesanan
if os.path.exists(file_pes) and os.path.exists(folder_master):
    df_pes = pd.read_excel(file_pes)
    print(f"[3] Simulasi pencarian 5 SKU pertama:")
    
    # Build index case-insensitive juga
    index_lower = {fname.lower(): fpath for fname, fpath in index.items()}

    pad_fn = lambda m: f"{m.group(1)}-{m.group(2).zfill(7)}-{m.group(3).upper()}"
    
    for i, row in df_pes.head(5).iterrows():
        try:
            sku = str(row['sku']).strip()
            sku = sku.replace('ATM-', 'ANM-')
            sku = re.sub(r'^(.*?)-(\d+)-(L|S|M|BS)$', pad_fn, sku, flags=re.IGNORECASE)
            ukuran = sku.split('-')[-1].upper()
            sku_dasar = sku.rsplit('-', 1)[0]
            nama_cari = f"{sku_dasar}-{ukuran}.cdr"
            nama_cari_l = nama_cari.lower()
            
            found_exact = nama_cari in index
            found_ci = nama_cari_l in index_lower
            
            print(f"  SKU: {sku!r}")
            print(f"    Cari: {nama_cari!r}")
            print(f"    Exact match: {found_exact}")
            print(f"    Case-insensitive match: {found_ci}")
            if found_ci and not found_exact:
                actual = index_lower[nama_cari_l]
                print(f"    Nama aktual: {os.path.basename(actual)!r}")
            print()
        except Exception as e:
            print(f"  Error baris {i}: {e}")

print("=" * 60)
print("SELESAI")
