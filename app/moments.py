"""朋友圈 API 与后台 AI 互动。"""
import io, json, random, re, threading, time, uuid
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, ImageDraw, ImageFont

from . import ai, config as C, db, llm

router = APIRouter(prefix='/api/wx/moments')
_started = False
LOCATIONS = {'', '公司', '学校', '家', '咖啡店', '公园', '不显示'}

def _valid(who):
    if who not in ('user', 'ai'): raise HTTPException(400, '身份无效')
    return who

def _rows(page=0, author=''):
    sql = "SELECT * FROM moments WHERE status='ok'"; args=[]
    if author: sql += ' AND author=?'; args.append(_valid(author))
    sql += ' ORDER BY id DESC LIMIT 20 OFFSET ?'; args.append(max(0,page)*20)
    out=[]; c=db.conn()
    for row in c.execute(sql,args):
        d=dict(row); d['imgs']=json.loads(d.pop('imgs_json') or '[]')
        d['likes']=[dict(x) for x in c.execute('SELECT who,ts FROM moment_likes WHERE mid=? ORDER BY ts',(d['id'],))]
        d['comments']=[dict(x) for x in c.execute("SELECT * FROM moment_comments WHERE mid=? AND status='ok' ORDER BY id",(d['id'],))]
        out.append(d)
    return out

@router.get('/feed')
def feed(page:int=0, author:str=''):
    last=db.kv_get('moments_seen','')
    unseen=db.conn().execute("SELECT count(*) FROM (SELECT l.ts e FROM moment_likes l JOIN moments m ON m.id=l.mid WHERE m.author='user' AND l.who='ai' UNION ALL SELECT c.ts e FROM moment_comments c JOIN moments m ON m.id=c.mid WHERE m.author='user' AND c.who='ai') WHERE e>?",(last,)).fetchone()[0]
    return {'items':_rows(page,author),'unseen':unseen,'cover':db.kv_get('moments_cover','')}

def _save_image(raw, stem=None, cover=False):
    try: im=Image.open(io.BytesIO(raw)); im.thumbnail((1600,900) if cover else (1280,1280)); im=im.convert('RGB')
    except Exception: raise HTTPException(400, '图片无效')
    name=(stem or uuid.uuid4().hex)+'.jpg'; im.save(C.MOMENT_DIR/name,'JPEG',quality=86,optimize=True)
    return '/api/wx/moments/media/'+name

@router.post('/post')
async def post(request:Request):
    form=await request.form(); text=str(form.get('text') or '').strip()[:1000]; loc=str(form.get('loc') or '')
    if loc not in LOCATIONS: raise HTTPException(400,'定位不支持')
    if loc=='不显示': loc=''
    imgs=[]
    for f in list(form.getlist('images'))[:9]:
        if hasattr(f,'read') and (getattr(f,'content_type','') or '').startswith('image/'): imgs.append(_save_image(await f.read()))
    if not text and not imgs: raise HTTPException(400,'不能发空朋友圈')
    c=db.conn(); cur=c.execute('INSERT INTO moments(ts,author,text,imgs_json,loc) VALUES(?,?,?,?,?)',(db.now(),'user',text,json.dumps(imgs),loc)); mid=cur.lastrowid
    lo=min(C.MOMENT_REACT_DELAY_MIN,C.MOMENT_REACT_DELAY_MAX); hi=max(lo,C.MOMENT_REACT_DELAY_MAX)
    due=(datetime.now()+timedelta(seconds=random.randint(lo,hi))).strftime('%Y-%m-%d %H:%M:%S')
    c.execute('INSERT INTO moment_jobs(mid,kind,due) VALUES(?,?,?)',(mid,'react',due)); c.commit()
    _context(mid, f'用户发布：{text}' + (f'，定位 {loc}' if loc else ''))
    return {'ok':True,'id':mid}

@router.get('/media/{name}')
def media(name:str):
    p=C.MOMENT_DIR/Path(name).name
    if not p.is_file(): raise HTTPException(404)
    return FileResponse(p)

@router.post('/del')
def delete(p:dict=Body(...)):
    c=db.conn(); c.execute("UPDATE moments SET status='deleted' WHERE id=? AND author='user'",(int(p.get('id',0)),)); c.commit(); return {'ok':True}

@router.post('/like')
def like(p:dict=Body(...)):
    mid=int(p.get('mid',0)); c=db.conn(); old=c.execute("SELECT 1 FROM moment_likes WHERE mid=? AND who='user'",(mid,)).fetchone()
    c.execute("DELETE FROM moment_likes WHERE mid=? AND who='user'",(mid,)) if old else c.execute('INSERT INTO moment_likes VALUES(?,?,?)',(mid,'user',db.now())); c.commit()
    return {'ok':True,'liked':not bool(old)}

@router.post('/comment')
def comment(p:dict=Body(...)):
    text=str(p.get('text') or '').strip()[:300]; mid=int(p.get('mid',0))
    if not text: raise HTTPException(400,'评论为空')
    c=db.conn(); m=c.execute("SELECT author FROM moments WHERE id=? AND status='ok'",(mid,)).fetchone()
    if not m: raise HTTPException(404)
    reply=str(p.get('reply_to') or '') if str(p.get('reply_to') or '') in ('user','ai') else ''
    cur=c.execute('INSERT INTO moment_comments(mid,who,text,reply_to,ts) VALUES(?,?,?,?,?)',(mid,'user',text,reply,db.now()))
    rounds=c.execute("SELECT count(*) FROM moment_comments WHERE mid=? AND who='ai'",(mid,)).fetchone()[0]
    if rounds < 3:
        due=(datetime.now()+timedelta(seconds=random.randint(max(1,C.MOMENT_REACT_DELAY_MIN),max(1,C.MOMENT_REACT_DELAY_MAX)))).strftime('%Y-%m-%d %H:%M:%S')
        c.execute('INSERT INTO moment_jobs(mid,kind,due,round) VALUES(?,?,?,?)',(mid,'reply',due,rounds+1))
    c.commit(); _context(mid,'用户评论：'+text); return {'ok':True,'id':cur.lastrowid}

@router.post('/seen')
def seen(): db.kv_set('moments_seen',db.now()); return {'ok':True}

@router.post('/cover')
async def cover(request:Request):
    f=(await request.form()).get('image')
    if not hasattr(f,'read'): raise HTTPException(400,'请选择图片')
    url=_save_image(await f.read(),'cover',True); db.kv_set('moments_cover',url); return {'ok':True,'cover':url}

def _json(text):
    m=re.search(r'\{[\s\S]*\}',text or '')
    try: return json.loads(m.group(0)) if m else {}
    except Exception: return {}

def _llm(prompt):
    if not llm.enabled(): return {}
    return _json(llm.chat(ai.persona()+'\n你在朋友圈中自然交流。只输出 JSON。',[{'role':'user','content':prompt}]))

def _context(mid, line):
    msg_id=db.add_msg('ai' if line.startswith('AI') else 'user','sys',f'[朋友圈] {line}',meta={'moment_id':mid})
    c=db.conn(); c.execute("UPDATE msgs SET status='hidden' WHERE id=?",(msg_id,)); c.commit()

def tick():
    c=db.conn(); jobs=c.execute("SELECT * FROM moment_jobs WHERE status='pending' AND due<=? ORDER BY id LIMIT 5",(db.now(),)).fetchall()
    for job in jobs:
        c.execute("UPDATE moment_jobs SET status='running' WHERE id=?",(job['id'],)); c.commit(); m=c.execute("SELECT * FROM moments WHERE id=? AND status='ok'",(job['mid'],)).fetchone()
        if not m: c.execute("UPDATE moment_jobs SET status='gone' WHERE id=?",(job['id'],)); c.commit(); continue
        if job['kind']=='react':
            d=_llm(f'用户发了朋友圈：{m["text"]}；定位：{m["loc"]}。回复 {{"like":true,"comment":"短评论或null","dm":"私聊或null"}}')
            if not llm.enabled(): d={'like':True,'comment':None,'dm':None}
            if d.get('like'): c.execute('INSERT OR IGNORE INTO moment_likes VALUES(?,?,?)',(m['id'],'ai',db.now())); _context(m['id'],'AI 点赞了用户的动态')
            if d.get('comment'): c.execute('INSERT INTO moment_comments(mid,who,text,reply_to,ts) VALUES(?,?,?,?,?)',(m['id'],'ai',str(d['comment'])[:120],'',db.now())); _context(m['id'],'AI 评论：'+str(d['comment'])[:120])
            if d.get('dm'): db.add_msg('ai','text',str(d['dm'])[:200],meta={'moment_id':m['id']})
        else:
            cs=[dict(x) for x in c.execute("SELECT who,text,reply_to FROM moment_comments WHERE mid=? AND status='ok' ORDER BY id",(m['id'],))]
            d=_llm('按这条动态和评论自然短回一句，输出 {"comment":"..."}。'+m['text']+json.dumps(cs,ensure_ascii=False))
            if d.get('comment'): c.execute('INSERT INTO moment_comments(mid,who,text,reply_to,ts) VALUES(?,?,?,?,?)',(m['id'],'ai',str(d['comment'])[:120],'user',db.now())); _context(m['id'],'AI 回复：'+str(d['comment'])[:120])
        c.execute("UPDATE moment_jobs SET status='done' WHERE id=?",(job['id'],)); c.commit()
    _schedule_post(c)

def _schedule_post(c):
    if C.MOMENT_AI_POSTS_PER_DAY<=0 or not llm.enabled(): return
    day=db.today(); key='moment_plan:'+day; plan=db.kv_get(key)
    if plan is None:
        times=sorted(random.sample(range(510,1411),min(C.MOMENT_AI_POSTS_PER_DAY,8)))
        plan=json.dumps([f'{day} {x//60:02d}:{x%60:02d}:00' for x in times]); db.kv_set(key,plan)
    for due in json.loads(plan):
        tag='moment_posted:'+due
        if due<=db.now() and not db.kv_get(tag):
            d=_llm('根据人设和最近聊天写一条不超过60字的日常动态。'+json.dumps(ai.history(12),ensure_ascii=False)+'\n输出 {"text":"...","card":"配图上的短句或空"}')
            text=str(d.get('text') or '')[:60]
            if text:
                imgs=[]; card=str(d.get('card') or '')[:18]
                if card:
                    name=uuid.uuid4().hex+'.jpg'; im=Image.new('RGB',(900,900),(239,229,208)); dr=ImageDraw.Draw(im); dr.text((80,410),card,fill=(70,62,50),font=ImageFont.load_default()); im.save(C.MOMENT_DIR/name,'JPEG',quality=88); imgs=['/api/wx/moments/media/'+name]
                c.execute('INSERT INTO moments(ts,author,text,imgs_json,loc) VALUES(?,?,?,?,?)',(db.now(),'ai',text,json.dumps(imgs),'')); c.commit()
            db.kv_set(tag,db.now())

def start():
    global _started
    if _started:return
    _started=True
    def run():
        while True:
            try: tick()
            except Exception as e: print('[moments]',repr(e),flush=True)
            time.sleep(10)
    threading.Thread(target=run,daemon=True,name='moments').start()
