from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform

from .const import DOMAIN
from .coordinator import NOSNewsCoordinator
from .speech import speak_news

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.MEDIA_PLAYER]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = NOSNewsCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    async def handle_speak_news(call: ServiceCall):
        await speak_news(hass, entry, coordinator)

    async def handle_next_item(call: ServiceCall):
        coordinator.index_next()
        coordinator.async_set_updated_data(coordinator.data)

    async def handle_previous_item(call: ServiceCall):
        coordinator.index_previous()
        coordinator.async_set_updated_data(coordinator.data)

    async def handle_refresh_now(call: ServiceCall):
        await coordinator.async_request_refresh()

    hass.services.async_register(DOMAIN, "speak_news", handle_speak_news)
    hass.services.async_register(DOMAIN, "next_item", handle_next_item)
    hass.services.async_register(DOMAIN, "previous_item", handle_previous_item)
    hass.services.async_register(DOMAIN, "refresh_now", handle_refresh_now)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator: NOSNewsCoordinator = entry.runtime_data
    await coordinator.async_shutdown()

    for service in ("speak_news", "next_item", "previous_item", "refresh_now"):
        hass.services.async_remove(DOMAIN, service)

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
