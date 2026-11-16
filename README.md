# Pulse News — bilingual news website

A static GitHub Pages news reader with a Python RSS collector. Bengali is the default interface language; users can switch to English.

## Features

- Collects English and Bengali-language headlines from Google News RSS, including searches targeted at configured Bengali and regional publishers.
- Collects RSS-provided descriptions and image metadata when a feed supplies them. Image availability depends on each feed.
- Clicking a story opens an accessible details dialog on Pulse News rather than immediately navigating away. The dialog displays the RSS-provided summary, publication/source, date, and image when available.
- The original publisher link is optional in the details dialog. Full publisher articles are not scraped or republished; some feeds provide only a short summary, and full text may require visiting the original publisher.
- Supports category filters, search, saved stories, language preference, and theme preference.

## Project paths

```text
.
├── index.html
├── styles.css
├── app.js
├── feeds.json
├── data/news.json
├── scripts/collect_feeds.py
└── .github/workflows/collect-news.yml
```

## Deploy

1. In repository Settings → Pages, choose **GitHub Actions** as the source.
2. Run **Collect and Deploy Pulse News** from the Actions tab, or push to `main` and wait for the scheduled collector.
3. The workflow collects RSS items, validates `data/news.json`, and deploys the static site.

The collector retains articles for 14 days. RSS feeds can rate-limit, omit images, or provide only short descriptions; source availability and feed terms determine what can be displayed. Headlines, summaries, and images remain attributable to their publishers.
