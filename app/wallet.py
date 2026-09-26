"""虚拟钱包：红包、转账、账单。全部是虚拟数字，不涉及任何真实支付。"""
from datetime import datetime

from fastapi import APIRouter, Body, HTTPException

from . import config as C
from . import db

router = APIRouter()
LABEL = {'packet': '红包', 'transfer': '转账'}


def _name(who):
    return C.USER_NAME if who == 'user' else C.AI_NAME


def ai_given_today():
    r = db.conn().execute("SELECT coalesce(sum(amount),0) FROM packets WHERE sender='ai' AND ts LIKE ?",
                          (db.today() + '%',)).fetchone()
    return round(r[0], 2)


def expire_old():
    """超时未领取的红包/转账退回发送方。"""
    c = db.conn()
    rows = c.execute("SELECT * FROM packets WHERE status='new'").fetchall()
    changed = False
    for p in rows:
        age = (datetime.now() - datetime.fromisoformat(p['ts'])).total_seconds()
        if age > C.PACKET_EXPIRE_HOURS * 3600:
            c.execute("UPDATE packets SET status='expired' WHERE id=?", (p['id'],))
            db.ledger(p['sender'], 'refund', p['amount'], f"{LABEL[p['type']]}过期退回", p['id'])
            db.add_msg('sys', 'sys', f"{LABEL[p['type']]}已过期，金额已退回", p['id'], commit=False)
            changed = True
    if changed:
        c.commit()


def create(sender, amount, msg='', type_='packet'):
    """发红包/转账。user→ai 对方立即收下；ai→user 需要用户点开领取。返回 packet id。"""
    if type_ not in LABEL:
        raise ValueError('类型不对')
    try:
        amount = round(float(amount), 2)
    except (TypeError, ValueError):
        raise ValueError('金额不对')
    if amount <= 0 or amount > C.PACKET_MAX_SINGLE:
        raise ValueError(f'金额要在 0.01–{C.PACKET_MAX_SINGLE:g} 之间')
    if db.balance(sender) < amount:
        raise ValueError(f'零钱不够，只有 ¥{db.balance(sender):.2f}')
    if sender == 'ai' and ai_given_today() + amount > C.AI_DAILY_LIMIT:
        raise ValueError('AI 今天发出的红包/转账已达上限')
    receiver = 'ai' if sender == 'user' else 'user'
    default = '恭喜发财，大吉大利' if type_ == 'packet' else ''
    msg = (str(msg or '').strip() or default)[:40]
    c = db.conn()
    auto = sender == 'user'
    cur = c.execute('INSERT INTO packets(ts,type,sender,receiver,amount,msg,status,opened_at) VALUES(?,?,?,?,?,?,?,?)',
                    (db.now(), type_, sender, receiver, amount, msg, 'opened' if auto else 'new', db.now() if auto else None))
    pid = cur.lastrowid
    db.ledger(sender, type_ + '_send', -amount, f'{LABEL[type_]}给{_name(receiver)}' + (f' · {msg}' if msg else ''), pid)
    db.add_msg(sender, type_, '', pid, commit=False)
    if auto:
        db.ledger('ai', type_ + '_recv', amount, f'{_name("user")}的{LABEL[type_]}' + (f' · {msg}' if msg else ''), pid)
        db.add_msg('sys', 'sys', f'{_name("ai")}已{"收款" if type_ == "transfer" else "领取了你的红包"}', pid, commit=False)
    c.commit()
    return pid


def open_packet(pid):
    """用户点开（领取）。返回 (packet dict, just_opened)。"""
    expire_old()
    c = db.conn()
    p = c.execute('SELECT * FROM packets WHERE id=?', (pid,)).fetchone()
    if not p:
        raise LookupError('没有这个红包')
    p = dict(p)
    just = False
    if p['status'] == 'new' and p['receiver'] == 'user':
        t = db.now()
        c.execute("UPDATE packets SET status='opened',opened_at=? WHERE id=?", (t, pid))
        db.ledger('user', p['type'] + '_recv', p['amount'], f"{_name('ai')}的{LABEL[p['type']]}" + (f" · {p['msg']}" if p['msg'] else ''), pid)
        db.add_msg('sys', 'sys', f"你{'已收款' if p['type'] == 'transfer' else '领取了' + _name('ai') + '的红包'}", pid, commit=False)
        c.commit()
        p['status'], p['opened_at'], just = 'opened', t, True
    return public(p), just


def public(p):
    """红包在被领取前不暴露金额；转账金额始终可见。"""
    p = dict(p)
    if p['type'] == 'packet' and p['status'] != 'opened':
        p.pop('amount', None)
    return p


@router.post('/api/wx/packet/send')
def api_send(payload: dict = Body(...)):
    try:
        pid = create('user', payload.get('amount'), payload.get('msg', ''), payload.get('type', 'packet'))
    except ValueError as e:
        raise HTTPException(400, str(e))
    from . import ai
    ai.kick()
    return {'ok': True, 'id': pid}


@router.post('/api/wx/packet/open')
def api_open(payload: dict = Body(...)):
    try:
        p, just = open_packet(int(payload.get('id', 0)))
    except (LookupError, ValueError):
        raise HTTPException(404, '没有这个红包')
    return {'packet': p, 'balance': db.balance('user'), 'justOpened': just}


@router.get('/api/wx/packet/{pid}')
def api_packet(pid: int):
    expire_old()
    p = db.conn().execute('SELECT * FROM packets WHERE id=?', (pid,)).fetchone()
    if not p:
        raise HTTPException(404)
    return public(p)


@router.get('/api/wallet/ledger')
def api_ledger(owner: str = 'user', n: int = 200):
    if owner not in ('user', 'ai'):
        raise HTTPException(400)
    expire_old()
    rows = [dict(r) for r in db.conn().execute('SELECT * FROM ledger WHERE owner=? ORDER BY id DESC LIMIT ?', (owner, n))]
    return {'owner': owner, 'balance': db.balance(owner), 'rows': rows}
