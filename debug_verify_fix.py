"""
Quick verification: apakah fix alias ATM/ANM berhasil menemukan file?
"""
import os, json, re, time

with open('config.json') as f:
    cfg = json.load(f)

FOLDER = cfg['folder_master']
FILE_PES = cfg['file_pesanan']

import pandas as pd

# Build index dengan alias
print('Scanning + building alias index...')
t0 = time.perf_counter()
index = {}
for root, dirs, files in os.walk(FOLDER):
    for fname in files:
        index[fname] = os.path.join(root, fname)
        if 'GK-ATM-' in fname:
            alias = fname.replace('GK-ATM-', 'GK-ANM-', 1)
            if alias not in index:
                index[alias] = os.path.join(root, fname)
        elif 'GK-ANM-' in fname:
            alias = fname.replace('GK-ANM-', 'GK-ATM-', 1)
            if alias not in index:
                index[alias] = os.path.join(root, fname)

print(f'Index size: {len(index):,} dalam {time.perf_counter()-t0:.1f}s')
print()

# Normalisasi SKU
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

ATURAN_BUNDLE = {'S': 5, 'M': 5, 'BS': 10}

def parse_dynamic_sku(base):
    import re
    m = re.search(r'(?:GK-)?(.*?)-(\d+)-(\d+)$', base, re.IGNORECASE)
    if m:
        kat = re.sub(r'-?SET-?', '', m.group(1), flags=re.IGNORECASE).strip('-')
        return [f"GK-ANM-{str(i).zfill(7)}" for i in range(int(m.group(2)), int(m.group(3))+1)]
    return []

df_pes = pd.read_excel(FILE_PES)
df_db  = pd.read_excel(cfg['file_database'])

found = 0
not_found = 0
not_found_ex = []

for idx, row in df_pes.iterrows():
    try:
        sku = normalkan_sku(str(row['sku']))
        ukuran = sku.split('-')[-1].upper()
        if ukuran not in ['L','S','M','BS']:
            continue
        sku_base = sku.rsplit('-',1)[0]

        is_bundle = False
        sku_list = []
        if '-SET-' in sku.upper():
            is_bundle = True
        elif ukuran in ['S','M','BS']:
            col = 'sku_bundling_base_bs' if ukuran=='BS' else 'sku_bundling_base'
            if col in df_db.columns:
                m = df_db[df_db[col]==sku_base]['sku_individu_base'].tolist()
                if m:
                    is_bundle = True
                    sku_list = m
            if not is_bundle:
                d = parse_dynamic_sku(sku_base)
                if d:
                    is_bundle = True
                    sku_list = d

        if is_bundle:
            if not sku_list:
                col = 'sku_bundling_base_bs' if ukuran=='BS' else 'sku_bundling_base'
                if col in df_db.columns:
                    sku_list = df_db[df_db[col]==sku_base]['sku_individu_base'].tolist()
                if not sku_list:
                    sku_list = parse_dynamic_sku(sku_base)
            if ukuran in ATURAN_BUNDLE:
                sku_list = sku_list[:ATURAN_BUNDLE[ukuran]]
        else:
            sku_list = [sku_base]

        for sd in sku_list:
            nama = f'{sd}-{ukuran}.cdr'
            if nama in index:
                found += 1
            else:
                not_found += 1
                if len(not_found_ex) < 5:
                    not_found_ex.append(nama)
    except Exception as e:
        print(f'ERR baris {idx}: {e}')

print(f'Hasil setelah fix alias:')
print(f'  Ditemukan   : {found}')
print(f'  Tidak ketemu: {not_found}')
if not_found_ex:
    print(f'  Contoh tidak ditemukan:')
    for x in not_found_ex:
        print(f'    {repr(x)}')
print()
print('SUKSES!' if not_found == 0 else f'Masih ada {not_found} file tidak ditemukan - mungkin memang belum ada di folder master.')
