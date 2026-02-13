from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import re
from collections import deque
from datetime import timedelta

import feedparser
from dateutil import parser

from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util
from .const import REFRESH_INTERVAL, DW_DISMISS_DELAY, DW_EVENT, MAX_QUEUE_SIZE

_LOGGER = logging.getLogger(__name__)

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
            return False

        now = dt_util.as_local(dt_util.utcnow())
        hour = now.hour

        if self._block_start < self._block_end:
            return self._block_start <= hour < self._block_end

        if self._block_start > self._block_end:
            return hour >= self._block_start or hour < self._block_end

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
        if entry.get("enclosures"):
            url = entry.enclosures[0].get("url")
            if url:
                return url

        media = entry.get("media_content")
        if media and isinstance(media, list):
            url = media[0].get("url")
            if url:
                return url

        thumb = entry.get("media_thumbnail")
        if thumb and isinstance(thumb, list):
            url = thumb[0].get("url")
            if url:
                return url

        html_text = entry.get("description") or entry.get("summary")
        if html_text:
            match = re.search(r'<img[^>]+src="([^">]+)"', html_text)
            if match:
                return match.group(1)

        return None

    def _extract_summary(self, entry):
        if entry.get("content"):
            value = entry.content[0].get("value")
            if value:
                return clean_html(value)

        summary = entry.get("summary")
        if summary:
            return clean_html(summary)

        description = entry.get("description")
        if description:
            return clean_html(description)

        return None

    async def async_refresh_now(self):
        """Force refresh even during block window."""
        self._force_refresh = True
        await self.async_refresh()

    async def _async_update_data(self):
        #_LOGGER.warning(
        #    "NOSNews update tick (force=%s blocked=%s)",
        #    self._force_refresh,
        #    self._is_blocked_now(),
        #)

        if not self._force_refresh and self._is_blocked_now():
            return self.data or self._cached_entries

        try:
            entries = await self.hass.async_add_executor_job(self._fetch)
        finally:
            self._force_refresh = False
            self.last_update = dt_util.now()

        if self._dwains_enabled and entries:
            # ---------------- SUMMARY ---------------- #
            new_counts: dict[str, int] = {}

            for article in entries:
                article_id = self._article_id(article)
                if article_id not in {
                    self._article_id(a) for a in self._cached_entries
                }:
                    feed = article.get("feed_name", "Unknown")
                    new_counts[feed] = new_counts.get(feed, 0) + 1

            if new_counts:
                message = "\n".join(
                    f"{feed}: {count} new articles"
                    for feed, count in new_counts.items()
                )

                if self.hass.services.has_service(
                    "dwains_dashboard", "notification_create"
                ):
                    await self.hass.services.async_call(
                        "dwains_dashboard",
                        "notification_create",
                        {
                            "notification_id": (
                                f"nosnews_{self.entry.entry_id}_summary"
                            ),
                            "title": "NOS News: New Articles",
                            "message": message,
                        },
                        blocking=True,
                    )

                    if not self._current_notification_active:
                        self._current_notification_active = True
                else:
                    _LOGGER.warning(
                        "Dwains notification service not available for summary"
                    )

            # ---------------- PER-ARTICLE QUEUE ---------------- #
            newest = entries[0] if entries else None

            if newest:
                if self._last_shown_published is None and not new_counts:
                    self.hass.loop.call_soon_threadsafe(
                        lambda: asyncio.create_task(
                            self._async_create_dwains_notification(
                                newest, is_last=True
                            )
                        )
                    )
                else:
                    self._enqueue_new_articles(entries)
                    if not new_counts:
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
        for article in entries:
            article_id = self._article_id(article)

            if article_id in self._seen_articles:
                continue

            if not article.get("published"):
                continue

            published_ts = parser.parse(article["published"]).timestamp()

            if self._last_shown_published and published_ts <= self._last_shown_published:
                continue

            if any(
                self._article_id(a) == article_id
                for a in self._notification_queue
            ):
                continue

            self._notification_queue.append(article)

    def _schedule_show_next(self):
        async def _show_next(_now=None):
            if self._current_notification_active:
                return
            if not self._notification_queue:
                return

            article = self._notification_queue.popleft()
            is_last = not self._notification_queue
            await self._async_create_dwains_notification(article, is_last)

        asyncio.run_coroutine_threadsafe(_show_next(), self.hass.loop)

    # ---------------- DWAIN'S ---------------- #

    async def _async_create_dwains_notification(
        self, article, is_last: bool = False
    ):
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
            self._last_shown_published = parser.parse(
                article["published"]
            ).timestamp()
        else:
            import time

            self._last_shown_published = time.time()

        if not self.hass.services.has_service(
            "dwains_dashboard", "notification_create"
        ):
            _LOGGER.warning("Dwains notification service not available")
            self._current_notification_active = False
            return

        published_time = None
        if article.get("published"):
            published_time = parser.parse(
                article["published"]
            ).strftime("%H:%M")

        time_prefix = f"{published_time} – " if published_time else ""
        suffix = "\n\nLast message" if is_last else ""

        await self.hass.services.async_call(
            "dwains_dashboard",
            "notification_create",
            {
                "notification_id": f"nosnews_{self.entry.entry_id}",
                "title": "NOS News",
                "message": (
                    f"{html.escape(article['feed_name'])} | "
                    f"{time_prefix}{article['title']}{suffix}",
                ),
                "entity_id_format": "dashboard.{}",
            },
            blocking=True,
        )

    def _dwains_listener(self, event):
        notification_id = event.data.get("notification_id")

        article_id = f"nosnews_{self.entry.entry_id}"
        summary_id = f"nosnews_{self.entry.entry_id}_summary"

        if notification_id not in (article_id, summary_id):
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

        return [
            article
            for article in self.data
            if self._article_id(article) not in self._seen_articles
        ]
