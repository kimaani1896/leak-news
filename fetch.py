#!/usr/bin/env python3
"""漏えいニュースを集めて docs/items.json と docs/feed.xml を更新する（標準ライブラリのみ）。"""
import json
import re
import unicodedata
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
    # 「漏えい」「不正アクセス」と書かれず「サイバー攻撃で障害」とだけ報じられる事件を拾う
    ("Googleニュース*", "https://news.google.com/rss/search?q=%E3%82%B5%E3%82%A4%E3%83%90%E3%83%BC%E6%94%BB%E6%92%83+when:7d&hl=ja&gl=JP&ceid=JP:ja"),
    ("Googleニュース*", "https://news.google.com/rss/search?q=%E3%83%A9%E3%83%B3%E3%82%B5%E3%83%A0%E3%82%A6%E3%82%A7%E3%82%A2+when:7d&hl=ja&gl=JP&ceid=JP:ja"),
]

INCLUDE = re.compile(r"漏えい|漏洩|漏れ|流出|不正アクセス|ランサム|情報窃取|閲覧できる状態|閲覧可能|誤送信|誤送付|誤配|紛失|盗難|不正ログイン|サイバー攻撃|個人情報")
# 見出しにあるときだけ拾う言葉（本文だけだと関係ない記事まで入るため）
INCLUDE_TITLE = re.compile(r"カード情報|改ざん|不正なページ")
# 対策製品の宣伝・イベント告知・一般論の記事を落とす
# 「サイバー攻撃」「ランサムウェア」の広い検索で拾った記事は、事件の見出しの形をしたものだけ残す
INCIDENT = re.compile(r"(に|へ|で|が|、|\s)(サイバー攻撃|ランサム|不正アクセス)|サイバー攻撃(を)?受け|ランサム\S{0,4}(被害|攻撃)|被害|障害|漏え|漏洩|流出")
NOISE = re.compile(r"対策|市場|支援|法|措置|社説|動向|白書|警鐘|専門家|とは|方法|選定|ナビ|EXPO|脆弱性|株価|サービス|製品|守る|備え|防御|無害化|集団|摘発|義務|報告書|検証|写真|コスト|復号|ページ目|解説|影響|どう|なぜ|？|\?|社長|狙う|急増|相次|立て続|調査|AIで|AIの|ツール|選択|エキスパート|映す|20[01]\d年|202[0-5]年")
EXCLUDE = re.compile(r"セミナー|ウェビナー|提供開始|発売|募集|キャンペーン|無料|ソリューション|導入事例|ホワイトペーパー|資格|調査レポート|ランキング|求人|資金流出|攻撃手法|優勝|大会|コンテスト|演習|\d{1,2}月.{0,20}まとめ(?!てみた)|急反落|反落|続落|急落|ストップ安|に買い")
# 事件の行には出さないが、捨てずに「その他の関連記事」へ回す（発言・統計・逮捕・注意喚起など）
SOFT = re.compile(r"注意を?喚起|営業秘密|官房長官|デジタル相|拘束|調停|最多ペース")
MISC_DAYS = 14
# 特定の事件ではなく「相次ぐ漏えい」「対策は」のような全般・解説のニュース（専門サイト以外で、まとめた記事がないものは載せない）
GENERAL = re.compile(r"相次|急増|狙われ|狙う|とは|どう|なぜ|解説|専門家|識者|警鐘|対策|備え|守る|？|\?|ヤバい|考えられる|立て続|注意点|手口|教訓")
# タグ（上から順に判定、複数可）
TAGS = [
    ("ランサムウェア", re.compile(r"ランサム")),
    ("不正アクセス", re.compile(r"不正アクセス|サイバー攻撃|不正侵入|ハッキング|情報窃取|マルウェア|Emotet|改ざん|不正なページ")),
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
            title = re.split(r"\s*[|｜]\s*|：日経$", title)[0].strip()
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
    # 見出しに漏えい系の言葉が無い記事（システム障害・営業秘密・政策など）は、本文に出てきても入れない
    return bool(INCLUDE.search(it["title"]) or INCLUDE_TITLE.search(it["title"])) and not EXCLUDE.search(it["title"])


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
    na, nb = find_name(a), find_name(b)
    if na and nb and (na in nb or nb in na) and min(len(na), len(nb)) >= 3:
        return True
    if any((ka in kb or kb in ka) and min(len(ka), len(kb)) >= 3 for ka in keys(a) for kb in keys(b)):
        return True
    if na and nb:
        return False  # 会社名がはっきり違うなら、見出しの文字が似ていても別の事件（「◯◯にサイバー攻撃」どうし等）
    x, y = bigrams(a), bigrams(b)
    return bool(x and y and len(x & y) / min(len(x), len(y)) >= 0.6)


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
        if it["source"] != "Have I Been Pwned":
            for o in old:
                if o["source"] != "Have I Been Pwned" and abs((datetime.fromisoformat(o["date"]) - d).days) <= 3 and similar(o["title"], it["title"]):
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


def base_name(c):
    """まとめ判定用の会社名。「旭化成子会社」「旭化成系」「〇〇グループ会社」などを親会社名にそろえる（表示は変えない）。"""
    return re.sub(r"(の)?(子会社|関連会社|グループ会社|グループ|傘下|系\d*社|系)$", "", c or "")


def consolidate(items, days=14):
    """続報が3日を過ぎて別の行になった事件を、会社名が同じなら1件にまとめる（日付は最初の報道のまま）。"""
    kept = []
    for it in sorted(items, key=lambda x: x["date"]):
        name = base_name(it.get("company"))
        host = None
        if it["source"] != "Have I Been Pwned" and len(name) >= 2:
            d = datetime.fromisoformat(it["date"])
            for k in kept:
                kn = base_name(k.get("company"))
                if (k["source"] != "Have I Been Pwned" and (kn == name or (min(len(kn), len(name)) >= 3 and (kn in name or name in kn)))
                        and (d - datetime.fromisoformat(k["last"])).days <= days):
                    host = k
                    break
        elif it["source"] != "Have I Been Pwned" and not name:
            # 会社名が取れない見出しでも、既にある行の会社名が見出しに入っていればそこにまとめる
            d, t = datetime.fromisoformat(it["date"]), alias_text(it["title"])
            for k in kept:
                kn = base_name(k.get("company"))
                if (k["source"] != "Have I Been Pwned" and len(kn) >= 3 and kn in t
                        and (d - datetime.fromisoformat(k["last"])).days <= days):
                    host = k
                    break
        if host:
            others = it.pop("others", [])
            promote(host, it)
            host.setdefault("others", []).extend(others)
            host["tags"] = sorted(set(host["tags"]) | set(it["tags"]), key=[t for t, _ in TAGS].index)
            host["last"] = it["date"]
        else:
            it["last"] = it["date"]
            kept.append(it)
    for k in kept:
        del k["last"]
    kept.sort(key=lambda x: x["date"], reverse=True)
    return kept


# 見出しが日本語でも、事件の舞台が海外ならこちらに回す
OVERSEAS = re.compile(r"韓国|中国|台湾|香港|米国|米[政企大当連軍国]|豪州|豪[政企]|英国|欧州|ドイツ|フランス|インド|ロシア|北朝鮮|デンマーク|スウェーデン|ノルウェー|フィンランド|オランダ|イタリア|スペイン|カナダ|ブラジル|メキシコ|ベトナム|シンガポール|インドネシア|フィリピン|オーストラリア|イスラエル|海外|現地報道|OpenAI|Anthropic|GoogleのAI|Dropbox|Unni|ApplyNow")
COUNT = re.compile(r"(約|最大|計|全)?\s*(\d[\d,，.]*(?:億\d*)?(?:万\d*千?)?)\s*(超)?(?:の)?\s*(人分|件分|人|件|名|アカウント|口座)(?!目)(超)?")
COUNT_LOOSE = re.compile(r"(約|最大|計)?\s*(\d[\d,，.]*(?:億|万)\d*)(超)")
# 会社名として採らない言葉
NOT_NAME = re.compile(
    r"相次|免許証|情報|個人|漏え|漏洩|流出|攻撃|まとめ|対策|リスク|識者|被害|能動的|本物|当選|払戻|保育|\d{4}年|国内|専門家"
    r"|ランサム|不正|本人|おわび|異様|猶予|借入|エキスパート|ITmedia|NEWS|ニュース|ページ|闇サイト|脆弱性|見つかった|悪用|企業や|もぬけ|円$|^\d+日|\d+機関"
    r"|ご不便|ご心配|ご迷惑|お詫び|^企業$|^ハッカー|^交通系IC$|^回転ずしチェーン$|侵害|^NISA口座$|手法|^異なる|^別の|^特例|[『』「」]|^(ID|CMS|会員|公式|社内|チケット|ブロガー|メーリング|研究用|荷物|従業員|顧客|作業|一部)|(システム|サーバー?|DB|アカウント|シリーズ)$"
)
GENERIC_HEAD = r"^(アンケートサイト\s*|ECサイト|チケット販売サイト|中古アニメグッズ|美容医療プラットフォーム|デジタル整理券システム|手間いらずの|ANA子会社のデジタルギフト)"
NAME_CH = r"[^\s、。，,「」『』（）()：:｜|—―…‐]"


def to_num(s):
    s = s.replace(",", "").replace("，", "")
    n, cur = 0.0, ""
    for ch in s:
        if ch in "億万千":
            n += float(cur or 1) * {"億": 1e8, "万": 1e4, "千": 1e3}[ch]; cur = ""
        else:
            cur += ch
    try:
        return n + float(cur or 0)
    except ValueError:
        return 0


def find_count(title):
    """見出しから影響人数・件数を取り出す（いちばん大きいもの）。"""
    title = title.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    best = None
    for m in list(COUNT.finditer(title)) + list(COUNT_LOOSE.finditer(title)):
        if re.search(r"[A-Za-z]\s?$", title[:m.start(2)]):  # Microsoft 365 など
            continue
        n = to_num(m.group(2))
        if n < 1 or re.fullmatch(r"20\d\d", m.group(2)):
            continue
        if title[m.end():].startswith("送信") or "メール" in title[max(0, m.start() - 6):m.start()]:
            continue  # 送ったメールの数は漏えい件数ではない
        loose = m.re is COUNT_LOOSE
        unit = "件" if loose else {"人分": "人", "件分": "件", "名": "人", "アカウント": "件", "口座": "件"}.get(m.group(4), m.group(4))
        over = m.group(3) or (not loose and m.group(5))
        txt = f"{m.group(1) or ''}{m.group(2)}{unit}{'超' if over else ''}"
        if not best or n > best[0]:
            best = (n, txt)
    return best


# 同じ会社のローマ字表記・カタカナ表記などの揺れ（見つけたら足す）。左を右にそろえる
ALIASES = {"ABAHOUSE": "アバハウス", "第一ライフ": "第一生命", "第一ライフグループ": "第一生命", "第一ライフG": "第一生命",
           "日経": "日本経済新聞", "日経新聞": "日本経済新聞", "日本経済新聞社": "日本経済新聞", "日経新聞社": "日本経済新聞", "日経グループ": "日本経済新聞",
           "MrMax": "ミスターマックス", "GMO系": "GMO", "セコマ": "セイコーマート", "infoQ": "GMO"}


def alias_text(t):
    """見出しの中の表記揺れをそろえる（会社名が取れない見出しを既存の行と突き合わせるため）。"""
    for k in sorted(ALIASES, key=len, reverse=True):
        t = t.replace(k, ALIASES[k])
    return t


def clean_name(c):
    c = unicodedata.normalize("NFKC", c)  # 全角英数（ＧＭＯ）と半角（GMO）をそろえる
    c = re.sub(GENERIC_HEAD, "", c.strip(" 　「」『』"))
    c = re.sub(r"の([A-Za-z].*|計|約|全)$", "", c)
    c = re.sub(r"(Webサイト|公式サイト|のシステム.*|従業員|職員|教員|社員|アプリ|の\S*(障害|被害|問題))$", "", c)
    return ALIASES.get(c, ALIASES.get(c.upper(), c))


def find_name(title):
    """見出しから会社・サービス名を取り出す。取れなければ空文字。"""
    cands = []
    m = re.search(r"【([^】]{2,15}?)(?:の)?(?:個人情報|顧客情報|会員情報|情報漏|情報流出|不正アクセス)[^】]*】", title)
    if m:
        cands.append(m.group(1))
    t = re.sub(r"【[^】]*】|（[^）]*）|\([^)]*\)", "", title).strip()
    # 見出しの先頭が加害側（AI・攻撃グループ・悪用された仕組み）のときは、被害側を取る
    if re.search(r"攻撃グループ|実在(する)?企業", t):
        return ""  # 解説記事・被害企業名なし
    m = re.search(r"(?:AI|エージェント|モデル)による(.{2,25}?)への", t)
    if m:
        return clean_name(m.group(1))
    m = re.search(r"^(?:ハッカー|攻撃者)(?:集団)?が([^\s、「」]{2,10}?)(?:会員|利用者|顧客|の)", t)
    if m:
        return clean_name(m.group(1))
    m = re.search(r"を悪用した([^\s、]{2,20}?)の(?:不正|情報)", t)
    if m:
        return clean_name(m.group(1))
    m = re.search(r"\s[-－]\s([^-－]{2,25})$", t)  # Security NEXT「… - 会社名」
    if m:
        cands.append(m.group(1))
    m = re.search(rf"[～〜]\s*({NAME_CH}{{2,25}}?)(?:で|に|の)", t)  # ScanNetSecurity「… ～ 会社名で…」
    if m:
        cands.append(m.group(1))
    m = re.match(rf"^({NAME_CH}{{2,10}}?)(?<![でがにのはも])(?:個人情報|顧客情報|会員情報)", t)  # 「セコマ個人情報漏えいは…」
    if m:
        cands.append(m.group(1))
    m = re.match(r"^[「『]([^」』]{2,20})[」』]", t)
    if m:
        cands.append(m.group(1))
    m = re.match(r"^([A-Za-z][A-Za-z0-9 .&'-]{1,30}?)(?:に|の|、|が|で)", t)  # The Japan Times に… など
    if m:
        cands.append(m.group(1))
    m = re.match(r"^([^\s、]{2,20})[\s、]", t)  # 「会社名、…」「会社名 …」
    if m:
        cands.append(m.group(1))
    m = re.match(rf"^({NAME_CH}{{2,20}}?)(?:の|、|\s|に|で|が|は|への|による|＝|「)", t)
    if m:
        cands.append(m.group(1))
    m = re.search(rf"({NAME_CH}{{2,20}}?)(?:の{NAME_CH}{{0,15}}?)?(?:に|で|へ|への|が)(?:また|も)?(?:不正|サイバー|ランサム|個人情報|顧客|情報漏|会員)", t)
    if m:
        cands.append(m.group(1))
    m = re.search(r"([^\s、「」]{2,15})が(?:謝罪|発表|公表)", t)
    if m:
        cands.append(m.group(1))
    cands += re.findall(r"[「『]([^」』]{2,20})[」』]", t)
    ok = [c for c in map(clean_name, cands)
          if len(c) >= 2 and not NOT_NAME.search(c) and not COUNT.search(c)
          and not (len(c) > 6 and re.search(r"[がを]", c))  # 「特例で広島県が免許再交付」のような文
          and not (len(c) > 10 and re.search(r"[ぁ-ん]{4,}", c) and not re.search(r"[市区町村県]", c))]
    if not ok:
        return ""
    # 「KKR京都くに荘」が「に」で切れて「KKR京都く」になるのを防ぐ（別の候補が同じ頭で長ければそちら）
    longer = [c for c in ok if len(c) > len(ok[0]) and c.startswith(ok[0]) and len(c) - len(ok[0]) <= 4]
    return longer[0] if longer else ok[0]


def people_text(n):
    """表示用の人数。1万以上は「◯万人」、それ未満は「◯人」にそろえる（件・アカウントも人として数える）。"""
    if n >= 100_000:
        return f"{round(n / 1e4):,}万人"
    if n >= 10_000:
        return f"{round(n / 1e4, 1):g}万人"
    return f"{n:,}人"


def label(it):
    """一覧用の会社名・人数・国内/海外を付ける（毎回つけ直す）。"""
    if it["source"] == "Have I Been Pwned":
        it["company"] = re.split(r"（|から約", it["title"])[0]
        it["people"] = people_text(it["count"])
        return
    titles = [it["title"]] + [o["title"] for o in it.get("others", [])]
    it["region"] = "海外" if OVERSEAS.search(it["title"]) else "国内"
    # 会社名は、まとめた見出し全部で一番多く取れた名前（同数なら代表の見出しのもの）
    names = [n for n in map(find_name, titles) if n]
    it["company"] = max(names, key=lambda n: (names.count(n), n == names[0])) if names else ""
    best = next((c for c in map(find_count, titles) if c), None)  # 代表の見出しを優先
    it["count"] = int(best[0]) if best else None
    it["people"] = people_text(it["count"]) if best else ""

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
    items = state["items"] + state.get("misc", [])  # 前回「その他」に回した記事も毎回判定し直す
    for it in items:  # 「見出し|サイト名」の後ろを落とす（前は空白なしの | を見逃していた）
        if it["source"] not in PRIORITY and it["source"] != "Have I Been Pwned":
            it["title"] = re.split(r"\s*[|｜]\s*", it["title"])[0].strip()
    fresh, errors = [], []
    for name, url in FEEDS:
        try:
            strict = name.endswith("*")
            fresh += [it for it in parse_feed(name.rstrip("*"), get(url))
                      if is_leak(it) and (not strict or (INCIDENT.search(it["title"]) and not NOISE.search(it["title"])))]
        except Exception as e:  # 1つ落ちても他は続ける
            errors.append(f"{name}: {e}")
    try:
        fresh += hibp()
    except Exception as e:
        errors.append(f"HIBP: {e}")
    fresh = [it for it in fresh if it["date"] and cutoff <= datetime.fromisoformat(it["date"]) <= now + timedelta(hours=1)]
    added = merge(items, fresh)
    items = [it for it in items if datetime.fromisoformat(it["date"]) >= cutoff]
    items = [it for it in items if is_leak(it) or it["source"] == "Have I Been Pwned"]
    for it in items:  # まとめた「他の報道」にも同じ除外をかける
        if it.get("others"):
            it["others"] = [o for o in it["others"] if not EXCLUDE.search(o["title"])]
    items.sort(key=lambda x: x["date"], reverse=True)
    for it in items:
        label(it)
    # 発言・統計・解説記事や、専門サイトでも会社名が取れないものは「その他」へ
    misc = [it for it in items if SOFT.search(it["title"]) or (it["source"] != "Have I Been Pwned" and GENERAL.search(it["title"])
            and (not it["company"] or (it["source"] not in PRIORITY and not it.get("others"))))]
    items = [it for it in items if it not in misc]
    items = consolidate(items)
    for it in items:
        label(it)
    # 続報としてまとまらず会社名も取れない行も「その他」へ（統計・政治家の発言・解説などが多い）
    misc += [it for it in items if it["source"] != "Have I Been Pwned" and not it["company"]]
    items = [it for it in items if it["source"] == "Have I Been Pwned" or it["company"]]
    misc = sorted((it for it in misc if datetime.fromisoformat(it["date"]) >= now - timedelta(days=MISC_DAYS)),
                  key=lambda x: x["date"], reverse=True)
    DOCS.mkdir(exist_ok=True)
    ITEMS.write_text(json.dumps({"updated": now.isoformat(), "items": items, "misc": misc}, ensure_ascii=False, indent=1), encoding="utf-8")
    write_feed(items)
    print(f"added={added} total={len(items)} misc={len(misc)}")
    for e in errors:
        print("WARN", e)


if __name__ == "__main__":
    main()
