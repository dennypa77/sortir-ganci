"""
Versi aplikasi yang di-derive dari git.

Format: ``<base>.<commit_count>-<sha7>`` (contoh: ``7.1.42-a3f2c1d``).

`VERSION_BASE` di-bump manual di file ini saat ada perubahan major/minor
yang ingin ditandai (mis. dari 7.1 → 7.2). Komponen `commit_count` & `sha7`
otomatis naik tiap commit baru di-push ke main → versi terlihat di title bar
otomatis bertambah tanpa perlu edit file ini.

Bila git tidak tersedia (mis. user download zip alih-alih clone), fungsi
gracefully fallback ke ``VERSION_BASE`` saja agar UI tetap bisa start.
"""

from __future__ import annotations

import os
import subprocess

VERSION_BASE = "7.1"


def _repo_root() -> str:
    """Lokasi root proyek (parent dari folder `core/`)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _git(*args) -> str:
    """Jalankan `git <args>` di repo root, return stdout (stripped) atau '' bila error."""
    try:
        out = subprocess.check_output(
            ["git", *args],
            cwd=_repo_root(),
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return out.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return ""


def get_version() -> str:
    """
    Return versi aplikasi.

    - Sukses git: ``<VERSION_BASE>.<count>-<sha7>``  (mis. ``7.1.42-a3f2c1d``)
    - Tanpa git:  ``VERSION_BASE``                    (mis. ``7.1``)
    """
    count = _git("rev-list", "--count", "HEAD")
    sha = _git("rev-parse", "--short=7", "HEAD")
    if count and sha:
        return f"{VERSION_BASE}.{count}-{sha}"
    return VERSION_BASE


def get_short_version() -> str:
    """Versi tanpa sha7 untuk konteks ruang sempit. Mis: ``7.1.42``."""
    count = _git("rev-list", "--count", "HEAD")
    if count:
        return f"{VERSION_BASE}.{count}"
    return VERSION_BASE


def get_commit_sha() -> str:
    """SHA commit HEAD (40 char), atau '' bila git tidak tersedia."""
    return _git("rev-parse", "HEAD")
