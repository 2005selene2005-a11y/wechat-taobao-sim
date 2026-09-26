"""OpenAI 兼容 chat/completions 调用（标准库实现，无额外依赖）。"""
import json
import random
import urllib.request

from . import config as C

FALLBACK = {
    'text': ['嗯嗯，我在听～', '哈哈，是这样啊', '然后呢然后呢？', '今天过得怎么样？', '听起来不错诶', '我也是这么想的',
             '那你要记得好好吃饭哦', '收到～', '真的假的，展开说说', '（点头）'],
    'sticker': ['这个表情我要存下来', '哈哈哈哈这个太可爱了', '收到你的表情包啦'],
    'image': ['这张拍得真好看', '哇，是在哪里拍的呀？', '看起来氛围很不错'],
    'product': ['这个看起来不错，颜色也好看', '你是想买它吗？价格还挺合适的', '我也觉得好看，可以考虑～'],
    'packet': ['谢谢你的红包！', '哇，收到啦，谢谢～'],
    'transfer': ['收到转账啦，谢谢！'],
}


def enabled():
    return bool(C.LLM_API_KEY)


def fallback_reply(kind='text'):
    return random.choice(FALLBACK.get(kind) or FALLBACK['text'])


def chat(system, messages, image_urls=None):
    """返回模型回复文本。image_urls 会附加到最后一条 user 消息（需要模型支持视觉）。"""
    msgs = [{'role': 'system', 'content': system}] + [dict(m) for m in messages]
    if image_urls and C.LLM_VISION:
        for m in reversed(msgs):
            if m['role'] == 'user':
                m['content'] = [{'type': 'text', 'text': m['content']}] + [
                    {'type': 'image_url', 'image_url': {'url': u}} for u in image_urls[:3]]
                break
    body = json.dumps({'model': C.LLM_MODEL, 'messages': msgs, 'temperature': C.LLM_TEMPERATURE, 'max_tokens': 800}).encode()
    req = urllib.request.Request(
        C.LLM_BASE_URL + '/chat/completions', data=body, method='POST',
        headers={'Authorization': 'Bearer ' + C.LLM_API_KEY, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=90) as r:
        d = json.loads(r.read())
    return (d['choices'][0]['message'].get('content') or '').strip()
