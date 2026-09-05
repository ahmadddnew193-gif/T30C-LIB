

but first follow these:

    """
    Simple async library for controlling an Ecovacs Deebot via MQTT.

    Parameters
    ----------
    account_id  : str  — Ecovacs app email
    password    : str  — Ecovacs app password (plaintext, hashed internally)
    country     : str  — ISO 3166-1 alpha-2 country code e.g. "IQ", "US", "DE"
    device_id   : str  — Stable 32-char hex ID (generate once, never change)
                         py -c "import hashlib,uuid; print(hashlib.md5(str(uuid.uuid4()).encode()).hexdigest())"
    device_name : str  — Nickname of your robot in the Ecovacs app (default "Sapna")
    """


Basic usage
```
"""python"""
import asyncio
from sapna import Deebot

async def main():
    bot = Deebot(
        account_id  = "your_email@example.com",
        password    = "your_password",
        country     = "IQ",
        device_id   = "your_stable_device_id",
        device_name = "Sapna",
    )

    await bot.connect()
    await bot.clean()
    await asyncio.sleep(10)
    await bot.pause()
    await asyncio.sleep(3)
    await bot.resume()
    await asyncio.sleep(5)
    await bot.dock()
    await bot.disconnect()

asyncio.run(main())
```
First time only — verify
```
"""python"""
async def first_time():
    bot = Deebot(...)
    await bot.verify()    # sends email, asks for code
    await bot.connect()   # works from now on forever
    await bot.clean()
    await bot.disconnect()
```
Context manager style
```
"""python"""
async def main():
    async with Deebot(...) as bot:
        await bot.clean()
        await asyncio.sleep(60)
        await bot.dock()
```
Check state + battery anytime
```
"""python"""
await bot.connect()
await asyncio.sleep(3)   # let events flush
print(bot.state)    # "DOCKED"
print(bot.battery)  # 87
```



**V6.0:**

NEW in v6:
  ✅ Hey Siri integration (Apple Shortcuts bridge)
  ✅ Ngrok tunnel (control Sapna from anywhere, not just home WiFi)
  ✅ Google Sheets live session logging
  ✅ Notion database logging
  ✅ Text-to-speech voice responses (offline + AI)
  ✅ Achievement system with Discord badges
  ✅ Gamification: XP + level system
  ✅ Carbon footprint tracker
  ✅ Pet hair seasonal mode
  ✅ Vacation mode
  ✅ Maintenance tracking (brush/filter/mop)
  ✅ Weekly heatmap
  ✅ Anomaly detector
  ✅ Coverage estimator
  ✅ Stuck pattern map
  ✅ Allergy mode (pollen API)
  ✅ iCal calendar (clean before events)
  ✅ Baby sleep guard
  ✅ Morning/bedtime routines
  ✅ Solar power mode
  ✅ Guest detection
  ✅ Weather-based scheduling
  ✅ Auto-resume after charge
  ✅ Silent windows
  ✅ Clean roulette
  ✅ Weekly/monthly auto-reports
  ✅ Alert if no clean in N days
  ✅ Humidity tracker
  ✅ Home Assistant entity sync
  ✅ Apple Shortcuts server

NEW in v6 — Voice/remote control that actually works everywhere:
  ✅ Telegram Bot (/clean /dock /pause — works in IQ + everywhere)
  ✅ WhatsApp Bot (send "clean" via Twilio WhatsApp)
  ✅ Google Assistant via IFTTT (Hey Google, clean the house)
  ✅ Alexa via webhook routine (Alexa, clean the house)
  ✅ NFC tag support (tap phone on sticker → Sapna cleans)
  ✅ Beautiful home screen widget server (one-tap web UI)
  ✅ Cloudflare Tunnel (free, works in Iraq + every country)


If u wanna silence the gibberish aio warning unclosed things do this function: silence_gibberish()
