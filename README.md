# NOS News

A Home Assistant custom integration that brings **NOS news** into your smart home as a media player, spoken news (TTS), and optional Dwains Dashboard notifications.

The integration fetches NOS RSS feeds, merges them into a single ordered news stream, and lets you browse, play, or listen to headlines directly from Home Assistant.

---

## ✨ Features

* 📡 Fetches multiple NOS RSS feeds
* 📰 Presents news as a `media_player` entity
* ⏯️ Play, pause, next, previous article support
* 🔊 Spoken news via Text-to-Speech (Dutch)
* 🗣️ Optional summaries in TTS
* 📻 Optional NOS Radio Journaal intro
* 🖼️ Article images and extra metadata as attributes
* 🔔 Optional Dwains Dashboard notifications for breaking news
* ⚙️ Fully configurable via the Home Assistant UI (Config Flow)

---

## 📦 Installation

### Via HACS (recommended)

1. Add this repository to HACS as a **Custom Repository** (category: Integration)
2. Search for **NOS News** in HACS
3. Install the integration
4. Restart Home Assistant

### Manual installation

1. Copy the `nosnews` folder into:

   ```
   custom_components/nosnews
   ```
2. Restart Home Assistant

---

## ⚙️ Configuration

Configuration is done **entirely via the UI**.

1. Go to **Settings → Devices & Services**
2. Click **Add Integration**
3. Search for **NOS News**

### Configuration options

* **News feeds** – Select one or more NOS feeds
* **Articles per feed** – Number of articles to fetch per feed
* **Additional article fields** – Extra metadata (summary, feed name, image, etc.)
* **TTS service** – Text-to-Speech service to use
* **Media player entity** – Target media player for spoken news
* **Pause between articles** – Time between articles (used for media player and TTS)
* **Radio Journaal** – Play NOS Radio Journaal before headlines
* **Dwains Dashboard notifications** – Enable breaking news notifications

All options can be changed later via **Configure** on the integration.

---

## ▶️ Media Player

The integration creates a media player entity:

```
media_player.nos_news
```

Supported actions:

* ▶️ Play – Start rotating through articles
* ⏸ Pause – Pause rotation
* ⏭ Next – Go to next article
* ⏮ Previous – Go to previous article
* ⏹ Stop – Stop playback

Article details (title, link, image, summary) are exposed as state attributes.

---

## 🔊 Spoken News (TTS)

You can trigger spoken news via the service:

```
nosnews.speak_news
```

The service will:

1. Optionally play the NOS Radio Journaal intro
2. Speak each article title (and summary, if enabled)
3. Pause naturally between articles

Text is automatically split and truncated to stay within TTS limits.

---

## 🔔 Services

The integration provides the following services:

| Service                 | Description                             |
| ----------------------- | --------------------------------------- |
| `nosnews.speak_news`    | Speak all current news articles via TTS |
| `nosnews.next_item`     | Go to the next article                  |
| `nosnews.previous_item` | Go to the previous article              |
| `nosnews.refresh_now`   | Refresh feeds immediately               |

---

## 📊 Dwains Dashboard Support

If enabled, the integration can send notifications to **Dwains Dashboard** when a new article appears.

* Duplicate notifications are automatically suppressed
* Notifications clear correctly when dismissed

Dwains support is optional and can be disabled at any time.

---

## 🛠️ Requirements

* Home Assistant 2024.1 or newer
* Internet access to fetch RSS feeds
* A configured TTS platform for spoken news

---

## 🐞 Issues & Feature Requests

Please report issues or suggest improvements via GitHub Issues.

---

## ❤️ Credits

* News content provided by **NOS**
* Built for Home Assistant using the DataUpdateCoordinator pattern

---

## 📜 Disclaimer

This project is not affiliated with or endorsed by NOS.
News content is fetched from publicly available RSS feeds.
