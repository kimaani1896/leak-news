# 漏えいウォッチ

個人情報の漏えい・流出・不正アクセスのニュースだけを集めて一覧にする静的サイト。

- `fetch.py` … RSSと Have I Been Pwned から集めて `docs/items.json` と `docs/feed.xml` を更新（標準ライブラリのみ）
- `docs/index.html` … 一覧ページ（GitHub Pages で `docs/` を公開）
- `.github/workflows/update.yml` … 1時間ごとに `fetch.py` を実行して差分をコミット

見出しとリンクのみを掲載し、記事本文は転載しない。
