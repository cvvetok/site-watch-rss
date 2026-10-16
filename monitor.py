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


# ============================================================
# SETTINGS
# ============================================================

SITES_FILE = "sites.json"
STATE_FILE = "state.json"
FEED_ITEMS_FILE = "feed_items.json"
FEED_FILE = "docs/feed.xml"

MAX_FEED_ITEMS = 500
MAX_TEXT_CHANGES = 25
MAX_LINK_CHANGES = 20

CONNECT_TIMEOUT = 15
READ_TIMEOUT = 40


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

retry = Retry(
    total=3,
    connect=3,
    read=3,
    backoff_factor=1,
    status_forcelist=[
        429,
        500,
        502,
        503,
        504,
    ],
    allowed_methods=["GET"],
    raise_on_status=False,
)

adapter = HTTPAdapter(
    max_retries=retry
)

session.mount(
    "http://",
    adapter
)

session.mount(
    "https://",
    adapter
)

session.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/141.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,"
            "application/xhtml+xml,"
            "application/xml;q=0.9,"
            "image/avif,"
            "image/webp,"
            "*/*;q=0.8"
        ),
        "Accept-Language": (
            "ru-RU,ru;q=0.9,en;q=0.8"
        ),
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
)


# ============================================================
# JSON
# ============================================================

def load_json(filename, default):

    if not os.path.exists(filename):
        return default

    try:

        with open(
            filename,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except (
        json.JSONDecodeError,
        OSError
    ) as error:

        print(
            f"ПРЕДУПРЕЖДЕНИЕ: "
            f"не удалось прочитать "
            f"{filename}: {error}"
        )

        return default


def save_json(filename, data):

    directory = os.path.dirname(
        filename
    )

    if directory:
        os.makedirs(
            directory,
            exist_ok=True
        )

    with open(
        filename,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )


# ============================================================
# HELPERS
# ============================================================

def normalize(text):

    if text is None:
        return ""

    text = str(text)

    text = text.replace(
        "\xa0",
        " "
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


def clean_url(url):

    return (
        url
        .split("#", 1)[0]
        .rstrip("/")
    )


def unique_keep_order(values):

    return list(
        dict.fromkeys(
            values
        )
    )


def hash_snapshot(snapshot):

    data = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True
    )

    return hashlib.sha256(
        data.encode("utf-8")
    ).hexdigest()


def tag_attrs(tag):

    attrs = getattr(
        tag,
        "attrs",
        None
    )

    if isinstance(
        attrs,
        dict
    ):
        return attrs

    return {}


# ============================================================
# HTML CLEANING
# ============================================================

def remove_noisy_elements(soup):

    # Технические элементы,
    # которые не должны участвовать
    # в сравнении страниц.

    technical_tags = [
        "script",
        "style",
        "noscript",
        "svg",
        "canvas",
        "iframe",
        "template",
    ]

    for tag in list(
        soup.find_all(
            technical_tags
        )
    ):

        if getattr(
            tag,
            "parent",
            None
        ) is not None:

            tag.decompose()

    # Блоки, которые часто
    # динамически меняются
    # и создают ложные изменения.

    noisy_patterns = [
        "cookie",
        "cookies",
        "captcha",
        "recaptcha",
        "chat-widget",
        "chatwidget",
        "chat_widget",
    ]

    to_remove = []

    # ВАЖНО:
    # здесь мы только собираем
    # элементы для удаления.
    #
    # Не удаляем их прямо во время
    # перебора BeautifulSoup.
    #
    # Именно это исправляет ошибку:
    #
    # 'NoneType' object has no attribute 'get'

    for tag in list(
        soup.find_all(True)
    ):

        attrs = tag_attrs(tag)

        classes = (
            attrs.get("class")
            or []
        )

        if isinstance(
            classes,
            str
        ):
            classes = [classes]

        classes_text = " ".join(
            str(item)
            for item in classes
        ).lower()

        tag_id = normalize(
            attrs.get(
                "id",
                ""
            )
        ).lower()

        combined = (
            classes_text
            + " "
            + tag_id
        )

        if any(
            pattern in combined
            for pattern in noisy_patterns
        ):

            to_remove.append(
                tag
            )

    # А уже здесь удаляем.

    for tag in to_remove:

        if getattr(
            tag,
            "parent",
            None
        ) is not None:

            tag.decompose()


# ============================================================
# HEADINGS
# ============================================================

def extract_headings(
    soup,
    tag_name
):

    values = []

    for tag in soup.find_all(
        tag_name
    ):

        text = normalize(
            tag.get_text(
                " ",
                strip=True
            )
        )

        if text:
            values.append(
                text
            )

    return unique_keep_order(
        values
    )


# ============================================================
# INTERNAL LINKS
# ============================================================

def extract_internal_links(
    soup,
    final_url
):

    links = []

    base_host = (
        urlparse(
            final_url
        )
        .netloc
        .lower()
        .replace(
            "www.",
            ""
        )
    )

    for tag in soup.find_all(
        "a",
        href=True
    ):

        attrs = tag_attrs(tag)

        href = normalize(
            attrs.get(
                "href",
                ""
            )
        )

        if not href:
            continue

        href_lower = href.lower()

        if href_lower.startswith(
            (
                "mailto:",
                "tel:",
                "javascript:",
                "data:",
                "#",
            )
        ):
            continue

        absolute = urljoin(
            final_url,
            href
        )

        parsed = urlparse(
            absolute
        )

        if parsed.scheme not in (
            "http",
            "https"
        ):
            continue

        host = (
            parsed.netloc
            .lower()
            .replace(
                "www.",
                ""
            )
        )

        # Только внутренние ссылки.

        if host != base_host:
            continue

        cleaned = (
            f"{parsed.scheme}://"
            f"{parsed.netloc}"
            f"{parsed.path}"
        )

        # Оставляем полезные query-параметры,
        # но удаляем рекламные метки.

        if parsed.query:

            params = []

            for param in (
                parsed.query.split("&")
            ):

                param_lower = (
                    param.lower()
                )

                if param_lower.startswith(
                    (
                        "utm_",
                        "yclid",
                        "gclid",
                        "fbclid",
                        "_ga",
                    )
                ):
                    continue

                params.append(
                    param
                )

            if params:

                cleaned += (
                    "?"
                    + "&".join(params)
                )

        cleaned = clean_url(
            cleaned
        )

        if cleaned:

            links.append(
                cleaned
            )

    return sorted(
        set(links)
    )


# ============================================================
# TEXT
# ============================================================

def extract_text_blocks(soup):

    blocks = []

    interesting_tags = [
        "p",
        "li",
        "h1",
        "h2",
        "h3",
        "h4",
        "blockquote",
    ]

    for tag in soup.find_all(
        interesting_tags
    ):

        text = normalize(
            tag.get_text(
                " ",
                strip=True
            )
        )

        # Очень короткие элементы —
        # чаще всего пункты меню,
        # кнопки и технический шум.

        if len(text) < 20:
            continue

        # Защита от огромных
        # технических блоков.

        if len(text) > 3000:
            continue

        blocks.append(
            text
        )

    return unique_keep_order(
        blocks
    )


# ============================================================
# SNAPSHOT VALIDATION
# ============================================================

def validate_snapshot(snapshot):

    useful_count = (
        bool(
            snapshot.get(
                "title"
            )
        )
        +
        len(
            snapshot.get(
                "h1",
                []
            )
        )
        +
        len(
            snapshot.get(
                "h2",
                []
            )
        )
        +
        len(
            snapshot.get(
                "h3",
                []
            )
        )
        +
        len(
            snapshot.get(
                "text_blocks",
                []
            )
        )
        +
        len(
            snapshot.get(
                "links",
                []
            )
        )
    )

    # Если вообще ничего полезного
    # не получили — вероятнее всего,
    # сайт требует JavaScript
    # или блокирует парсер.

    if useful_count == 0:

        raise RuntimeError(
            "Не удалось извлечь полезное "
            "содержимое страницы. "
            "Вероятно, сайт отдаёт контент "
            "только после выполнения JavaScript "
            "или блокирует автоматические запросы."
        )


def is_placeholder_snapshot(snapshot):

    if not isinstance(
        snapshot,
        dict
    ):
        return False

    texts = []

    texts.extend(
        snapshot.get(
            "h1",
            []
        )
        or []
    )

    texts.extend(
        snapshot.get(
            "h2",
            []
        )
        or []
    )

    texts.extend(
        snapshot.get(
            "h3",
            []
        )
        or []
    )

    texts.extend(
        snapshot.get(
            "text_blocks",
            []
        )
        or []
    )

    combined = " ".join(
        normalize(text)
        for text in texts
    ).lower()

    placeholder_phrases = [
        "javascript отключен",
        "включите javascript",
        "enable javascript",
        "javascript is disabled",
    ]

    return any(
        phrase in combined
        for phrase in placeholder_phrases
    )


# ============================================================
# PAGE SNAPSHOT
# ============================================================

def get_snapshot(url):

    response = session.get(
        url,
        timeout=(
            CONNECT_TIMEOUT,
            READ_TIMEOUT
        ),
        allow_redirects=True
    )

    response.raise_for_status()

    content_type = (
        response.headers
        .get(
            "Content-Type",
            ""
        )
        .lower()
    )

    if (
        "text/html"
        not in content_type
        and
        "application/xhtml+xml"
        not in content_type
    ):

        raise RuntimeError(
            "Страница не является HTML. "
            f"Content-Type: "
            f"{content_type or 'не указан'}"
        )

    # Иногда сервер не указывает
    # кодировку правильно.

    if not response.encoding:

        response.encoding = (
            response.apparent_encoding
        )

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    final_url = response.url

    # --------------------------------------------------------
    # TITLE
    # --------------------------------------------------------

    title = ""

    if soup.title:

        title = normalize(
            soup.title.get_text(
                " ",
                strip=True
            )
        )

    # --------------------------------------------------------
    # META DESCRIPTION
    # --------------------------------------------------------

    description = ""

    description_tag = soup.find(
        "meta",
        attrs={
            "name": re.compile(
                r"^description$",
                re.I
            )
        }
    )

    if description_tag:

        description = normalize(
            tag_attrs(
                description_tag
            ).get(
                "content",
                ""
            )
        )

    # --------------------------------------------------------
    # CLEAN PAGE
    # --------------------------------------------------------

    remove_noisy_elements(
        soup
    )

    # --------------------------------------------------------
    # HEADINGS
    # --------------------------------------------------------

    h1 = extract_headings(
        soup,
        "h1"
    )

    h2 = extract_headings(
        soup,
        "h2"
    )

    h3 = extract_headings(
        soup,
        "h3"
    )

    # --------------------------------------------------------
    # LINKS
    # --------------------------------------------------------

    links = extract_internal_links(
        soup,
        final_url
    )

    # --------------------------------------------------------
    # TEXT
    # --------------------------------------------------------

    blocks = extract_text_blocks(
        soup
    )

    snapshot = {
        "title": title,
        "description": description,
        "h1": h1,
        "h2": h2,
        "h3": h3,
        "text_blocks": blocks,
        "links": links,
    }

    validate_snapshot(
        snapshot
    )

    return snapshot


# ============================================================
# COMPARISON
# ============================================================

def compare_lists(
    old,
    new
):

    old = old or []
    new = new or []

    old_set = set(old)
    new_set = set(new)

    added = [
        item
        for item in new
        if item not in old_set
    ]

    removed = [
        item
        for item in old
        if item not in new_set
    ]

    return (
        added,
        removed
    )


def compare_snapshots(
    old,
    new
):

    old = old or {}
    new = new or {}

    changes = []

    # --------------------------------------------------------
    # TITLE
    # --------------------------------------------------------

    if (
        old.get(
            "title",
            ""
        )
        !=
        new.get(
            "title",
            ""
        )
    ):

        changes.append(
            {
                "type": "title",
                "title": "Изменён TITLE",
                "old": old.get(
                    "title",
                    ""
                ),
                "new": new.get(
                    "title",
                    ""
                ),
            }
        )

    # --------------------------------------------------------
    # DESCRIPTION
    # --------------------------------------------------------

    if (
        old.get(
            "description",
            ""
        )
        !=
        new.get(
            "description",
            ""
        )
    ):

        changes.append(
            {
                "type": "description",
                "title": (
                    "Изменён META DESCRIPTION"
                ),
                "old": old.get(
                    "description",
                    ""
                ),
                "new": new.get(
                    "description",
                    ""
                ),
            }
        )

    # --------------------------------------------------------
    # H1 / H2 / H3
    # --------------------------------------------------------

    for heading in [
        "h1",
        "h2",
        "h3"
    ]:

        old_values = (
            old.get(
                heading,
                []
            )
            or []
        )

        new_values = (
            new.get(
                heading,
                []
            )
            or []
        )

        added, removed = (
            compare_lists(
                old_values,
                new_values
            )
        )

        if (
            added
            or
            removed
        ):

            changes.append(
                {
                    "type": heading,
                    "title": (
                        "Изменения "
                        + heading.upper()
                    ),
                    "added": added,
                    "removed": removed,
                }
            )

    # --------------------------------------------------------
    # TEXT
    # --------------------------------------------------------

    old_blocks = (
        old.get(
            "text_blocks",
            []
        )
        or []
    )

    new_blocks = (
        new.get(
            "text_blocks",
            []
        )
        or []
    )

    added, removed = (
        compare_lists(
            old_blocks,
            new_blocks
        )
    )

    if (
        added
        or
        removed
    ):

        changes.append(
            {
                "type": "text",
                "title": (
                    "Изменения текста"
                ),
                "added": (
                    added[
                        :MAX_TEXT_CHANGES
                    ]
                ),
                "removed": (
                    removed[
                        :MAX_TEXT_CHANGES
                    ]
                ),
            }
        )

    # --------------------------------------------------------
    # LINKS
    # --------------------------------------------------------

    old_links = (
        old.get(
            "links",
            []
        )
        or []
    )

    new_links = (
        new.get(
            "links",
            []
        )
        or []
    )

    added, removed = (
        compare_lists(
            old_links,
            new_links
        )
    )

    if (
        added
        or
        removed
    ):

        changes.append(
            {
                "type": "links",
                "title": (
                    "Изменения внутренних ссылок"
                ),
                "added": (
                    added[
                        :MAX_LINK_CHANGES
                    ]
                ),
                "removed": (
                    removed[
                        :MAX_LINK_CHANGES
                    ]
                ),
            }
        )

    return changes


# ============================================================
# RSS HTML
# ============================================================

def changes_to_html(changes):

    result = []

    for change in changes:

        result.append(
            "<h3>"
            +
            html.escape(
                change[
                    "title"
                ]
            )
            +
            "</h3>"
        )

        if (
            "old" in change
            and
            "new" in change
        ):

            result.append(
                "<p>"
                "<strong>Было:</strong>"
                "<br>"
                +
                html.escape(
                    normalize(
                        change["old"]
                    )
                )
                +
                "</p>"
            )

            result.append(
                "<p>"
                "<strong>Стало:</strong>"
                "<br>"
                +
                html.escape(
                    normalize(
                        change["new"]
                    )
                )
                +
                "</p>"
            )

        if change.get(
            "added"
        ):

            result.append(
                "<p>"
                "<strong>"
                "Добавлено:"
                "</strong>"
                "</p>"
            )

            result.append(
                "<ul>"
            )

            for item in change[
                "added"
            ]:

                result.append(
                    "<li>"
                    "➕ "
                    +
                    html.escape(
                        normalize(
                            item
                        )
                    )
                    +
                    "</li>"
                )

            result.append(
                "</ul>"
            )

        if change.get(
            "removed"
        ):

            result.append(
                "<p>"
                "<strong>"
                "Удалено:"
                "</strong>"
                "</p>"
            )

            result.append(
                "<ul>"
            )

            for item in change[
                "removed"
            ]:

                result.append(
                    "<li>"
                    "➖ "
                    +
                    html.escape(
                        normalize(
                            item
                        )
                    )
                    +
                    "</li>"
                )

            result.append(
                "</ul>"
            )

    return "\n".join(
        result
    )


# ============================================================
# CREATE RSS ITEM
# ============================================================

def create_feed_item(
    site_name,
    url,
    changes
):

    now = datetime.now(
        timezone.utc
    )

    description = (
        changes_to_html(
            changes
        )
    )

    fingerprint_data = (
        site_name
        +
        url
        +
        json.dumps(
            changes,
            ensure_ascii=False,
            sort_keys=True
        )
        +
        now.isoformat()
    )

    guid = hashlib.sha256(
        fingerprint_data.encode(
            "utf-8"
        )
    ).hexdigest()

    important = []

    for change in changes:

        change_type = (
            change.get(
                "type"
            )
        )

        if change_type == "title":

            important.append(
                "TITLE"
            )

        elif change_type == "description":

            important.append(
                "Description"
            )

        elif change_type in [
            "h1",
            "h2",
            "h3"
        ]:

            important.append(
                change_type.upper()
            )

        elif change_type == "text":

            important.append(
                "текст"
            )

        elif change_type == "links":

            important.append(
                "ссылки"
            )

    important = (
        unique_keep_order(
            important
        )
    )

    if important:

        title = (
            site_name
            +
            " — изменены "
            +
            ", ".join(
                important
            )
        )

    else:

        title = (
            site_name
            +
            " — обнаружены изменения"
        )

    return {
        "title": title,
        "link": url,
        "description": description,
        "date": now.isoformat(),
        "guid": guid,
    }


# ============================================================
# RSS
# ============================================================

def safe_cdata(text):

    return str(
        text
    ).replace(
        "]]>",
        "]]]]><![CDATA[>"
    )


def build_rss(items):

    os.makedirs(
        "docs",
        exist_ok=True
    )

    now = datetime.now(
        timezone.utc
    )

    xml = []

    xml.append(
        '<?xml version="1.0" '
        'encoding="UTF-8"?>'
    )

    xml.append(
        '<rss version="2.0">'
    )

    xml.append(
        "<channel>"
    )

    xml.append(
        "<title>"
        "Мониторинг сайтов конкурентов"
        "</title>"
    )

    xml.append(
        "<description>"
        "Изменения отслеживаемых сайтов"
        "</description>"
    )

    xml.append(
        "<link>"
        "https://cvvetok.github.io/"
        "site-watch-rss/"
        "</link>"
    )

    xml.append(
        "<language>"
        "ru"
        "</language>"
    )

    xml.append(
        "<lastBuildDate>"
        +
        html.escape(
            format_datetime(
                now
            )
        )
        +
        "</lastBuildDate>"
    )

    for item in items:

        try:

            item_date = (
                datetime.fromisoformat(
                    item["date"]
                )
            )

        except (
            KeyError,
            TypeError,
            ValueError
        ):

            item_date = now

        title = normalize(
            item.get(
                "title",
                "Изменение сайта"
            )
        )

        link = normalize(
            item.get(
                "link",
                ""
            )
        )

        guid = normalize(
            item.get(
                "guid",
                ""
            )
        )

        xml.append(
            "<item>"
        )

        xml.append(
            "<title>"
            +
            html.escape(
                title
            )
            +
            "</title>"
        )

        xml.append(
            "<link>"
            +
            html.escape(
                link
            )
            +
            "</link>"
        )

        xml.append(
            '<guid '
            'isPermaLink="false">'
            +
            html.escape(
                guid
            )
            +
            "</guid>"
        )

        xml.append(
            "<pubDate>"
            +
            html.escape(
                format_datetime(
                    item_date
                )
            )
            +
            "</pubDate>"
        )

        xml.append(
            "<description>"
            "<![CDATA["
        )

        xml.append(
            safe_cdata(
                item.get(
                    "description",
                    ""
                )
            )
        )

        xml.append(
            "]]>"
            "</description>"
        )

        xml.append(
            "</item>"
        )

    xml.append(
        "</channel>"
    )

    xml.append(
        "</rss>"
    )

    with open(
        FEED_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        f.write(
            "\n".join(
                xml
            )
        )


# ============================================================
# MAIN
# ============================================================

def main():

    sites = load_json(
        SITES_FILE,
        []
    )

    state = load_json(
        STATE_FILE,
        {}
    )

    feed_items = load_json(
        FEED_ITEMS_FILE,
        []
    )

    if not isinstance(
        sites,
        list
    ):

        raise RuntimeError(
            "sites.json должен "
            "содержать JSON-массив."
        )

    if not isinstance(
        state,
        dict
    ):

        print(
            "ПРЕДУПРЕЖДЕНИЕ: "
            "state.json имеет "
            "неверный формат."
        )

        state = {}

    if not isinstance(
        feed_items,
        list
    ):

        print(
            "ПРЕДУПРЕЖДЕНИЕ: "
            "feed_items.json имеет "
            "неверный формат."
        )

        feed_items = []

    successful = 0
    failed = 0

    for site in sites:

        if not isinstance(
            site,
            dict
        ):

            print("")
            print(
                "=" * 60
            )

            print(
                "ОШИБКА: некорректная "
                "запись в sites.json"
            )

            failed += 1
            continue

        name = normalize(
            site.get(
                "name",
                "Без названия"
            )
        )

        url = normalize(
            site.get(
                "url",
                ""
            )
        )

        print("")
        print(
            "=" * 60
        )

        print(
            f"Проверяем: {name}"
        )

        print(
            url
        )

        if not url:

            print(
                "ОШИБКА: URL не указан."
            )

            failed += 1
            continue

        try:

            new_snapshot = (
                get_snapshot(
                    url
                )
            )

        except Exception as error:

            print(
                "ОШИБКА: "
                f"{type(error).__name__}: "
                f"{error}"
            )

            print(
                "Старое состояние "
                "этой страницы "
                "сохранено без изменений."
            )

            failed += 1
            continue

        successful += 1

        new_hash = (
            hash_snapshot(
                new_snapshot
            )
        )

        old_data = (
            state.get(
                url
            )
        )

        old_snapshot = {}

        if isinstance(
            old_data,
            dict
        ):

            old_snapshot = (
                old_data.get(
                    "snapshot",
                    {}
                )
                or {}
            )

        # У старой версии WebCanape
        # мог сохраниться ложный снимок:
        #
        # "JavaScript отключен..."
        #
        # Такой снимок не считаем
        # настоящим исходным состоянием.

        invalid_old_snapshot = (
            is_placeholder_snapshot(
                old_snapshot
            )
        )

        # ----------------------------------------------------
        # FIRST SUCCESSFUL SNAPSHOT
        # ----------------------------------------------------

        if (
            not isinstance(
                old_data,
                dict
            )
            or
            not old_snapshot
            or
            invalid_old_snapshot
        ):

            if invalid_old_snapshot:

                print(
                    "Обнаружен старый "
                    "некорректный JS-снимок."
                )

                print(
                    "Он будет заменён "
                    "нормальным исходным "
                    "состоянием."
                )

            else:

                print(
                    "Первый успешный запуск "
                    "для этой страницы."
                )

            print(
                "Сохраняем исходное состояние."
            )

            print(
                "Запись в RSS "
                "пока не создаём."
            )

            state[url] = {
                "name": name,
                "hash": new_hash,
                "snapshot": new_snapshot,
                "checked_at": (
                    datetime.now(
                        timezone.utc
                    ).isoformat()
                ),
            }

            continue

        old_hash = (
            old_data.get(
                "hash"
            )
        )

        # ----------------------------------------------------
        # NOTHING CHANGED
        # ----------------------------------------------------

        if (
            old_hash
            ==
            new_hash
        ):

            print(
                "Изменений нет."
            )

            old_data[
                "name"
            ] = name

            old_data[
                "checked_at"
            ] = (
                datetime.now(
                    timezone.utc
                ).isoformat()
            )

            state[url] = (
                old_data
            )

            continue

        # ----------------------------------------------------
        # COMPARE
        # ----------------------------------------------------

        changes = (
            compare_snapshots(
                old_snapshot,
                new_snapshot
            )
        )

        if changes:

            print(
                "Найдены изменения:"
            )

            for change in changes:

                print(
                    " - "
                    +
                    change[
                        "title"
                    ]
                )

            item = (
                create_feed_item(
                    name,
                    url,
                    changes
                )
            )

            feed_items.insert(
                0,
                item
            )

        else:

            print(
                "Снимок изменился, "
                "но отслеживаемых "
                "значимых изменений "
                "не обнаружено."
            )

        # ----------------------------------------------------
        # SAVE CURRENT STATE
        # ----------------------------------------------------

        state[url] = {
            "name": name,
            "hash": new_hash,
            "snapshot": new_snapshot,
            "checked_at": (
                datetime.now(
                    timezone.utc
                ).isoformat()
            ),
        }

    # Ограничиваем размер истории.

    feed_items = (
        feed_items[
            :MAX_FEED_ITEMS
        ]
    )

    save_json(
        STATE_FILE,
        state
    )

    save_json(
        FEED_ITEMS_FILE,
        feed_items
    )

    build_rss(
        feed_items
    )

    print("")
    print(
        "=" * 60
    )

    print(
        "Проверка завершена."
    )

    print(
        f"Успешно проверено: "
        f"{successful}"
    )

    print(
        f"С ошибкой: "
        f"{failed}"
    )

    print(
        f"Записей в RSS: "
        f"{len(feed_items)}"
    )


if __name__ == "__main__":
    main()
