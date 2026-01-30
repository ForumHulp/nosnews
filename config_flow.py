from __future__ import annotations

import logging
import re
import voluptuous as vol
import feedparser

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import config_validation as cv

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)

NOS_FEEDS_URL = "https://nos.nl/feeds"
VOORWAARHEID_URL = "https://voorwaarheid.nl/category/informeren/feed"

EXCLUDED_FIELDS = {
    "guidislink",
    "id",
    "links",
    "published_parsed",
    "title_detail",
    "summary_detail",
    "author",
    "author_detail",
    "authors",
    "tags"
}

CORE_FIELDS = {
    "title",
    "link",
    "entity_picture",
}


async def fetch_nos_feeds(hass):
    session = async_get_clientsession(hass)
    try:
        async with session.get(NOS_FEEDS_URL, timeout=15) as resp:
            text = await resp.text()
    except Exception as err:
        _LOGGER.error("Failed to fetch NOS feeds page: %s", err)
        return {}

    feeds = {}
    for url, title in re.findall(
        r'href="(https://feeds\.nos\.nl/[^"]+)".*?>([^<]+)<', text
    ):
        title = title.strip().lower()
        title = title.replace("nos nieuws", "").strip()
        if not title or "sport" in title or "sport" in url.lower():
            continue

        feeds[title.capitalize()] = url

    feeds["Voorwaarheid"] = VOORWAARHEID_URL
    return feeds


async def get_available_inclusions(hass, feed_urls):
    if not feed_urls:
        return []

    fields = set()
    for url in feed_urls:
        try:
            parsed = await hass.async_add_executor_job(feedparser.parse, url)
            for entry in parsed.entries[:3]:
                fields.update(entry.keys())
        except Exception:
            continue

    fields -= EXCLUDED_FIELDS
    fields -= CORE_FIELDS
    fields.add("feed_name")
    fields.add("entity_picture")
    return sorted(fields)


class NOSNewsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 3

    async def async_step_user(self, user_input=None):
        errors = {}
        feeds = await fetch_nos_feeds(self.hass)

        if not feeds:
            errors["base"] = "cannot_fetch_feeds"

        feeds_select = {name: name for name in feeds}

        if user_input:
            selected = user_input["feeds"]
            feeds_data = {name: feeds[name] for name in selected}

            inclusions_available = await get_available_inclusions(
                self.hass, list(feeds_data.values())
            )

            user_input["feeds_data"] = feeds_data
            user_input["inclusions"] = [
                i for i in user_input.get("inclusions", [])
                if i in inclusions_available
            ]
            user_input.pop("feeds")

            await self.async_set_unique_id("nosnews")
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title="NOS News",
                data=user_input,
            )

        inclusions = await get_available_inclusions(
            self.hass, list(feeds.values())[:1]
        )

        schema = vol.Schema(
            {
                vol.Required(
                    "feeds",
                    default=list(feeds_select)[:1],
                ): cv.multi_select(feeds_select),
                vol.Optional("articles_per_feed", default=5): int,
                vol.Optional(
                    "inclusions",
                    default=[],
                ): cv.multi_select({i: i for i in inclusions}),
                vol.Optional("tts_service", default="google_translate_say"): str,
                vol.Optional(
                    "media_player_entity",
                    default="media_player.woonkamer",
                ): str,
                vol.Optional("pause_seconds", default=5): int,
                vol.Optional("radio_journaal", default=True): bool,
                vol.Optional("dwains_notifications", default=True): bool,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(entry):
        return NOSNewsOptionsFlow(entry)


class NOSNewsOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, entry):
        self.entry = entry

    async def async_step_init(self, user_input=None):
        feeds = await fetch_nos_feeds(self.hass)
        current = {**self.entry.data, **self.entry.options}

        feeds_select = {name: name for name in feeds}
        current_feed_urls = current.get("feeds_data", {})
        selected_feeds = [
            name for name, url in feeds.items()
            if url in current_feed_urls.values()
        ]

        current_feed_urls_list = list(current_feed_urls.values())
        inclusions_available = await get_available_inclusions(self.hass, current_feed_urls_list)

        if user_input:
            feeds_data = {name: feeds[name] for name in user_input["feeds"]}
            user_input["feeds_data"] = feeds_data

            user_input["inclusions"] = [
                i for i in user_input.get("inclusions", [])
                if i in inclusions_available
            ]
            user_input.pop("feeds")

            return self.async_create_entry(
                title="",
                data=user_input,
            )

        schema = vol.Schema(
            {
                vol.Required(
                    "feeds",
                    default=selected_feeds,
                ): cv.multi_select(feeds_select),
                vol.Optional(
                    "articles_per_feed",
                    default=current.get("articles_per_feed", 5),
                ): int,
                vol.Optional(
                    "inclusions",
                    default=current.get("inclusions", []),
                ): cv.multi_select({i: i for i in inclusions_available}),
                vol.Optional(
                    "tts_service",
                    default=current.get("tts_service"),
                ): str,
                vol.Optional(
                    "media_player_entity",
                    default=current.get("media_player_entity"),
                ): str,
                vol.Optional(
                    "pause_seconds",
                    default=current.get("pause_seconds", 5),
                ): int,
                vol.Optional(
                    "radio_journaal",
                    default=current.get("radio_journaal", True),
                ): bool,
                vol.Optional(
                    "dwains_notifications",
                    default=current.get("dwains_notifications", True),
                ): bool,
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
        )
