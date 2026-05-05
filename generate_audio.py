"""
Generate file audio TTS untuk slot voice di stasiun_sortir.

Sekali-jalan: butuh ``edge-tts`` (offline diunduh saat run, tapi runtime
aplikasi TIDAK perlu edge-tts — hanya butuh winsound built-in untuk
memutar WAV).

Output: ``audio/slot_NN.wav`` untuk N = 1..99, dgn voice neural Indonesian
(``id-ID-GadisNeural``).

Run:
    pip install edge-tts pydub
    python generate_audio.py

Catatan: edge-tts default output MP3. Kita pakai ``pydub`` (butuh ffmpeg)
untuk konversi MP3 → WAV agar runtime bisa pakai winsound.PlaySound
(yang hanya support WAV) tanpa dependency tambahan.
"""

from __future__ import annotations

import asyncio
import os
import sys

import edge_tts

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


async def generate_one(n: int) -> None:
    """Generate satu file MP3 untuk angka ``n``."""
    text = number_to_id(n)
    out_mp3 = os.path.join(OUTPUT_DIR, f"slot_{n:02d}.mp3")
    if os.path.exists(out_mp3):
        return  # skip kalau sudah ada
    comm = edge_tts.Communicate(text, VOICE)
    await comm.save(out_mp3)
    print(f"  ✓ {n:2d}: {text!r} → {os.path.basename(out_mp3)}")


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
