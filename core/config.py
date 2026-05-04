"""
Layered config loading di-share antar `sortir_desain.py` & `stasiun_sortir.py`.

Skema dua file:

- ``config.json``       — template tracked git, di-overwrite tiap ``git pull``.
                          JANGAN simpan path personal di sini.
- ``user_config.json``  — override per-mesin, gitignored. Field non-empty di
                          file ini menang atas template.

Pertimbangan: empty string di ``user_config.json`` SENGAJA diabaikan saat merge
supaya nilai non-empty di template (mis. ``spreadsheet_id`` shared) tidak
ter-clobber jika user belum pernah menyentuh field tsb di UI.
"""

from __future__ import annotations

import json
import os

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_CONFIG  = os.path.join(_BASE_DIR, 'config.json')
USER_CONFIG_FILE = os.path.join(_BASE_DIR, 'user_config.json')

DEFAULT_CONFIG = {
    'file_pesanan':    'pesanan_harian.xlsx',
    'file_database':   'database_sku.xlsx',
    'folder_master':   '',
    'folder_output':   '',
    'spreadsheet_id':  '',     # ID Google Sheets (dari URL)
    'json_key_path':   '',     # Path ke file JSON Service Account
    'mode':            1,
}


def _read_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def load_config():
    """Merge: ``DEFAULT_CONFIG`` → ``config.json`` (template) → ``user_config.json``."""
    cfg = DEFAULT_CONFIG.copy()
    cfg.update(_read_json(TEMPLATE_CONFIG))
    user_cfg = _read_json(USER_CONFIG_FILE)
    for k, v in user_cfg.items():
        if v != '' and v is not None:
            cfg[k] = v
    return cfg


def save_config(cfg: dict) -> None:
    """Tulis ``cfg`` ke ``user_config.json`` saja — template tidak disentuh."""
    try:
        with open(USER_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except OSError:
        pass
