#!/usr/bin/env python3
"""服务启动后，在 390×844 视口生成 README 截图。"""
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default='http://127.0.0.1:18099')
    a = ap.parse_args()
    out = Path(__file__).resolve().parents[1] / 'docs' / 'screenshots'
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        page = b.new_page(viewport={'width': 390, 'height': 844}, device_scale_factor=1)
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(a.base + '/wx'); page.wait_for_timeout(800)
        page.screenshot(path=out / '01-chat-list.png')
        page.locator('.contact').click(); page.wait_for_timeout(500)
        page.screenshot(path=out / '02-chat.png')
        page.locator('.packet').first.click(); page.wait_for_timeout(300)
        page.screenshot(path=out / '03-red-packet.png')
        page.goto(a.base + '/wx'); page.locator('#home .tab').last.click(); page.locator('text=钱包 / 零钱').click(); page.wait_for_timeout(400)
        page.screenshot(path=out / '04-wallet.png')
        page.goto(a.base + '/tb'); page.wait_for_timeout(700)
        page.screenshot(path=out / '05-shop-home.png')
        page.locator('.item').first.click(); page.wait_for_timeout(400)
        page.screenshot(path=out / '06-product-detail.png')
        b.close()
    if errors:
        raise SystemExit('页面脚本错误: ' + '; '.join(errors))
    print(f'已生成 6 张截图：{out}')


if __name__ == '__main__':
    main()
