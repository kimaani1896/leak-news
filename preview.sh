#!/usr/bin/env bash
# docs/index.html を確認用サイト（leak-news-preview）にだけ出す。本番には触らない。
# データは本番の items.json をそのまま読む（同じ github.io の中なので相対パスで届く）。
set -euo pipefail
cd "$(dirname "$0")"
PREV="${PREVIEW_DIR:-../leak-news-preview}"
python3 - docs/index.html "$PREV/index.html" <<'PY'
import sys
src, dst = sys.argv[1], sys.argv[2]
s = open(src, encoding="utf-8").read()
for a, b in [
    ("<title>漏えいウォッチ</title>", '<title>【確認用】漏えいウォッチ</title>\n<meta name="robots" content="noindex, nofollow">'),
    ('fetch("items.json"', 'fetch("../leak-news/items.json"'),
    ('href="feed.xml"', 'href="../leak-news/feed.xml"'),
]:
    assert a in s, f"置き換え先が見つからない: {a}"
    s = s.replace(a, b)
open(dst, "w", encoding="utf-8").write(s)
PY
cd "$PREV"
git add -A
if git diff --cached --quiet; then echo "変更なし"; exit 0; fi
git -c user.name=leak-news -c user.email=leak-news@users.noreply.github.com commit -qm "確認用に更新"
git push -q
echo "https://kimaani1896.github.io/leak-news-preview/"
