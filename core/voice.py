"""
Voice playback untuk slot number di stasiun sortir.

Saat packer scan charm dan aplikasi tunjuk slot tujuan (mis. slot 7),
modul ini mainkan suara TTS Indonesia ("tujuh") supaya packer tidak perlu
melihat layar — bisa fokus ke kotak fisik.

File audio (`audio/slot_NN.mp3`) di-generate sekali lewat ``generate_audio.py``
pakai voice neural Microsoft (``id-ID-GadisNeural``). Runtime hanya butuh
Windows MCI (winmm.dll, built-in) — tidak ada Python dependency tambahan.

Public API:
    * :func:`set_audio_dir`  — set lokasi folder ``audio/``.
    * :func:`is_available`   — apakah voice playback siap dipakai?
    * :func:`play_slot_voice` — putar audio untuk slot ``n`` (non-blocking).

Semua fungsi gracefully no-op kalau:
    * OS bukan Windows.
    * Folder audio tidak ada / file ``slot_NN.mp3`` tidak ada.
    * winmm.dll tidak bisa di-load.
"""

from __future__ import annotations

import itertools
import os
import sys
import threading

_audio_dir: str = ""
_mci = None
_alias_counter = itertools.count(1)
_lock = threading.Lock()
_active_alias: str = ""  # alias dari audio yang sedang main (untuk di-stop bila tertimpa)

if sys.platform == "win32":
    try:
        import ctypes
        _mci = ctypes.windll.winmm
    except (OSError, AttributeError):
        _mci = None


def set_audio_dir(path: str) -> None:
    """Set folder berisi ``slot_NN.mp3``. Dipanggil sekali saat startup."""
    global _audio_dir
    _audio_dir = path


def is_available() -> bool:
    """True kalau voice playback siap dipakai (Windows + folder audio ada)."""
    return _mci is not None and bool(_audio_dir) and os.path.isdir(_audio_dir)


def _audio_path_for(n: int) -> str:
    return os.path.join(_audio_dir, f"slot_{n:02d}.mp3")


def _stop_active_locked() -> None:
    """Stop & close audio yang sedang aktif. Caller harus pegang ``_lock``."""
    global _active_alias
    if _active_alias:
        _mci.mciSendStringW(f'stop {_active_alias}', None, 0, 0)
        _mci.mciSendStringW(f'close {_active_alias}', None, 0, 0)
        _active_alias = ""


def _play_mci_blocking(path: str, alias: str) -> None:
    """
    Putar file MP3 sampai selesai pakai Windows MCI.

    Jalankan di thread terpisah supaya UI tidak freeze. Bila ada audio lain
    yang masih main saat fungsi ini dipanggil, audio sebelumnya di-stop
    dulu agar tidak overlap.
    """
    global _active_alias
    quoted = path.replace('"', '')  # MCI tidak ramah quote di nama file
    open_cmd = f'open "{quoted}" type mpegvideo alias {alias}'
    try:
        with _lock:
            _stop_active_locked()
            rc = _mci.mciSendStringW(open_cmd, None, 0, 0)
            if rc != 0:
                return  # gagal open — silent (mungkin codec MP3 tidak ada)
            _active_alias = alias
        # ``play <alias> wait`` blok thread ini sampai selesai → bisa close.
        _mci.mciSendStringW(f'play {alias} wait', None, 0, 0)
    finally:
        with _lock:
            # Hanya close kalau alias ini masih yang aktif. Kalau ada call
            # lain yang sudah override _active_alias, dia yang akan close.
            if _active_alias == alias:
                _mci.mciSendStringW(f'close {alias}', None, 0, 0)
                _active_alias = ""


def play_slot_voice(n: int) -> None:
    """
    Putar audio TTS untuk slot ``n`` (non-blocking).

    Spawn thread daemon supaya scan berikutnya tidak nunggu audio selesai.
    Bila masih ada audio sebelumnya yang main, audio itu di-cut agar
    feedback selalu update terhadap scan terakhir.

    Slot di luar 1..99 → silent (file tidak di-generate).
    """
    if not is_available():
        return
    path = _audio_path_for(n)
    if not os.path.isfile(path):
        return  # file tidak ada (mis. slot > 99)
    alias = f"slot_voice_{next(_alias_counter)}"
    t = threading.Thread(target=_play_mci_blocking, args=(path, alias), daemon=True)
    t.start()
