import os, json
from collections import Counter

with open('config.json') as f:
    cfg = json.load(f)

FOLDER = cfg['folder_master']
print('Scanning CDR files...')
files = []
for root, dirs, fs in os.walk(FOLDER):
    for fname in fs:
        if fname.lower().endswith('.cdr'):
            files.append(fname)

print(f'Total .cdr: {len(files)}')
print()

# Distribusi suffix (part terakhir sebelum .cdr)
suffix_count = Counter()
for f in files:
    name = os.path.splitext(f)[0]
    parts = name.split('-')
    suffix = parts[-1].upper() if parts else '?'
    suffix_count[suffix] += 1

print('Distribusi suffix terakhir file .cdr:')
for suf, cnt in suffix_count.most_common(20):
    print(f'  {repr(suf)}: {cnt}')
print()

# File mengandung BS
bs_files = [f for f in files if 'BS' in f.upper()]
print(f'File mengandung "BS": {len(bs_files)}')
if bs_files:
    for f in bs_files[:10]:
        print(f'  {repr(f)}')
else:
    print('  (tidak ada sama sekali!)')
print()

# Cek nomor 3851-3860
print('Cek file untuk nomor 3851-3860:')
for num in range(3851, 3861):
    pattern = 'GK-ANM-' + str(num).zfill(7)
    matches = [f for f in files if pattern in f]
    status = repr(matches) if matches else 'TIDAK ADA'
    print(f'  {pattern}: {status}')

# Cek nomor 2797 (dari pesanan tunggal L)
print()
print('Cek nomor 2797 (GK-ANM-0002797):')
matches = [f for f in files if '0002797' in f]
print(f'  {repr(matches) if matches else "TIDAK ADA"}')

# Tampilkan 20 contoh file random
print()
print('20 contoh nama file .cdr di folder master:')
for f in files[::max(1, len(files)//20)][:20]:
    print(f'  {repr(f)}')
