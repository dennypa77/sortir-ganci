"""
Animasi UI ringan untuk customtkinter / tkinter.

Tk tidak punya transisi CSS-style, jadi semua animasi di sini berbasis
``widget.after(ms, callback)`` yang update properti widget step-by-step.
Frame rate target ~30fps (33 ms per frame) — cukup smooth tanpa lag pada
laptop low-end.

Pola pemanggilan:
    animations.animate_color(widget, "#7c6af7", "#23c78e", 300, set_fn)
    animations.pulse_color(tile, base, peak, 400, set_fn)
    animations.bounce_font(label, font_obj, base=64, peak=74, duration_ms=180)

Setiap animasi di-track via attribute ``_anim_<key>`` pada widget. Kalau
animasi dengan key yang sama dipanggil ulang sebelum yang lama selesai,
yang lama otomatis di-cancel — supaya panggilan beruntun (mis. scan
rapid) tidak menumpuk callback ratusan.

Fail-soft: kalau widget sudah destroyed di tengah animasi, exception
dari ``widget.after_cancel`` / ``configure`` dimakan diam-diam.
"""

from __future__ import annotations

from typing import Callable, Optional

# ~30fps. Lebih tinggi dari ini boros CPU di Tk (mainloop single-thread).
FRAME_MS = 33


# ── Color helpers ──────────────────────────────────────────────────────────
def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_to_hex(r: float, g: float, b: float) -> str:
    return f"#{int(round(r)):02x}{int(round(g)):02x}{int(round(b)):02x}"


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def interpolate_color(from_hex: str, to_hex: str, t: float) -> str:
    """Linear interpolation RGB. ``t ∈ [0, 1]``."""
    t = max(0.0, min(1.0, t))
    r1, g1, b1 = _hex_to_rgb(from_hex)
    r2, g2, b2 = _hex_to_rgb(to_hex)
    return _rgb_to_hex(_lerp(r1, r2, t), _lerp(g1, g2, t), _lerp(b1, b2, t))


# ── Anim manager: cancel previous animation pada key yang sama ─────────────
def _cancel_anim(widget, key: str) -> None:
    aid = getattr(widget, key, None)
    if aid:
        try:
            widget.after_cancel(aid)
        except Exception:
            pass
        setattr(widget, key, None)


def _safe_call(fn: Callable, *args) -> bool:
    """Panggil ``fn`` & return True kalau sukses; widget destroyed → False."""
    try:
        fn(*args)
        return True
    except Exception:
        return False


# ── Animasi warna ──────────────────────────────────────────────────────────
def animate_color(
    widget,
    from_hex: str,
    to_hex: str,
    duration_ms: int,
    set_fn: Callable[[str], None],
    on_done: Optional[Callable[[], None]] = None,
    key: str = "_anim_color",
) -> None:
    """
    Animate warna dari ``from_hex`` ke ``to_hex`` selama ``duration_ms``.

    ``set_fn(hex_color)`` dipanggil tiap frame untuk apply warna ke widget(s).
    Caller bertanggung jawab atas widget mana yang di-update.
    """
    _cancel_anim(widget, key)
    total_frames = max(1, duration_ms // FRAME_MS)

    def step(frame: int) -> None:
        t = frame / total_frames
        c = interpolate_color(from_hex, to_hex, t)
        if not _safe_call(set_fn, c):
            return  # widget destroyed
        if frame < total_frames:
            try:
                aid = widget.after(FRAME_MS, lambda: step(frame + 1))
                setattr(widget, key, aid)
            except Exception:
                pass
        else:
            setattr(widget, key, None)
            if on_done:
                _safe_call(on_done)

    step(0)


def pulse_color(
    widget,
    base_hex: str,
    peak_hex: str,
    duration_ms: int,
    set_fn: Callable[[str], None],
    on_done: Optional[Callable[[], None]] = None,
    key: str = "_anim_pulse",
) -> None:
    """
    Pulse: ``base → peak → base`` selama ``duration_ms`` total.

    Cocok untuk highlight singkat (mis. tile yang baru di-target oleh scan).
    """
    _cancel_anim(widget, key)
    half = max(1, duration_ms // 2)

    def back() -> None:
        animate_color(widget, peak_hex, base_hex, half, set_fn, on_done=on_done, key=key)

    animate_color(widget, base_hex, peak_hex, half, set_fn, on_done=back, key=key)


# ── Animasi font size (bounce / pop) ───────────────────────────────────────
def bounce_font(
    widget,
    font_obj,
    base_size: int,
    peak_size: int,
    duration_ms: int = 180,
    key: str = "_anim_font",
) -> None:
    """
    Bounce font size: ``base → peak → base`` linear.

    ``font_obj`` adalah :class:`customtkinter.CTkFont` shared dgn label.
    Update lewat ``font_obj.configure(size=...)`` lebih efisien daripada
    ``label.configure(font=...)`` karena tidak recreate font object.
    """
    _cancel_anim(widget, key)
    half = max(1, duration_ms // 2)
    half_frames = max(1, half // FRAME_MS)

    def up(frame: int) -> None:
        t = frame / half_frames
        size = int(round(_lerp(base_size, peak_size, t)))
        if not _safe_call(font_obj.configure, **{"size": size}):
            return
        if frame < half_frames:
            try:
                aid = widget.after(FRAME_MS, lambda: up(frame + 1))
                setattr(widget, key, aid)
            except Exception:
                pass
        else:
            down(0)

    def down(frame: int) -> None:
        t = frame / half_frames
        size = int(round(_lerp(peak_size, base_size, t)))
        if not _safe_call(font_obj.configure, **{"size": size}):
            return
        if frame < half_frames:
            try:
                aid = widget.after(FRAME_MS, lambda: down(frame + 1))
                setattr(widget, key, aid)
            except Exception:
                pass
        else:
            setattr(widget, key, None)
            # Pastikan kembali persis ke base (kalau ada rounding error)
            _safe_call(font_obj.configure, **{"size": base_size})

    up(0)
