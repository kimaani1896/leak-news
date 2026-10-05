#!/usr/bin/env python3
"""漏えいニュースを集めて docs/items.json と docs/feed.xml を更新する（標準ライブラリのみ）。"""
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime, parsedate_to_datetime
from html import escape, unescape
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"
ITEMS = DOCS / "items.json"
SITE_URL = "https://kimaani1896.github.io/leak-news/"  # 公開後に書き換える
UA = "leak-news/1.0 (+https://github.com/)"
KEEP_DAYS = 180
HIBP_MIN = 1_000_000  # 海外はこの件数以上の大型だけ

FEEDS = [
    ("piyolog", "https://piyolog.hatenadiary.jp/rss"),
    ("Security NEXT", "https://www.security-next.com/feed"),
    ("ScanNetSecurity", "https://scan.netsecurity.ne.jp/rss/index.rdf"),
    ("ITmedia", "https://rss.itmedia.co.jp/rss/2.0/news_security.xml"),
    ("Googleニュース", "https://news.google.com/rss/search?q=%E5%80%8B%E4%BA%BA%E6%83%85%E5%A0%B1+%E6%BC%8F%E3%81%88%E3%81%84+when:7d&hl=ja&gl=JP&ceid=JP:ja"),
    ("Googleニュース", "https://news.google.com/rss/search?q=%E4%B8%8D%E6%AD%A3%E3%82%A2%E3%82%AF%E3%82%BB%E3%82%B9+%E6%83%85%E5%A0%B1+when:7d&hl=ja&gl=JP&ceid=JP:ja"),
]

INCLUDE = re.compile(r"漏えい|漏洩|漏れ|流出|不正アクセス|ランサム|情報窃取|閲覧できる状態|閲覧可能|誤送信|誤送付|誤配|紛失|盗難|不正ログイン|サイバー攻撃|個人情報")
# 対策製品の宣伝・イベント告知・一般論の記事を落とす
EXCLUDE = re.compile(r"セミナー|ウェビナー|提供開始|発売|募集|キャンペーン|無料|ソリューション|導入事例|ホワイトペーパー|資格|調査レポート|ランキング|求人")
# タグ（上から順に判定、複数可）
TAGS = [
    ("ランサムウェア", re.compile(r"ランサム")),
    ("不正アクセス", re.compile(r"不正アクセス|サイバー攻撃|不正侵入|ハッキング|情報窃取|マルウェア|Emotet")),
    ("不正ログイン", re.compile(r"不正ログイン|リスト型|なりすましログイン")),
    ("設定ミス", re.compile(r"閲覧できる状態|閲覧可能|設定(の)?不備|設定ミス|公開状態")),
    ("誤送信・紛失", re.compile(r"誤送信|誤送付|誤配|誤掲載|紛失|盗難|置き忘れ")),
    ("内部不正", re.compile(r"持ち出し|元従業員|元社員|従業員が|委託先社員")),
]
DATA_CLASSES = {
    "Email addresses": "メール", "Passwords": "パスワード", "Names": "氏名",
    "Phone numbers": "電話番号", "Physical addresses": "住所", "Dates of birth": "生年月日",
    "IP addresses": "IPアドレス", "Usernames": "ユーザー名", "Genders": "性別",
    "Credit cards": "クレジットカード", "Partial credit card data": "カード情報の一部",
    "Government issued IDs": "公的身分証", "Social security numbers": "社会保障番号",
    "Bank account numbers": "銀行口座", "Geographic locations": "位置情報",
}


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def strip_html(s):
    s = re.sub(r"<[^>]+>", " ", unescape(s or ""))
    return re.sub(r"\s+", " ", s).strip()


def parse_date(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def local(tag):
    return tag.rsplit("}", 1)[-1]


def parse_feed(name, raw):
    root = ET.fromstring(raw)
    out = []
    for el in root.iter():
        if local(el.tag) != "item":
            continue
        f = {local(c.tag): (c.text or "") for c in el}
        title = strip_html(f.get("title"))
        source = name
        if name == "Googleニュース":
            # 「見出し - 媒体名」の形
            m = re.match(r"^(.*)\s+-\s+([^-]+)$", title)
            if m:
                title, source = m.group(1).strip(), m.group(2).strip()
            title = re.split(r"\s+[|｜]\s+|：日経$", title)[0].strip()
        d = parse_date(f.get("pubDate") or f.get("date") or f.get("published"))
        out.append({
            "title": title,
            "url": (f.get("link") or "").strip(),
            "source": source,
            "date": d.isoformat() if d else None,
            "summary": strip_html(f.get("description"))[:160] if name != "Googleニュース" else "",
            "region": "国内",
        })
    return out


def is_leak(it):
    text = it["title"] + " " + it["summary"]
    return bool(INCLUDE.search(text)) and not EXCLUDE.search(it["title"])


def hibp():
    data = json.loads(get("https://haveibeenpwned.com/api/v3/breaches"))
    out = []
    for b in data:
        if b.get("PwnCount", 0) < HIBP_MIN or b.get("IsSpamList") or b.get("IsFabricated"):
            continue
        n = b["PwnCount"]
        cnt = f"{n / 1e8:.1f}億" if n >= 1e8 else f"{n / 1e4:,.0f}万"
        kinds = [DATA_CLASSES[c] for c in b.get("DataClasses", []) if c in DATA_CLASSES]
        dom = f"（{b['Domain']}）" if b.get("Domain") else ""
        out.append({
            "title": f"{b['Title']}{dom}から約{cnt}件のアカウント情報が流出",
            "url": f"https://haveibeenpwned.com/Breach/{b['Name']}",
            "source": "Have I Been Pwned",
            "date": b["AddedDate"],
            "summary": f"漏えい発生 {b.get('BreachDate', '?')}。含まれる情報: {'・'.join(kinds) or '不明'}",
            "region": "海外",
            "count": n,
        })
    return out


def tag(it):
    text = it["title"] + " " + it["summary"]
    return [t for t, rx in TAGS if rx.search(text)]


def bigrams(s):
    s = re.sub(r"[\s「」『』【】（）()、。・:：\-—|｜]", "", s)
    return {s[i:i + 2] for i in range(len(s) - 1)}


def keys(title):
    """見出しから会社・サービス名らしい部分を取り出す（「」の中身と、先頭の区切りまで）。"""
    out = re.findall(r"[「『]([^」』]{2,20})[」』]", title)
    m = re.match(r"^([^、､，,　 ：:｜|（(にがはの]{2,20})", title)
    if m:
        out.append(m.group(1))
    return [k for k in out if not INCLUDE.search(k) and not re.fullmatch(r"[0-9０-９万千億件人分約]+", k)]


def similar(a, b):
    x, y = bigrams(a), bigrams(b)
    if x and y and len(x & y) / min(len(x), len(y)) >= 0.6:
        return True
    return any((ka in kb or kb in ka) and min(len(ka), len(kb)) >= 3 for ka in keys(a) for kb in keys(b))


# 代表の見出しに使う優先順（数字が小さいほど優先、Googleニュース経由の媒体は最後）
PRIORITY = {"piyolog": 0, "Security NEXT": 1, "ITmedia": 2, "ScanNetSecurity": 3}


def promote(host, it):
    """専門サイトの記事が後から来たら、代表の見出しをそちらに入れ替える。"""
    if PRIORITY.get(it["source"], 9) >= PRIORITY.get(host["source"], 9):
        host.setdefault("others", []).append({"title": it["title"], "url": it["url"], "source": it["source"]})
        return
    host.setdefault("others", []).append({"title": host["title"], "url": host["url"], "source": host["source"]})
    for k in ("title", "url", "source", "summary"):
        host[k] = it[k]


def merge(old, new):
    """URLで重複を落とし、3日以内の似た見出しは1件にまとめて「他の報道」に入れる。"""
    by_url = {}
    for it in old:
        by_url[it["url"]] = it
        for o in it.get("others", []):
            by_url[o["url"]] = it
    added = 0
    for it in sorted(new, key=lambda x: x["date"] or ""):
        if not it["url"] or it["url"] in by_url:
            continue
        d = datetime.fromisoformat(it["date"])
        host = None
        if it["region"] == "国内":
            for o in old:
                if o["region"] == "国内" and abs((datetime.fromisoformat(o["date"]) - d).days) <= 3 and similar(o["title"], it["title"]):
                    host = o
                    break
        if host:
            promote(host, it)
            host["tags"] = sorted(set(host["tags"]) | set(tag(it)), key=[t for t, _ in TAGS].index)
        else:
            it["tags"] = tag(it)
            old.append(it)
            host = it
            added += 1
        by_url[it["url"]] = host
    return added


def write_feed(items):
    now = format_datetime(datetime.now(timezone.utc))
    rows = []
    for it in items[:50]:
        pub = format_datetime(datetime.fromisoformat(it["date"]))
        rows.append(
            f"<item><title>{escape(it['title'])}</title><link>{escape(it['url'])}</link>"
            f"<guid isPermaLink=\"false\">{escape(it['url'])}</guid><pubDate>{pub}</pubDate>"
            f"<description>{escape(it['source'] + ' / ' + it['summary'])}</description></item>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
        f"<title>漏えいウォッチ</title><link>{SITE_URL}</link>"
        f"<description>個人情報漏えいのニュースだけを集めたフィード</description><lastBuildDate>{now}</lastBuildDate>"
        + "".join(rows) + "</channel></rss>"
    )
    (DOCS / "feed.xml").write_text(xml, encoding="utf-8")


def main():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=KEEP_DAYS)
    state = json.loads(ITEMS.read_text(encoding="utf-8")) if ITEMS.exists() else {"items": []}
    items = state["items"]
    fresh, errors = [], []
    for name, url in FEEDS:
        try:
            fresh += [it for it in parse_feed(name, get(url)) if is_leak(it)]
        except Exception as e:  # 1つ落ちても他は続ける
            errors.append(f"{name}: {e}")
    try:
        fresh += hibp()
    except Exception as e:
        errors.append(f"HIBP: {e}")
    fresh = [it for it in fresh if it["date"] and cutoff <= datetime.fromisoformat(it["date"]) <= now + timedelta(hours=1)]
    added = merge(items, fresh)
    items = [it for it in items if datetime.fromisoformat(it["date"]) >= cutoff]
    items.sort(key=lambda x: x["date"], reverse=True)
    DOCS.mkdir(exist_ok=True)
    ITEMS.write_text(json.dumps({"updated": now.isoformat(), "items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
    write_feed(items)
    print(f"added={added} total={len(items)}")
    for e in errors:
        print("WARN", e)


if __name__ == "__main__":
    main()
