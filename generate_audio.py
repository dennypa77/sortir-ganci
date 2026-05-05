"""
Generate file audio TTS untuk slot voice di stasiun_sortir.

Sekali-jalan: butuh ``edge-tts`` (online sekali saat run, tapi runtime
aplikasi TIDAK perlu edge-tts — hanya butuh winsound built-in untuk
memutar WAV).

Output:
  * ``audio/slot_NN.mp3`` — file utama dari edge-tts.
  * ``audio/slot_NN.wav`` — hasil konversi via ``imageio-ffmpeg``.
    Runtime prefer WAV (winsound, paling reliable). Kalau ``imageio-ffmpeg``
    tidak terinstall, hanya MP3 yang dibuat — runtime fallback ke MCI.

Run:
    pip install edge-tts imageio-ffmpeg
    python generate_audio.py
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys

import edge_tts

try:
    import imageio_ffmpeg
    _FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except ImportError:
    _FFMPEG = None

VOICE = "id-ID-GadisNeural"  # voice modern, perempuan, natural
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audio")

# 1..9
SATUAN = ["", "satu", "dua", "tiga", "empat", "lima", "enam", "tujuh", "delapan", "sembilan"]
# 10..19
BELASAN = {
    10: "sepuluh", 11: "sebelas", 12: "dua belas", 13: "tiga belas",
    14: "empat belas", 15: "lima belas", 16: "enam belas", 17: "tujuh belas",
    18: "delapan belas", 19: "sembilan belas",
}


def number_to_id(n: int) -> str:
    """Convert 1..99 ke kata Indonesia. ``20 → 'dua puluh'``, ``45 → 'empat puluh lima'``."""
    if 1 <= n <= 9:
        return SATUAN[n]
    if 10 <= n <= 19:
        return BELASAN[n]
    if 20 <= n <= 99:
        puluhan, satuan = divmod(n, 10)
        word = f"{SATUAN[puluhan]} puluh"
        if satuan:
            word += f" {SATUAN[satuan]}"
        return word
    raise ValueError(f"Angka di luar jangkauan 1-99: {n}")


def _convert_to_wav(mp3_path: str) -> bool:
    """Konversi MP3 → WAV (PCM 16-bit 22050Hz mono) bila ``ffmpeg`` ada."""
    if _FFMPEG is None:
        return False
    wav_path = mp3_path.replace(".mp3", ".wav")
    if os.path.exists(wav_path):
        return True
    r = subprocess.run(
        [_FFMPEG, "-loglevel", "error", "-y", "-i", mp3_path,
         "-ar", "22050", "-ac", "1", "-acodec", "pcm_s16le", wav_path],
        capture_output=True,
    )
    return r.returncode == 0


async def generate_one(n: int) -> None:
    """Generate file MP3 (+ WAV bila ffmpeg tersedia) untuk angka ``n``."""
    text = number_to_id(n)
    out_mp3 = os.path.join(OUTPUT_DIR, f"slot_{n:02d}.mp3")
    out_wav = out_mp3.replace(".mp3", ".wav")

    if not os.path.exists(out_mp3):
        comm = edge_tts.Communicate(text, VOICE)
        await comm.save(out_mp3)
        print(f"  + {n:2d}: {text!r} -> {os.path.basename(out_mp3)}")

    if not os.path.exists(out_wav):
        if _convert_to_wav(out_mp3):
            print(f"     wav: {os.path.basename(out_wav)}")
        else:
            if _FFMPEG is None:
                pass  # silent — runtime akan pakai MCI fallback
            else:
                print(f"     wav: GAGAL konversi", file=sys.stderr)


async def main() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print(f"Generating audio ke {OUTPUT_DIR} (voice: {VOICE})")
    print(f"Total: 99 file (1..99)")
    print()
    for n in range(1, 100):
        try:
            await generate_one(n)
        except Exception as e:
            print(f"  ✗ {n}: gagal — {e}", file=sys.stderr)
    print()
    print(f"Selesai. {len(os.listdir(OUTPUT_DIR))} file di {OUTPUT_DIR}")


if __name__ == "__main__":
    asyncio.run(main())
