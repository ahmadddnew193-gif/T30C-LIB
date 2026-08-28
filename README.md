

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
Ifu wanna silence the gibberish aio warning uncliosed things do this function: silence_gibberish()
