#!/usr/bin/env python3
"""把自备商品 JSON 导入本地数据库；格式参见 seed/products.json。"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
from app import db  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument('json_file', type=Path)
    p.add_argument('--replace', action='store_true', help='同 ID 商品存在时覆盖')
    a = p.parse_args()
    raw = json.loads(a.json_file.read_text(encoding='utf-8'))
    products = raw.get('products', raw) if isinstance(raw, dict) else raw
    if not isinstance(products, list):
        raise SystemExit('JSON 应为商品数组，或包含 products 数组')
    db.init(); c = db.conn(); n = 0
    sql = ('INSERT OR REPLACE' if a.replace else 'INSERT OR IGNORE') + \
          ' INTO items(id,title,sub,price,orig,emoji,color,shop,mall,cat,sold,tags,active) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1)'
    for x in products:
        if not x.get('id') or not x.get('title') or x.get('price') is None:
            continue
        c.execute(sql, (int(x['id']), str(x['title'])[:80], str(x.get('sub', ''))[:160], float(x['price']),
                        float(x.get('orig', x['price'])), str(x.get('emoji', '🛍️'))[:8], str(x.get('color', '#ffe0cc'))[:16],
                        str(x.get('shop', '自定义小店'))[:60], int(bool(x.get('mall'))), str(x.get('cat', '其他'))[:30],
                        int(x.get('sold', 0)), json.dumps(x.get('tags', []), ensure_ascii=False)))
        n += c.execute('SELECT changes()').fetchone()[0]
    c.commit(); print(f'已导入 {n} 件商品')


if __name__ == '__main__':
    main()
