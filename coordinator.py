from __future__ import annotations

import asyncio
import hashlib
import html
import re
import logging
import feedparser
from dateutil import parser

from datetime import timedelta
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util
from homeassistant.helpers.event import async_call_later

_LOGGER = logging.getLogger(__name__)

REFRESH_INTERVAL = 1800
DW_DISMISS_DELAY = 10.0
DW_EVENT = "dwains_dashboard_notifications_updated"


def clean_html(text: str) -> str:
    if not text:
        return ""
    text = html.unescape(text)
    return re.sub("<[^<]+?>", "", text).strip()


class NOSNewsCoordinator(DataUpdateCoordinator[list[dict]]):
    def __init__(self, hass, entry):
        self.hass = hass
        self.entry = entry

        self.feed_urls = entry.data["feed_urls"]
        self.articles_per_feed = entry.options.get(
            "articles_per_feed", entry.data.get("articles_per_feed", 5)
        )

        self._cached_entries: list[dict] = []
        self.index = 0

        # Dwains state
        self._dwains_enabled = entry.options.get(
            "dwains_notifications", entry.data.get("dwains_notifications", True)
        )
        self._current_notification_active = False
        self._seen_articles = set()
        self._pending_latest_article: dict | None = None
        self._last_shown_published: float | None = None

        super().__init__(
            hass,
            _LOGGER,
            name="NOS News",
            update_interval=timedelta(seconds=REFRESH_INTERVAL),
        )

        if self._dwains_enabled:
            self._remove_dw_listener = hass.bus.async_listen(
                DW_EVENT, self._dwains_listener
            )

    async def async_shutdown(self):
        if self._dwains_enabled and self._remove_dw_listener:
            self._remove_dw_listener()

    # ---------------- INDEX CONTROL ---------------- #

    def index_next(self):
        if self.data:
            self.index = (self.index + 1) % len(self.data)

    def index_previous(self):
        if self.data:
            self.index = (self.index - 1) % len(self.data)

    # ---------------- UPDATE ---------------- #

    def _extract_image(self, entry):
        if "enclosures" in entry and entry.get("enclosures"):
            return entry.enclosures[0].get("url")
        elif "media_content" in entry:
            try:
                return entry.media_content[0].get("url")
            except Exception:
                pass

    def _extract_summary(self, entry):
        if "summary" in entry:
            return clean_html(entry.summary)
        if "content" in entry and entry.content:
            return clean_html(entry.content[0].get("value"))
        return None

    async def _async_update_data(self):
        self.last_update = dt_util.now()
        _LOGGER.warning("NOSNews async update running")
        entries = await self.hass.async_add_executor_job(self._fetch)

        if self._dwains_enabled and entries:
            latest = entries[0]
            latest_id = self._article_id(latest)

            # Show the newest article if it hasn't been seen yet
            if latest_id not in self._seen_articles:
                if self._current_notification_active:
                    self._pending_latest_article = latest
                else:
                    self.hass.loop.call_soon_threadsafe(
                        lambda: asyncio.create_task(
                            self._async_create_dwains_notification(latest)
                        )
                    )

        self.index = 0
        return entries

    def _fetch(self):
        entries = []

        for url in self.feed_urls:
            parsed = feedparser.parse(url)
            for entry in parsed.entries[: self.articles_per_feed]:
                entries.append(
                    {
                        "title": entry.get("title", ""),
                        "link": entry.get("link", ""),
                        "entity_picture": self._extract_image(entry)
                        or "https://www.home-assistant.io/images/favicon-192x192-full.png",
                        "feed_name": url.rstrip("/")
                        .split("/")[-1]
                        .replace("nosnieuws", "")
                        .replace("-", " ")
                        .capitalize(),
                        "summary": self._extract_summary(entry),
                        "published_parsed": entry.get("published_parsed"),
                        "published": entry.get("published"),
                    }
                )

        if not entries:
            return self._cached_entries

        # Sort by published time using feed's original string (respects timezone/DST)
        entries.sort(
            key=lambda e: parser.parse(e["published"]).timestamp()
            if e.get("published")
            else 0,
            reverse=True,
        )

        self._cached_entries = entries
        return entries

    # ---------------- DWAIN'S ---------------- #

    def _article_id(self, article) -> str:
        raw = f"{article.get('title','')}{article.get('feed_name','')}"
        return hashlib.md5(raw.encode()).hexdigest()

    async def _async_create_dwains_notification(self, article):
        """Create Dwains notification asynchronously (thread-safe)."""
        if not self._dwains_enabled or self._current_notification_active:
            return

        article_id = self._article_id(article)
        if article_id in self._seen_articles:
            return

        self._seen_articles.add(article_id)
        self._current_notification_active = True

        if article.get("published"):
            self._last_shown_published = parser.parse(article["published"]).timestamp()
        else:
            import time
            self._last_shown_published = time.time()

        if not self.hass.services.has_service("dwains_dashboard", "notification_create"):
            return

        published_time = None
        if article.get("published"):
            dt = parser.parse(article["published"])
            published_time = dt.strftime("%H:%M o'clock")

        time_prefix = f"{published_time} – " if published_time else ""

        await self.hass.services.async_call(
            "dwains_dashboard",
            "notification_create",
            {
                "notification_id": f"nosnews_{self.entry.entry_id}",
                "title": "NOS News",
                "message": f"{html.escape(article['feed_name'])} | {time_prefix}{article['title']}",
            },
            blocking=True,
        )

    def _dwains_listener(self, event):
        """Handle Dwains notification dismissed events."""
        data = event.data
        if data.get("notification_id") != f"nosnews_{self.entry.entry_id}":
            return

        self._current_notification_active = False

        # Schedule async-safe next notification
        def _schedule_show_next():
            async def _show_next(_now=None):
                if self._current_notification_active:
                    return

                # Show pending latest article first
                if self._pending_latest_article:
                    article = self._pending_latest_article
                    self._pending_latest_article = None
                    await self._async_create_dwains_notification(article)
                    return

                # Then show next unseen newer article
                if self._last_shown_published:
                    for article in self._cached_entries:
                        published_ts = (
                            parser.parse(article["published"]).timestamp()
                            if article.get("published")
                            else 0
                        )
                        article_id = self._article_id(article)
                        if (
                            published_ts > self._last_shown_published
                            and article_id not in self._seen_articles
                        ):
                            await self._async_create_dwains_notification(article)
                            break

            asyncio.run_coroutine_threadsafe(_show_next(), self.hass.loop)

        async_call_later(self.hass, DW_DISMISS_DELAY, lambda _: _schedule_show_next())
