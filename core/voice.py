"""
Voice playback untuk slot number di stasiun sortir.

Saat packer scan charm dan aplikasi tunjuk slot tujuan (mis. slot 7),
modul ini mainkan suara TTS Indonesia ("tujuh") supaya packer tidak perlu
melihat layar — bisa fokus ke kotak fisik.

File audio (`audio/slot_NN.wav` atau `slot_NN.mp3`) di-generate sekali
lewat ``generate_audio.py`` pakai voice neural Microsoft
(``id-ID-GadisNeural``).

Backend playback (urutan preferensi):

1. **winsound + WAV** — paling reliable di Windows, built-in di Python,
   non-blocking via ``SND_ASYNC``, auto-cancel saat play berikutnya
   (cocok untuk scan rapid). Butuh ``slot_NN.wav``.
2. **MCI + MP3** — fallback bila WAV tidak ada. Pakai ``winmm.dll`` via
   ctypes, manual thread management.

Public API:
    * :func:`set_audio_dir`  — set lokasi folder ``audio/``.
    * :func:`is_available`   — apakah voice playback siap dipakai?
    * :func:`play_slot_voice` — putar audio untuk slot ``n`` (non-blocking).
    * :func:`last_error`     — pesan error terakhir (untuk diagnostik).

Semua fungsi gracefully no-op kalau:
    * OS bukan Windows.
    * Folder audio tidak ada / file tidak ada untuk slot tsb.
"""

from __future__ import annotations

import itertools
import os
import sys
import threading

_audio_dir: str = ""
_mci = None
_winsound = None
_alias_counter = itertools.count(1)
_lock = threading.Lock()
_active_alias: str = ""
_last_error: str = ""

if sys.platform == "win32":
    try:
        import winsound
        _winsound = winsound
    except ImportError:
        _winsound = None

    try:
        import ctypes
        from ctypes import wintypes
        _mci = ctypes.windll.winmm
        _mci.mciSendStringW.argtypes = [
            wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.UINT, wintypes.HANDLE,
        ]
        _mci.mciSendStringW.restype = wintypes.DWORD
        _mci.mciGetErrorStringW.argtypes = [
            wintypes.DWORD, wintypes.LPWSTR, wintypes.UINT,
        ]
        _mci.mciGetErrorStringW.restype = wintypes.BOOL
    except (OSError, AttributeError):
        _mci = None


def _mci_err(rc: int) -> str:
    if rc == 0 or _mci is None:
        return "OK"
    try:
        buf = ctypes.create_unicode_buffer(256)
        _mci.mciGetErrorStringW(rc, buf, 256)
        return f"RC={rc} ({buf.value})"
    except Exception:
        return f"RC={rc}"


def set_audio_dir(path: str) -> None:
    """Set folder berisi ``slot_NN.wav`` / ``slot_NN.mp3``. Sekali saat startup."""
    global _audio_dir
    _audio_dir = path


def is_available() -> bool:
    """True kalau voice playback siap dipakai."""
    if not (_audio_dir and os.path.isdir(_audio_dir)):
        return False
    return _winsound is not None or _mci is not None


def last_error() -> str:
    """Pesan error terakhir (kosong = OK)."""
    return _last_error


def _wav_path_for(n: int) -> str:
    return os.path.join(_audio_dir, f"slot_{n:02d}.wav")


def _mp3_path_for(n: int) -> str:
    return os.path.join(_audio_dir, f"slot_{n:02d}.mp3")


# ── Backend 1: winsound + WAV (preferred) ──────────────────────────────────
def _play_wav(path: str) -> None:
    """
    Putar WAV non-blocking via ``winsound.PlaySound``.

    ``SND_ASYNC`` = return langsung tanpa nunggu selesai.
    ``SND_FILENAME`` = path adalah file path (bukan resource name).
    Panggilan baru otomatis interrupt yang sebelumnya — perfect untuk
    scan rapid.
    """
    global _last_error
    try:
        flags = _winsound.SND_FILENAME | _winsound.SND_ASYNC | _winsound.SND_NODEFAULT
        _winsound.PlaySound(path, flags)
        _last_error = ""
    except Exception as e:
        _last_error = f"winsound: {e}"


# ── Backend 2: MCI + MP3 (fallback) ────────────────────────────────────────
def _stop_active_locked() -> None:
    global _active_alias
    if _active_alias:
        _mci.mciSendStringW(f'stop {_active_alias}', None, 0, 0)
        _mci.mciSendStringW(f'close {_active_alias}', None, 0, 0)
        _active_alias = ""


def _play_mci_blocking(path: str, alias: str) -> None:
    global _active_alias, _last_error
    open_cmd = f'open "{path}" type mpegvideo alias {alias}'
    try:
        with _lock:
            _stop_active_locked()
            rc = _mci.mciSendStringW(open_cmd, None, 0, 0)
            if rc != 0:
                _last_error = f"mci open: {_mci_err(rc)}"
                return
            _active_alias = alias
        rc = _mci.mciSendStringW(f'play {alias} wait', None, 0, 0)
        if rc != 0:
            _last_error = f"mci play: {_mci_err(rc)}"
        else:
            _last_error = ""
    finally:
        with _lock:
            if _active_alias == alias:
                _mci.mciSendStringW(f'close {alias}', None, 0, 0)
                _active_alias = ""


# ── Public dispatcher ──────────────────────────────────────────────────────
def play_slot_voice(n: int) -> None:
    """
    Putar audio TTS untuk slot ``n`` (non-blocking).

    Coba WAV dulu (winsound, paling reliable di Windows), fallback ke MP3
    (MCI) bila WAV tidak ada. Slot di luar 1..99 → silent.
    """
    if not (_audio_dir and os.path.isdir(_audio_dir)):
        return

    # Backend 1: WAV via winsound — paling reliable, no thread needed.
    if _winsound is not None:
        wav = _wav_path_for(n)
        if os.path.isfile(wav):
            _play_wav(wav)
            return

    # Backend 2: MP3 via MCI fallback.
    if _mci is not None:
        mp3 = _mp3_path_for(n)
        if os.path.isfile(mp3):
            alias = f"slot_voice_{next(_alias_counter)}"
            t = threading.Thread(
                target=_play_mci_blocking, args=(mp3, alias), daemon=True,
            )
            t.start()
