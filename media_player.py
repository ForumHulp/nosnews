from __future__ import annotations

import asyncio
import logging

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

ICON = "mdi:newspaper-variant"
_LOGGER = logging.getLogger(__name__)

PLAY_INTERVAL = 5  # seconds per article

async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the NOS News media player entity."""
    coordinator = entry.runtime_data

    async_add_entities(
        [
            NOSNewsPlayer(coordinator, entry),
        ],
        update_before_add=True,
    )


class NOSNewsPlayer(CoordinatorEntity, MediaPlayerEntity):
    _attr_icon = ICON
    _attr_has_entity_name = True
    _attr_name = "NOS News"

    def __init__(self, coordinator, entry):
        self._playing = False
        self._play_task: asyncio.Task | None = None
        super().__init__(coordinator)
        self.entry = entry

    @property
    def unique_id(self):
        return f"{DOMAIN}_{self.entry.entry_id}"

    @property
    def state(self):
        """Return the current state of the media player."""
        if not self.coordinator.data:
            return MediaPlayerState.IDLE
        return MediaPlayerState.PLAYING if getattr(self, "_playing", False) else MediaPlayerState.PAUSED

    @property
    def supported_features(self):
        return (
            MediaPlayerEntityFeature.NEXT_TRACK
            | MediaPlayerEntityFeature.PREVIOUS_TRACK
            | MediaPlayerEntityFeature.PLAY
            | MediaPlayerEntityFeature.PAUSE
            | MediaPlayerEntityFeature.STOP
        )

    @property
    def available(self):
        return self.coordinator.last_update_success

    @property
    def media_title(self):
        if not self.coordinator.data:
            return "No articles"
        return self.coordinator.data[self.coordinator.index].get("title")

    @property
    def media_content_id(self):
        if not self.coordinator.data:
            return None
        return self.coordinator.data[self.coordinator.index].get("link")

    @property
    def extra_state_attributes(self):
        if not self.coordinator.data:
            return {}

        article = self.coordinator.data[self.coordinator.index]
        attrs = {
            "article_number": f"{self.coordinator.index + 1}/{len(self.coordinator.data)}",
        }

        inclusions = self.entry.options.get("inclusions") or self.entry.data.get("inclusions", [])
        for extra in ["feed_name", "entity_picture"]:
            if extra not in inclusions:
                inclusions.append(extra)
        for key in inclusions:
            if key in article:
                attrs[key] = article[key]

        return attrs

    # ---------------------------
    # Play / Pause / Stop / Next / Previous
    # ---------------------------

    async def async_media_play(self):
        """Start playing and rotate articles automatically."""
        if not self.coordinator.data or self._playing:
            return

        self._playing = True
        self.async_write_ha_state()

        # Prevent duplicate loops
        if self._play_task and not self._play_task.done():
            self._play_task.cancel()

        self._play_task = self.hass.async_create_task(
            self._play_next_article_loop()
        )

    async def async_media_pause(self):
        """Pause the current article."""
        self._playing = False
        if self._play_task:
            self._play_task.cancel()
            self._play_task = None
        self.async_write_ha_state()

    async def async_media_stop(self):
        """Stop the current article."""
        self._playing = False
        if self._play_task:
            self._play_task.cancel()
            self._play_task = None
        self.async_write_ha_state()

    async def async_media_next_track(self):
        """Go to the next article immediately."""
        if not self.coordinator.data:
            return
        self.coordinator.index = (self.coordinator.index + 1) % len(self.coordinator.data)
        self._playing = True
        self.async_write_ha_state()

    async def async_media_previous_track(self):
        """Go to the previous article immediately."""
        if not self.coordinator.data:
            return
        self.coordinator.index = (self.coordinator.index - 1) % len(self.coordinator.data)
        self._playing = True
        self.async_write_ha_state()

    # ---------------------------
    # Internal helper: auto-rotate articles
    # ---------------------------
    async def _play_next_article_loop(self):
        try:
            while self._playing and self.coordinator.data:
                await asyncio.sleep(PLAY_INTERVAL)
                if not self._playing:
                    break
                self.coordinator.index = (
                    self.coordinator.index + 1
                ) % len(self.coordinator.data)
                self.async_write_ha_state()
        except asyncio.CancelledError:
            pass
