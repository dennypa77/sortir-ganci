"""
Auto-update aplikasi via ``git pull --rebase --autostash``.

Alur kerja yang dirancang untuk laptop karyawan (clone fresh, tidak ada
modifikasi lokal):

1. ``run.bat`` memanggil ``python -m core.updater`` SEBELUM start GUI.
2. Updater melakukan ``git fetch origin main`` lalu bandingkan SHA HEAD
   lokal dengan ``origin/main``.
3. Jika berbeda → ``git pull --rebase --autostash`` lalu tulis flag
   ``.last_update.json`` berisi versi sebelum/sesudah update.
4. ``sortir_desain.py`` saat startup memanggil :func:`consume_update_flag`
   untuk menampilkan notifikasi "Berhasil update vX → vY", lalu menghapus
   flag (one-shot, tidak muncul lagi sampai ada update berikutnya).

Update bersifat **silent auto-apply** (tanpa prompt sebelum pull) sesuai
permintaan: notifikasi muncul SETELAH update sukses, bukan sebelumnya.

Edge cases yang ditangani:

- **Tidak ada koneksi internet** → ``git fetch`` gagal → status
  ``no-internet``, app tetap lanjut dengan versi lokal.
- **Bukan git repo** (mis. user download zip) → status ``no-git``, app
  tetap lanjut.
- **Konflik rebase / autostash** → ``git rebase --abort`` dipanggil
  defensively agar working tree tidak terjebak di state mid-rebase.
- **Output git non-ASCII** → ``encoding='utf-8', errors='replace'``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple

from core.version import get_version


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


UPDATE_FLAG_FILE = os.path.join(_repo_root(), ".last_update.json")


def _git(*args) -> Tuple[int, str, str]:
    """Jalankan ``git <args>``. Return ``(returncode, stdout, stderr)`` (stripped)."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=_repo_root(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except FileNotFoundError:
        # git tidak ada di PATH
        return 127, "", "git executable not found"
    except OSError as e:
        return 1, "", str(e)


@dataclass
class UpdateResult:
    """Hasil cek & update.

    ``status`` ∈ {``updated``, ``current``, ``no-internet``, ``no-git``,
    ``error``}. ``old_version`` & ``new_version`` selalu terisi (sama kalau
    tidak ada perubahan). ``message`` opsional untuk diagnostik.
    """

    status: str
    old_version: str
    new_version: str
    message: str = ""


def check_and_update() -> UpdateResult:
    """
    Cek update di GitHub & apply jika ada.

    Return:
        :class:`UpdateResult` dengan status sesuai outcome. Function ini
        TIDAK raise — semua failure direport via ``status``.
    """
    old_version = get_version()

    # 1. Pastikan ini git repo & git tersedia.
    rc, _, _ = _git("rev-parse", "--git-dir")
    if rc != 0:
        return UpdateResult(
            status="no-git",
            old_version=old_version,
            new_version=old_version,
            message="Bukan git repo atau git tidak terinstall",
        )

    # 2. Fetch remote main (silent). Network error → no-internet.
    rc, _, err = _git("fetch", "origin", "main")
    if rc != 0:
        return UpdateResult(
            status="no-internet",
            old_version=old_version,
            new_version=old_version,
            message=err or "git fetch gagal",
        )

    # 3. Bandingkan HEAD lokal dgn origin/main.
    rc1, local_head, _ = _git("rev-parse", "HEAD")
    rc2, remote_head, _ = _git("rev-parse", "origin/main")
    if rc1 != 0 or rc2 != 0:
        return UpdateResult(
            status="error",
            old_version=old_version,
            new_version=old_version,
            message="Gagal membaca SHA HEAD lokal/remote",
        )

    if local_head == remote_head:
        return UpdateResult(
            status="current",
            old_version=old_version,
            new_version=old_version,
            message="Sudah versi terbaru",
        )

    # 4. Pull dgn rebase + autostash (preserve uncommitted changes).
    rc, out, err = _git("pull", "--rebase", "--autostash", "origin", "main")
    if rc != 0:
        # Defensive: abort rebase jika sedang mid-rebase agar working tree
        # tidak terjebak. `rebase --abort` aman dipanggil meski tidak ada
        # rebase in-progress (akan exit non-zero diam-diam).
        _git("rebase", "--abort")
        return UpdateResult(
            status="error",
            old_version=old_version,
            new_version=old_version,
            message=(err or out or "git pull gagal"),
        )

    new_version = get_version()
    return UpdateResult(
        status="updated",
        old_version=old_version,
        new_version=new_version,
        message=out,
    )


def write_update_flag(result: UpdateResult) -> None:
    """Tulis flag file untuk dikonsumsi GUI sebagai notifikasi 'Update Berhasil'."""
    payload = {
        "old_version": result.old_version,
        "new_version": result.new_version,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        with open(UPDATE_FLAG_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except OSError:
        # Jangan ganggu run flow kalau gagal tulis flag.
        pass


def consume_update_flag() -> Optional[dict]:
    """
    Baca & hapus flag update.

    Return ``dict`` berisi ``old_version``, ``new_version``, ``timestamp``
    bila ada update yang baru saja sukses. Return ``None`` bila tidak ada
    update sejak run terakhir.

    Sifat one-shot: file dihapus setelah dibaca → notifikasi tidak muncul
    berulang kali.
    """
    if not os.path.exists(UPDATE_FLAG_FILE):
        return None
    try:
        with open(UPDATE_FLAG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    finally:
        try:
            os.remove(UPDATE_FLAG_FILE)
        except OSError:
            pass
    return data


def main() -> int:
    """CLI entry point untuk dipanggil dari ``run.bat``.

    Output text-friendly muncul di terminal cmd window sebelum GUI. Selalu
    exit 0 supaya run.bat lanjut menjalankan aplikasi meski update gagal.
    """
    print("=" * 55)
    print("  Memeriksa update dari GitHub...")
    print("=" * 55)

    result = check_and_update()

    if result.status == "updated":
        print(f"  [OK] Update berhasil: {result.old_version} -> {result.new_version}")
        write_update_flag(result)
    elif result.status == "current":
        print(f"  [OK] Sudah versi terbaru ({result.old_version})")
    elif result.status == "no-internet":
        print(
            f"  [SKIP] Tidak bisa konek ke GitHub. "
            f"Lanjut dgn versi lokal ({result.old_version})."
        )
    elif result.status == "no-git":
        print(f"  [SKIP] Git tidak tersedia. Lanjut dgn versi lokal ({result.old_version}).")
    else:  # error
        print(f"  [WARN] Update gagal: {result.message}")
        print(f"         Lanjut dgn versi lokal ({result.old_version}).")

    print("")
    return 0


if __name__ == "__main__":
    sys.exit(main())
