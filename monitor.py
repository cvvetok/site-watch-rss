import os
import re
import json
import hashlib
import html
from datetime import datetime, timezone
from email.utils import format_datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


SITES_FILE = "sites.json"
STATE_FILE = "state.json"
FEED_ITEMS_FILE = "feed_items.json"
FEED_FILE = "docs/feed.xml"

MAX_FEED_ITEMS = 500
MAX_TEXT_CHANGES = 25
MAX_LINK_CHANGES = 20


session = requests.Session()

retry = Retry(
    total=3,
    connect=3,
    read=3,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
)

adapter = HTTPAdapter(max_retries=retry)
session.mount("http://", adapter)
session.mount("https://", adapter)

session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/141.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
})


def load_json(filename, default):
    if not os.path.exists(filename):
        return default

    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def normalize(text):
    if not text:
        return ""

    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_url(url):
    return url.split("#")[0].rstrip("/")


def hash_snapshot(snapshot):
    data = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def get_snapshot(url):
    response = session.get(url, timeout=40, allow_redirects=True)
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "")
    if "text/html" not in content_type:
        raise Exception(f"Страница не является HTML: {content_type}")

    soup = BeautifulSoup(response.text, "html.parser")
    final_url = response.url

    title = ""
    if soup.title:
        title = normalize(soup.title.get_text(" ", strip=True))

    description = ""
    description_tag = soup.find(
        "meta",
        attrs={"name": re.compile(r"^description$", re.I)},
    )
    if description_tag:
        description = normalize(description_tag.get("content", ""))

    h1 = []
    for tag in soup.find_all("h1"):
        text = normalize(tag.get_text(" ", strip=True))
        if text:
            h1.append(text)
    h1 = list(dict.fromkeys(h1))

    h2 = []
    for tag in soup.find_all("h2"):
        text = normalize(tag.get_text(" ", strip=True))
        if text:
            h2.append(text)
    h2 = list(dict.fromkeys(h2))

    h3 = []
    for tag in soup.find_all("h3"):
        text = normalize(tag.get_text(" ", strip=True))
        if text:
            h3.append(text)
    h3 = list(dict.fromkeys(h3))

    links = []
    base_host = urlparse(final_url).netloc.replace("www.", "")

    for tag in soup.find_all("a", href=True):
        href = normalize(tag.get("href"))

        if not href:
            continue

        if href.startswith(("mailto:", "tel:", "javascript:")):
            continue

        absolute = urljoin(final_url, href)
        parsed = urlparse(absolute)
        host = parsed.netloc.replace("www.", "")

        if host != base_host:
            continue

        cleaned = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

        if parsed.query:
            params = [
                p
                for p in parsed.query.split("&")
                if not p.lower().startswith(
                    ("utm_", "yclid", "gclid", "fbclid")
                )
            ]
            if params:
                cleaned += "?" + "&".join(params)

        cleaned = clean_url(cleaned)
        if cleaned:
            links.append(cleaned)

    links = sorted(set(links))

    for tag in soup.find_all(
        ["script", "style", "noscript", "svg", "canvas", "iframe", "template"]
    ):
        tag.decompose()

    noisy_patterns = [
        "cookie",
        "captcha",
        "recaptcha",
        "chat-widget",
        "chatwidget",
    ]

    for tag in soup.find_all(True):
        classes = " ".join(tag.get("class", [])).lower()
        tag_id = str(tag.get("id", "")).lower()
        combined = classes + " " + tag_id

        if any(pattern in combined for pattern in noisy_patterns):
            tag.decompose()

    blocks = []
    interesting_tags = ["p", "li", "h1", "h2", "h3", "h4", "blockquote"]

    for tag in soup.find_all(interesting_tags):
        text = normalize(tag.get_text(" ", strip=True))

        if len(text) < 20:
            continue

        if len(text) > 3000:
            continue

        blocks.append(text)

    blocks = list(dict.fromkeys(blocks))

    return {
        "title": title,
        "description": description,
        "h1": h1,
        "h2": h2,
        "h3": h3,
        "text_blocks": blocks,
        "links": links,
    }


def compare_lists(old, new):
    old_set = set(old)
    new_set = set(new)

    added = [item for item in new if item not in old_set]
    removed = [item for item in old if item not in new_set]

    return added, removed


def compare_snapshots(old, new):
    changes = []

    if old.get("title") != new.get("title"):
        changes.append({
            "type": "title",
            "title": "Изменён TITLE",
            "old": old.get("title", ""),
            "new": new.get("title", ""),
        })

    if old.get("description") != new.get("description"):
        changes.append({
            "type": "description",
            "title": "Изменён META DESCRIPTION",
            "old": old.get("description", ""),
            "new": new.get("description", ""),
        })

    for heading in ["h1", "h2", "h3"]:
        old_values = old.get(heading, [])
        new_values = new.get(heading, [])

        added, removed = compare_lists(old_values, new_values)

        if added or removed:
            changes.append({
                "type": heading,
                "title": f"Изменения {heading.upper()}",
                "added": added,
                "removed": removed,
            })

    old_blocks = old.get("text_blocks", [])
    new_blocks = new.get("text_blocks", [])

    added, removed = compare_lists(old_blocks, new_blocks)

    if added or removed:
        changes.append({
            "type": "text",
            "title": "Изменения текста",
            "added": added[:MAX_TEXT_CHANGES],
            "removed": removed[:MAX_TEXT_CHANGES],
        })

    old_links = old.get("links", [])
    new_links = new.get("links", [])

    added, removed = compare_lists(old_links, new_links)

    if added or removed:
        changes.append({
            "type": "links",
            "title": "Изменения внутренних ссылок",
            "added": added[:MAX_LINK_CHANGES],
            "removed": removed[:MAX_LINK_CHANGES],
        })

    return changes


def changes_to_html(changes):
    result = []

    for change in changes:
        result.append("<h3>" + html.escape(change["title"]) + "</h3>")

        if "old" in change and "new" in change:
            result.append(
                "<p><strong>Было:</strong><br>"
                + html.escape(change["old"])
                + "</p>"
            )
            result.append(
                "<p><strong>Стало:</strong><br>"
                + html.escape(change["new"])
                + "</p>"
            )

        if change.get("added"):
            result.append("<p><strong>Добавлено:</strong></p>")
            result.append("<ul>")

            for item in change["added"]:
                result.append("<li>➕ " + html.escape(item) + "</li>")

            result.append("</ul>")

        if change.get("removed"):
            result.append("<p><strong>Удалено:</strong></p>")
            result.append("<ul>")

            for item in change["removed"]:
                result.append("<li>➖ " + html.escape(item) + "</li>")

            result.append("</ul>")

    return "\n".join(result)


def create_feed_item(site_name, url, changes):
    now = datetime.now(timezone.utc)
    description = changes_to_html(changes)

    fingerprint_data = (
        site_name
        + url
        + json.dumps(changes, ensure_ascii=False, sort_keys=True)
        + now.isoformat()
    )

    guid = hashlib.sha256(
        fingerprint_data.encode("utf-8")
    ).hexdigest()

    important = []

    for change in changes:
        if change["type"] == "title":
            important.append("TITLE")
        elif change["type"] == "description":
            important.append("Description")
        elif change["type"] in ["h1", "h2", "h3"]:
            important.append(change["type"].upper())
        elif change["type"] == "text":
            important.append("текст")
        elif change["type"] == "links":
            important.append("ссылки")

    important = list(dict.fromkeys(important))

    title = site_name + " — изменены " + ", ".join(important)

    return {
        "title": title,
        "link": url,
        "description": description,
        "date": now.isoformat(),
        "guid": guid,
    }


def build_rss(items):
    os.makedirs("docs", exist_ok=True)

    now = datetime.now(timezone.utc)
    xml = []

    xml.append('<?xml version="1.0" encoding="UTF-8"?>')
    xml.append('<rss version="2.0">')
    xml.append("<channel>")
    xml.append("<title>Мониторинг сайтов конкурентов</title>")
    xml.append("<description>Изменения отслеживаемых сайтов</description>")
    xml.append("<link>https://github.com/</link>")
    xml.append("<language>ru</language>")
    xml.append(
        "<lastBuildDate>"
        + html.escape(format_datetime(now))
        + "</lastBuildDate>"
    )

    for item in items:
        item_date = datetime.fromisoformat(item["date"])

        xml.append("<item>")
        xml.append(
            "<title>"
            + html.escape(item["title"])
            + "</title>"
        )
        xml.append(
            "<link>"
            + html.escape(item["link"])
            + "</link>"
        )
        xml.append(
            '<guid isPermaLink="false">'
            + html.escape(item["guid"])
            + "</guid>"
        )
        xml.append(
            "<pubDate>"
            + html.escape(format_datetime(item_date))
            + "</pubDate>"
        )
        xml.append("<description><![CDATA[")
        xml.append(item["description"])
        xml.append("]]></description>")
        xml.append("</item>")

    xml.append("</channel>")
    xml.append("</rss>")

    with open(FEED_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(xml))


def main():
    sites = load_json(SITES_FILE, [])
    state = load_json(STATE_FILE, {})
    feed_items = load_json(FEED_ITEMS_FILE, [])

    for site in sites:
        name = site["name"]
        url = site["url"]

        print("")
        print("=" * 60)
        print(f"Проверяем: {name}")
        print(url)

        try:
            new_snapshot = get_snapshot(url)
        except Exception as error:
            print(f"ОШИБКА: {error}")
            continue

        new_hash = hash_snapshot(new_snapshot)
        old_data = state.get(url)

        if not old_data:
            print("Первый запуск.")
            print("Сохраняем исходное состояние.")

            state[url] = {
                "name": name,
                "hash": new_hash,
                "snapshot": new_snapshot,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
            continue

        old_hash = old_data.get("hash")

        if old_hash == new_hash:
            print("Изменений нет.")
            state[url]["checked_at"] = datetime.now(timezone.utc).isoformat()
            continue

        old_snapshot = old_data.get("snapshot", {})
        changes = compare_snapshots(old_snapshot, new_snapshot)

        if changes:
            print("Найдены изменения:")

            for change in changes:
                print(" - " + change["title"])

            item = create_feed_item(name, url, changes)
            feed_items.insert(0, item)
        else:
            print(
                "HTML изменился, но значимых изменений не обнаружено."
            )

        state[url] = {
            "name": name,
            "hash": new_hash,
            "snapshot": new_snapshot,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    feed_items = feed_items[:MAX_FEED_ITEMS]

    save_json(STATE_FILE, state)
    save_json(FEED_ITEMS_FILE, feed_items)
    build_rss(feed_items)

    print("")
    print("=" * 60)
    print("Проверка завершена.")


if __name__ == "__main__":
    main()
