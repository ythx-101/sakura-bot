"""fallback_sources.py
JavLibrary (403) / Avgle (closed) after alternative sources.
"""
import random
import jvav
import types
import sys

sys.path.insert(0, "/opt/tg-search-bot")
from dmm_patch import patched_get_nice_avs_by_star_name

POPULAR_ACTRESSES = [
    "三上悠亜", "橋本ありな", "白石茉莉奈", "深田えいみ",
    "天使もえ", "夢乃あいか", "小倉由菜", "楪カレン",
    "凪ひかる", "瀬戸環奈", "星奈あい", "本庄鈴",
    "河北彩花", "桃乃木かな", "葵つかさ", "明里つむぎ",
    "羽咲みはる", "美谷朱里", "桃井あずさ", "吉高寧々",
]


def get_random_nice_id(proxy: str):
    """DMM patched search → rate >= 4.0 from a random popular actress.
    Returns (code, id_str) where code is 200 on success.
    """
    d = jvav.DmmUtil(proxy)
    d.get_nice_avs_by_star_name = types.MethodType(patched_get_nice_avs_by_star_name, d)
    actresses = POPULAR_ACTRESSES.copy()
    random.shuffle(actresses)
    for actress in actresses[:4]:
        try:
            code, avs = d.get_nice_avs_by_star_name(actress)
            if code == 200 and avs:
                chosen = random.choice(avs)
                return 200, chosen["id"].upper()
        except Exception:
            continue
    return 404, None


def get_random_new_id(proxy: str):
    """JavBus home page → newest releases, return a random one.
    Returns (code, id_str) where code is 200 on success.
    """
    j = jvav.JavBusUtil(proxy)
    page = random.randint(1, 5)
    try:
        code, ids = j.get_ids_from_page(j.base_url + "/page", page)
        if code == 200 and ids:
            return 200, random.choice(ids).upper()
    except Exception:
        pass
    # fallback: page 1
    try:
        code, ids = j.get_ids_from_page(j.base_url + "/page", 1)
        if code == 200 and ids:
            return 200, random.choice(ids).upper()
    except Exception:
        pass
    return 404, None


def get_top_stars(proxy: str, limit: int = 20):
    """JavBus actress page → real-time popularity ranking.
    Returns (code, [star_name, ...])
    """
    from bs4 import BeautifulSoup
    j = jvav.JavBusUtil(proxy)
    try:
        code, resp = j.send_req(url='https://www.javbus.com/actresses', headers=j.get_headers())
        if code != 200:
            return code, None
        soup = BeautifulSoup(resp.text, 'html.parser')
        boxes = soup.find_all('a', class_='avatar-box')
        stars = []
        for b in boxes[:limit]:
            name_span = b.find('span')
            if name_span:
                stars.append(name_span.text.strip())
        if not stars:
            return 404, None
        return 200, stars
    except Exception:
        return 500, None
