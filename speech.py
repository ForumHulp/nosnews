import asyncio
import html
import re
import logging

_LOGGER = logging.getLogger(__name__)

MAX_TTS_TITLE = 180
MAX_TTS_SUMMARY = 220


def split_text(text: str, max_len: int = 200):
    """Split text into TTS-safe chunks without breaking words."""
    words = re.findall(r'\S+\s*', text)
    chunks = []
    current = ""
    for w in words:
        if len(current) + len(w) > max_len:
            chunks.append(current.strip())
            current = w
        else:
            current += w
    if current:
        chunks.append(current.strip())
    return chunks


async def speak_news(hass, entry, coordinator):
    """
    Speak news via TTS using coordinator.data and entry.options/data.
    - Safe truncation at word boundaries
    - Optional summary
    - Works with Dutch TTS
    """
    if not coordinator.data:
        return

    # Merge options from entry
    options = {**entry.data, **entry.options}
    tts_service = options.get("tts_service")
    media_player = options.get("media_player_entity")
    pause = options.get("pause_seconds", 5)
    include_summary = "summary" in options.get("inclusions", [])
    radio_journaal = options.get("radio_journaal", False)

    if not tts_service or not media_player:
        _LOGGER.warning("TTS service or media player not configured")
        return

    if radio_journaal:
        try:
            await hass.services.async_call(
                "media_player",
                "play_media",
                {
                    "entity_id": media_player,
                    "media_content_id": "http://192.168.178.12:8123/local/media/nos_journaal.wav",
                    "media_content_type": "music",
                },
                blocking=True,
            )
            await asyncio.sleep(pause)
        except Exception as e:
            _LOGGER.error("Failed to play Radio Journaal: %s", e)

    for idx, article in enumerate(coordinator.data):
        feed_name = html.unescape(article.get("feed_name", ""))
        title = html.unescape(article.get("title", ""))
        summary = html.unescape(article.get("summary", ""))

        # Safe truncation
        if len(title) > MAX_TTS_TITLE:
            title = title[:MAX_TTS_TITLE].rsplit(" ", 1)[0] + "..."
        if include_summary and len(summary) > MAX_TTS_SUMMARY:
            summary = summary[:MAX_TTS_SUMMARY].rsplit(" ", 1)[0] + "..."

        # Prefix
        prefix = (
            "Eerste bericht" if idx == 0 else
            "Laatste bericht" if idx == len(coordinator.data) - 1 else
            "Volgende bericht"
        )

        # Full text
        full_text = f"{prefix}. {feed_name}. {title}."
        if include_summary and summary:
            full_text += f" Samenvatting: {summary}"

        # Split text into chunks
        chunks = split_text(full_text)

        # Speak each chunk
        for chunk in chunks:
            await hass.services.async_call(
                "tts",
                tts_service,
                {
                    "entity_id": media_player,
                    "message": chunk,
                    "language": "nl",
                },
                blocking=True
            )

        # Pause between articles
        spoken_len = sum(len(c) for c in chunks)
        await asyncio.sleep(pause + spoken_len / 15)

    if radio_journaal:
        try:
            await hass.services.async_call(
                "tts",
                tts_service,
                {
                    "entity_id": media_player,
                    "message": "Einde journaal",
                    "language": "nl"
                },
                blocking=True
            )
        except Exception as e:
            _LOGGER.error("Failed to say 'Einde journaal': %s", e)