#!/usr/bin/env python3
"""Collect English and Bengali RSS news for the PulseNews static site.

Only RSS-provided titles, descriptions, dates, and image metadata are stored.
Full publisher articles are not scraped or republished.
"""
import concurrent.futures
import datetime as dt
import email.utils
import hashlib
import html
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "feeds.json"
DATA_PATH = ROOT / "data" / "news.json"
USER_AGENT = "PulseNewsRSS/3.0 (bilingual RSS reader; contact: site owner)"
REQUEST_TIMEOUT = 18
MAX_WORKERS = 16
RETENTION_DAYS = 14
MAX_FEED_BYTES = 3_000_000


def utc_now():
    return dt.datetime.now(dt.timezone.utc)


def iso_date(value):
    if not value:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            return parsed.astimezone(dt.timezone.utc).isoformat()
        except (TypeError, ValueError, AttributeError):
            return None


def clean_text(value):
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def local_name(tag):
    return tag.rsplit("}", 1)[-1].split(":")[-1].lower()


def child_text(element, names):
    for child in element:
        if local_name(child.tag) in names and child.text:
            return child.text.strip()
    return ""


def safe_http_url(value):
    value = (value or "").strip()
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return ""
    return value if parsed.scheme in ("http", "https") and parsed.netloc else ""


def image_from_entry(entry, description=""):
    # RSS/Atom media extensions and enclosure tags are common image carriers.
    for node in entry.iter():
        tag = local_name(node.tag)
        if tag in {"thumbnail", "content", "image"}:
            url = node.attrib.get("url") or node.attrib.get("href")
            if url and tag in {"thumbnail", "content", "image"}:
                parsed = urllib.parse.urlsplit(url)
                if parsed.path and not re.search(r"\.(mp3|mp4|m4v|webm|pdf)$", parsed.path, re.I):
                    safe = safe_http_url(url)
                    if safe:
                        return safe
        if tag == "enclosure" and node.attrib.get("url"):
            media_type = node.attrib.get("type", "")
            if media_type.startswith("image/"):
                safe = safe_http_url(node.attrib["url"])
                if safe:
                    return safe

    # Some feeds put the lead image directly in the description HTML.
    match = re.search(r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"']", description or "", re.I)
    return safe_http_url(html.unescape(match.group(1))) if match else ""


def article_id(url):
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]


def read_existing():
    try:
        data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        if isinstance(data.get("articlesByLanguage"), dict):
            merged = []
            for lang in ("en", "bn"):
                merged.extend(data["articlesByLanguage"].get(lang, []))
            merged.extend(data.get("articles", []))
            return merged
        return data.get("articles", [])
    except (OSError, json.JSONDecodeError, AttributeError):
        return []


def fetch_source(category, keywords, domain=None, limit=6, language="en"):
    query = f"{keywords} site:{domain}" if domain else keywords
    if language == "bn":
        locale = {"hl": "bn", "gl": "BD", "ceid": "BD:bn"}
    else:
        locale = {"hl": "en-US", "gl": "US", "ceid": "US:en"}

    feed_url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": query, **locale})
    request = urllib.request.Request(
        feed_url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml"},
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            if response.status != 200:
                return []
            payload = response.read(MAX_FEED_BYTES)
        root = ET.fromstring(payload)
        entries = [node for node in root.iter() if local_name(node.tag) in {"item", "entry"}]
        results = []
        for entry in entries[:limit]:
            title = clean_text(child_text(entry, {"title"}))
            link = child_text(entry, {"link"})
            if not link:
                for node in entry:
                    if local_name(node.tag) == "link":
                        href = node.attrib.get("href", "")
                        rel = node.attrib.get("rel", "alternate")
                        if href and (rel == "alternate" or not link):
                            link = href
                            if rel == "alternate":
                                break
            link = safe_http_url(link)
            if not title or not link:
                continue

            raw_summary = child_text(entry, {"description", "summary", "content", "encoded"})
            summary = clean_text(raw_summary)
            published_raw = child_text(entry, {"pubdate", "published", "updated", "date"})
            source = ""
            for node in entry:
                if local_name(node.tag) == "source":
                    source = clean_text(node.text or node.attrib.get("title", ""))
                    break

            results.append({
                "id": article_id(link),
                "title": title[:400],
                "summary": summary[:1800],
                "content": summary[:5000],
                "url": link,
                "publisher": source or domain or "Google News",
                "category": category,
                "published": iso_date(published_raw) or utc_now().isoformat(),
                "lang": language,
                "image": image_from_entry(entry, raw_summary),
            })
        return results
    except Exception as exc:
        print(f"[WARN] {language} | {category} | {domain or 'topic'}: {type(exc).__name__}: {exc}")
        return []


def normalized_published(item):
    value = item.get("published", "")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=dt.timezone.utc) if parsed.tzinfo is None else parsed
    except (ValueError, TypeError, AttributeError):
        return utc_now()


def main():
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    category_configs = config["categories"]
    per_source = int(config.get("max_articles_per_source", 6))
    per_category = int(config.get("max_articles_per_category", 160))
    total_limit = int(config.get("max_total_articles", 5000))

    jobs = []
    for category in category_configs:
        name = category["name"]
        for domain in category.get("sources", []):
            jobs.append((name, category["query"], domain, per_source, "en"))

        bn_query = category.get("bn_query", name)
        # Search Bengali-language news independently, then target configured
        # Bengali/regional publishers for greater source coverage.
        jobs.append((name, bn_query, None, per_source * 2, "bn"))
        for domain in category.get("bn_sources", []):
            jobs.append((name, bn_query, domain, per_source, "bn"))

    print(f"Starting bilingual Pulse News collection: {len(jobs)} searches")
    fresh = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(fetch_source, *job) for job in jobs]
        for future in concurrent.futures.as_completed(futures):
            try:
                fresh.extend(future.result())
            except Exception as exc:
                print(f"[WARN] Worker failed: {exc}")

    cutoff = utc_now() - dt.timedelta(days=RETENTION_DAYS)
    deduped = {}
    for raw in read_existing() + fresh:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        url = safe_http_url(item.get("url", ""))
        title = clean_text(item.get("title", ""))
        if not url or not title:
            continue

        published = normalized_published(item)
        if published < cutoff:
            continue

        lang = item.get("lang", "en")
        if lang not in ("en", "bn"):
            lang = "en"
        item.update({
            "id": item.get("id") or article_id(url),
            "url": url,
            "title": title[:400],
            "summary": clean_text(item.get("summary", ""))[:1800],
            "content": clean_text(item.get("content", item.get("summary", "")))[:5000],
            "image": safe_http_url(item.get("image", "")),
            "published": published.astimezone(dt.timezone.utc).isoformat(),
            "lang": lang,
        })
        key = (lang, url)
        if key not in deduped or published > normalized_published(deduped[key]):
            deduped[key] = item

    by_language = {"en": [], "bn": []}
    for item in deduped.values():
        by_language[item["lang"]].append(item)

    for lang, items in by_language.items():
        items.sort(key=lambda item: item.get("published", ""), reverse=True)
        counts = {}
        selected = []
        for item in items:
            category = item.get("category", "")
            if counts.get(category, 0) >= per_category:
                continue
            counts[category] = counts.get(category, 0) + 1
            selected.append(item)
        by_language[lang] = selected[:total_limit]

    english_articles = list(by_language["en"])
    output = {
        "updatedAt": utc_now().isoformat(),
        "categories": [category["name"] for category in category_configs],
        "total": len(english_articles),
        "languageTotals": {key: len(value) for key, value in by_language.items()},
        "articlesByLanguage": by_language,
        "articles": english_articles,
    }
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Collection complete: EN={len(by_language['en'])}, BN={len(by_language['bn'])}")
    print(f"Output written to: {DATA_PATH}")


if __name__ == "__main__":
    main()
