# Robot Sortir Ganci

Tools internal untuk produksi ganci akrilik. Repo ini berisi **dua aplikasi**
yang berbagi infrastruktur core (parsing SKU, bundle resolution, auto-update):

| Aplikasi | File | Kapan Dipakai |
|---|---|---|
| **Robot Sortir Desain** | `sortir_desain.py` (jalankan via `run.bat`) | Setelah pesanan harian masuk — auto-sortir file desain (.cdr) dari folder master ke folder output per-resi |
| **Stasiun Sortir** | `stasiun_sortir.py` (jalankan via `run-stasiun.bat`) | Setelah laser cutting — scan barcode charm → sistem tunjuk slot resi mana yang butuh charm itu |

---

## Daftar Isi

1. [Prasyarat](#1-prasyarat)
2. [Setup pertama kali di komputer baru](#2-setup-pertama-kali-di-komputer-baru)
3. [Menjalankan aplikasi](#3-menjalankan-aplikasi)
4. [Tutorial — Robot Sortir Desain](#4-tutorial--robot-sortir-desain)
5. [Tutorial — Stasiun Sortir](#5-tutorial--stasiun-sortir)
6. [Auto-update](#6-auto-update)
7. [Struktur file & config](#7-struktur-file--config)
8. [Troubleshooting](#8-troubleshooting)

---

## 1. Prasyarat

Program akan berjalan di Windows 10/11. Tiga tools harus terinstall **sekali
saja** di tiap komputer:

1. **Python 3.10+** — https://www.python.org/downloads/
   Saat install, centang **"Add python.exe to PATH"**.

2. **Git for Windows** — https://git-scm.com/download/win
   Klik next-next-finish dengan setting default.

3. **Microsoft Excel** atau LibreOffice (untuk membuka file .xlsx hasil log).

> **Cek instalasi**: buka cmd (tekan `Win+R`, ketik `cmd`, Enter), lalu jalankan:
> ```
> python --version
> git --version
> ```
> Kedua perintah harus mengeluarkan nomor versi (bukan error).

---

## 2. Setup pertama kali di komputer baru

Lakukan langkah berikut **sekali per komputer** (mis. saat onboarding karyawan
baru, atau pindah ke laptop baru).

### Langkah 1 — Clone repo dari GitHub

Buka cmd di folder tempat kamu mau menyimpan aplikasi (mis. `D:\Project\`):

```
cd D:\Project
git clone https://github.com/dennypa77/sortir-ganci.git
cd sortir-ganci
```

### Langkah 2 — Install dependencies Python

```
pip install -r requirements.txt
```

Daftar dependency: `pandas`, `openpyxl`, `gspread`, `google-auth`,
`customtkinter`. Total ±50MB, sekali install.

### Langkah 3 — Salin file Service Account Google

File JSON service account (mis. `ganci-sortir-512bcf3d9183.json`) **TIDAK**
ikut clone karena di-gitignore (sensitif). Owner aplikasi (Denny) harus
mengirim file ini secara terpisah (via Drive / WhatsApp / USB) dan tempatkan
di folder `sortir-ganci/` (root repo, sejajar dengan `sortir_desain.py`).

> Tanpa file ini, aplikasi tetap jalan tapi fitur sinkronisasi Google Sheets
> akan di-skip otomatis (warning di log).

### Langkah 4 — Konfigurasi path lewat UI

Klik dua kali `run.bat`. UI Robot Sortir Desain akan terbuka. Pada tab
**"Pengaturan File & Folder"**, isi:

- **Data Pesanan** — pilih `pesanan_harian.xlsx` (file harian dari operasional)
- **Database SKU** — pilih `database_sku.xlsx` (database master bundle)
- **Folder Master** — pilih folder Google Drive yang berisi semua file .cdr master
- **Folder Output** — pilih folder lokal tempat hasil sortir disimpan

Tab **"Integrasi Google Sheets"**:

- **Spreadsheet ID** — sudah terisi dari template (jangan diubah kecuali Denny minta)
- **File JSON Key** — pilih file `ganci-sortir-512bcf3d9183.json` yang barusan disalin

Tutup aplikasi sekali. Setting akan tersimpan ke `user_config.json` lokal —
**file ini tidak ikut ke GitHub** sehingga setting tiap komputer tidak akan
saling menimpa.

### Langkah 5 — Selesai

Setiap kali kamu klik `run.bat` atau `run-stasiun.bat` setelah ini, aplikasi
otomatis cek update dari GitHub, lalu jalan dengan setting yang sudah disimpan.

---

## 3. Menjalankan aplikasi

| Aksi | Cara |
|---|---|
| Jalankan **Robot Sortir Desain** | Klik dua kali `run.bat` |
| Jalankan **Stasiun Sortir** | Klik dua kali `run-stasiun.bat` |

Kedua launcher akan:

1. Cek update aplikasi (`python -m core.updater` → git pull kalau ada versi baru).
2. Tampilkan output update di terminal cmd.
3. Buka aplikasi GUI.

Kalau update barusan terjadi, popup notifikasi muncul setelah aplikasi siap:

> ✅ **Aplikasi Telah Diupdate**
> Versi sebelumnya : 7.1.5-aaaaaaa
> Versi sekarang   : 7.1.6-bbbbbbb

---

## 4. Tutorial — Robot Sortir Desain

### Tujuan
Otomatis mengumpulkan file desain (.cdr) dari folder master ke folder output,
diorganisir per resi. Output siap dibuka di CorelDraw untuk dilayout & dicetak.

### Input yang dibutuhkan
- `pesanan_harian.xlsx` dengan kolom: `resi`, `sku`, `jumlah`
- `database_sku.xlsx` dengan kolom: `sku_bundling_base`, `sku_bundling_base_bs`,
  `sku_individu_base` (untuk resolve bundle SKU non-numerik)
- Folder master desain (.cdr files)

### Mode Output

| Mode | Deskripsi | Kapan Dipakai |
|---|---|---|
| **Layout Masal** (default) | Semua file `.cdr` di-flatten dalam 1 folder, nama: `<sku>_<resi>_<urutan>.cdr` | Saat layout di Corel mau pakai banyak file sekaligus |
| **Sortir per Resi** | File dikelompokkan dalam subfolder per nomor resi | Saat audit per-resi atau cetak terpisah per pesanan |

Pilih lewat radio button di UI sebelum klik **▶ Mulai Pemrosesan**.

### Output yang dihasilkan
- Folder `Output/<tanggal>_LAYOUT_MASAL/` atau `Output/<tanggal>_SORTIR_PER_RESI/`
- `Output/log_<tanggal>.xlsx` — log lengkap (sheet "Semua Proses" + "Ambil Gudang")
- `Output/label_untuk_cetak_<tanggal>.xlsx` — daftar SKU untuk dicetak label

### Fitur Stok Gudang (otomatis)
Kalau Google Sheets tab `DATABASE_PRODUK` tersedia, aplikasi akan:

1. Cek stok ready di gudang sebelum cari file di folder master.
2. Kalau stok cukup → tandai sebagai "✨ Diambil dari Gudang" (file desain tidak di-copy karena sudah ada barang fisiknya).
3. Catat ke tab `LOG_KELUAR` di Google Sheets.
4. Kurangi stok di memori agar tidak bentrok dengan resi berikutnya di run yang sama.

### Sinkronisasi Google Sheets
Setelah proses selesai, baris pesanan di-append ke tab `Data_Sales` Google
Sheets (1 baris per kombinasi resi+SKU). Tanpa setup Google Sheets, fitur ini
di-skip diam-diam.

---

## 5. Tutorial — Stasiun Sortir

### Tujuan
Setelah laser cutting, 50-80 charm akrilik dari banyak order tumpah dalam
satu wadah campur. Packer butuh tahu **charm ini untuk resi yang mana?**.

Logika dibalik dari pendekatan manual: **bukan packer cari charm untuk resi,
tapi sistem tunjuk resi untuk charm**. Hasilnya, error salah-kirim SKU turun
drastis terutama saat volume tinggi atau SDM baru.

### Persiapan fisik
- Sediakan rak dengan kotak-kotak kosong (mis. 30 kotak) — jumlah kotak
  fisik **tidak harus** sama dengan jumlah resi total. Slot bisa di-recycle:
  saat kotak penuh diambil, slot itu bisa dipakai resi lain.
- Spidol untuk tulis nomor slot di kotak.
- Barcode scanner USB (langsung dikenali sebagai keyboard, tidak butuh driver).

### Aplikasi punya 2 mode

| Mode | Yang di-scan | Hasil |
|---|---|---|
| **📋 Setup Slot** (default) | Stiker resi | Aplikasi assign nomor slot — packer tulis nomor di kotak fisik |
| **📦 Sortir Charm** | Barcode SKU di charm | Aplikasi tunjuk slot mana yang butuh charm itu |

Tombol **Mode** di header untuk toggle. Bisa dipakai bolak-balik kapan saja —
tidak harus 100% setup dulu sebelum sortir.

### Workflow lengkap

**Step 1 — Load pesanan**

Klik `run-stasiun.bat` → klik 📂 **Load Pesanan** → pilih `pesanan_harian.xlsx`.
Aplikasi akan baca pesanan, expand bundle SKU sesuai aturan ukuran:

| Ukuran | Charm per Unit | Contoh |
|---|---|---|
| L | 1 | `GK-ANM-487-L` qty=2 → 2 charm |
| S | 5 | `GK-ANM-SET-1-5-S` qty=1 → 5 charm (1 sampai 5) |
| M | 5 | `GK-ANM-SET-1-5-M` qty=1 → 5 charm |
| BS | 10 | `GK-ANM-SET-1-10-BS` qty=1 → 10 charm |

**Step 2 — Setup slot (Mode SETUP)**

Aplikasi otomatis di mode SETUP. Status bar tampil "Setup: 0/N resi".

Untuk tiap resi:
1. Ambil kotak kosong dari rak.
2. Scan stiker resi pada label pengiriman.
3. Layar tampil **Slot N** (besar, ungu).
4. Tulis "N" di kotak fisik dengan spidol → taruh di rak (urutan bebas).
5. Lanjut ke resi berikutnya → Slot N+1, dst.

Edge cases yang ditangani:
- Scan resi yang sudah ter-register → tampil slot existing (no duplikat).
- Scan resi yang **tidak ada** di pesanan → warning merah.

**Step 3 — Sortir charm (Mode SORTIR)**

Klik tombol Mode → switch ke 📦 SORTIR. Cursor fokus ke input scan.

Untuk tiap charm di wadah campur:
1. Ambil 1 charm random.
2. Scan barcode SKU.
3. Layar tampil salah satu:

   | Tampilan | Arti | Aksi |
   |---|---|---|
   | ✅ **DROP CHARM DI SLOT INI**<br>Slot **42** (hijau besar) | Charm ini untuk resi di slot 42 | Drop charm ke kotak slot 42 |
   | ⚠️ **RESI BELUM TER-REGISTER** | SKU valid tapi resi yang butuh belum di-setup | Switch ke Mode SETUP, scan stiker resi tsb dulu |
   | ⚠️ **SUDAH LENGKAP** | SKU dikenal tapi semua resi sudah lengkap — kelebihan produksi | Simpan ke kotak "sisa stok" |
   | ❌ **SKU TIDAK TERDAFTAR** | Barcode tidak match SKU manapun di pesanan | Cek ulang barcode / desain salah cetak |

   Tiap status punya beep berbeda (match=tinggi, overflow=sedang, unknown=rendah ganda).

**Step 4 — Ambil kotak yang sudah lengkap (kapan saja)**

Saat semua charm untuk satu resi sudah masuk, tile slot di panel kanan
berubah **hijau** dengan label "✓ LENGKAP — klik untuk ambil". Packer bisa:

1. Ambil kotak fisik dari rak (charm-nya siap dikemas).
2. Klik tile slot hijau di panel kanan aplikasi → muncul confirm dialog.
3. Klik "Ya" → slot kembali ke pool (warna abu-abu, status "kosong").

**Step 5 — Recycle slot ke resi baru (opsional)**

Setelah slot dikosongkan, kembali ke Mode SETUP → scan stiker resi baru.
Aplikasi otomatis assign **slot terkecil yang tersedia** (slot yg baru saja
dikosongkan). Sehingga rak fisik 30 kotak bisa untuk 50+ resi sehari, dengan
turnover.

### Distribusi FCFS — saat 1 SKU dipesan banyak resi

Skenario: SKU `GK-ANM-487-L` dipesan oleh resi A (qty 2), B (qty 3), C (qty 1).
Total 6 charm.

Saat packer scan charm dengan SKU itu satu per satu, aplikasi otomatis:

| Scan ke- | Tujuan slot | State |
|---|---|---|
| 1 | Slot A (sisa 1) | A: 1/2 |
| 2 | Slot A (sisa 0) ✓ | A: 2/2 LENGKAP |
| 3 | Slot B (sisa 2) | B: 1/3 |
| 4 | Slot B (sisa 1) | B: 2/3 |
| 5 | Slot B (sisa 0) ✓ | B: 3/3 LENGKAP |
| 6 | Slot C (sisa 0) ✓ | C: 1/1 LENGKAP |
| 7 | ⚠️ SUDAH LENGKAP | overflow |

Sistem menjaga **no duplikat** — saat resi A penuh, scan berikutnya
otomatis lompat ke resi B. Tidak akan pernah dobel-isi resi A.

### SKU lookup yang fleksibel
Aplikasi menerima beberapa format barcode untuk SKU yang sama:

- `GK-ATM-0010750-L` (format full padded)
- `GK-ATM-10750-L` (tanpa padding 7-digit)
- `GK-ANM-10750-L` (alias ATM↔ANM)
- `atm-10750-l` (lowercase)

### Resume sesi setelah crash / restart

State sortir (slot map, scan progress, history) auto-save ke
`.scan_state.json` (gitignored) per scan. Saat aplikasi dibuka lagi:

- Kalau file pesanan masih sama (path + mtime) → dialog "Resume sesi?"
  muncul dengan ringkasan progress.
- Kalau file pesanan dimodifikasi → dialog tawarkan reset state lama.

Ini berarti packer bisa istirahat tengah hari, atau aplikasi crash, tanpa
kehilangan progress.

### Tombol Reset
Klik 🔄 **Reset** untuk hapus state & muat ulang pesanan dari file. Akan
minta konfirmasi.

---

## 6. Auto-update

### Cara kerja
Setiap kali `run.bat` atau `run-stasiun.bat` di-klik, sebelum aplikasi GUI
buka, terminal cmd akan menjalankan:

```
python -m core.updater
```

Yang melakukan: `git fetch origin main` → bandingkan SHA lokal dengan remote →
kalau berbeda, `git pull --rebase --autostash` → tulis flag `.last_update.json`.

Saat GUI siap, kalau ada flag → muncul popup "✅ Aplikasi Telah Diupdate"
dengan info versi sebelum/sesudah dan timestamp.

### Versi otomatis naik
Format versi: `<base>.<commit_count>-<sha7>` (mis. `7.1.42-a3f2c1d`).

- `base` = `7.1` (di-bump manual hanya saat ada perubahan major/minor)
- `commit_count` = jumlah commit di branch main → otomatis naik tiap push
- `sha7` = 7 karakter pertama SHA commit terakhir

Versi tampil di title bar dan header kedua aplikasi.

### Kalau internet mati / git tidak terinstall
Aplikasi tetap jalan dengan versi lokal yang ada. Terminal akan log:
```
[SKIP] Tidak bisa konek ke GitHub. Lanjut dgn versi lokal (7.1.42-a3f2c1d).
```

### Kalau ada konflik git (jarang)
Updater mendeteksi error pull → otomatis `git rebase --abort` → app jalan
dengan versi lokal. Detail error muncul di terminal. Hubungi Denny untuk
resolve manual kalau berulang.

---

## 7. Struktur file & config

```
sortir-ganci/
├── README.md                   ← file ini
├── requirements.txt            ← dependencies pip
├── run.bat                     ← launcher Robot Sortir Desain
├── run-stasiun.bat             ← launcher Stasiun Sortir
├── sortir_desain.py            ← aplikasi 1 (production, jangan ubah manual)
├── stasiun_sortir.py           ← aplikasi 2 (scan-to-sort)
├── config.json                 ← TEMPLATE config (di-track git, jangan isi path personal)
├── user_config.json            ← (gitignored) setting per-mesin, otomatis dibuat saat save UI
├── ganci-sortir-XXXX.json      ← (gitignored) Service Account Google — salin manual
├── pesanan_harian.xlsx         ← (gitignored) input harian
├── database_sku.xlsx           ← (gitignored) database master
├── Output/                     ← (gitignored) hasil run sortir
├── .scan_state.json            ← (gitignored) state stasiun sortir, auto-save per scan
├── .last_update.json           ← (gitignored) flag one-shot notif update
└── core/
    ├── sku_utils.py            ← parse/normalize/bundle SKU (di-share 2 app)
    ├── pesanan.py              ← load_pesanan_demand() untuk stasiun
    ├── state.py                ← state manager: slot map, persistence, recycling
    ├── config.py               ← layered config loader
    ├── version.py              ← derive versi dari git
    └── updater.py              ← logic auto-update
```

### Pemisahan `config.json` vs `user_config.json`

| File | Tracked git? | Berisi |
|---|---|---|
| `config.json` | ✅ ya | Template default — path relatif, spreadsheet_id shared. Di-overwrite saat git pull. |
| `user_config.json` | ❌ gitignored | Path lokal per-mesin. Tidak akan ditimpa update. |

**Field non-empty di `user_config.json` menang** atas `config.json`. Empty
string di user file diabaikan supaya nilai shared di template tetap terpakai.

Saat klik **Browse** di UI dan save, semua setting masuk ke `user_config.json`.

---

## 8. Troubleshooting

### "ModuleNotFoundError: No module named 'customtkinter'"
Dependencies belum terinstall. Buka cmd di folder repo:
```
pip install -r requirements.txt
```

### Aplikasi tidak auto-update — selalu bilang "[SKIP] Git tidak tersedia"
Git for Windows belum terinstall. Download dari https://git-scm.com/download/win.

### Aplikasi tidak auto-update — selalu bilang "[SKIP] Tidak bisa konek ke GitHub"
- Cek koneksi internet.
- Cek apakah folder repo masih `.git`-aware: jalankan `git status` di cmd
  pada folder tsb.
- Cek apakah firewall blok HTTPS ke github.com.

### "Sortir Selesai, tapi gagal update ke Cloud"
Service Account JSON tidak ditemukan, atau email service account belum
ditambah sebagai Editor di Google Sheets. Hubungi Denny untuk verifikasi
email `client_email` di JSON sudah di-share Editor.

### Karyawan dapat versi yang salah / config-nya hilang setelah pull
Cek apakah `user_config.json` masih ada di folder repo. Kalau tidak ada,
re-konfigurasi via UI sekali → akan otomatis dibuat ulang. File ini tidak
pernah di-overwrite oleh git pull.

### Stasiun Sortir bilang "SKU TIDAK TERDAFTAR" untuk SKU yang ada di pesanan
- Cek format barcode di sticker apakah sesuai SKU pesanan (terutama bagian angka padding).
- Cek apakah pesanan sudah di-load (ada notif "✓ PESANAN DIMUAT").
- Untuk SKU bundle (mis. `GK-ANM-PAKET-X-S`), pastikan `database_sku.xlsx`
  punya entri `sku_bundling_base` yang match.

### "Gagal membuat resep bundle '<sku>'"
SKU bundle tidak dikenali — bukan format range numerik (mis. `GK-ANM-SET-81-90`)
dan tidak ada di `database_sku.xlsx` kolom `sku_bundling_base`. Tambah entri
ke database SKU.

---

## Lisensi & maintainer

Repo internal Ganci Project. Maintainer: **Denny (@dennypa77)**.

Kontak teknis: lewat owner repo.
