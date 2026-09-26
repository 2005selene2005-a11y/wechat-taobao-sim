import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path=None):
    """极简 .env 加载：不覆盖已存在的环境变量。"""
    p = Path(path) if path else ROOT / '.env'
    if not p.is_file():
        return
    for line in p.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


load_env()


def _f(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


def _i(name, default):
    try:
        return int(float(os.environ.get(name, default)))
    except ValueError:
        return int(default)


def _s(name, default=''):
    v = os.environ.get(name)
    return default if v is None or v == '' else v


HOST = _s('HOST', '127.0.0.1')
PORT = _i('PORT', 8000)

_data = Path(_s('DATA_DIR', './data'))
DATA_DIR = _data if _data.is_absolute() else (ROOT / _data)
DATA_DIR = DATA_DIR.resolve()
DB_PATH = DATA_DIR / 'app.db'
MEDIA_DIR = DATA_DIR / 'media'
STICKER_DIR = DATA_DIR / 'stickers'
AVATAR_DIR = DATA_DIR / 'avatars'
STATIC_DIR = ROOT / 'static'
SEED_DIR = ROOT / 'seed'

LLM_BASE_URL = _s('LLM_BASE_URL', 'https://openrouter.ai/api/v1').rstrip('/')
LLM_API_KEY = _s('LLM_API_KEY')
LLM_MODEL = _s('LLM_MODEL', 'deepseek/deepseek-chat')
LLM_TEMPERATURE = _f('LLM_TEMPERATURE', 0.9)
LLM_VISION = _i('LLM_VISION', 0) == 1

USER_NAME = _s('USER_NAME', '我')
AI_NAME = _s('AI_NAME', 'AI')
_persona = Path(_s('PERSONA_FILE', 'persona.example.md'))
PERSONA_FILE = _persona if _persona.is_absolute() else (ROOT / _persona)

USER_INIT_BALANCE = _f('USER_INIT_BALANCE', 1000)
AI_INIT_BALANCE = _f('AI_INIT_BALANCE', 1000)
PACKET_MAX_SINGLE = _f('PACKET_MAX_SINGLE', 200)
AI_DAILY_LIMIT = _f('AI_DAILY_LIMIT', 300)
PACKET_EXPIRE_HOURS = _f('PACKET_EXPIRE_HOURS', 24)
AI_GIFT_DAILY_LIMIT = _i('AI_GIFT_DAILY_LIMIT', 2)
AI_GIFT_MAX_PRICE = _f('AI_GIFT_MAX_PRICE', 300)

REPLY_DELAY_SCALE = _f('REPLY_DELAY_SCALE', 1)
REPLY_DEBOUNCE_SECONDS = _f('REPLY_DEBOUNCE_SECONDS', 4)
AI_RECALL_RETYPE = _i('AI_RECALL_RETYPE', 0) == 1
AI_RECALL_RETYPE_PROB = _f('AI_RECALL_RETYPE_PROB', 0.04)
HISTORY_LIMIT = _i('HISTORY_LIMIT', 30)
RECALL_SECONDS = 120
DEV_API = _i('DEV_API', 0) == 1

for _d in (DATA_DIR, MEDIA_DIR, STICKER_DIR, AVATAR_DIR):
    _d.mkdir(parents=True, exist_ok=True)
