"""仿微信：消息流、撤回/删除、表情包、图片、头像。"""
import io
import json
import time
from datetime import datetime
from html import escape

from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from . import config as C
from . import ai, db, llm, stickers, wallet

router = APIRouter()


def _expand(rows):
    c = db.conn()
    out = []
    for m in rows:
        m = dict(m)
        try:
            m['meta'] = json.loads(m['meta']) if m.get('meta') else {}
        except ValueError:
            m['meta'] = {}
        st = m.get('status') or 'sent'
        if st == 'deleted':
            continue
        if st == 'recalled':
            who = '你' if m['sender'] == 'user' else C.AI_NAME
            out.append({'id': m['id'], 'ts': m['ts'], 'sender': m['sender'], 'kind': 'sys', 'text': f'{who}撤回了一条消息', 'recalled': True})
            continue
        if m['kind'] in ('packet', 'transfer'):
            p = c.execute('SELECT * FROM packets WHERE id=?', (m['ref'],)).fetchone()
            if not p:
                continue
            m['packet'] = wallet.public(p)
        elif m['kind'] == 'gift':
            o = c.execute('SELECT id,item_id,title,price,msg FROM orders WHERE id=?', (m['ref'],)).fetchone()
            if not o:
                continue
            m['gift'] = dict(o)
            m['gift']['img'] = f"/api/tb/img/{o['item_id']}.svg"
        elif m['kind'] == 'product':
            r = c.execute('SELECT id,title,price,shop FROM items WHERE id=?', (m['ref'],)).fetchone()
            if not r:
                continue
            m['product'] = dict(r)
            m['product']['img'] = f"/api/tb/img/{r['id']}.svg"
        out.append(m)
    return out


@router.get('/api/wx/state')
def state():
    wallet.expire_old()
    seen = int(db.kv_get('user_seen', '0') or 0)
    unread = db.conn().execute("SELECT count(*) FROM msgs WHERE sender='ai' AND kind!='sys' AND status='sent' AND id>?", (seen,)).fetchone()[0]
    return {'user_name': C.USER_NAME, 'ai_name': C.AI_NAME, 'wallet': {'user': db.balance('user'), 'ai': db.balance('ai')},
            'unread': unread, 'llm': llm.enabled(), 'packet_max': C.PACKET_MAX_SINGLE, 'expire_hours': C.PACKET_EXPIRE_HOURS,
            'avatar_v': {w: db.kv_get('avatar_v:' + w, '0') for w in ('user', 'ai')}}


@router.get('/api/wx/profile')
def profile():
    return {'user': {'wxid': C.USER_WXID, 'region': C.USER_REGION},
            'ai': {'wxid': C.AI_WXID, 'region': C.AI_REGION}}


@router.post('/api/wx/seen')
def seen():
    r = db.conn().execute('SELECT max(id) FROM msgs').fetchone()[0] or 0
    db.kv_set('user_seen', r)
    return {'ok': True}


@router.get('/api/wx/feed')
def feed(n: int = 500):
    wallet.expire_old()
    rows = db.conn().execute("SELECT * FROM msgs WHERE status NOT IN ('deleted','hidden') ORDER BY id DESC LIMIT ?", (n,)).fetchall()[::-1]
    return _expand(rows)


@router.get('/api/wx/poll')
def poll(since: int = 0):
    wallet.expire_old()
    c = db.conn()
    rows = _expand(c.execute("SELECT * FROM msgs WHERE id>? AND status!='hidden' ORDER BY id LIMIT 50", (since,)).fetchall())
    gone = [{'id': r['id'], 'status': r['status'], 'sender': r['sender']} for r in c.execute(
        "SELECT id,status,sender FROM msgs WHERE status IN ('recalled','deleted') AND id>?", (max(0, since - 200),))]
    return {'msgs': rows, 'typing': ai.typing(), 'gone': gone}


@router.post('/api/wx/say')
def say(payload: dict = Body(...)):
    text = str(payload.get('text', '')).strip()[:500]
    st = str(payload.get('sticker', '')).strip()
    if st:
        if st not in stickers.names():
            raise HTTPException(400, '没有这个表情')
        mid = db.add_msg('user', 'sticker', st)
    elif text:
        mid = db.add_msg('user', 'text', text)
    else:
        raise HTTPException(400, '消息为空')
    ai.kick()
    return {'ok': True, 'id': mid}


@router.post('/api/wx/recall')
def recall(payload: dict = Body(...)):
    mid = int(payload.get('id', 0))
    c = db.conn()
    m = c.execute('SELECT * FROM msgs WHERE id=?', (mid,)).fetchone()
    if not m or m['sender'] != 'user' or m['kind'] not in ('text', 'sticker', 'image') or m['status'] != 'sent':
        raise HTTPException(400, '这条不能撤回')
    if (datetime.now() - datetime.fromisoformat(m['ts'])).total_seconds() > C.RECALL_SECONDS:
        raise HTTPException(400, '超过两分钟，撤不回了')
    c.execute("UPDATE msgs SET status='recalled' WHERE id=?", (mid,))
    c.commit()
    return {'ok': True}


@router.post('/api/wx/del')
def delete(payload: dict = Body(...)):
    """删除：只在用户这边消失，AI 的上下文里仍然记得（和真实聊天软件一致）。"""
    c = db.conn()
    c.execute("UPDATE msgs SET status='deleted' WHERE id=? AND kind IN ('text','sticker','image','sys','product')", (int(payload.get('id', 0)),))
    c.commit()
    return {'ok': True}


# ---------- 图片 ----------
@router.post('/api/wx/media')
async def media_upload(file: UploadFile = File(...)):
    data = await file.read()
    if not data or len(data) > 15 * 1024 * 1024:
        raise HTTPException(400, '文件为空或太大（上限 15MB）')
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        im.thumbnail((1600, 1600))
        mid = db.add_msg('user', 'image', '')
        name = f'{mid}.jpg'
        im.convert('RGB').save(C.MEDIA_DIR / name, 'JPEG', quality=86)
    except Exception:
        raise HTTPException(400, '不是有效的图片')
    c = db.conn()
    c.execute('UPDATE msgs SET text=? WHERE id=?', (name, mid))
    c.commit()
    ai.kick()
    return {'ok': True, 'id': mid}


@router.get('/api/wx/media/{name}')
def media(name: str):
    p = C.MEDIA_DIR / (name.replace('/', '').replace('\\', ''))
    if not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, headers={'Cache-Control': 'private,max-age=86400'})


# ---------- 表情包 ----------
@router.get('/api/wx/stickers')
def sticker_list():
    d = stickers.desc()
    return [{'name': n, 'desc': d.get(n, '')} for n in stickers.names()]


@router.get('/api/wx/sticker/{name}')
def sticker_file(name: str):
    p = stickers.path(name)
    if not p:
        raise HTTPException(404)
    return FileResponse(p, headers={'Cache-Control': 'max-age=3600'})


@router.post('/api/wx/stickers/upload')
async def sticker_upload(file: UploadFile = File(...), desc: str = Form('')):
    import os
    ext = os.path.splitext(file.filename or '')[1].lower()
    if ext not in ('.png', '.jpg', '.jpeg', '.gif', '.webp'):
        raise HTTPException(400, '只收 png/jpg/gif/webp 图片')
    data = await file.read()
    if not data or len(data) > 5 * 1024 * 1024:
        raise HTTPException(400, '文件为空或太大（上限 5MB）')
    name = datetime.now().strftime('s%Y%m%d%H%M%S') + f'{int(time.time() * 1000) % 1000:03d}' + ext
    (C.STICKER_DIR / name).write_bytes(data)
    if desc.strip():
        stickers.save_desc(name, desc.strip())
    return {'ok': True, 'name': name}


# ---------- 头像 ----------
def _default_avatar(who):
    return (C.STATIC_DIR / f'avatar-{who}.svg').read_text(encoding='utf-8')


@router.get('/api/avatar/{who}')
def avatar(who: str):
    if who not in ('user', 'ai'):
        raise HTTPException(404)
    for f in sorted(C.AVATAR_DIR.glob(who + '.*')):
        return FileResponse(f, headers={'Cache-Control': 'no-cache'})
    return Response(_default_avatar(who), media_type='image/svg+xml', headers={'Cache-Control': 'no-cache'})


@router.post('/api/avatar/{who}')
async def avatar_upload(who: str, file: UploadFile = File(...)):
    if who not in ('user', 'ai'):
        raise HTTPException(404)
    data = await file.read()
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data)).convert('RGB')
        w, h = im.size
        s = min(w, h)
        im = im.crop(((w - s) // 2, (h - s) // 2, (w + s) // 2, (h + s) // 2)).resize((256, 256))
    except Exception:
        raise HTTPException(400, '不是有效的图片')
    for f in C.AVATAR_DIR.glob(who + '.*'):
        f.unlink()
    im.save(C.AVATAR_DIR / f'{who}.jpg', 'JPEG', quality=90)
    db.kv_set('avatar_v:' + who, int(time.time()))
    return {'ok': True}


@router.delete('/api/avatar/{who}')
def avatar_reset(who: str):
    if who not in ('user', 'ai'):
        raise HTTPException(404)
    for f in C.AVATAR_DIR.glob(who + '.*'):
        f.unlink()
    db.kv_set('avatar_v:' + who, int(time.time()))
    return {'ok': True}


# ---------- 开发调试 ----------
if C.DEV_API:
    @router.post('/api/dev/ai_say')
    def dev_ai_say(payload: dict = Body(...)):
        actions = ai.parse_reply(str(payload.get('text', '')))
        return {'actions': actions, 'result': ai.deliver(actions, delays=False)}
