@echo off
REM ===========================================================
REM == Tombol Start Stasiun Sortir Ganci (scan-to-sort)      ==
REM == File ini harus 1 folder dengan stasiun_sortir.py      ==
REM ===========================================================

cd /d "%~dp0"

echo.
echo ============================================================
echo  Memeriksa update aplikasi...
echo ============================================================
python -m core.updater

echo.
echo ============================================================
echo  Menjalankan Stasiun Sortir...
echo  (scan barcode SKU, lihat layar untuk nomor slot resi)
echo ============================================================
echo.

python stasiun_sortir.py

echo.
echo ============================================================
echo Stasiun Sortir telah ditutup.
echo ============================================================
echo.

pause
