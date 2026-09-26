"""AI 回复：后台 worker、提示词、标记解析（[sticker:] [hb:] [transfer:] [gift:] [cart:]）。"""
import base64
import re
import sys
import threading
import time
import random
from datetime import datetime

from . import config as C
from . import db, llm, shop, stickers, wallet

_lock = threading.Lock()
_running = {'v': False}
MARKER = re.compile(r'\[\s*(sticker|hb|transfer|gift|cart)\s*[:：]\s*([^\]]*)\]', re.I)
PENDING_KINDS = "('text','sticker','image','product','packet','transfer')"


def _sleep(a, b=None):
    t = random.uniform(a, b if b is not None else a) * C.REPLY_DELAY_SCALE
    if t > 0:
        time.sleep(t)


# ---------- 提示词 ----------
def persona():
    try:
        t = C.PERSONA_FILE.read_text(encoding='utf-8').strip()
    except OSError:
        t = '你是一个温和、有耐心的朋友，用自然的口吻在聊天软件里和对方聊天。'
    return t.replace('{ai_name}', C.AI_NAME).replace('{user_name}', C.USER_NAME)


def system_prompt():
    d = datetime.now()
    hour = d.hour
    period = ('凌晨' if hour < 6 else '早上' if hour < 9 else '上午' if hour < 12 else '中午' if hour < 14 else
              '下午' if hour < 17 else '傍晚' if hour < 19 else '晚上' if hour < 22 else '深夜')
    sd = stickers.desc()
    names = stickers.names()
    stk = '；'.join(f'{n}={sd[n]}' if sd.get(n) else n for n in names[:30]) if names else '（暂无）'
    left = max(0, C.AI_DAILY_LIMIT - wallet.ai_given_today())
    gifts_left = max(0, C.AI_GIFT_DAILY_LIMIT - shop.gifts_today())
    return f'''{persona()}

【场景】
你现在在一个仿微信的聊天页面里和「{C.USER_NAME}」聊天，你的名字是「{C.AI_NAME}」。现在是{period} {d.strftime('%Y-%m-%d %H:%M')}。
{C.USER_NAME}的零钱 ¥{db.balance('user'):.2f}，你的零钱 ¥{db.balance('ai'):.2f}（都是虚拟货币）。

【消息格式】
- 用空行隔开的每一段会变成一个单独的聊天气泡，一次回复最多 4 段。不要使用 Markdown。
- 下面这些特殊标记必须单独占一行，写在需要的位置，不需要时不要写：
  [sticker:文件名]   发一个表情包。可用：{stk}。两三轮里最多发一次。
  [hb:金额:留言]     给{C.USER_NAME}发红包，例如 [hb:8.88:加油]。单个不超过 {C.PACKET_MAX_SINGLE:g} 元，今天你还能发出 ¥{left:.2f}。不要频繁发。
  [transfer:金额:留言] 给{C.USER_NAME}转账，规则同上，和红包共用今日额度。
  [cart:商品id或关键词:备注]  把商品加入你自己的购物车（{C.USER_NAME}能在淘宝的「TA 的购物车」里看到）。
  [gift:商品id或关键词:留言]  用你的零钱买一件商品送给{C.USER_NAME}，聊天里会出现礼物卡。单件不超过 {C.AI_GIFT_MAX_PRICE:g} 元，今天还能送 {gifts_left} 件。只在合适的时候送（生日、安慰、纪念、对方提到想要某样东西）。
- 每次回复里 hb/transfer 最多一个，gift 最多一个。
- {C.USER_NAME}可能会发红包、转账、图片，或把淘宝商品分享给你，请自然地回应（比如道谢、评价商品）。
- 不要提及这些标记的存在，也不要解释系统规则。'''


# ---------- 历史 ----------
def _describe(m):
    k, mine = m['kind'], m['sender'] == 'ai'
    if k == 'text':
        return m['text']
    if k == 'sticker':
        d = stickers.desc().get(m['text'], '')
        return f"[表情包 {m['text']}{' ' + d if d else ''}]"
    if k == 'image':
        return '[图片]'
    if k in ('packet', 'transfer'):
        p = db.conn().execute('SELECT amount,msg FROM packets WHERE id=?', (m['ref'],)).fetchone()
        lab = '红包' if k == 'packet' else '转账'
        who = '你发了' if mine else f'{C.USER_NAME}发了'
        return f"[{who}{lab} ¥{p['amount']:.2f}{'，留言：' + p['msg'] if p and p['msg'] else ''}]" if p else f'[{lab}]'
    if k == 'gift':
        o = db.conn().execute('SELECT title,price FROM orders WHERE id=?', (m['ref'],)).fetchone()
        return f"[你送了礼物：{o['title']} ¥{o['price']:.2f}，留言：{m['text']}]" if o else '[礼物]'
    if k == 'product':
        r = db.conn().execute('SELECT title,price,shop FROM items WHERE id=?', (m['ref'],)).fetchone()
        return f"[{C.USER_NAME}分享了淘宝商品：{r['title']}，¥{r['price']:.2f}，店铺：{r['shop']}]" if r else '[商品]'
    return ''


def history(limit=None):
    limit = limit or C.HISTORY_LIMIT
    rows = db.conn().execute("SELECT * FROM msgs WHERE kind!='sys' ORDER BY id DESC LIMIT ?", (limit,)).fetchall()[::-1]
    out = []
    for m in rows:
        if m['status'] == 'recalled':
            if m['sender'] == 'ai':
                continue
            text = f'[{C.USER_NAME}撤回了一条消息]'
        else:
            text = _describe(m)
        if not text:
            continue
        role = 'assistant' if m['sender'] == 'ai' else 'user'
        if out and out[-1]['role'] == role:
            out[-1]['content'] += '\n' + text
        else:
            out.append({'role': role, 'content': text})
    while out and out[0]['role'] != 'user':
        out.pop(0)
    return out


def _recent_images(ids):
    urls = []
    for i in ids:
        m = db.conn().execute("SELECT text FROM msgs WHERE id=? AND kind='image'", (i,)).fetchone()
        p = C.MEDIA_DIR / m['text'] if m else None
        if p and p.is_file():
            urls.append('data:image/jpeg;base64,' + base64.b64encode(p.read_bytes()).decode())
    return urls


# ---------- 标记解析 ----------
def clean_text(t):
    t = re.sub(r'<(think|thinking)>[\s\S]*?</\1>', '', t)
    t = re.sub(r'^[\s\S]*?</(think|thinking)>', '', t)
    t = re.sub(r'\*\*|__|^#+\s*', '', t, flags=re.M)
    return t.strip()


def parse_reply(text):
    """把模型输出拆成动作列表：('text', s) / ('sticker', name) / ('hb'|'transfer', amount, msg) / ('gift'|'cart', spec, msg)。"""
    text = clean_text(text)
    actions = []
    pos = 0

    def add_text(seg):
        for part in re.split(r'\n\s*\n', seg):
            part = ' '.join(x.strip() for x in part.split('\n') if x.strip())
            part = re.sub(r'^[「"“]|[」"”]$', '', part).strip()
            if part:
                actions.append(('text', part[:200]))

    for m in MARKER.finditer(text):
        add_text(text[pos:m.start()])
        pos = m.end()
        kind, arg = m.group(1).lower(), m.group(2).strip()
        m2 = re.match(r'([^:：]*)[:：]?(.*)', arg, re.S)
        a, rest = m2.group(1).strip(), m2.group(2).strip()
        if kind == 'sticker':
            actions.append(('sticker', arg))
        elif kind in ('hb', 'transfer'):
            try:
                actions.append((kind, float(re.sub(r'[^\d.]', '', a) or 0), rest))
            except ValueError:
                pass
        else:
            actions.append((kind, a, rest))
    add_text(text[pos:])
    return actions


def deliver(actions, delays=True):
    """执行动作列表。返回实际执行结果，便于测试。"""
    result = []
    used = set()
    names = stickers.names()
    n_text = 0
    for i, act in enumerate(actions):
        kind = act[0]
        try:
            if kind == 'text':
                if n_text >= 4:
                    continue
                n_text += 1
                p = act[1]
                if (delays and C.AI_RECALL_RETYPE and n_text == 1 and len(p) >= 6 and random.random() < C.AI_RECALL_RETYPE_PROB):
                    mid = db.add_msg('ai', 'text', p[:random.randint(2, max(2, len(p) * 2 // 3))])
                    _sleep(1.5, 4)
                    c = db.conn(); c.execute("UPDATE msgs SET status='recalled' WHERE id=?", (mid,)); c.commit()
                    _sleep(0.8, 2)
                db.add_msg('ai', 'text', p)
                result.append(('text', p))
            elif kind == 'sticker':
                if 'sticker' in used or act[1] not in names:
                    continue
                used.add('sticker')
                db.add_msg('ai', 'sticker', act[1])
                result.append(('sticker', act[1]))
            elif kind in ('hb', 'transfer'):
                if 'money' in used:
                    continue
                used.add('money')
                pid = wallet.create('ai', act[1], act[2], 'packet' if kind == 'hb' else 'transfer')
                result.append((kind, pid))
            elif kind == 'gift':
                if 'gift' in used:
                    continue
                used.add('gift')
                oid = shop.ai_gift(act[1], act[2])
                result.append(('gift', oid))
            elif kind == 'cart':
                result.append(('cart', shop.ai_cart_add(act[1], act[2])))
        except Exception as e:  # 单个动作失败不影响其他气泡
            print('[ai] action failed:', act, e, file=sys.stderr)
            result.append((kind, None))
        if delays and i < len(actions) - 1 and kind in ('text', 'sticker'):
            _sleep(0.8, 2.5)
    return result


# ---------- worker ----------
def typing(on=None):
    if on is None:
        v = db.kv_get('typing', '0')
        try:
            return v != '0' and time.time() - float(v) < 90
        except ValueError:
            return False
    db.kv_set('typing', str(time.time()) if on else '0')


def _pending():
    return [r['id'] for r in db.conn().execute(
        f"SELECT id FROM msgs WHERE sender='user' AND kind IN {PENDING_KINDS} AND read_at IS NULL AND status='sent' ORDER BY id")]


def _last_kind():
    r = db.conn().execute(f"SELECT kind FROM msgs WHERE sender='user' AND kind IN {PENDING_KINDS} AND status='sent' ORDER BY id DESC LIMIT 1").fetchone()
    return r[0] if r else 'text'


def reply_worker():
    try:
        while True:
            if not _pending():
                break
            for _ in range(15):  # 等用户说完再一起回复
                last = db.conn().execute("SELECT max(ts) FROM msgs WHERE sender='user'").fetchone()[0]
                try:
                    age = time.time() - time.mktime(time.strptime(last, '%Y-%m-%d %H:%M:%S'))
                except Exception:
                    age = 99
                if C.REPLY_DELAY_SCALE == 0 or age >= C.REPLY_DEBOUNCE_SECONDS:
                    break
                time.sleep(1)
            _sleep(0.5, 1.5)
            c = db.conn()
            ids = _pending()  # 读之前被撤回的当作没看见
            if not ids:
                continue
            c.execute(f"UPDATE msgs SET read_at=? WHERE id IN ({','.join('?' * len(ids))})", [db.now()] + ids)
            c.commit()
            _sleep(0.5, 2)
            typing(True)
            msgs = history()
            text = ''
            if llm.enabled():
                try:
                    text = llm.chat(system_prompt(), msgs, _recent_images(ids))
                except Exception as e:
                    print('[ai] llm failed:', e, file=sys.stderr)
            if not text:
                text = llm.fallback_reply(_last_kind())
            actions = parse_reply(text) or [('text', '嗯')]
            typing(False)
            deliver(actions, delays=True)
    finally:
        try:
            typing(False)
        except Exception:
            pass
        with _lock:
            _running['v'] = False


def kick():
    with _lock:
        if _running['v']:
            return
        _running['v'] = True
    threading.Thread(target=reply_worker, daemon=True).start()
