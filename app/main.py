from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import config as C
from . import ai, db, moments, shop, wallet, wechat

app = FastAPI(title='wechat-taobao-sim')
db.init()
shop.seed_items()

app.include_router(wechat.router)
app.include_router(wallet.router)
app.include_router(shop.router)
app.include_router(moments.router)

_NOCACHE = {'Cache-Control': 'no-store, must-revalidate'}


def _page(name):
    def h():
        text = (C.STATIC_DIR / name).read_text(encoding='utf-8')
        if name == 'wx.html':
            text = text.replace('</body>', '<script src="/static/wx-enhance.js"></script></body>')
        return HTMLResponse(text, headers=_NOCACHE)
    return h


app.add_api_route('/', _page('index.html'), include_in_schema=False)
app.add_api_route('/wx', _page('wx.html'), include_in_schema=False)
app.add_api_route('/tb', _page('tb.html'), include_in_schema=False)
app.add_api_route('/moments', _page('moments.html'), include_in_schema=False)
app.mount('/static', StaticFiles(directory=str(C.STATIC_DIR)), name='static')


@app.on_event('startup')
def _resume_pending():
    ai.kick()
    moments.start()


@app.get('/healthz', include_in_schema=False)
def healthz():
    return {'ok': True}
