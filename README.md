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
- Sediakan rak / kotak slot bernomor 1 sampai N (sesuai jumlah resi hari itu).
- Tempel label nomor di tiap slot.
- Letakkan barcode scanner USB di samping komputer (scanner USB umumnya
  langsung dikenali sebagai keyboard, tidak butuh driver).

### Workflow

1. Klik `run-stasiun.bat` → window stasiun sortir terbuka.

2. Klik tombol **📂 Load Pesanan** → pilih `pesanan_harian.xlsx`. Aplikasi
   akan baca pesanan, expand bundle (mis. `GK-ANM-SET-81-90-S` → 5 charm
   individu), lalu assign nomor slot ke tiap resi (sortir naik per nomor resi).

3. Status bar akan tampil:
   ```
   Resi: 150     Charm: 487     Sudah Sortir: 0/487     [░░░░░░░░░░]
   ```

4. Cursor langsung fokus ke kotak scan. Ambil 1 charm dari wadah campur,
   scan barcode SKU. Salah satu dari 3 hal akan tampil di display besar:

   | Tampilan | Arti | Aksi Packer |
   |---|---|---|
   | **✅ DROP CHARM DI SLOT INI**<br>Slot **42** (hijau besar) | SKU ini dibutuhkan resi di slot 42 | Drop charm ke slot 42 |
   | **⚠️ SUDAH LENGKAP**<br>✕ (oranye) | SKU dikenal tapi semua resi yang butuh sudah lengkap — kelebihan produksi | Simpan ke kotak "sisa stok" |
   | **❌ SKU TIDAK TERDAFTAR**<br>? (merah) | Barcode tidak match SKU manapun di pesanan | Cek ulang barcode / desain salah cetak |

5. Setiap scan otomatis mengurangi counter dan membunyikan beep berbeda untuk
   tiap status (match=tinggi, overflow=sedang, unknown=rendah ganda).

6. Riwayat 10 scan terakhir tampil di bawah untuk audit cepat.

7. Saat semua charm sudah masuk slot resinya, layar menampilkan:
   ```
   🎉 SELESAI — semua charm sudah masuk slot resinya!
   ```

### SKU lookup yang fleksibel
Aplikasi menerima beberapa format barcode untuk SKU yang sama:

- `GK-ATM-0010750-L` (format full padded)
- `GK-ATM-10750-L` (tanpa padding 7-digit)
- `GK-ANM-10750-L` (alias ATM↔ANM)
- `atm-10750-l` (lowercase)

Semua dikenali sebagai SKU yang sama → packer tidak perlu khawatir kalau
sticker barcode pakai format yang sedikit berbeda.

### Tombol Reset
Klik 🔄 **Reset** untuk muat ulang pesanan dari file (mis. kalau ada
revisi pesanan harian). Akan minta konfirmasi.

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
└── core/
    ├── sku_utils.py            ← parse/normalize/bundle SKU (di-share 2 app)
    ├── pesanan.py              ← load_pesanan_demand() untuk stasiun
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
