from __future__ import annotations

import asyncio
import hashlib
import html
import re
import logging
import time
import feedparser

from datetime import timedelta
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util
from collections import deque

_LOGGER = logging.getLogger(__name__)

REFRESH_INTERVAL = 1800
SUMMARY_MAX_LENGTH = 400

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
            "articles_per_feed",
            entry.data.get("articles_per_feed", 5),
        )

        self._cached_entries: list[dict] = []
        self.index = 0

        # Dwains state
        self._dwains_enabled = entry.options.get(
            "dwains_notifications",
            entry.data.get("dwains_notifications", True),
        )
        self._current_notification_active = False
        self._seen_articles = deque(maxlen=100)

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
        entries, latest = await self.hass.async_add_executor_job(self._fetch)

        if latest:
            self._handle_dwains_notification(latest)

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
                        "entity_picture": self._extract_image(entry) or "https://www.home-assistant.io/images/favicon-192x192-full.png",
                        "feed_name": url.rstrip("/").split("/")[-1].replace("nosnieuws", "").replace("-", " ").capitalize(),
                        "summary": self._extract_summary(entry),
                        "published_parsed": entry.get("published_parsed"),
                    }
                )

        if not entries:
            return self._cached_entries, None

        entries.sort(
            key=lambda e: time.mktime(e["published_parsed"])
            if isinstance(e.get("published_parsed"), tuple)
            else 0,
            reverse=True,
        )

        self._cached_entries = entries
        return entries, entries[0]  # ⬅️ latest article


    # ---------------- DWAIN'S ---------------- #

    def _handle_dwains_notification(self, latest):
        if not self._dwains_enabled or self._current_notification_active:
            return

        raw_id = f"{latest.get('title','')}{latest.get('feed_name','')}"
        article_id = hashlib.md5(raw_id.encode()).hexdigest()

        if article_id in self._seen_articles:
            return

        self._seen_articles.append(article_id)
        self._current_notification_active = True

        if self.hass.services.has_service("dwains_dashboard", "notification_create"):
            self.hass.async_create_task(
                self.hass.services.async_call(
                    "dwains_dashboard",
                    "notification_create",
                    {
                        "notification_id": f"nosnews_{self.entry.entry_id}",
                        "title": "NOS News",
                        "message": f"{html.escape(latest['feed_name'])} | "
                                   f"{latest['title']}",
                    },
                    blocking=True,
                )
            )
        else:
            # Service not ready yet, maybe retry later
            pass

    def _dwains_listener(self, event):
        data = event.data
        if data.get("notification_id") == f"nosnews_{self.entry.entry_id}":
            _LOGGER.debug("Dwains notification dismissed")
            self._current_notification_active = False
