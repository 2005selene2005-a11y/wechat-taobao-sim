"""仿淘宝：商品、搜索、购物车、下单、AI 送礼。"""
import json
import random
import re
import time
from html import escape

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import Response

from . import config as C
from . import db

router = APIRouter()


# ---------- 商品数据 ----------
def seed_items(force=False):
    c = db.conn()
    if not force and c.execute('SELECT count(*) FROM items').fetchone()[0]:
        return 0
    p = C.SEED_DIR / 'products.json'
    if not p.is_file():
        return 0
    data = json.loads(p.read_text(encoding='utf-8'))
    n = 0
    for it in data.get('products', []):
        c.execute('INSERT OR REPLACE INTO items(id,title,sub,price,orig,emoji,color,shop,mall,cat,sold,tags,active) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1)',
                  (it.get('id'), it['title'], it.get('sub', ''), float(it['price']), float(it.get('orig') or it['price']),
                   it.get('emoji', '🛍️'), it.get('color', '#ffd9c2'), it.get('shop', ''), int(it.get('mall', 0)),
                   it.get('cat', ''), int(it.get('sold', 0)), json.dumps(it.get('tags', []), ensure_ascii=False)))
        n += 1
    c.commit()
    return n


def categories():
    p = C.SEED_DIR / 'products.json'
    cats = json.loads(p.read_text(encoding='utf-8')).get('categories', []) if p.is_file() else []
    have = {r[0] for r in db.conn().execute('SELECT DISTINCT cat FROM items WHERE active=1')}
    out = [x for x in cats if x['name'] in have]
    known = {x['name'] for x in out}
    out += [{'name': n, 'emoji': '🛍️', 'color': '#ffe9d6'} for n in sorted(have - known)]
    return out


def _item(r, favs=(), cart=()):
    d = dict(r)
    d['tags'] = json.loads(d.get('tags') or '[]')
    d['fav'] = d['id'] in favs
    d['in_cart'] = d['id'] in cart
    d['img'] = f"/api/tb/img/{d['id']}.svg"
    return d


def _sets():
    c = db.conn()
    return {r[0] for r in c.execute('SELECT item_id FROM fav')}, {r[0] for r in c.execute('SELECT item_id FROM cart')}


def _words(q):
    return [w for w in re.split(r'[\s,，/、]+', q.strip()) if w]


def _hit(r, words):
    hay = ' '.join([r['title'] or '', r['sub'] or '', r['cat'] or '', r['tags'] or '', r['shop'] or ''])
    return sum(1 for w in words if w.lower() in hay.lower())


def find_item(spec):
    """把 id 或关键词解析成商品行；找不到返回 None。"""
    spec = (spec or '').strip()
    c = db.conn()
    if spec.isdigit():
        r = c.execute('SELECT * FROM items WHERE id=? AND active=1', (int(spec),)).fetchone()
        if r:
            return r
    words = _words(spec)
    if not words:
        return None
    rows = [r for r in c.execute('SELECT * FROM items WHERE active=1') if _hit(r, words)]
    if not rows:
        return None
    rows.sort(key=lambda r: (-_hit(r, words), r['price']))
    return rows[0]


# ---------- 商品图（服务端生成 SVG 占位图） ----------
@router.get('/api/tb/img/{iid}.svg')
def item_img(iid: int):
    r = db.conn().execute('SELECT title,emoji,color FROM items WHERE id=?', (iid,)).fetchone()
    if not r:
        raise HTTPException(404)
    color = r['color'] if re.fullmatch(r'#[0-9a-fA-F]{3,8}', r['color'] or '') else '#ffd9c2'
    title = escape((r['title'] or '')[:10])
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 400">'
           f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{color}"/>'
           f'<stop offset="1" stop-color="#ffffff" stop-opacity=".55"/></linearGradient></defs>'
           f'<rect width="400" height="400" fill="url(#g)"/>'
           f'<circle cx="200" cy="180" r="110" fill="#fff" fill-opacity=".45"/>'
           f'<text x="200" y="222" font-size="132" text-anchor="middle">{escape(r["emoji"] or "🛍️")}</text>'
           f'<text x="200" y="370" font-size="22" text-anchor="middle" fill="#000" fill-opacity=".45" '
           f'font-family="sans-serif">{title}</text></svg>')
    return Response(svg, media_type='image/svg+xml', headers={'Cache-Control': 'public,max-age=3600'})


# ---------- 浏览 ----------
@router.get('/api/tb/feed')
def feed(page: int = 0, seed: int = 0, cat: str = ''):
    favs, cart = _sets()
    rows = list(db.conn().execute('SELECT * FROM items WHERE active=1' + (' AND cat=?' if cat else ''), (cat,) if cat else ()))
    random.Random(seed or int(time.time() // 3600)).shuffle(rows)
    per = 10
    chunk = rows[page * per:(page + 1) * per]
    return {'items': [_item(r, favs, cart) for r in chunk], 'more': (page + 1) * per < len(rows),
            'balance': db.balance('user'), 'total': len(rows)}


@router.get('/api/tb/categories')
def api_categories():
    return categories()


@router.get('/api/tb/search')
def search(q: str = ''):
    q = q.strip()[:30]
    if not q:
        raise HTTPException(400, '请输入关键词')
    favs, cart = _sets()
    words = _words(q)
    rows = [r for r in db.conn().execute('SELECT * FROM items WHERE active=1') if _hit(r, words)]
    rows.sort(key=lambda r: (-_hit(r, words), -r['sold']))
    return {'q': q, 'items': [_item(r, favs, cart) for r in rows[:40]], 'balance': db.balance('user')}


@router.get('/api/tb/item/{iid}')
def item(iid: int):
    c = db.conn()
    r = c.execute('SELECT * FROM items WHERE id=? AND active=1', (iid,)).fetchone()
    if not r:
        raise HTTPException(404, '商品不存在')
    c.execute('INSERT INTO foot(ts,item_id) VALUES(?,?)', (db.now(), iid))
    c.commit()
    favs, cart = _sets()
    d = _item(r, favs, cart)
    d['balance'] = db.balance('user')
    d['similar'] = [_item(x, favs, cart) for x in c.execute(
        'SELECT * FROM items WHERE active=1 AND cat=? AND id!=? ORDER BY random() LIMIT 4', (r['cat'], iid))]
    return d


# ---------- 购物车 / 收藏 ----------
@router.post('/api/tb/cart')
def cart_op(payload: dict = Body(...)):
    iid = int(payload.get('id', 0))
    op = payload.get('op', 'add')
    c = db.conn()
    if op == 'add':
        if not c.execute('SELECT 1 FROM items WHERE id=? AND active=1', (iid,)).fetchone():
            raise HTTPException(404, '商品不存在')
        c.execute('INSERT OR REPLACE INTO cart(item_id,qty,ts) VALUES(?,1,?)', (iid, db.now()))
    else:
        c.execute('DELETE FROM cart WHERE item_id=?', (iid,))
    c.commit()
    return {'ok': True, 'n': c.execute('SELECT count(*) FROM cart').fetchone()[0]}


@router.get('/api/tb/cart')
def cart_list():
    favs, cart = _sets()
    rows = [_item(r, favs, cart) for r in db.conn().execute(
        'SELECT i.* FROM cart k JOIN items i ON i.id=k.item_id ORDER BY k.ts DESC')]
    return {'items': rows, 'balance': db.balance('user')}


@router.post('/api/tb/fav')
def fav_toggle(payload: dict = Body(...)):
    iid = int(payload.get('id', 0))
    c = db.conn()
    if c.execute('SELECT 1 FROM fav WHERE item_id=?', (iid,)).fetchone():
        c.execute('DELETE FROM fav WHERE item_id=?', (iid,))
        f = False
    else:
        c.execute('INSERT INTO fav(item_id,ts) VALUES(?,?)', (iid, db.now()))
        f = True
    c.commit()
    return {'ok': True, 'fav': f}


# ---------- 下单 ----------
@router.post('/api/tb/buy')
def buy(payload: dict = Body(...)):
    ids = [int(x) for x in payload.get('ids', [])]
    if not ids:
        raise HTTPException(400, '没选东西')
    c = db.conn()
    items = [dict(r) for r in c.execute(f"SELECT * FROM items WHERE active=1 AND id IN ({','.join('?' * len(ids))})", ids)]
    if not items:
        raise HTTPException(404, '商品不存在')
    total = round(sum(i['price'] for i in items), 2)
    bal = db.balance('user')
    if total > bal:
        raise HTTPException(400, f'零钱不够，还差 ¥{total - bal:.2f}')
    oids = []
    for i in items:
        cur = c.execute('INSERT INTO orders(ts,item_id,title,price,status,buyer,gift,msg) VALUES(?,?,?,?,?,?,0,?)',
                        (db.now(), i['id'], i['title'], i['price'], '已付款', 'user', ''))
        oids.append(cur.lastrowid)
        c.execute('DELETE FROM cart WHERE item_id=?', (i['id'],))
        db.ledger('user', 'shop', -i['price'], '淘宝 · ' + i['title'][:20], cur.lastrowid)
    c.commit()
    return {'ok': True, 'total': total, 'balance': db.balance('user'), 'orders': oids}


@router.get('/api/tb/orders')
def orders():
    rows = [dict(r) for r in db.conn().execute(
        'SELECT o.*, i.shop, i.emoji FROM orders o LEFT JOIN items i ON i.id=o.item_id ORDER BY o.id DESC')]
    for r in rows:
        r['img'] = f"/api/tb/img/{r['item_id']}.svg"
        r['from_ai'] = r['buyer'] == 'ai'
    return rows


@router.post('/api/tb/confirm')
def confirm(payload: dict = Body(...)):
    c = db.conn()
    c.execute("UPDATE orders SET status='已收货',done_at=? WHERE id=? AND status='已付款'", (db.now(), int(payload.get('id', 0))))
    c.commit()
    return {'ok': True}


@router.get('/api/tb/me')
def me():
    c = db.conn()
    o = {k: c.execute('SELECT count(*) FROM orders WHERE status=?', (k,)).fetchone()[0] for k in ('已付款', '已收货')}
    foot = [dict(r) for r in c.execute(
        'SELECT DISTINCT i.id,i.title,i.price FROM foot f JOIN items i ON i.id=f.item_id ORDER BY f.id DESC LIMIT 6')]
    for f in foot:
        f['img'] = f"/api/tb/img/{f['id']}.svg"
    gifts = c.execute('SELECT count(*) FROM orders WHERE gift=1').fetchone()[0]
    return {'balance': db.balance('user'), 'ai_balance': db.balance('ai'), 'orders': o, 'nfav': c.execute('SELECT count(*) FROM fav').fetchone()[0],
            'ncart': c.execute('SELECT count(*) FROM cart').fetchone()[0], 'foot': foot, 'gifts': gifts,
            'user_name': C.USER_NAME, 'ai_name': C.AI_NAME}


@router.get('/api/tb/ai')
def ai_side():
    """AI 的购物车 + 它送出的礼物。"""
    c = db.conn()
    cart = [dict(r) for r in c.execute(
        'SELECT x.id AS cart_id,x.note,x.ts,i.* FROM ai_cart x JOIN items i ON i.id=x.item_id ORDER BY x.id DESC')]
    for r in cart:
        r['img'] = f"/api/tb/img/{r['id']}.svg"
    gifts = [dict(r) for r in c.execute('SELECT * FROM orders WHERE gift=1 ORDER BY id DESC')]
    for g in gifts:
        g['img'] = f"/api/tb/img/{g['item_id']}.svg"
    return {'cart': cart, 'gifts': gifts, 'ai_name': C.AI_NAME}


# ---------- 分享到聊天 ----------
@router.post('/api/tb/share')
def share(payload: dict = Body(...)):
    iid = int(payload.get('id', 0))
    if not db.conn().execute('SELECT 1 FROM items WHERE id=? AND active=1', (iid,)).fetchone():
        raise HTTPException(404, '商品不存在')
    mid = db.add_msg('user', 'product', '', iid)
    from . import ai
    ai.kick()
    return {'ok': True, 'id': mid}


# ---------- AI 侧动作（由 ai.py 调用） ----------
def ai_cart_add(spec, note=''):
    r = find_item(spec)
    if not r:
        return None
    c = db.conn()
    if c.execute('SELECT 1 FROM ai_cart WHERE item_id=?', (r['id'],)).fetchone():
        return r['id']
    c.execute('INSERT INTO ai_cart(ts,item_id,note) VALUES(?,?,?)', (db.now(), r['id'], (note or '')[:120]))
    c.commit()
    return r['id']


def gifts_today():
    return db.conn().execute("SELECT count(*) FROM orders WHERE gift=1 AND ts LIKE ?", (db.today() + '%',)).fetchone()[0]


def ai_gift(spec, msg=''):
    """AI 从自己的余额下单送给用户，并在聊天里发礼物卡。成功返回订单 id，否则 None。"""
    if gifts_today() >= C.AI_GIFT_DAILY_LIMIT:
        return None
    c = db.conn()
    r = None
    spec = (spec or '').strip()
    if not spec.isdigit():
        words = _words(spec)
        for x in c.execute('SELECT i.*, a.note anote FROM ai_cart a JOIN items i ON i.id=a.item_id ORDER BY a.id DESC'):
            if _hit(x, words) or any(w in (x['anote'] or '') for w in words):
                r = x
                break
    if not r:
        r = find_item(spec)
    if not r or r['price'] > C.AI_GIFT_MAX_PRICE or r['price'] > db.balance('ai'):
        return None
    msg = (msg or '送你的小礼物')[:160]
    cur = c.execute('INSERT INTO orders(ts,item_id,title,price,status,buyer,gift,msg) VALUES(?,?,?,?,?,?,1,?)',
                    (db.now(), r['id'], r['title'], r['price'], '已付款', 'ai', msg))
    oid = cur.lastrowid
    db.ledger('ai', 'gift', -r['price'], f"送{C.USER_NAME} · {r['title'][:24]}", oid)
    c.execute('DELETE FROM ai_cart WHERE item_id=?', (r['id'],))
    db.add_msg('ai', 'gift', msg, oid, commit=False)
    c.commit()
    return oid
