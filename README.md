Basic usage
python
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
First time only — verify
python
async def first_time():
    bot = Deebot(...)
    await bot.verify()    # sends email, asks for code
    await bot.connect()   # works from now on forever
    await bot.clean()
    await bot.disconnect()
Context manager style 😤
python
async def main():
    async with Deebot(...) as bot:
        await bot.clean()
        await asyncio.sleep(60)
        await bot.dock()
Check state + battery anytime
python
await bot.connect()
await asyncio.sleep(3)   # let events flush
print(bot.state)    # "DOCKED"
print(bot.battery)  # 87
