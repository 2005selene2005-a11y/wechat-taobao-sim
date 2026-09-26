"""表情包：内置几个原创 SVG + 用户上传（存放在 DATA_DIR/stickers）。"""
import json
from pathlib import Path

from . import config as C

BUILTIN = C.STATIC_DIR / 'stickers'
EXTS = ('.svg', '.png', '.jpg', '.jpeg', '.gif', '.webp')


def _load_desc(d: Path):
    try:
        return json.loads((d / '_desc.json').read_text(encoding='utf-8'))
    except Exception:
        return {}


def desc():
    d = _load_desc(BUILTIN)
    d.update(_load_desc(C.STICKER_DIR))
    return d


def names():
    out = []
    for d in (BUILTIN, C.STICKER_DIR):
        if d.is_dir():
            out += [f.name for f in sorted(d.iterdir()) if f.suffix.lower() in EXTS and not f.name.startswith('_')]
    return out


def path(name):
    name = Path(name).name
    for d in (C.STICKER_DIR, BUILTIN):
        p = d / name
        if p.is_file() and p.suffix.lower() in EXTS:
            return p
    return None


def save_desc(name, text):
    d = _load_desc(C.STICKER_DIR)
    d[name] = text[:60]
    (C.STICKER_DIR / '_desc.json').write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding='utf-8')
