[Uploading README.md…]()
# 🎁 Telegram Gift Bot — بوت هدايا تيليغرام (v2)

An AI-powered personal Telegram bot that monitors source channels, translates content to professional Arabic using **Groq AI (free)**, and auto-posts to your channel — including photos, videos, GIFs, and stickers.

---

## ✨ What It Does

| Feature | Details |
|---|---|
| 📡 Channel Monitor | Watches any number of source channels via Telethon |
| 🤖 AI Rewriter | Translates + rewrites content in professional Arabic (Groq / Llama 3.3 70B) |
| 🖼 Full Media Support | Photos, videos, GIFs, stickers, voice notes, documents |
| 🚫 Premium Filter | Automatically skips premium video stickers & custom emoji (free account safe) |
| 📤 Smart Auto-Post | Posts to your channel on a configurable schedule |
| ✅ Approval Mode | Optional: review every post before it goes live |
| 📋 Queue Management | Full control: approve, reject, edit, skip, preview posts |
| 📊 Daily Summary | Auto-generated Arabic digest posted at a set hour |
| 🗄 SQLite Database | All posts, logs, and settings persist across restarts |

---

## 🚀 Setup (5 steps)

### Step 1 — Get your credentials

**A. Bot Token** — Open [@BotFather](https://t.me/BotFather) → `/newbot` → copy token

**B. API ID & Hash** — Go to [my.telegram.org/apps](https://my.telegram.org/apps) → create app → copy both values

**C. Groq API Key (free)** — Sign up at [console.groq.com](https://console.groq.com) → API Keys → create key

**D. Your User ID** — Message [@userinfobot](https://t.me/userinfobot) on Telegram → copy your ID

### Step 2 — Install dependencies

```bash
# Python 3.10+ required
pip install -r requirements.txt
```

### Step 3 — Configure

```bash
cp .env.example .env
nano .env   # fill in all values
```

Key settings:
```env
BOT_TOKEN=           # from @BotFather
API_ID=              # from my.telegram.org
API_HASH=            # from my.telegram.org
GROQ_API_KEY=        # from console.groq.com
ADMIN_ID=            # your Telegram user ID
TARGET_CHANNELS=     # @yourchannel (where bot posts)
SOURCE_CHANNELS=     # @channel1,@channel2 (channels to monitor)
PHONE_NUMBER=        # +966... (your phone for Telethon session)
POST_DELAY_MINUTES=  # 30 (minutes between auto-posts)
REQUIRE_APPROVAL=    # false (set true to review before posting)
```

### Step 4 — Make your bot an admin

In your target channel → **Administrators** → add your bot → enable **Post Messages**

### Step 5 — Run

```bash
python main.py
```

On first run, Telethon will ask for your phone's verification code. After that a `user_session.session` file is saved — you won't need to log in again.

---

## 📱 Admin Commands

### Queue Management
| Command | Description |
|---|---|
| `/queue` | Show pending/approved posts with action buttons |
| `/preview <id>` | See the full text of any queued post |
| `/approve <id>` | Approve a pending post |
| `/reject <id>` | Reject a post |
| `/editpost <id> <text>` | Replace the Arabic text before publishing |
| `/clearqueue` | Delete all rejected posts (cleanup) |

### Posting
| Command | Description |
|---|---|
| `/postnow` | Force-post the next approved item immediately |
| `/postnow <id>` | Force-post a specific post by ID |
| `/skippost` | Move the next post to the back of the queue |
| `/addpost <text>` | Manually add content (AI translates it) |
| `/summary` | Generate + post today's daily digest |

### Settings
| Command | Description |
|---|---|
| `/status` | Full stats and current settings |
| `/pause` | Stop auto-posting |
| `/resume` | Resume auto-posting |
| `/setdelay <n>` | Change posting interval to N minutes (live, no restart) |
| `/approval on\|off` | Toggle require-approval mode (persists across restarts) |
| `/help` | Command reference |

---

## 🖼 Media Handling

| Media Type | Behavior |
|---|---|
| Photo | Downloaded + reposted with Arabic caption |
| Video | Downloaded + reposted with Arabic caption |
| GIF / Animation | Downloaded + reposted with Arabic caption |
| Regular sticker (static/animated) | Downloaded + reposted, text sent separately |
| Round video | Downloaded + reposted, text sent separately |
| Voice / Audio | Downloaded + reposted with Arabic caption |
| Document / File | Downloaded + reposted with Arabic caption |
| **Premium video sticker** (webm) | ⛔ Skipped automatically |
| **Custom emoji** (premium inline) | ⛔ Stripped from text before AI processing |
| File > MAX_MEDIA_MB | ⛔ Skipped (configurable, default 50 MB) |

---

## ⚙️ Run as a Background Service (Linux/VPS)

```bash
sudo nano /etc/systemd/system/giftbot.service
```

```ini
[Unit]
Description=Telegram Gift Bot
After=network.target

[Service]
Type=simple
User=your_username
WorkingDirectory=/path/to/gift_bot
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=15
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable giftbot
sudo systemctl start giftbot
sudo systemctl status giftbot
# View live logs:
sudo journalctl -u giftbot -f
```

---

## 🏗️ Project Structure

```
gift_bot/
├── main.py          # Entry point — wires everything together
├── config.py        # .env loading + typed constants
├── database.py      # All SQLite operations (aiosqlite)
├── ai.py            # Groq AI: translation, captions, daily summary
├── client.py        # Singleton Telethon user client
├── monitor.py       # Watches source channels for new content
├── poster.py        # Sends posts to target channels with media support
├── scheduler.py     # APScheduler: auto-post + daily summary jobs
├── bot.py           # All admin commands and inline button handlers
├── requirements.txt
├── .env.example     # Config template
└── README.md
```

---

## 🔄 How It Works

```
Source Channel (any language)
        ↓  Telethon monitors in real-time
        ↓
  Classify media type
  → skip premium stickers / oversized files
        ↓
  Download media locally (if eligible)
        ↓
  Groq AI: translate + rewrite in professional Arabic
        ↓
  Add to SQLite queue
  → if REQUIRE_APPROVAL=true: notify admin for review
        ↓
  APScheduler: every N minutes → post next approved item
  → send to all TARGET_CHANNELS (text + media)
  → delete local media file after successful post
        ↓
  8 PM: auto-generate + post daily Arabic summary
```

---

## 🛠️ Troubleshooting

| Problem | Solution |
|---|---|
| Bot won't start | Check all required fields in .env are filled |
| Monitor not picking up posts | Make sure SOURCE_CHANNELS are correct and your account is a member |
| Bot can't post to channel | Add the bot as admin with "Post Messages" permission |
| Telethon session error | Delete `user_session.session` and re-run to re-authenticate |
| AI not working | Check GROQ_API_KEY at [console.groq.com](https://console.groq.com) |
| Media not posting | Check that bot has enough disk space; increase MAX_MEDIA_MB in .env if needed |

---

*Built with Python · Telethon · python-telegram-bot · Groq (Llama 3.3 70B — free tier) · APScheduler · aiosqlite*
# TelegramGiftBot
