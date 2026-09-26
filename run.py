"""一条命令启动：python run.py"""
import uvicorn

from app import config as C

if __name__ == '__main__':
    print(f'\n  仿微信 + 仿淘宝 已启动:  http://{C.HOST}:{C.PORT}/\n')
    uvicorn.run('app.main:app', host=C.HOST, port=C.PORT, log_level='info')
