#!/usr/bin/env python3
"""给截图或本地体验加入一组普通朋友闲聊演示数据。可重复运行。"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from app import db, shop, wallet  # noqa: E402


def main():
    db.init()
    shop.seed_items()
    c = db.conn()
    if db.kv_get('demo_seeded') == '1':
        print('演示数据已经存在')
        return
    db.add_msg('ai', 'text', '嗨，今天过得怎么样？')
    db.add_msg('user', 'text', '挺好的，刚整理完书桌。')
    db.add_msg('ai', 'text', '听起来很舒服。收拾好之后，记得给自己留一点休息时间。')
    wallet.create('ai', 18.88, '给今天加一点好心情')
    shop.ai_gift('桌面暖光小夜灯', '看到它就想起轻松的夜晚')
    db.add_msg('user', 'product', '', 1)
    db.add_msg('ai', 'text', '这个看起来很实用，颜色也很清爽。')
    db.kv_set('demo_seeded', '1')
    c.commit()
    print('演示数据已写入')


if __name__ == '__main__':
    main()
