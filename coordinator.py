from __future__ import annotations

import asyncio
import hashlib
import html
import re
import logging
import feedparser
from dateutil import parser
from collections import deque

from datetime import timedelta
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util
from homeassistant.helpers.event import async_call_later

_LOGGER = logging.getLogger(__name__)


REFRESH_INTERVAL = 1800
DW_DISMISS_DELAY = 5.0
DW_EVENT = "dwains_dashboard_notifications_updated"

MAX_QUEUE_SIZE = 10   # Max unseen articles kept in memory


def clean_html(text: str) -> str:
    if not text:
        return ""
    text = html.unescape(text)
    return re.sub("<[^<]+?>", "", text).strip()


class NOSNewsCoordinator(DataUpdateCoordinator[list[dict]]):
    def __init__(self, hass, entry):
        self.hass = hass
        self.entry = entry

        self.feeds_data = entry.options.get(
            "feeds_data", entry.data.get("feeds_data", [])
        )
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
        self._seen_articles: set[str] = set()
        self._last_shown_published: float | None = None

        # Bounded queue for new articles
        self._notification_queue: deque[dict] = deque(maxlen=MAX_QUEUE_SIZE)
        self._block_start = 23
        self._block_end = 6
        self._force_refresh = False

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

    def _is_blocked_now(self) -> bool:
        """Return True if feed fetching is blocked by schedule."""
        if self._block_start is None or self._block_end is None:
            return False  # feature disabled / not configured

        now = dt_util.as_local(dt_util.utcnow())
        hour = now.hour

        # Normal range (e.g., 08 → 22)
        if self._block_start < self._block_end:
            return self._block_start <= hour < self._block_end

        # Overnight range (e.g., 22 → 06)
        if self._block_start > self._block_end:
            return hour >= self._block_start or hour < self._block_end

        # start == end → treat as disabled
        return False

    # ---------------- INDEX CONTROL ---------------- #

    def index_next(self):
        if self.data:
            self.index = (self.index + 1) % len(self.data)

    def index_previous(self):
        if self.data:
            self.index = (self.index - 1) % len(self.data)

    # ---------------- UPDATE ---------------- #

    def _extract_image(self, entry):
        # 1. Enclosures (best case)
        if entry.get("enclosures"):
            url = entry.enclosures[0].get("url")
            if url:
                return url

        # 2. media:content
        media = entry.get("media_content")
        if media and isinstance(media, list):
            url = media[0].get("url")
            if url:
                return url

        # 3. media:thumbnail
        thumb = entry.get("media_thumbnail")
        if thumb and isinstance(thumb, list):
            url = thumb[0].get("url")
            if url:
                return url

        # 4. <img> inside description / summary (WordPress feeds)
        html = entry.get("description") or entry.get("summary")
        if html:
            match = re.search(r'<img[^>]+src="([^">]+)"', html)
            if match:
                return match.group(1)

        return None

    def _extract_summary(self, entry):
        # 1. content:encoded (best, full article when present)
        if entry.get("content"):
            value = entry.content[0].get("value")
            if value:
                return clean_html(value)

        # 2. summary (many feeds)
        summary = entry.get("summary")
        if summary:
            return clean_html(summary)

        # 3. description (WordPress feeds like Voorwaarheid)
        description = entry.get("description")
        if description:
            return clean_html(description)

        return None

    async def async_refresh_now(self):
        """Force refresh even during block window."""
        self._force_refresh = True
        await self.async_refresh()

    async def _async_update_data(self):
        _LOGGER.warning("NOSNews update tick (force=%s blocked=%s)",
                self._force_refresh,
                self._is_blocked_now())

        # BLOCK FETCH WINDOW
        if not self._force_refresh and self._is_blocked_now():
            # Return last known data WITHOUT changing state
            return self.data or self._cached_entries

        entries = None
        try:
            entries = await self.hass.async_add_executor_job(self._fetch)
        finally:
            self._force_refresh = False
            self.last_update = dt_util.now()

        if self._dwains_enabled and entries:
            newest = entries[0]  # Always newest (already sorted desc)
            newest_id = self._article_id(newest)

            # If nothing has ever been shown → show newest immediately
            if self._last_shown_published is None:
                if not self._current_notification_active:
                    self.hass.loop.call_soon_threadsafe(
                        lambda: asyncio.create_task(
                            self._async_create_dwains_notification(newest)
                        )
                    )
            else:
                # Normal case: queue only newer unseen articles
                self._enqueue_new_articles(entries)
                self._schedule_show_next()

        self.index = 0
        return entries

    def _fetch(self):
        if not self._force_refresh and self._is_blocked_now():
            return self._cached_entries

        entries = []
        for feed_name, url in self.feeds_data.items():
            parsed = feedparser.parse(url)
            for entry in parsed.entries[: self.articles_per_feed]:
                entries.append(
                    {
                        "title": entry.get("title", ""),
                        "link": entry.get("link", ""),
                        "entity_picture": self._extract_image(entry)
                        or "https://www.home-assistant.io/images/favicon-192x192-full.png",
                        "feed_name": feed_name,
                        "summary": self._extract_summary(entry),
                        "published_parsed": entry.get("published_parsed"),
                        "published": entry.get("published"),
                    }
                )

        if not entries:
            return self._cached_entries

        entries.sort(
            key=lambda e: parser.parse(e["published"]).timestamp()
            if e.get("published")
            else 0,
            reverse=True,
        )

        self._cached_entries = entries
        return entries

    # ---------------- QUEUE LOGIC ---------------- #

    def _article_id(self, article) -> str:
        raw = f"{article.get('title','')}{article.get('feed_name','')}"
        return hashlib.md5(raw.encode()).hexdigest()

    def _enqueue_new_articles(self, entries: list[dict]):
        """Queue only articles newer than last shown."""
        for article in entries:
            article_id = self._article_id(article)

            if article_id in self._seen_articles:
                continue

            if not article.get("published"):
                continue

            published_ts = parser.parse(article["published"]).timestamp()

            # Only queue NEWER items
            if self._last_shown_published and published_ts <= self._last_shown_published:
                continue

            # Avoid duplicates already queued
            if any(self._article_id(a) == article_id for a in self._notification_queue):
                continue

            self._notification_queue.append(article)

    def _schedule_show_next(self):
        async def _show_next(_now=None):
            if self._current_notification_active:
                return

            if not self._notification_queue:
                return

            # Newest-first
            article = self._notification_queue.popleft()
            await self._async_create_dwains_notification(article)

        asyncio.run_coroutine_threadsafe(_show_next(), self.hass.loop)

    # ---------------- DWAIN'S ---------------- #

    async def _async_create_dwains_notification(self, article):
        if (
            not self._dwains_enabled
            or self._current_notification_active
            or not article
        ):
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
            _LOGGER.warning("Dwains notification service not available")
            self._current_notification_active = False
            return

        published_time = None
        if article.get("published"):
            dt = parser.parse(article["published"])
            published_time = dt.strftime("%H:%M")

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

        async_call_later(
            self.hass,
            DW_DISMISS_DELAY,
            lambda _: self._schedule_show_next(),
        )

    def get_unseen_articles(self) -> list[dict]:
        if not self.data:
            return []

        unseen = []
        for article in self.data:
            article_id = self._article_id(article)
            if article_id not in self._seen_articles:
                unseen.append(article)

        return unseen