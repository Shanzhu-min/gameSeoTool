from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse
import re
import xml.etree.ElementTree as ET

from .config import SiteConfig
from .http import fetch
from .models import GamePage


def discover_game_pages(site: SiteConfig, max_sitemaps: int = 20) -> list[GamePage]:
    sitemap_urls = expand_sitemap(site.sitemap_url, max_sitemaps=max_sitemaps)
    pages: list[GamePage] = []
    for sitemap_url in sitemap_urls:
        response = fetch(sitemap_url)
        if response.status < 200 or response.status >= 400:
            continue
        pages.extend(parse_urlset(response.text, site))
    return dedupe_pages(pages)


def expand_sitemap(sitemap_url: str, max_sitemaps: int = 20) -> list[str]:
    response = fetch(sitemap_url)
    if response.status < 200 or response.status >= 400:
        return []
    root = parse_xml(response.text)
    if root is None:
        return [sitemap_url]

    if root.tag.endswith("sitemapindex"):
        urls = [text for text in iter_child_text(root, "sitemap", "loc")]
        game_like = [url for url in urls if any(token in url.lower() for token in ["game", "games"])]
        return (game_like or urls)[:max_sitemaps]
    return [sitemap_url]


def parse_urlset(xml_text: str, site: SiteConfig) -> list[GamePage]:
    root = parse_xml(xml_text)
    if root is None:
        return []

    pages: list[GamePage] = []
    for node in iter_children(root, "url"):
        loc_text = first_child_text(node, "loc")
        if not loc_text:
            continue
        url = loc_text.strip()
        if site.url_patterns and not any(pattern in url for pattern in site.url_patterns):
            continue
        slug = slug_from_url(url)
        if not slug:
            continue
        lastmod = first_child_text(node, "lastmod")
        pages.append(
            GamePage(
                site_name=site.name,
                url=url,
                slug=slug,
                title=title_from_slug(slug),
                lastmod=lastmod,
                discovered_at=datetime.now(timezone.utc),
            )
        )
    return pages


def parse_xml(xml_text: str) -> ET.Element | None:
    try:
        return ET.fromstring(xml_text)
    except ET.ParseError:
        return None


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def iter_children(root: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in root.iter() if local_name(child.tag) == name]


def first_child_text(root: ET.Element, name: str) -> str | None:
    for child in root:
        if local_name(child.tag) == name and child.text:
            return child.text.strip()
    return None


def iter_child_text(root: ET.Element, parent_name: str, child_name: str) -> list[str]:
    values: list[str] = []
    for parent in iter_children(root, parent_name):
        value = first_child_text(parent, child_name)
        if value:
            values.append(value)
    return values


def slug_from_url(url: str) -> str:
    path = urlparse(url).path.strip("/")
    if not path:
        return ""
    slug = path.split("/")[-1]
    return re.sub(r"\.(html|php|aspx)$", "", slug, flags=re.IGNORECASE)


def title_from_slug(slug: str) -> str:
    normalized = re.sub(r"[-_]+", " ", slug)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def dedupe_pages(pages: list[GamePage]) -> list[GamePage]:
    seen: set[str] = set()
    unique: list[GamePage] = []
    for page in pages:
        if page.url in seen:
            continue
        seen.add(page.url)
        unique.append(page)
    return unique
