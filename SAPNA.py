"""
sapna.py — Ecovacs Deebot T30C Python Library  v4.0
=====================================================
Install core:
    pip install "git+https://github.com/bentbrain/client.py.git@agent/device-verification" aiohttp certifi

Optional extras:
    pip install pyttsx3                     # offline TTS voice
    pip install pygame                      # sound file playback
    pip install speechrecognition pyaudio   # voice control
    pip install spotipy                     # spotify
    pip install tweepy                      # twitter
    pip install weasyprint                  # PDF reports
    pip install qrcode[pil]                 # QR codes
    pip install icalendar                   # calendar integration
    pip install gspread oauth2client        # google sheets
    pip install notion-client               # notion logging
    pip install pyngrok                     # REMOVED — use Cloudflare Tunnel instead (free, works everywhere)

NEW in v4:
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
"""

import ssl
import csv
import json
import time
import socket
import hashlib
import asyncio
import logging
import warnings
import datetime
import threading
import subprocess
import os
import sys
from collections import defaultdict
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Callable, Optional, List, Dict, Tuple, Any

import aiohttp
import certifi

warnings.filterwarnings("ignore", category=ResourceWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)

from deebot_client.api_client import ApiClient
from deebot_client.authentication import Authenticator, create_rest_config
from deebot_client.commands.json.clean import CleanV2, CleanAction
from deebot_client.commands.json.charge import Charge
from deebot_client.events import StateEvent, ErrorEvent, BatteryEvent
from deebot_client.exceptions import DeviceVerificationRequiredError
from deebot_client.mqtt_client import MqttClient, create_mqtt_config
from deebot_client.device import Device

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

log = logging.getLogger("sapna")


# ─────────────────────────────────────────────────────────────────
# SILENCE GIBBERISH
# ─────────────────────────────────────────────────────────────────
def silence_gibberish() -> None:
    """Suppress all aiohttp/asyncio/deebot noise from terminal."""
    warnings.filterwarnings("ignore", category=ResourceWarning)
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    logging.getLogger("aiohttp").setLevel(logging.CRITICAL)
    logging.getLogger("asyncio").setLevel(logging.CRITICAL)
    logging.getLogger("deebot_client").setLevel(logging.CRITICAL)


# ─────────────────────────────────────────────────────────────────
# CLEAN SESSION
# ─────────────────────────────────────────────────────────────────
class CleanSession:
    """Represents a single cleaning session with full metrics."""

    def __init__(self) -> None:
        self.start_time    = datetime.datetime.now()
        self.end_time: Optional[datetime.datetime] = None
        self.battery_start: Optional[int] = None
        self.battery_end:   Optional[int] = None
        self.duration_min:  Optional[float] = None
        self.drain_rate:    Optional[float] = None
        self.end_reason:    Optional[str]   = None
        self.errors:        List[str]       = []

    def finish(self, reason: str, battery_end: int) -> None:
        self.end_time     = datetime.datetime.now()
        self.battery_end  = battery_end
        self.end_reason   = reason
        delta             = (self.end_time - self.start_time).total_seconds()
        self.duration_min = delta / 60.0
        if self.battery_start is not None and self.duration_min > 0:
            self.drain_rate = (self.battery_start - battery_end) / self.duration_min

    def to_dict(self) -> dict:
        return {
            "start":         self.start_time.isoformat(),
            "end":           self.end_time.isoformat() if self.end_time else None,
            "battery_start": self.battery_start,
            "battery_end":   self.battery_end,
            "duration_min":  round(self.duration_min, 2) if self.duration_min else None,
            "drain_pct_min": round(self.drain_rate, 3)   if self.drain_rate   else None,
            "end_reason":    self.end_reason,
            "errors":        self.errors,
        }


# ─────────────────────────────────────────────────────────────────
# REST API HANDLER
# ─────────────────────────────────────────────────────────────────
class _ApiHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler for the built-in REST server."""

    def log_message(self, *args: Any) -> None:
        pass  # silence HTTP server logs

    def _json(self, data: dict, code: int = 200) -> None:
        body = json.dumps(data, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        bot: "Deebot" = self.server.deebot  # type: ignore
        routes = {
            "/":        lambda: {"ok": True, "device": bot.device_name},
            "/status":  lambda: bot.get_status(),
            "/battery": lambda: {"battery": bot.battery},
            "/stats":   lambda: bot.cleaning_stats(),
            "/history": lambda: {"sessions": bot.cleaning_history()},
            "/predict": lambda: {"prediction": bot.predict_next_clean()},
        }
        fn = routes.get(self.path)
        if fn:
            self._json(fn())
        else:
            self._json({"error": "Not found"}, 404)

    def do_POST(self) -> None:
        bot: "Deebot" = self.server.deebot  # type: ignore
        loop: asyncio.AbstractEventLoop = self.server.loop  # type: ignore
        actions: Dict[str, Callable] = {
            "/clean":  bot.clean,
            "/pause":  bot.pause,
            "/resume": bot.resume,
            "/dock":   bot.dock,
        }
        fn = actions.get(self.path)
        if fn:
            future = asyncio.run_coroutine_threadsafe(fn(), loop)
            try:
                future.result(timeout=10)
                self._json({"ok": True, "action": self.path.strip("/")})
            except Exception as e:
                self._json({"error": str(e)}, 500)
        else:
            self._json({"error": "Not found"}, 404)


# ─────────────────────────────────────────────────────────────────
# MAIN CLASS
# ─────────────────────────────────────────────────────────────────
class Deebot:
    """
    Full-featured async Python library for Ecovacs Deebot T30C.

    Quick start
    -----------
    import asyncio
    from sapna import Deebot, silence_gibberish

    silence_gibberish()

    async def main():
        bot = Deebot("email", "password", "IQ", "device_id", log_file="sessions.csv")
        await bot.connect()
        await bot.clean()
        await asyncio.sleep(300)
        await bot.dock()
        await bot.disconnect()

    asyncio.run(main())
    """

    def __init__(
        self,
        account_id:  str,
        password:    str,
        country:     str,
        device_id:   str,
        device_name: str = "Sapna",
        log_file:    Optional[str] = None,
    ) -> None:
        self.account_id  = account_id
        self.password    = password
        self.country     = country
        self.device_id   = device_id
        self.device_name = device_name
        self.log_file    = log_file

        self._password_hash = hashlib.md5(password.encode()).hexdigest()
        self._session:       Optional[aiohttp.ClientSession] = None
        self._authenticator: Optional[Authenticator]         = None
        self._bot:           Optional[Device]                = None
        self._mqtt:          Optional[MqttClient]            = None

        # ── Public state (MQTT-driven) ────────────────────────────
        self.state:        Optional[str]             = None
        self.battery:      Optional[int]             = None
        self.error:        Optional[Tuple[int, str]] = None
        self.connected_at: Optional[datetime.datetime] = None

        # ── Internal tracking ─────────────────────────────────────
        self._current_session: Optional[CleanSession]    = None
        self._all_sessions:    List[CleanSession]        = []
        self._battery_history: List[Tuple[float, int]]   = []
        self._state_history:   List[Tuple[float, str]]   = []
        self._last_state_time: float                     = time.time()

        # ── Background tasks ──────────────────────────────────────
        self._watchdog_task:  Optional[asyncio.Task] = None  # type: ignore
        self._voice_task:     Optional[asyncio.Task] = None  # type: ignore
        self._dashboard_task: Optional[asyncio.Task] = None  # type: ignore
        self._schedule_tasks: List[asyncio.Task]     = []    # type: ignore
        self._http_server:    Optional[HTTPServer]   = None
        self._cf_process:     Optional[subprocess.Popen] = None  # type: ignore
        self._macros:         Dict[str, List[Callable]] = {}

        # ── Anti-theft ────────────────────────────────────────────
        self._anti_theft_armed:     bool = False
        self._anti_theft_user_cmds: int  = 0

        # ── Callbacks ─────────────────────────────────────────────
        self._on_state_cbs:      List[Callable]           = []
        self._on_low_bat_cbs:    List[Tuple[int,Callable]]= []
        self._on_error_cbs:      List[Callable]           = []
        self._on_clean_done_cbs: List[Callable]           = []

        # ── Notifications ─────────────────────────────────────────
        self._webhook_urls:  List[str]  = []
        self._discord_urls:  List[str]  = []
        self._telegram_cfgs: List[dict] = []
        self._twitter_cfg:   Optional[dict] = None
        self._spotify_cfg:   Optional[dict] = None

    # ══════════════════════════════════════════════════════════════
    # INTERNAL HELPERS
    # ══════════════════════════════════════════════════════════════

    def _ssl_session(self) -> aiohttp.ClientSession:
        ctx = ssl.create_default_context(cafile=certifi.where())
        return aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ctx))

    def _find_verification_error(self, exc: BaseException) -> bool:
        if isinstance(exc, DeviceVerificationRequiredError):
            return True
        if isinstance(exc, (ExceptionGroup, BaseExceptionGroup)):
            return any(self._find_verification_error(e) for e in exc.exceptions)
        if hasattr(exc, "__cause__") and exc.__cause__:
            return self._find_verification_error(exc.__cause__)
        return False

    def _require_connected(self) -> None:
        if self._bot is None:
            raise RuntimeError("Not connected — call await bot.connect() first.")

    async def _fire(self, cbs: list, *args: Any) -> None:
        for cb in cbs:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(*args)
                else:
                    cb(*args)
            except Exception as exc:
                log.warning(f"Callback error: {exc}")

    def _write_csv(self, session: CleanSession) -> None:
        if not self.log_file:
            return
        exists = os.path.exists(self.log_file)
        with open(self.log_file, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=session.to_dict().keys())
            if not exists:
                writer.writeheader()
            writer.writerow(session.to_dict())

    # ══════════════════════════════════════════════════════════════
    # EVENT HANDLERS
    # ══════════════════════════════════════════════════════════════

    async def _on_state(self, e: StateEvent) -> None:
        prev           = self.state
        self.state     = e.state.name
        self._last_state_time = time.time()
        self._state_history.append((time.time(), self.state))
        if len(self._state_history) > 500:
            self._state_history = self._state_history[-500:]

        icons = {
            "CLEANING":  "🧹",
            "DOCKED":    "🏠",
            "IDLE":      "💤",
            "PAUSED":    "⏸",
            "RETURNING": "↩️",
            "ERROR":     "⚠️",
        }
        print(f"{icons.get(self.state, '🤖')} {self.state}")

        # Session start
        if self.state == "CLEANING" and prev != "CLEANING":
            self._current_session = CleanSession()
            self._current_session.battery_start = self.battery
            await self._spotify_play()

        # Session end
        if prev == "CLEANING" and self.state in ("DOCKED", "IDLE", "PAUSED", "RETURNING"):
            await self._spotify_pause()
            if self._current_session:
                self._current_session.finish(self.state.lower(), self.battery or 0)
                self._all_sessions.append(self._current_session)
                self._write_csv(self._current_session)
                if self.state == "DOCKED":
                    await self._fire(self._on_clean_done_cbs, self._current_session)
                    sess = self._current_session
                    dur  = sess.duration_min or 0
                    bs   = sess.battery_start or 0
                    be   = sess.battery_end   or 0
                    msg  = (
                        f"✅ Sapna finished cleaning!\n"
                        f"Duration: {dur:.1f} min\n"
                        f"Battery: {bs}% → {be}%"
                    )
                    await self._notify(msg)
                    await self._tweet(f"✅ Sapna finished! Took {dur:.1f} min 🤖🧹")
                    await self._check_achievements()
                self._current_session = None

        # Anti-theft check
        if self._anti_theft_armed and prev == "DOCKED" and self.state not in ("DOCKED", "CLEANING"):
            if self._anti_theft_user_cmds == 0:
                msg = f"🚨 ANTI-THEFT: Sapna moved unexpectedly! State: {self.state}"
                print(msg)
                await self._notify(msg)
                await self._tweet(msg)
        if self.state == "CLEANING":
            self._anti_theft_user_cmds = max(0, self._anti_theft_user_cmds - 1)

        await self._fire(self._on_state_cbs, self.state, prev)

    async def _on_battery(self, e: BatteryEvent) -> None:
        self.battery = e.value
        self._battery_history.append((time.time(), e.value))
        if len(self._battery_history) > 300:
            self._battery_history = self._battery_history[-300:]
        print(f"{'🪫' if e.value <= 20 else '🔋'} Battery: {e.value}%")
        for threshold, cb in self._on_low_bat_cbs:
            if e.value <= threshold:
                await self._fire([cb], e.value)

    async def _on_error(self, e: ErrorEvent) -> None:
        if e.code != 0:
            self.error = (e.code, e.description)
            print(f"⚠️  Error [{e.code}]: {e.description}")
            if self._current_session:
                self._current_session.errors.append(f"[{e.code}] {e.description}")
            await self._fire(self._on_error_cbs, e.code, e.description)
            await self._notify(f"⚠️ Sapna error [{e.code}]: {e.description}")

    # ══════════════════════════════════════════════════════════════
    # NOTIFICATIONS
    # ══════════════════════════════════════════════════════════════

    async def _notify(self, msg: str) -> None:
        tasks = []
        for url in self._webhook_urls:
            tasks.append(self._send_webhook(url, msg))
        for url in self._discord_urls:
            tasks.append(self._send_discord(url, msg))
        for cfg in self._telegram_cfgs:
            tasks.append(self._send_telegram(cfg["token"], cfg["chat_id"], msg))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _send_webhook(self, url: str, msg: str) -> None:
        """Send webhook — handles both regular webhooks and Slack format."""
        try:
            if url.startswith("__slack__"):
                parts   = url.split("__")
                real    = parts[2]
                channel = parts[3]
                async with aiohttp.ClientSession() as s:
                    await s.post(real, json={
                        "channel":    channel,
                        "text":       msg,
                        "username":   "Sapna 🤖",
                        "icon_emoji": ":robot_face:",
                    }, timeout=aiohttp.ClientTimeout(total=5))
            else:
                async with aiohttp.ClientSession() as s:
                    await s.post(url, json={
                        "event":   "sapna",
                        "message": msg,
                        "state":   self.state,
                        "battery": self.battery,
                        "time":    datetime.datetime.now().isoformat(),
                    }, timeout=aiohttp.ClientTimeout(total=5))
        except Exception:
            pass

    async def _send_discord(self, url: str, msg: str) -> None:
        try:
            async with aiohttp.ClientSession() as s:
                await s.post(url, json={"content": msg},
                             timeout=aiohttp.ClientTimeout(total=5))
        except Exception:
            pass

    async def _send_telegram(self, token: str, chat_id: str, msg: str) -> None:
        try:
            async with aiohttp.ClientSession() as s:
                await s.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": msg},
                    timeout=aiohttp.ClientTimeout(total=5),
                )
        except Exception:
            pass

    async def _tweet(self, msg: str) -> None:
        if not self._twitter_cfg:
            return
        try:
            import tweepy  # type: ignore
            cfg = self._twitter_cfg
            client = tweepy.Client(
                consumer_key=cfg["consumer_key"],
                consumer_secret=cfg["consumer_secret"],
                access_token=cfg["access_token"],
                access_token_secret=cfg["access_token_secret"],
            )
            client.create_tweet(text=msg[:280])
        except ImportError:
            log.warning("tweepy not installed: pip install tweepy")
        except Exception as exc:
            log.warning(f"Twitter error: {exc}")

    # ══════════════════════════════════════════════════════════════
    # SPOTIFY
    # ══════════════════════════════════════════════════════════════

    async def _spotify_play(self) -> None:
        if not self._spotify_cfg:
            return
        try:
            import spotipy  # type: ignore
            from spotipy.oauth2 import SpotifyOAuth  # type: ignore
            sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
                client_id=self._spotify_cfg["client_id"],
                client_secret=self._spotify_cfg["client_secret"],
                redirect_uri="http://localhost:8888/callback",
                scope="user-modify-playback-state",
            ))
            uri = self._spotify_cfg.get("playlist_uri")
            if uri:
                sp.start_playback(context_uri=uri)
            else:
                sp.start_playback()
            print("🎵 Spotify: playing!")
        except ImportError:
            log.warning("spotipy not installed: pip install spotipy")
        except Exception as exc:
            log.warning(f"Spotify play error: {exc}")

    async def _spotify_pause(self) -> None:
        if not self._spotify_cfg:
            return
        try:
            import spotipy  # type: ignore
            from spotipy.oauth2 import SpotifyOAuth  # type: ignore
            sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
                client_id=self._spotify_cfg["client_id"],
                client_secret=self._spotify_cfg["client_secret"],
                redirect_uri="http://localhost:8888/callback",
                scope="user-modify-playback-state",
            ))
            sp.pause_playback()
            print("🎵 Spotify: paused.")
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════
    # VERIFICATION + CONNECTION
    # ══════════════════════════════════════════════════════════════

    async def verify(self) -> None:
        """One-time email verification. Only needed once per device_id."""
        print(f"\n⚠️  Sending verification code to {self.account_id}...")
        session = self._ssl_session()
        try:
            rc   = create_rest_config(session, device_id=self.device_id,
                                      alpha_2_country=self.country)
            auth = Authenticator(rc, self.account_id, self._password_hash)
            await auth.request_device_verification_code()
            code = input("📧 Enter the code from your email: ").strip()
            await auth.verify_device(code)
            print("✅ Verified! Won't need to do this again.\n")
        finally:
            await session.close()

    async def connect(self) -> None:
        """Authenticate and connect via MQTT. Call before any command."""
        self._session       = self._ssl_session()
        rc                  = create_rest_config(
            self._session, device_id=self.device_id,
            alpha_2_country=self.country,
        )
        self._authenticator = Authenticator(rc, self.account_id, self._password_hash)
        api                 = ApiClient(self._authenticator)

        try:
            devices = await api.get_devices()
        except Exception as exc:
            if self._find_verification_error(exc):
                raise RuntimeError(
                    "Run await bot.verify() first, then connect() again."
                ) from exc
            raise

        if not devices.mqtt:
            raise RuntimeError("No devices found — is Sapna powered on?")

        target = next(
            (d for d in devices.mqtt
             if (getattr(d, "nick", "") or getattr(d, "name", "")).lower()
             == self.device_name.lower()),
            devices.mqtt[0],
        )

        self._bot = Device(target, self._authenticator)
        self._bot.events.subscribe(StateEvent,   self._on_state)
        self._bot.events.subscribe(BatteryEvent, self._on_battery)
        self._bot.events.subscribe(ErrorEvent,   self._on_error)

        self._mqtt = MqttClient(
            create_mqtt_config(device_id=self.device_id, country=self.country),
            self._authenticator,
        )
        await self._bot.initialize(self._mqtt)
        await asyncio.sleep(3)
        self.connected_at = datetime.datetime.now()
        print(f"✅ Connected to {self.device_name}! 🚀")

    async def disconnect(self) -> None:
        """Cleanly close all connections and cancel background tasks."""
        for task in self._schedule_tasks:
            task.cancel()
        for task in [self._watchdog_task, self._voice_task, self._dashboard_task]:
            if task:
                task.cancel()
        if self._http_server:
            self._http_server.shutdown()
        self.stop_cloudflare_tunnel()
        if self._session and not self._session.closed:
            await self._session.close()
        print("👋 Disconnected.")

    # ══════════════════════════════════════════════════════════════
    # CORE COMMANDS
    # ══════════════════════════════════════════════════════════════

    async def clean(self) -> None:
        """Start full auto-clean."""
        self._require_connected()
        self._anti_theft_user_cmds += 1
        print("▶ Starting clean...")
        await self._bot.execute_command(CleanV2(CleanAction.START))  # type: ignore

    async def pause(self) -> None:
        """Pause current clean."""
        self._require_connected()
        print("⏸ Pausing...")
        await self._bot.execute_command(CleanV2(CleanAction.PAUSE))  # type: ignore

    async def resume(self) -> None:
        """Resume paused clean."""
        self._require_connected()
        print("▶ Resuming...")
        await self._bot.execute_command(CleanV2(CleanAction.RESUME))  # type: ignore

    async def dock(self) -> None:
        """Stop and return to dock to charge."""
        self._require_connected()
        self._anti_theft_user_cmds += 1
        print("🏠 Returning to dock...")
        await self._bot.execute_command(Charge())  # type: ignore

    # ══════════════════════════════════════════════════════════════
    # STATUS + ANALYTICS
    # ══════════════════════════════════════════════════════════════

    def get_status(self) -> dict:
        """Full status dict — perfect for logging or your own apps."""
        return {
            "state":      self.state,
            "battery":    self.battery,
            "error":      self.error,
            "uptime_min": self.uptime(),
            "connected":  self._bot is not None,
            "drain_rate": self.battery_drain_rate(),
            "eta_min":    self.estimated_time_remaining(),
        }

    def get_battery(self) -> Optional[int]:
        """Return current battery %."""
        return self.battery

    def uptime(self) -> Optional[float]:
        """Minutes since connect() was called."""
        if not self.connected_at:
            return None
        return (datetime.datetime.now() - self.connected_at).total_seconds() / 60.0

    def battery_drain_rate(self) -> Optional[float]:
        """Battery drain rate in % per minute from real readings."""
        hist = self._battery_history
        if len(hist) < 2:
            return None
        t0, b0 = hist[0]
        t1, b1 = hist[-1]
        elapsed = (t1 - t0) / 60.0
        if elapsed < 0.5:
            return None
        rate = (b0 - b1) / elapsed
        return round(rate, 3) if rate > 0 else None

    def estimated_time_remaining(self) -> Optional[float]:
        """Predict cleaning minutes remaining based on current drain rate."""
        rate = self.battery_drain_rate()
        if not rate or not self.battery or rate <= 0:
            return None
        return round(max(0, self.battery - 10) / rate, 1)

    def cleaning_history(self) -> List[dict]:
        """All recorded sessions as list of dicts."""
        return [s.to_dict() for s in self._all_sessions]

    def cleaning_stats(self) -> dict:
        """Aggregate stats across all sessions."""
        if not self._all_sessions:
            return {"total_sessions": 0}
        durations = [s.duration_min for s in self._all_sessions if s.duration_min]
        drains    = [s.drain_rate   for s in self._all_sessions if s.drain_rate]
        errors    = sum(len(s.errors) for s in self._all_sessions)
        return {
            "total_sessions":    len(self._all_sessions),
            "total_minutes":     round(sum(durations), 1) if durations else 0,
            "avg_duration_min":  round(sum(durations) / len(durations), 1) if durations else None,
            "avg_drain_pct_min": round(sum(drains) / len(drains), 3) if drains else None,
            "total_errors":      errors,
        }

    # ══════════════════════════════════════════════════════════════
    # SMART AUTOMATION
    # ══════════════════════════════════════════════════════════════

    async def clean_for(self, minutes: float) -> None:
        """Clean for exactly N minutes then dock."""
        self._require_connected()
        print(f"⏱ Cleaning for {minutes} min...")
        await self.clean()
        await asyncio.sleep(minutes * 60)
        print(f"⏱ Time's up — docking.")
        await self.dock()

    async def wait_until_docked(self, timeout: float = 300) -> bool:
        """Block until docked. Returns True if docked, False if timeout."""
        self._require_connected()
        print("⏳ Waiting for dock...")
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.state == "DOCKED":
                print("✅ Docked!")
                return True
            await asyncio.sleep(2)
        print("⚠️  Dock timeout.")
        return False

    async def wait_for_state(self, target: str, timeout: float = 300) -> bool:
        """Block until Sapna reaches target state. Returns True if reached."""
        self._require_connected()
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.state == target.upper():
                return True
            await asyncio.sleep(1)
        return False

    async def clean_if_battery_above(self, threshold: int = 50) -> bool:
        """Start cleaning only if battery >= threshold%. Returns True if started."""
        self._require_connected()
        if self.battery is None:
            await asyncio.sleep(3)
        if self.battery is not None and self.battery >= threshold:
            print(f"🔋 {self.battery}% ≥ {threshold}% — starting!")
            await self.clean()
            return True
        print(f"🪫 {self.battery}% < {threshold}% — skipping.")
        return False

    async def clean_until_battery(self, threshold: int = 20) -> None:
        """Clean and auto-dock when battery hits threshold%."""
        self._require_connected()
        print(f"🧹 Cleaning until battery hits {threshold}%...")
        await self.clean()
        while True:
            await asyncio.sleep(15)
            if self.battery is not None and self.battery <= threshold:
                print(f"🪫 Battery at {threshold}% — docking.")
                await self.dock()
                break
            if self.state == "DOCKED":
                break

    async def retry_on_error(self, max_retries: int = 3, delay: float = 30) -> None:
        """Start cleaning with automatic retry on errors/failures."""
        self._require_connected()
        for attempt in range(1, max_retries + 1):
            print(f"▶ Attempt {attempt}/{max_retries}...")
            await self.clean()
            await asyncio.sleep(delay)
            if self.state == "CLEANING":
                print(f"✅ Cleaning started on attempt {attempt}.")
                return
            elif self.state == "ERROR":
                print(f"⚠️  Error on attempt {attempt}. Retrying in {delay}s...")
                await asyncio.sleep(delay)
            else:
                print(f"State: {self.state}")
                return
        print(f"❌ Failed after {max_retries} attempts.")

    async def chain(self, *commands: Any) -> None:
        """
        Run multiple commands in sequence.
        Pass coroutines or (coroutine, delay_seconds) tuples.

        Example:
            await bot.chain(
                bot.clean(),
                (asyncio.sleep(1800), 0),
                bot.dock(),
            )
        """
        for cmd in commands:
            if isinstance(cmd, tuple) and len(cmd) == 2:
                coro, delay = cmd
                await coro
                await asyncio.sleep(delay)
            else:
                await cmd

    async def smart_battery_protect(self, safe_threshold: int = 15) -> None:
        """
        Background monitor: predicts depletion from drain rate,
        auto-docks before hitting safe_threshold%.

        Example:
            asyncio.ensure_future(bot.smart_battery_protect(20))
        """
        self._require_connected()
        print(f"🛡️  Smart battery protect active (safe floor: {safe_threshold}%)")
        while True:
            await asyncio.sleep(30)
            if self.state != "CLEANING":
                continue
            rate = self.battery_drain_rate()
            bat  = self.battery
            if not rate or not bat or rate <= 0:
                continue
            mins_left = (bat - safe_threshold) / rate
            if mins_left <= 5:
                print(f"🛡️  ~{mins_left:.1f}min to {safe_threshold}% — docking early!")
                await self._notify(
                    f"🛡️ Smart protect: docking early! "
                    f"~{mins_left:.1f}min to {safe_threshold}%"
                )
                await self.dock()
                break

    # ══════════════════════════════════════════════════════════════
    # MACROS
    # ══════════════════════════════════════════════════════════════

    def define_macro(self, name: str, *commands: Callable) -> None:
        """
        Define a named sequence of commands as a macro.

        Example:
            bot.define_macro("morning", bot.clean)
            bot.define_macro("full_cycle",
                bot.clean,
                lambda: asyncio.sleep(1800),
                bot.dock,
            )
        """
        self._macros[name] = list(commands)
        print(f"📋 Macro '{name}' defined ({len(commands)} steps)")

    async def run_macro(self, name: str) -> None:
        """
        Run a named macro.

        Example:
            await bot.run_macro("morning")
        """
        self._require_connected()
        if name not in self._macros:
            print(f"❌ Macro '{name}' not found. Defined: {list(self._macros.keys())}")
            return
        print(f"📋 Running macro: {name}")
        for step in self._macros[name]:
            result = step()
            if asyncio.iscoroutine(result):
                await result

    # ══════════════════════════════════════════════════════════════
    # AI SCHEDULE OPTIMIZER
    # ══════════════════════════════════════════════════════════════

    def predict_next_clean(self) -> str:
        """
        Predict the best time for the next clean based on history.
        Returns human-readable string.
        """
        if len(self._all_sessions) < 3:
            return "Need at least 3 sessions to predict."
        hours: Dict[int, int] = defaultdict(int)
        for s in self._all_sessions:
            hours[s.start_time.hour] += 1
        best_hour = max(hours, key=lambda h: hours[h])
        now  = datetime.datetime.now()
        nxt  = now.replace(hour=best_hour, minute=0, second=0, microsecond=0)
        if nxt <= now:
            nxt += datetime.timedelta(days=1)
        return f"Predicted next clean: {nxt.strftime('%A at %H:%M')} (from {len(self._all_sessions)} sessions)"

    def auto_schedule_optimizer(self) -> dict:
        """
        Analyze cleaning history and suggest an optimal schedule.
        Returns recommendation dict with reasoning.
        """
        if len(self._all_sessions) < 3:
            return {"recommendation": "Need 3+ sessions.", "sessions_analyzed": 0}

        hours:    Dict[int, int] = defaultdict(int)
        weekdays: Dict[int, int] = defaultdict(int)
        for s in self._all_sessions:
            hours[s.start_time.hour]        += 1
            weekdays[s.start_time.weekday()] += 1

        best_hour = max(hours,    key=lambda h: hours[h])
        best_day  = max(weekdays, key=lambda d: weekdays[d])
        day_names = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]

        durations = [s.duration_min for s in self._all_sessions if s.duration_min]
        drains    = [s.drain_rate   for s in self._all_sessions if s.drain_rate]
        avg_dur   = round(sum(durations)/len(durations), 1) if durations else None
        avg_drain = round(sum(drains)/len(drains), 3)       if drains    else None

        return {
            "sessions_analyzed":  len(self._all_sessions),
            "best_hour":          best_hour,
            "best_day":           day_names[best_day],
            "recommendation":     f"Schedule: {day_names[best_day]}s at {best_hour:02d}:00",
            "avg_duration_min":   avg_dur,
            "avg_drain_pct_min":  avg_drain,
            "reasoning": (
                f"Most cleans happen around {best_hour:02d}:00 on {day_names[best_day]}s. "
                f"Avg session: {avg_dur} min. "
                f"Expected battery use: "
                f"{round(avg_drain * avg_dur, 1) if avg_drain and avg_dur else '?'}%."
            ),
        }

    # ══════════════════════════════════════════════════════════════
    # PRESENCE DETECTION
    # ══════════════════════════════════════════════════════════════

    def _is_on_network(self, ip: str, timeout: float = 1.0) -> bool:
        """Check if a device is reachable on the local network."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect((ip, 80))
            s.close()
            return True
        except Exception:
            pass
        try:
            flag   = "-n" if sys.platform == "win32" else "-c"
            result = subprocess.run(
                ["ping", flag, "1", ip],
                capture_output=True, timeout=3,
            )
            return result.returncode == 0
        except Exception:
            return False

    async def clean_when_away(
        self,
        device_ip: str,
        check_interval: float = 60,
        away_confirmations: int = 3,
    ) -> None:
        """
        Monitor your phone's IP. Clean when you leave, dock when you return.
        Set a static IP for your phone in your router for best results.

        Example:
            asyncio.ensure_future(bot.clean_when_away("192.168.1.42"))
        """
        self._require_connected()
        print(f"👀 Presence monitor: watching {device_ip}...")
        away_count = 0
        while True:
            home = self._is_on_network(device_ip)
            if not home:
                away_count += 1
                print(f"📡 Device away ({away_count}/{away_confirmations})")
                if away_count >= away_confirmations and self.state not in ("CLEANING","RETURNING"):
                    print("🏃 You left — cleaning!")
                    await self.clean()
                    await self._notify("🧹 Sapna started — you left home!")
            else:
                if away_count >= away_confirmations:
                    print("🏠 You're back!")
                    if self.state == "CLEANING":
                        await self.dock()
                        await self._notify("🏠 You arrived home — Sapna docking.")
                away_count = 0
            await asyncio.sleep(check_interval)

    # ══════════════════════════════════════════════════════════════
    # ANTI-THEFT
    # ══════════════════════════════════════════════════════════════

    async def arm_anti_theft(self) -> None:
        """
        Arm anti-theft monitoring.
        Sends alert if Sapna moves without a command from this library.

        Example:
            await bot.arm_anti_theft()
        """
        self._require_connected()
        await self.wait_for_state("DOCKED", timeout=300)
        self._anti_theft_armed = True
        self._anti_theft_user_cmds = 0
        print("🔒 Anti-theft ARMED — Sapna is docked and monitored.")
        await self._notify("🔒 Sapna anti-theft armed.")

    def disarm_anti_theft(self) -> None:
        """Disarm anti-theft monitoring."""
        self._anti_theft_armed = False
        print("🔓 Anti-theft disarmed.")

    # ══════════════════════════════════════════════════════════════
    # SCHEDULING
    # ══════════════════════════════════════════════════════════════

    async def _schedule_loop(
        self, hour: int, minute: int, func: Callable, label: str
    ) -> None:
        print(f"📅 Scheduled '{label}' daily at {hour:02d}:{minute:02d}")
        while True:
            now = datetime.datetime.now()
            nxt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if nxt <= now:
                nxt += datetime.timedelta(days=1)
            await asyncio.sleep((nxt - now).total_seconds())
            print(f"⏰ Running scheduled task: {label}")
            try:
                if asyncio.iscoroutinefunction(func):
                    await func()
                else:
                    func()
            except Exception as exc:
                print(f"⚠️  Scheduled task error: {exc}")

    def schedule(
        self, hour: int, minute: int, func: Callable, label: str = "task"
    ) -> None:
        """
        Run a function every day at hour:minute. Call after connect().

        Example:
            bot.schedule(8, 30, bot.clean, label="morning clean")
            bot.schedule(22, 0, bot.dock,  label="night dock")
        """
        task = asyncio.ensure_future(self._schedule_loop(hour, minute, func, label))
        self._schedule_tasks.append(task)

    # ══════════════════════════════════════════════════════════════
    # WATCHDOG
    # ══════════════════════════════════════════════════════════════

    async def _watchdog_loop(self, timeout: float, action: Callable) -> None:
        print(f"🐕 Watchdog active — stuck threshold: {timeout}s")
        while True:
            await asyncio.sleep(30)
            if self.state == "CLEANING":
                stuck = time.time() - self._last_state_time
                if stuck > timeout:
                    print(f"🐕 Stuck {stuck:.0f}s — recovering...")
                    await self._notify(
                        f"🐕 Watchdog: Sapna stuck {stuck:.0f}s — auto-recovering"
                    )
                    try:
                        if asyncio.iscoroutinefunction(action):
                            await action()
                        else:
                            action()
                    except Exception as exc:
                        print(f"⚠️  Watchdog recovery failed: {exc}")

    def start_watchdog(
        self, stuck_timeout: float = 300, action: Optional[Callable] = None
    ) -> None:
        """
        Background watchdog — auto-recovers if Sapna gets stuck.

        Example:
            bot.start_watchdog(stuck_timeout=180, action=bot.dock)
        """
        self._watchdog_task = asyncio.ensure_future(
            self._watchdog_loop(stuck_timeout, action or self.dock)
        )

    def stop_watchdog(self) -> None:
        """Stop watchdog."""
        if self._watchdog_task:
            self._watchdog_task.cancel()
            self._watchdog_task = None
            print("🐕 Watchdog stopped.")

    # ══════════════════════════════════════════════════════════════
    # VOICE CONTROL
    # ══════════════════════════════════════════════════════════════

    async def _voice_loop(self, wake_word: str) -> None:
        try:
            import speech_recognition as sr  # type: ignore
        except ImportError:
            print("❌ pip install speechrecognition pyaudio")
            return

        recognizer = sr.Recognizer()
        mic        = sr.Microphone()
        commands: Dict[str, Callable] = {
            "clean": self.clean, "start": self.clean,
            "dock":  self.dock,  "home":  self.dock,
            "pause": self.pause, "resume":self.resume,
            "stop":  self.dock,
        }
        loop = asyncio.get_event_loop()
        print(f"🎤 Voice control active. Say '{wake_word} [clean/dock/pause/resume/stop]'")

        with mic as source:
            recognizer.adjust_for_ambient_noise(source, duration=1)

        while True:
            try:
                await asyncio.sleep(0.1)
                with mic as source:
                    audio = await loop.run_in_executor(
                        None,
                        lambda: recognizer.listen(source, timeout=5, phrase_time_limit=5),
                    )
                text = recognizer.recognize_google(audio).lower()
                print(f"🎤 Heard: '{text}'")
                if wake_word.lower() in text:
                    for kw, fn in commands.items():
                        if kw in text:
                            print(f"🎤 Command: {kw}")
                            await fn()
                            break
            except Exception:
                pass

    def start_voice_control(self, wake_word: str = "sapna") -> None:
        """
        Offline voice control. Say "[wake_word] clean / dock / pause / resume / stop"
        Requires: pip install speechrecognition pyaudio

        Example:
            bot.start_voice_control("sapna")
        """
        self._voice_task = asyncio.ensure_future(self._voice_loop(wake_word))
        print(f"🎤 Voice control started. Say '{wake_word} [command]'")

    def stop_voice_control(self) -> None:
        """Stop voice control."""
        if self._voice_task:
            self._voice_task.cancel()
            self._voice_task = None
            print("🎤 Voice control stopped.")

    # ══════════════════════════════════════════════════════════════
    # LIVE TERMINAL DASHBOARD
    # ══════════════════════════════════════════════════════════════

    async def _dashboard_loop(self, refresh: float) -> None:
        while True:
            await asyncio.sleep(refresh)
            os.system("cls" if sys.platform == "win32" else "clear")
            now  = datetime.datetime.now().strftime("%H:%M:%S")
            rate = self.battery_drain_rate()
            eta  = self.estimated_time_remaining()
            up   = self.uptime()
            icons = {
                "CLEANING":"🧹","DOCKED":"🏠","IDLE":"💤",
                "PAUSED":"⏸","RETURNING":"↩️","ERROR":"⚠️",
            }
            state_icon = icons.get(self.state or "", "🤖")
            bat_bar    = ""
            if self.battery is not None:
                filled  = int(self.battery / 5)
                bat_bar = "█" * filled + "░" * (20 - filled)

            print("╔══════════════════════════════════════════╗")
            print(f"║  🤖 Sapna Live Dashboard     {now}  ║")
            print("╠══════════════════════════════════════════╣")
            print(f"║  State   : {state_icon} {(self.state or '—'):<32}║")
            print(f"║  Battery : [{bat_bar}] {self.battery or '—'}%{'':<5}║")
            print(f"║  Drain   : {(str(rate)+'%/min') if rate else '—':<34}║")
            print(f"║  ETA     : {(str(eta)+' min') if eta else '—':<34}║")
            print(f"║  Uptime  : {(str(round(up,1))+' min') if up else '—':<34}║")
            print(f"║  Sessions: {len(self._all_sessions):<34}║")
            print("╠══════════════════════════════════════════╣")
            stats = self.cleaning_stats()
            print(f"║  Total clean time : {stats.get('total_minutes',0)} min{'':<15}║")
            print(f"║  Avg session      : {stats.get('avg_duration_min','—')} min{'':<14}║")
            print("╚══════════════════════════════════════════╝")

    def start_dashboard(self, refresh: float = 5.0) -> None:
        """
        Live terminal dashboard — updates every N seconds.
        Shows state, battery bar, drain rate, ETA, session stats.

        Example:
            bot.start_dashboard(refresh=3)
        """
        self._dashboard_task = asyncio.ensure_future(self._dashboard_loop(refresh))
        print(f"📊 Dashboard started (refresh: {refresh}s)")

    def stop_dashboard(self) -> None:
        """Stop live dashboard."""
        if self._dashboard_task:
            self._dashboard_task.cancel()
            self._dashboard_task = None

    # ══════════════════════════════════════════════════════════════
    # REST API SERVER
    # ══════════════════════════════════════════════════════════════

    def stop_http_server(self) -> None:
        """Stop the REST API server."""
        if self._http_server:
            self._http_server.shutdown()
            self._http_server = None
            print("🌐 REST API stopped.")

    # ══════════════════════════════════════════════════════════════
    # INTERACTIVE CLI
    # ══════════════════════════════════════════════════════════════

    async def cli(self) -> None:
        """
        Interactive terminal menu — no coding needed.

        Example:
            await bot.cli()
        """
        self._require_connected()
        loop = asyncio.get_event_loop()
        print("""
╔══════════════════════════════════════╗
║        Sapna Controller CLI          ║
╠══════════════════════════════════════╣
║  c  - clean          d  - dock       ║
║  p  - pause          r  - resume     ║
║  s  - status         b  - battery    ║
║  h  - history        x  - stats      ║
║  o  - optimizer      n  - predict    ║
║  e  - export HTML    q  - quit       ║
╚══════════════════════════════════════╝""")
        cmds: Dict[str, Any] = {
            "c": self.clean,
            "d": self.dock,
            "p": self.pause,
            "r": self.resume,
            "s": lambda: print(json.dumps(self.get_status(), indent=2, default=str)),
            "b": lambda: print(f"🔋 {self.battery}%"),
            "h": lambda: [print(json.dumps(s, indent=2)) for s in self.cleaning_history()],
            "x": lambda: print(json.dumps(self.cleaning_stats(), indent=2)),
            "o": lambda: print(json.dumps(self.auto_schedule_optimizer(), indent=2)),
            "n": lambda: print(self.predict_next_clean()),
            "e": lambda: self.export_html_report(),
        }
        while True:
            try:
                cmd = await loop.run_in_executor(
                    None, lambda: input("\n> ").strip().lower()
                )
            except (EOFError, KeyboardInterrupt):
                break
            if cmd == "q":
                print("👋 Bye!")
                break
            fn = cmds.get(cmd)
            if fn:
                result = fn()
                if asyncio.iscoroutine(result):
                    await result
            else:
                print(f"Unknown: '{cmd}'")

    # ══════════════════════════════════════════════════════════════
    # REPORT EXPORT
    # ══════════════════════════════════════════════════════════════

    def export_html_report(self, path: str = "sapna_report.html") -> str:
        """
        Export a full HTML cleaning report. Opens in any browser.

        Example:
            bot.export_html_report("report.html")
        """
        stats    = self.cleaning_stats()
        sessions = self.cleaning_history()
        drain    = self.battery_drain_rate()
        eta      = self.estimated_time_remaining()
        predict  = self.predict_next_clean()
        optimizer= self.auto_schedule_optimizer()
        ts       = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

        rows = ""
        for s in sessions:
            dur = f"{s['duration_min']:.1f}" if s["duration_min"] else "—"
            dr  = f"{s['drain_pct_min']:.3f}" if s["drain_pct_min"] else "—"
            bs  = f"{s['battery_start']}%" if s["battery_start"] is not None else "—"
            be  = f"{s['battery_end']}%"   if s["battery_end"]   is not None else "—"
            err = "✅" if not s["errors"] else ("⚠️ " + "; ".join(s["errors"]))
            rows += (
                f"<tr><td>{s['start'][:16].replace('T',' ')}</td>"
                f"<td>{dur}</td><td>{bs} → {be}</td>"
                f"<td>{dr}</td><td>{s['end_reason'] or '—'}</td>"
                f"<td>{err}</td></tr>"
            )

        html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sapna Report</title>
<style>
body{{font-family:'Segoe UI',sans-serif;background:#0a0c10;color:#dde1ec;margin:0;padding:24px}}
h1{{color:#f5a623;font-size:26px;margin-bottom:4px}}
.sub{{color:#6b7280;font-size:12px;margin-bottom:28px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:28px}}
.card{{background:#12151c;border:1px solid #1e2330;border-radius:10px;padding:18px}}
.cl{{font-size:9px;color:#6b7280;text-transform:uppercase;letter-spacing:.1em}}
.cv{{font-size:26px;font-weight:700;color:#f5a623;margin-top:4px;font-family:monospace}}
.cu{{font-size:10px;color:#6b7280}}
.sec{{background:#12151c;border:1px solid #1e2330;border-radius:10px;padding:18px;margin-bottom:16px}}
.sec h2{{font-size:11px;text-transform:uppercase;letter-spacing:.1em;color:#6b7280;margin-bottom:14px}}
table{{width:100%;border-collapse:collapse;font-size:11px}}
th{{text-align:left;padding:7px 10px;color:#6b7280;border-bottom:1px solid #1e2330;font-size:9px;text-transform:uppercase}}
td{{padding:7px 10px;border-bottom:1px solid #1e2330;font-family:monospace}}
tr:last-child td{{border-bottom:none}}
tr:hover td{{background:#161920}}
.box{{background:#0d1118;border:1px solid #1e2330;border-radius:6px;padding:14px;color:#4d9fff;font-size:12px}}
</style></head><body>
<h1>🤖 Sapna Cleaning Report</h1>
<div class="sub">Generated {ts} · {self.device_name}</div>
<div class="cards">
  <div class="card"><div class="cl">Total Sessions</div>
    <div class="cv">{stats.get("total_sessions",0)}</div></div>
  <div class="card"><div class="cl">Total Minutes</div>
    <div class="cv">{stats.get("total_minutes",0)}</div><div class="cu">min</div></div>
  <div class="card"><div class="cl">Avg Duration</div>
    <div class="cv">{stats.get("avg_duration_min","—")}</div><div class="cu">min</div></div>
  <div class="card"><div class="cl">Avg Drain</div>
    <div class="cv">{stats.get("avg_drain_pct_min","—")}</div><div class="cu">%/min</div></div>
  <div class="card"><div class="cl">Battery Now</div>
    <div class="cv">{self.battery or "—"}</div><div class="cu">%</div></div>
  <div class="card"><div class="cl">Live Drain</div>
    <div class="cv">{drain or "—"}</div><div class="cu">%/min</div></div>
  <div class="card"><div class="cl">ETA</div>
    <div class="cv">{eta or "—"}</div><div class="cu">min</div></div>
</div>
<div class="sec"><h2>🤖 AI Schedule Optimizer</h2>
  <div class="box">{optimizer.get("recommendation","—")}<br>
  <small style="color:#6b7280">{optimizer.get("reasoning","")}</small></div></div>
<div class="sec"><h2>🔮 Next Clean Prediction</h2>
  <div class="box">{predict}</div></div>
<div class="sec"><h2>📋 Session History</h2>
  <table><tr>
    <th>Started</th><th>Duration</th><th>Battery</th>
    <th>Drain</th><th>End Reason</th><th>Errors</th>
  </tr>{rows if rows else
  '<tr><td colspan="6" style="color:#6b7280;text-align:center;padding:20px">No sessions yet.</td></tr>'}
  </table></div>
</body></html>"""

        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"📊 HTML report → {path}")
        return path

    def export_pdf_report(self, path: str = "sapna_report.pdf") -> str:
        """
        Export PDF report. Requires: pip install weasyprint

        Example:
            bot.export_pdf_report("report.pdf")
        """
        try:
            from weasyprint import HTML  # type: ignore
            tmp = path.replace(".pdf", "_tmp.html")
            self.export_html_report(tmp)
            HTML(filename=tmp).write_pdf(path)
            os.remove(tmp)
            print(f"📄 PDF report → {path}")
            return path
        except ImportError:
            print("❌ pip install weasyprint")
            return ""
        except Exception as exc:
            print(f"❌ PDF failed: {exc}")
            return ""

    # ══════════════════════════════════════════════════════════════
    # NOTIFICATION SETUP
    # ══════════════════════════════════════════════════════════════

    def add_webhook(self, url: str) -> None:
        """POST JSON to url on events. Works with HA, IFTTT, n8n, Zapier."""
        self._webhook_urls.append(url)
        print(f"🔗 Webhook: {url}")

    def add_discord_webhook(self, url: str) -> None:
        """Discord alerts. Get URL: Server → Integrations → Webhooks."""
        self._discord_urls.append(url)
        print("💬 Discord webhook added.")

    def add_telegram_bot(self, token: str, chat_id: str) -> None:
        """
        Telegram alerts.
        1. @BotFather → /newbot → get token
        2. @userinfobot → get chat_id
        """
        self._telegram_cfgs.append({"token": token, "chat_id": chat_id})
        print(f"📱 Telegram bot added (chat_id={chat_id})")

    def add_twitter(
        self,
        consumer_key: str,
        consumer_secret: str,
        access_token: str,
        access_token_secret: str,
    ) -> None:
        """
        Tweet on clean done / anti-theft alerts.
        Requires: pip install tweepy
        Keys from developer.twitter.com
        """
        self._twitter_cfg = {
            "consumer_key":        consumer_key,
            "consumer_secret":     consumer_secret,
            "access_token":        access_token,
            "access_token_secret": access_token_secret,
        }
        print("🐦 Twitter/X configured.")

    def add_spotify(
        self,
        client_id: str,
        client_secret: str,
        playlist_uri: Optional[str] = None,
    ) -> None:
        """
        Play Spotify while cleaning, pause when done.
        Requires: pip install spotipy
        Keys from developer.spotify.com
        playlist_uri example: "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"
        """
        self._spotify_cfg = {
            "client_id":     client_id,
            "client_secret": client_secret,
            "playlist_uri":  playlist_uri,
        }
        print("🎵 Spotify configured.")

    async def test_notifications(self) -> None:
        """Send a test message to all configured notification channels."""
        await self._notify(
            f"🧪 Test from Sapna!\nState: {self.state}\nBattery: {self.battery}%"
        )
        print("📨 Test sent to all channels!")

    # ══════════════════════════════════════════════════════════════
    # CALLBACKS
    # ══════════════════════════════════════════════════════════════

    def on_state_change(self, cb: Callable) -> None:
        """Fire cb(new_state, old_state) on every state change."""
        self._on_state_cbs.append(cb)

    def on_clean_done(self, cb: Callable) -> None:
        """Fire cb(session: CleanSession) when Sapna docks after cleaning."""
        self._on_clean_done_cbs.append(cb)

    def on_low_battery(self, cb: Callable, threshold: int = 20) -> None:
        """Fire cb(battery_pct) when battery drops below threshold%."""
        self._on_low_bat_cbs.append((threshold, cb))

    def on_error(self, cb: Callable) -> None:
        """Fire cb(error_code, description) on any Sapna error."""
        self._on_error_cbs.append(cb)

    # ══════════════════════════════════════════════════════════════
    # WEATHER-BASED ADAPTIVE SCHEDULING
    # ══════════════════════════════════════════════════════════════

    async def adaptive_schedule(
        self,
        city: str,
        api_key: str,
        dusty_conditions: Optional[List[str]] = None,
    ) -> dict:
        """
        Check weather and decide if an extra clean is needed today.
        Uses OpenWeatherMap free API (openweathermap.org).

        Dusty conditions that trigger extra clean:
          "dust", "sand", "haze", "smoke", "fog", "mist"

        Example:
            await bot.adaptive_schedule("Baghdad", "your_owm_api_key")
        """
        if dusty_conditions is None:
            dusty_conditions = ["dust", "sand", "haze", "smoke", "fog", "mist", "squalls"]

        url = (
            f"https://api.openweathermap.org/data/2.5/weather"
            f"?q={city}&appid={api_key}&units=metric"
        )
        try:
            async with aiohttp.ClientSession() as s:
                resp = await s.get(url, timeout=aiohttp.ClientTimeout(total=8))
                data = await resp.json()
        except Exception as exc:
            return {"error": str(exc), "extra_clean": False}

        weather_main = data.get("weather", [{}])[0].get("main", "").lower()
        weather_desc = data.get("weather", [{}])[0].get("description", "").lower()
        temp         = data.get("main", {}).get("temp", 0)
        humidity     = data.get("main", {}).get("humidity", 0)
        wind_speed   = data.get("wind", {}).get("speed", 0)

        # High wind = more dust
        extra_clean = (
            any(cond in weather_main for cond in dusty_conditions)
            or any(cond in weather_desc for cond in dusty_conditions)
            or wind_speed > 10
        )

        result = {
            "city":        city,
            "weather":     weather_desc,
            "temp_c":      temp,
            "humidity":    humidity,
            "wind_kmh":    round(wind_speed * 3.6, 1),
            "extra_clean": extra_clean,
            "reason":      (
                f"High wind ({wind_speed:.1f}m/s) or dusty conditions"
                if extra_clean else "Weather is clean today"
            ),
        }

        if extra_clean:
            print(f"🌬️  Dusty/windy day ({weather_desc}) — triggering extra clean!")
            await self._notify(f"🌬️ Extra clean triggered!\nWeather: {weather_desc}\nWind: {wind_speed:.1f}m/s")
            await self.clean()
        else:
            print(f"☀️  Weather clean ({weather_desc}) — no extra clean needed.")

        return result

    async def sunrise_schedule(
        self,
        lat: float,
        lon: float,
        offset_minutes: int = 30,
        func: Optional[Callable] = None,
    ) -> None:
        """
        Schedule a clean at sunrise every day (± offset_minutes).
        Uses open-meteo free API — no key needed.

        Example:
            asyncio.ensure_future(
                bot.sunrise_schedule(33.34, 44.40, offset_minutes=30)
            )
        """
        if func is None:
            func = self.clean
        print(f"🌅 Sunrise scheduler active (±{offset_minutes}min from sunrise)")
        while True:
            try:
                url = (
                    f"https://api.open-meteo.com/v1/forecast"
                    f"?latitude={lat}&longitude={lon}"
                    f"&daily=sunrise&timezone=auto&forecast_days=1"
                )
                async with aiohttp.ClientSession() as s:
                    r    = await s.get(url, timeout=aiohttp.ClientTimeout(total=8))
                    data = await r.json()
                sunrise_str = data["daily"]["sunrise"][0]  # e.g. "2026-08-29T05:34"
                sunrise_dt  = datetime.datetime.fromisoformat(sunrise_str)
                fire_time   = sunrise_dt + datetime.timedelta(minutes=offset_minutes)
                now         = datetime.datetime.now()
                if fire_time <= now:
                    fire_time += datetime.timedelta(days=1)
                wait = (fire_time - now).total_seconds()
                print(f"🌅 Next sunrise clean: {fire_time.strftime('%Y-%m-%d %H:%M')}")
                await asyncio.sleep(wait)
                print(f"🌅 Sunrise! Running scheduled task.")
                if asyncio.iscoroutinefunction(func):
                    await func()
                else:
                    func()
            except Exception as exc:
                log.warning(f"Sunrise scheduler error: {exc}")
                await asyncio.sleep(3600)

    # ══════════════════════════════════════════════════════════════
    # GUEST DETECTION
    # ══════════════════════════════════════════════════════════════

    async def clean_after_guests(
        self,
        router_ip: str = "192.168.1.1",
        known_devices: Optional[List[str]] = None,
        guest_threshold: int = 2,
        clean_delay_min: float = 30,
    ) -> None:
        """
        Detect guests via new WiFi devices, clean after they leave.
        Pings your router's ARP table to count connected devices.

        known_devices: list of your own device IPs to ignore.
        guest_threshold: how many extra devices = "guests arrived".
        clean_delay_min: how long after guests leave to start cleaning.

        Example:
            asyncio.ensure_future(bot.clean_after_guests(
                router_ip="192.168.1.1",
                known_devices=["192.168.1.10", "192.168.1.11"],
                guest_threshold=2,
                clean_delay_min=30,
            ))
        """
        self._require_connected()
        if known_devices is None:
            known_devices = []

        print(f"👥 Guest detector active (threshold: +{guest_threshold} devices)")
        guests_present   = False
        guest_left_time: Optional[float] = None

        def get_arp_devices() -> List[str]:
            """Get IPs from ARP table (works on Windows/Linux/Mac)."""
            try:
                result = subprocess.run(
                    ["arp", "-a"],
                    capture_output=True, text=True, timeout=5,
                )
                lines = result.stdout.splitlines()
                ips   = []
                for line in lines:
                    parts = line.split()
                    for p in parts:
                        # Match IPv4
                        if p.count(".") == 3:
                            try:
                                socket.inet_aton(p)
                                ips.append(p)
                            except Exception:
                                pass
                return list(set(ips))
            except Exception:
                return []

        baseline_count = len(
            [ip for ip in get_arp_devices() if ip not in known_devices]
        )
        print(f"👥 Baseline: {baseline_count} unknown devices on network")

        while True:
            await asyncio.sleep(60)
            current = get_arp_devices()
            unknown = [ip for ip in current if ip not in known_devices]
            extra   = len(unknown) - baseline_count

            if extra >= guest_threshold and not guests_present:
                guests_present   = True
                guest_left_time  = None
                print(f"👥 Guests detected! ({extra} extra devices)")
                await self._notify(f"👥 Guests arrived — {extra} new devices on WiFi")

            elif extra < guest_threshold and guests_present:
                if guest_left_time is None:
                    guest_left_time = time.time()
                    print(f"👥 Guests left — waiting {clean_delay_min}min before cleaning...")
                elif time.time() - guest_left_time >= clean_delay_min * 60:
                    print("👥 Guests gone — cleaning up!")
                    await self._notify("👥 Guests left — Sapna is cleaning up!")
                    await self.clean()
                    guests_present  = False
                    guest_left_time = None

    # ══════════════════════════════════════════════════════════════
    # BATTERY HEALTH
    # ══════════════════════════════════════════════════════════════

    def battery_health_score(self) -> dict:
        """
        Estimate battery health from historical drain rates.
        A healthy T30C drains ~0.3-0.5%/min. Higher = degraded battery.

        Returns a score 0-100 and a recommendation.
        """
        if len(self._all_sessions) < 5:
            return {
                "score": None,
                "status": "Need 5+ sessions for analysis.",
                "sessions_analyzed": len(self._all_sessions),
            }

        drains = [s.drain_rate for s in self._all_sessions if s.drain_rate and s.drain_rate > 0]
        if not drains:
            return {"score": None, "status": "No drain data yet."}

        avg_drain = sum(drains) / len(drains)
        # T30C baseline: ~0.35%/min = 100 health
        # 0.7%/min = 50 health (degraded)
        # 1.0%/min = 0 health (replace soon)
        baseline = 0.35
        score = max(0, min(100, int(100 - ((avg_drain - baseline) / 0.65) * 100)))

        if score >= 80:
            status = "✅ Excellent — battery is healthy"
            recommendation = "No action needed."
        elif score >= 60:
            status = "🟡 Good — slight degradation"
            recommendation = "Monitor over the next month."
        elif score >= 40:
            status = "🟠 Fair — noticeable degradation"
            recommendation = "Consider replacing battery within 6 months."
        elif score >= 20:
            status = "🔴 Poor — significant degradation"
            recommendation = "Replace battery soon."
        else:
            status = "💀 Critical — battery near end of life"
            recommendation = "Replace battery immediately."

        # Estimate sessions until replacement (when score hits 0)
        drain_per_session = (avg_drain - baseline) / max(len(drains), 1) * 0.01
        sessions_left = int(score / 100 / max(drain_per_session, 0.001)) if drain_per_session > 0 else None

        return {
            "score":            score,
            "status":           status,
            "recommendation":   recommendation,
            "avg_drain_pct_min":round(avg_drain, 3),
            "sessions_analyzed":len(drains),
            "sessions_left":    sessions_left,
        }

    # ══════════════════════════════════════════════════════════════
    # STREAK TRACKER
    # ══════════════════════════════════════════════════════════════

    def streak_tracker(self) -> dict:
        """
        Track cleaning streaks like a habit app.
        Returns current streak, longest streak, and last clean date.

        Example:
            print(bot.streak_tracker())
            # {'current_streak': 7, 'longest_streak': 14, 'last_clean': '2026-08-29'}
        """
        if not self._all_sessions:
            return {"current_streak": 0, "longest_streak": 0, "last_clean": None}

        # Get unique clean dates
        clean_dates = sorted(set(
            s.start_time.date()
            for s in self._all_sessions
            if s.end_reason == "docked"
        ))

        if not clean_dates:
            return {"current_streak": 0, "longest_streak": 0, "last_clean": None}

        today         = datetime.date.today()
        current       = 0
        longest       = 0
        streak        = 0
        prev: Optional[datetime.date] = None

        for d in clean_dates:
            if prev is None or (d - prev).days == 1:
                streak += 1
            else:
                streak = 1
            longest = max(longest, streak)
            prev    = d

        # Current streak: count back from today
        current = 0
        for d in reversed(clean_dates):
            if (today - d).days == current:
                current += 1
            else:
                break

        emoji = "🔥" if current >= 7 else ("✨" if current >= 3 else "💪")
        return {
            "current_streak": current,
            "longest_streak": longest,
            "last_clean":     str(clean_dates[-1]),
            "emoji":          emoji,
            "message":        f"{emoji} {current}-day streak!" if current > 0 else "No active streak.",
            "total_clean_days": len(clean_dates),
        }

    # ══════════════════════════════════════════════════════════════
    # COST CALCULATOR
    # ══════════════════════════════════════════════════════════════

    def cost_calculator(
        self,
        electricity_rate_per_kwh: float = 0.12,
        wattage: float = 22.0,
    ) -> dict:
        """
        Calculate electricity cost per session and total.
        T30C uses ~22W during cleaning.

        electricity_rate_per_kwh: your rate in USD (default $0.12)
        wattage: robot wattage (T30C ≈ 22W)

        Example:
            print(bot.cost_calculator(electricity_rate_per_kwh=0.08))
        """
        if not self._all_sessions:
            return {"total_cost_usd": 0, "sessions": 0}

        durations = [s.duration_min for s in self._all_sessions if s.duration_min]
        if not durations:
            return {"total_cost_usd": 0, "sessions": 0}

        total_hours    = sum(durations) / 60.0
        total_kwh      = (wattage / 1000.0) * total_hours
        total_cost     = total_kwh * electricity_rate_per_kwh
        avg_cost       = total_cost / len(durations)
        cost_per_hour  = (wattage / 1000.0) * electricity_rate_per_kwh

        return {
            "sessions":               len(durations),
            "total_hours":            round(total_hours, 2),
            "total_kwh":              round(total_kwh, 4),
            "total_cost_usd":         round(total_cost, 4),
            "avg_cost_per_session":   round(avg_cost, 4),
            "cost_per_hour":          round(cost_per_hour, 4),
            "rate_per_kwh":           electricity_rate_per_kwh,
            "robot_wattage":          wattage,
            "annual_estimate_usd":    round(avg_cost * 365, 2),
        }

    # ══════════════════════════════════════════════════════════════
    # SESSION COMPARISON
    # ══════════════════════════════════════════════════════════════

    def compare_sessions(self, n: int = 5) -> List[dict]:
        """
        Compare last N sessions side by side with efficiency scores.
        Efficiency = area cleaned per % battery used (estimated).

        Example:
            for s in bot.compare_sessions(3):
                print(s)
        """
        sessions = self._all_sessions[-n:]
        results  = []
        for i, s in enumerate(sessions):
            dur   = s.duration_min or 0
            drain = s.drain_rate or 0
            bat_used = (s.battery_start or 0) - (s.battery_end or 0)
            # Efficiency: longer clean per battery % used = better
            efficiency = round(dur / bat_used, 2) if bat_used > 0 else None

            results.append({
                "session_number":   len(self._all_sessions) - len(sessions) + i + 1,
                "date":             s.start_time.strftime("%Y-%m-%d %H:%M"),
                "duration_min":     round(dur, 1),
                "battery_used_pct": bat_used,
                "drain_pct_min":    round(drain, 3) if drain else None,
                "efficiency_min_per_pct": efficiency,
                "end_reason":       s.end_reason,
                "errors":           len(s.errors),
            })
        return results

    def clean_efficiency_score(self) -> Optional[float]:
        """
        Score 0-100 measuring how efficiently Sapna uses battery.
        Based on: duration per battery % across all sessions.
        Higher = better (more cleaning per charge).
        """
        sessions = [s for s in self._all_sessions
                    if s.duration_min and s.battery_start and s.battery_end]
        if not sessions:
            return None
        efficiencies = []
        for s in sessions:
            bat_used = (s.battery_start or 0) - (s.battery_end or 0)
            if bat_used > 0:
                efficiencies.append(s.duration_min / bat_used)  # type: ignore
        if not efficiencies:
            return None
        avg = sum(efficiencies) / len(efficiencies)
        # T30C baseline: ~2 min/% = score 100
        score = min(100, int(avg / 2.0 * 100))
        return score

    # ══════════════════════════════════════════════════════════════
    # AI DIRTY FLOOR DETECTION
    # ══════════════════════════════════════════════════════════════

    async def detect_dirty_floor(
        self,
        image_path: str,
        api_key: str,
        auto_clean: bool = True,
        dirty_threshold: float = 0.6,
    ) -> dict:
        """
        Analyze a photo of your floor with AI (OpenAI Vision).
        If floor looks dirty, auto-starts cleaning.

        Requires: pip install requests
        Get API key: platform.openai.com

        image_path: path to a floor photo (.jpg/.png)
        dirty_threshold: 0.0-1.0, how dirty before auto-clean (default 0.6)
        auto_clean: automatically clean if dirty

        Example:
            result = await bot.detect_dirty_floor(
                "floor.jpg", "sk-...", auto_clean=True
            )
        """
        import base64
        import json as json_mod

        if not os.path.exists(image_path):
            return {"error": f"Image not found: {image_path}"}

        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("utf-8")

        ext = image_path.rsplit(".", 1)[-1].lower()
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"

        payload = {
            "model": "gpt-4o-mini",
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Analyze this floor image. Rate cleanliness 0.0-1.0 "
                            "(0=spotless, 1=very dirty). "
                            "Return ONLY valid JSON: "
                            "{\"dirty_score\": 0.0, \"observations\": \"...\", \"clean_recommended\": true}"
                        ),
                    },
                    {
                        "type":      "image_url",
                        "image_url": {"url": f"data:{mime};base64,{img_b64}"},
                    },
                ],
            }],
            "max_tokens": 150,
        }

        try:
            async with aiohttp.ClientSession() as s:
                resp = await s.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=20),
                )
                data = await resp.json()

            content = data["choices"][0]["message"]["content"].strip()
            # Strip markdown fences if present
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            result  = json_mod.loads(content.strip())

            dirty_score = float(result.get("dirty_score", 0))
            is_dirty    = dirty_score >= dirty_threshold
            result["dirty_threshold"] = dirty_threshold
            result["is_dirty"]        = is_dirty
            result["image"]           = image_path

            if is_dirty:
                print(f"🧹 Floor is dirty (score: {dirty_score:.2f}) — {result.get('observations','')}")
                if auto_clean:
                    print("🧹 Auto-cleaning!")
                    await self._notify(
                        f"🧹 Dirty floor detected (score: {dirty_score:.2f})\n"
                        f"{result.get('observations','')}\nSapna is cleaning!"
                    )
                    await self.clean()
            else:
                print(f"✅ Floor looks clean (score: {dirty_score:.2f})")

            return result

        except Exception as exc:
            return {"error": str(exc)}

    # ══════════════════════════════════════════════════════════════
    # AUTOMATION RULES ENGINE
    # ══════════════════════════════════════════════════════════════

    def add_rule(
        self,
        name: str,
        condition: Callable[[], bool],
        action: Callable,
        check_interval: float = 60,
        run_once: bool = False,
    ) -> None:
        """
        Add a conditional automation rule.
        Condition is checked every check_interval seconds.
        If True, action fires.

        Example:
            # Clean every time battery hits 100%
            bot.add_rule(
                "clean_when_full",
                condition=lambda: bot.battery == 100 and bot.state == "DOCKED",
                action=bot.clean,
                check_interval=30,
                run_once=False,
            )

            # Dock if error detected
            bot.add_rule(
                "dock_on_error",
                condition=lambda: bot.error is not None,
                action=bot.dock,
                check_interval=10,
                run_once=True,
            )
        """
        async def rule_loop():
            fired = False
            while True:
                await asyncio.sleep(check_interval)
                try:
                    if condition():
                        if run_once and fired:
                            continue
                        print(f"📋 Rule '{name}' triggered!")
                        if asyncio.iscoroutinefunction(action):
                            await action()
                        else:
                            action()
                        fired = True
                except Exception as exc:
                    log.warning(f"Rule '{name}' error: {exc}")

        task = asyncio.ensure_future(rule_loop())
        self._schedule_tasks.append(task)
        print(f"📋 Rule '{name}' registered (check every {check_interval}s)")

    # ══════════════════════════════════════════════════════════════
    # SLACK NOTIFICATIONS
    # ══════════════════════════════════════════════════════════════

    def add_slack(self, webhook_url: str, channel: str = "#home") -> None:
        """
        Send Slack messages on clean done / errors / low battery.
        Get webhook: api.slack.com/messaging/webhooks

        Example:
            bot.add_slack("https://hooks.slack.com/services/xxx/yyy/zzz")
        """
        # Slack uses same webhook format — add as a special webhook
        self._webhook_urls.append(f"__slack__{webhook_url}__{channel}")
        print(f"💬 Slack webhook added (channel: {channel})")

    # ══════════════════════════════════════════════════════════════
    # QR CODE GENERATOR
    # ══════════════════════════════════════════════════════════════

    def generate_qr_commands(
        self,
        server_ip: str,
        port: int = 8080,
        output_dir: str = ".",
    ) -> List[str]:
        """
        Generate QR code PNGs for quick commands.
        Scan with your phone to trigger clean/dock/pause/status.
        Requires: pip install qrcode[pil]

        Example:
            bot.start_http_server(8080)
            bot.generate_qr_commands("192.168.1.100", port=8080)
            # Creates clean_qr.png, dock_qr.png, etc.
        """
        try:
            import qrcode  # type: ignore
        except ImportError:
            print("❌ pip install 'qrcode[pil]'")
            return []

        commands = {
            "clean":  f"http://{server_ip}:{port}/clean",
            "dock":   f"http://{server_ip}:{port}/dock",
            "pause":  f"http://{server_ip}:{port}/pause",
            "status": f"http://{server_ip}:{port}/status",
        }
        paths = []
        for name, url in commands.items():
            img  = qrcode.make(url)
            path = os.path.join(output_dir, f"{name}_qr.png")
            img.save(path)
            paths.append(path)
            print(f"📱 QR code → {path}  ({url})")

        return paths

    # ══════════════════════════════════════════════════════════════
    # SESSION EXPORT
    # ══════════════════════════════════════════════════════════════

    def export_json(self, path: str = "sapna_sessions.json") -> str:
        """
        Export all session data as JSON.

        Example:
            bot.export_json("backup.json")
        """
        data = {
            "device":       self.device_name,
            "exported_at":  datetime.datetime.now().isoformat(),
            "stats":        self.cleaning_stats(),
            "health":       self.battery_health_score(),
            "streak":       self.streak_tracker(),
            "sessions":     self.cleaning_history(),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        print(f"📦 JSON export → {path}")
        return path

    def import_sessions(self, path: str) -> int:
        """
        Import sessions from a previously exported JSON file.
        Useful for restoring history after reinstall.

        Returns number of sessions imported.

        Example:
            bot.import_sessions("backup.json")
        """
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            raw_sessions = data.get("sessions", [])
            count = 0
            for sd in raw_sessions:
                s = CleanSession()
                s.start_time    = datetime.datetime.fromisoformat(sd["start"])
                s.end_time      = datetime.datetime.fromisoformat(sd["end"]) if sd.get("end") else None
                s.battery_start = sd.get("battery_start")
                s.battery_end   = sd.get("battery_end")
                s.duration_min  = sd.get("duration_min")
                s.drain_rate    = sd.get("drain_pct_min")
                s.end_reason    = sd.get("end_reason")
                s.errors        = sd.get("errors", [])
                self._all_sessions.append(s)
                count += 1
            print(f"📦 Imported {count} sessions from {path}")
            return count
        except Exception as exc:
            print(f"❌ Import failed: {exc}")
            return 0

    # ══════════════════════════════════════════════════════════════
    # MULTI-ROBOT SUPPORT
    # ══════════════════════════════════════════════════════════════

    @staticmethod
    def create_fleet(robots: List[dict]) -> List["Deebot"]:
        """
        Create multiple Deebot instances (one per robot).

        Example:
            fleet = Deebot.create_fleet([
                {"account_id":"a@b.com","password":"pw","country":"IQ",
                 "device_id":"id1","device_name":"Sapna"},
                {"account_id":"a@b.com","password":"pw","country":"IQ",
                 "device_id":"id2","device_name":"Robot2"},
            ])
            for bot in fleet:
                await bot.connect()
                await bot.clean()
        """
        return [Deebot(**cfg) for cfg in robots]

    async def fleet_clean(self, robots: List["Deebot"]) -> None:
        """
        Start cleaning on all robots in the fleet simultaneously.

        Example:
            await bot.fleet_clean([bot1, bot2, bot3])
        """
        print(f"🤖 Fleet clean: starting {len(robots)} robots...")
        await asyncio.gather(*[r.clean() for r in robots], return_exceptions=True)
        print("🤖 Fleet clean dispatched!")

    async def fleet_dock(self, robots: List["Deebot"]) -> None:
        """Dock all robots in fleet simultaneously."""
        print(f"🤖 Fleet dock: sending {len(robots)} robots home...")
        await asyncio.gather(*[r.dock() for r in robots], return_exceptions=True)
        print("🤖 Fleet docked!")

    # ══════════════════════════════════════════════════════════════
    # AUTO-PAUSE ON PHONE CALL (NETWORK ACTIVITY SPIKE)
    # ══════════════════════════════════════════════════════════════

    async def auto_pause_on_call(
        self,
        call_detection_url: str,
        check_interval: float = 5,
    ) -> None:
        """
        Auto-pause Sapna when you're on a phone call.
        Works by polling a URL that returns {"on_call": true/false}.
        Pair with a Tasker/Shortcuts automation that sets this flag.

        Example setup:
            # On your phone (Tasker/Shortcuts): POST {"on_call": true}
            # to a tiny Flask server on your PC when call starts.

            asyncio.ensure_future(
                bot.auto_pause_on_call("http://192.168.1.100:9090/call_status")
            )
        """
        self._require_connected()
        print(f"📞 Call monitor active → {call_detection_url}")
        was_paused = False
        while True:
            await asyncio.sleep(check_interval)
            try:
                async with aiohttp.ClientSession() as s:
                    resp = await s.get(
                        call_detection_url,
                        timeout=aiohttp.ClientTimeout(total=3),
                    )
                    data = await resp.json()
                on_call = data.get("on_call", False)

                if on_call and self.state == "CLEANING":
                    print("📞 Call detected — pausing Sapna!")
                    await self.pause()
                    was_paused = True
                elif not on_call and was_paused and self.state == "PAUSED":
                    print("📞 Call ended — resuming Sapna!")
                    await self.resume()
                    was_paused = False
            except Exception:
                pass

    # ══════════════════════════════════════════════════════════════
    # HEY SIRI INTEGRATION + NGROK TUNNEL
    # ══════════════════════════════════════════════════════════════

    def start_siri_server(
        self,
        port: int = 8080,
        cloudflare_tunnel: bool = False,
        cf_tunnel_name: Optional[str] = None,
    ) -> dict:
        """
        Start an Apple Shortcuts / Hey Siri compatible HTTP server.
        Optionally expose it anywhere via Cloudflare Tunnel (free, works in ALL countries).

        Cloudflare Tunnel vs ngrok:
          ✅ Works in Iraq, Iran, China — everywhere ngrok doesn't
          ✅ Free forever, no account needed for quick tunnels
          ✅ Faster (Cloudflare's global network)
          ✅ HTTPS automatically
          ✅ No bandwidth limits

        ── QUICK TUNNEL (no account needed) ─────────────────────
        cloudflare_tunnel=True → runs: cloudflared tunnel --url http://localhost:PORT
        Gets you a random https://xxxx.trycloudflare.com URL instantly.

        ── NAMED TUNNEL (permanent URL, needs free CF account) ──
        cf_tunnel_name="sapna" → always gets https://sapna.yourdomain.com
        Setup: cloudflare.com → Zero Trust → Tunnels → Create tunnel

        ── CLOUDFLARED INSTALL ───────────────────────────────────
        Windows: winget install Cloudflare.cloudflared
                 OR: https://github.com/cloudflare/cloudflared/releases
                     download cloudflared-windows-amd64.exe → rename to cloudflared.exe
                     put it in C:\\Windows\\System32\\
        Linux:   curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o cloudflared && chmod +x cloudflared && sudo mv cloudflared /usr/local/bin
        Mac:     brew install cloudflared

        ── SIRI ENDPOINTS ───────────────────────────────────────
        POST /siri/clean   → "Hey Siri, clean the house"
        POST /siri/dock    → "Hey Siri, dock Sapna"
        POST /siri/pause   → "Hey Siri, pause cleaning"
        POST /siri/resume  → "Hey Siri, resume cleaning"
        GET  /siri/status  → "Hey Siri, how is Sapna doing"

        Example (quick tunnel, no account):
            info = bot.start_siri_server(port=8080, cloudflare_tunnel=True)
            print(info["public_url"])   # paste this in your iPhone Shortcut

        Example (named tunnel, permanent URL):
            info = bot.start_siri_server(port=8080, cloudflare_tunnel=True, cf_tunnel_name="sapna")
        """
        self._require_connected()
        self.start_http_server(port)

        local_ip   = self._get_local_ip()
        local_url  = f"http://{local_ip}:{port}"
        public_url: Optional[str] = None

        if cloudflare_tunnel:
            public_url = self._start_cloudflare_tunnel(port, cf_tunnel_name)

        url = public_url or local_url

        shortcuts = [
            {"phrase": "Clean the house",   "url": f"{url}/siri/clean",  "method": "POST"},
            {"phrase": "Dock Sapna",         "url": f"{url}/siri/dock",   "method": "POST"},
            {"phrase": "Pause cleaning",     "url": f"{url}/siri/pause",  "method": "POST"},
            {"phrase": "Resume cleaning",    "url": f"{url}/siri/resume", "method": "POST"},
            {"phrase": "How is Sapna doing", "url": f"{url}/siri/status", "method": "GET"},
        ]

        print("\n📱 HEY SIRI SETUP GUIDE (iOS 16+)")
        print("══════════════════════════════════════════════")
        if public_url:
            print(f"🌍 Public URL: {public_url}")
            print(f"   ✅ Works from ANYWHERE — not just home WiFi!")
        else:
            print(f"📡 Local URL: {local_url}")
            print(f"   ⚠️  Only works on home WiFi")
            print(f"   💡 Pass cloudflare_tunnel=True for global access")
        print()
        print("── Step 1: Create the Shortcut ───────────────")
        print("  1. Open Shortcuts app on iPhone")
        print("  2. Tap '+' (top right) to create new shortcut")
        print("  3. Tap 'Add Action'")
        print("  4. Search for 'URL' → tap 'URL' action")
        print(f"  5. Paste your URL, e.g: {url}/siri/clean")
        print("  6. Tap '+' again → search 'Get Contents of URL'")
        print("  7. Tap that action → change Method to POST")
        print()
        print("── Step 2: Add to Siri ───────────────────────")
        print("  8. Tap the shortcut name at the top to rename it")
        print("     e.g. 'Clean the house'")
        print("  9. Tap '...' (three dots, top right of shortcut)")
        print(" 10. Tap 'Add to Siri' button in that menu")
        print(" 11. Tap the red record button → say your phrase")
        print("     e.g. 'Clean the house'")
        print(" 12. Tap Done")
        print()
        print("── Available commands ─────────────────────────")
        for s in shortcuts:
            print(f"  {s['method']:4s} {s['url']}")
            print(f"       Siri phrase: \"{s['phrase']}\"")
        print()
        print("── Test it ────────────────────────────────────")
        print("  Say: 'Hey Siri, Clean the house'")
        print("  Sapna should start cleaning! 🎉")
        print("══════════════════════════════════════════════\n")

        return {
            "local_url":  local_url,
            "public_url": public_url,
            "port":       port,
            "tunnel":     "cloudflare" if public_url else "none",
            "shortcuts":  shortcuts,
        }

    def _get_local_ip(self) -> str:
        """Get the local network IP of this machine."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "localhost"

    def _start_cloudflare_tunnel(
        self,
        port: int,
        tunnel_name: Optional[str] = None,
    ) -> Optional[str]:
        """
        Start cloudflared quick tunnel (no account needed).
        Uses two strategies to get the URL:
          1. Direct API call to api.trycloudflare.com (fast, reliable)
          2. Fallback: parse stderr output line by line
        """
        import re

        # ── Check cloudflared is installed ────────────────────────
        cloudflared_path = "cloudflared"
        try:
            r = subprocess.run(
                [cloudflared_path, "--version"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode != 0:
                raise FileNotFoundError
        except (FileNotFoundError, subprocess.TimeoutExpired):
            # Try common Windows paths
            for candidate in [
                r"C:\Windows\System32\cloudflared.exe",
                r"C:\Program Files\Cloudflare\cloudflared.exe",
                os.path.join(os.path.expanduser("~"), "cloudflared.exe"),
            ]:
                if os.path.exists(candidate):
                    cloudflared_path = candidate
                    break
            else:
                print("\n❌ cloudflared not found!")
                print("   Install it with ONE of these:")
                print()
                print("   Windows (easiest):")
                print("     winget install Cloudflare.cloudflared")
                print()
                print("   OR download manually:")
                print("     https://github.com/cloudflare/cloudflared/releases/latest")
                print("     → cloudflared-windows-amd64.exe")
                print("     → rename to cloudflared.exe")
                print("     → move to C:\\Windows\\System32\\")
                print()
                print("   Then restart your terminal and try again.")
                return None

        # ── Start cloudflared process ─────────────────────────────
        cmd = [cloudflared_path, "tunnel", "--url", f"http://localhost:{port}"]
        print(f"🌍 Starting Cloudflare Tunnel on port {port}...")
        print(f"   (No account needed — free trycloudflare.com URL)")

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except Exception as exc:
            print(f"❌ Failed to start cloudflared: {exc}")
            return None

        # Store for cleanup
        self._cf_process = proc  # type: ignore

        # ── Parse URL from stderr ─────────────────────────────────
        # cloudflared prints the URL in stderr in multiple formats:
        # Format 1 (box):  | https://xxx.trycloudflare.com      |
        # Format 2 (INF):  INF ... https://xxx.trycloudflare.com
        # Format 3 (plain): https://xxx.trycloudflare.com
        url_pattern = re.compile(r"https://[a-zA-Z0-9\-]+\.trycloudflare\.com")
        public_url:  Optional[str] = None
        deadline     = time.time() + 40  # wait up to 40 seconds

        print("   Waiting for tunnel URL", end="", flush=True)
        while time.time() < deadline:
            if proc.stderr is None:
                break
            if proc.poll() is not None:
                # Process died
                print("\n❌ cloudflared exited unexpectedly.")
                remaining = proc.stderr.read()
                if remaining:
                    print(f"   Output: {remaining[:500]}")
                return None
            try:
                line = proc.stderr.readline()
                if not line:
                    time.sleep(0.3)
                    print(".", end="", flush=True)
                    continue
                match = url_pattern.search(line)
                if match:
                    public_url = match.group(0)
                    break
                print(".", end="", flush=True)
            except Exception:
                time.sleep(0.3)

        print()  # newline after dots

        if public_url:
            print(f"✅ Cloudflare Tunnel active!")
            print(f"   URL: {public_url}")
            print(f"   Works from ANYWHERE — paste this in your iPhone Shortcut 📱")
        else:
            # Last resort: try reading all remaining stderr
            try:
                proc.stderr.readline()  # type: ignore
                out = ""
                for _ in range(50):
                    line = proc.stderr.readline()  # type: ignore
                    if not line:
                        break
                    out += line
                match = url_pattern.search(out)
                if match:
                    public_url = match.group(0)
                    print(f"✅ Cloudflare Tunnel: {public_url}")
            except Exception:
                pass

        if not public_url:
            print("⚠️  Could not auto-detect tunnel URL.")
            print("   Check your terminal output for the https://xxxx.trycloudflare.com URL")
            print("   and paste it into your iPhone Shortcut manually.")

        return public_url

    def cloudflare_account_tunnel(
        self,
        tunnel_token: str,
        port: int = 8080,
    ) -> Optional[str]:
        """
        Start a permanent Cloudflare Tunnel using your free CF account.
        Unlike the quick tunnel (random URL every time), this gives you
        a PERMANENT URL that never changes — same URL every single run.

        ── HOW TO GET YOUR TOKEN (5 min, free forever) ──────────
        1. Create free account: https://dash.cloudflare.com/sign-up

        2. Go to Zero Trust:
           dashboard.cloudflare.com → left sidebar → "Zero Trust"

        3. Create tunnel:
           Networks → Tunnels → "Create a tunnel"
           → Choose "Cloudflared" → name it "sapna" → Next

        4. Copy the token:
           You'll see a command like:
           cloudflared service install eyJhIjoiMTIz...
           Copy ONLY the long token part (starts with eyJ...)

        5. Configure public hostname (to get your URL):
           → "Public Hostname" tab
           → Subdomain: sapna (or anything you want)
           → Domain: your domain OR workers.dev subdomain
           → Service: HTTP → localhost:YOUR_PORT
           → Save

        Your permanent URL will be: https://sapna.yourdomain.com
        OR if using workers.dev: https://sapna.YOURZONE.workers.dev

        ── INSTALL cloudflared FIRST ────────────────────────────
        Windows: winget install Cloudflare.cloudflared
        Mac:     brew install cloudflared
        Linux:   sudo apt install cloudflared

        ── USAGE ────────────────────────────────────────────────
        Example:
            # Start server first
            bot.start_http_server(8080)

            # Then start permanent tunnel
            url = bot.cloudflare_account_tunnel(
                tunnel_token="eyJhIjoiMTIzNDU2...",
                port=8080,
            )
            print(f"Permanent URL: {url}")
            # → https://sapna.yourdomain.com (never changes!)
        """
        import re

        # ── Check cloudflared installed ───────────────────────────
        cloudflared_path = "cloudflared"
        try:
            r = subprocess.run(
                [cloudflared_path, "--version"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode != 0:
                raise FileNotFoundError
        except (FileNotFoundError, subprocess.TimeoutExpired):
            for candidate in [
                r"C:\Windows\System32\cloudflared.exe",
                r"C:\Program Files\Cloudflare\cloudflared.exe",
                os.path.join(os.path.expanduser("~"), "cloudflared.exe"),
            ]:
                if os.path.exists(candidate):
                    cloudflared_path = candidate
                    break
            else:
                print("\n❌ cloudflared not found!")
                print("   Windows: winget install Cloudflare.cloudflared")
                print("   Mac:     brew install cloudflared")
                print("   Linux:   sudo apt install cloudflared")
                return None

        # ── Start tunnel with token ───────────────────────────────
        cmd = [cloudflared_path, "tunnel", "--no-autoupdate", "run",
               "--token", tunnel_token]

        print("🌍 Starting permanent Cloudflare account tunnel...")
        print("   (This URL will be the same every time you run)")

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except Exception as exc:
            print(f"❌ Failed to start cloudflared: {exc}")
            return None

        self._cf_process = proc

        # ── Wait for connection confirmation ──────────────────────
        # Account tunnels don't print the URL (it's set in the dashboard)
        # but they print a "connection" confirmation
        connected_pattern = re.compile(
            r"(connection|registered|connected|tunnelID|Registered tunnel)",
            re.IGNORECASE
        )
        url_pattern = re.compile(r"https://[^\s]+")

        public_url:  Optional[str] = None
        deadline     = time.time() + 30
        print("   Connecting", end="", flush=True)

        while time.time() < deadline:
            if proc.poll() is not None:
                print("\n❌ cloudflared exited! Check your token is correct.")
                err = proc.stderr.read() if proc.stderr else ""
                if err:
                    print(f"   Error: {err[:300]}")
                return None
            try:
                line = proc.stderr.readline() if proc.stderr else ""
                if not line:
                    time.sleep(0.3)
                    print(".", end="", flush=True)
                    continue

                # Try to extract URL from output
                url_match = url_pattern.search(line)
                if url_match and "trycloudflare" not in url_match.group(0):
                    candidate = url_match.group(0).rstrip(".")
                    if candidate.startswith("https://"):
                        public_url = candidate

                if connected_pattern.search(line):
                    print("\n✅ Permanent Cloudflare Tunnel connected!")
                    break

                print(".", end="", flush=True)
            except Exception:
                time.sleep(0.3)

        print()

        if public_url:
            print(f"   URL: {public_url}")
        else:
            print("   ✅ Tunnel is running!")
            print("   📋 Your permanent URL is set in the Cloudflare dashboard:")
            print("      dash.cloudflare.com → Zero Trust → Tunnels → sapna → Public Hostnames")
            print("      It looks like: https://sapna.yourdomain.com")

        print()
        print("💡 This URL NEVER changes — save it in your IFTTT / Telegram setup!")

        return public_url
        """Stop the Cloudflare Tunnel process."""
        cf = getattr(self, "_cf_process", None)
        if cf:
            try:
                cf.terminate()
                cf.wait(timeout=3)
            except Exception:
                pass
            self._cf_process = None  # type: ignore
            print("🌍 Cloudflare Tunnel stopped.")

    def tailscale_setup(self, port: int = 8080) -> dict:
        """
        Auto-detect your Tailscale IP and print your permanent URL.
        Tailscale is FREE forever, no credit card, works in Iraq + everywhere.

        ── SETUP (3 min, free, no card) ─────────────────────────
        1. Download Tailscale: https://tailscale.com/download
           → Install on your PC
           → Sign in with Google (no card needed ever)

        2. Install Tailscale on your iPhone/iPad:
           → App Store → search "Tailscale"
           → Sign in with SAME Google account

        3. Run this method — auto-detects your Tailscale IP
           and prints your permanent URL

        4. Use that URL in IFTTT — it NEVER changes!

        ── WHY TAILSCALE BEATS CLOUDFLARE TUNNEL ─────────────────
        ✅ Free forever — no card, no account limits
        ✅ Permanent IP — same URL every single run
        ✅ Works in Iraq + every country
        ✅ Nothing extra to install (no cloudflared)
        ✅ Phone reaches PC from ANYWHERE in the world
        ✅ Fully encrypted — more secure than any tunnel
        ✅ Works even if router blocks ports

        Example:
            await bot.connect()
            bot.start_http_server(8080)
            info = bot.tailscale_setup(port=8080)
            print(info["commands"]["clean"])
            # → http://100.64.x.x:8080/clean  (permanent, never changes)
        """
        import re

        tailscale_ip:   Optional[str] = None
        tailscale_name: Optional[str] = None

        # Strategy 1: tailscale ip command
        try:
            r = subprocess.run(
                ["tailscale", "ip", "-4"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                tailscale_ip = r.stdout.strip()
        except Exception:
            pass

        # Strategy 2: tailscale status --json
        if not tailscale_ip:
            try:
                r = subprocess.run(
                    ["tailscale", "status", "--json"],
                    capture_output=True, text=True, timeout=5,
                )
                if r.returncode == 0:
                    data      = json.loads(r.stdout)
                    self_node = data.get("Self", {})
                    ips       = self_node.get("TailscaleIPs", [])
                    if ips:
                        tailscale_ip   = ips[0]
                        tailscale_name = self_node.get("DNSName", "").rstrip(".")
            except Exception:
                pass

        # Strategy 3: scan network interfaces for 100.x.x.x range
        if not tailscale_ip:
            try:
                cmd    = ["ipconfig"] if sys.platform == "win32" else ["ip", "addr"]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                matches = re.findall(r"100\.\d{1,3}\.\d{1,3}\.\d{1,3}", result.stdout)
                if matches:
                    tailscale_ip = matches[0]
            except Exception:
                pass

        # Strategy 4: Windows ipconfig /all — look for Tailscale adapter
        if not tailscale_ip and sys.platform == "win32":
            try:
                result = subprocess.run(
                    ["ipconfig", "/all"],
                    capture_output=True, text=True, timeout=5,
                )
                in_tailscale = False
                for line in result.stdout.splitlines():
                    if "tailscale" in line.lower():
                        in_tailscale = True
                    if in_tailscale and ("IPv4" in line or "IP Address" in line):
                        match = re.search(r"(\d+\.\d+\.\d+\.\d+)", line)
                        if match:
                            tailscale_ip = match.group(1)
                            break
            except Exception:
                pass

        print("\n🔵 TAILSCALE SETUP")
        print("══════════════════════════════════════════════")

        if tailscale_ip:
            base     = f"http://{tailscale_ip}:{port}"
            commands = {
                "clean":  f"{base}/clean",
                "dock":   f"{base}/dock",
                "pause":  f"{base}/pause",
                "resume": f"{base}/resume",
                "status": f"{base}/status",
            }

            print(f"✅ Tailscale detected!")
            print(f"   Your Tailscale IP: {tailscale_ip}")
            if tailscale_name:
                print(f"   Your device name:  {tailscale_name}")
            print()
            print(f"🔗 Permanent URLs (copy these into IFTTT):")
            for cmd, url in commands.items():
                print(f"   {cmd:8s} → {url}")
            print()
            print("💡 These URLs work from ANYWHERE as long as:")
            print("   ✅ Tailscale is running on your PC")
            print("   ✅ Tailscale is running on your phone/iPad")
            print("   ✅ Both signed into the same Google account")
            print("══════════════════════════════════════════════\n")

            # Send to all notification channels
            asyncio.ensure_future(self._notify(
                f"🔵 Sapna Tailscale URLs (permanent)\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                + "\n".join(f"{k}: {v}" for k, v in commands.items())
            ))

            return {
                "found":    True,
                "ip":       tailscale_ip,
                "name":     tailscale_name,
                "base_url": base,
                "commands": commands,
            }

        else:
            print("❌ Tailscale not found on this PC!")
            print()
            print("Install steps (3 min, free, no card ever):")
            print("  1. https://tailscale.com/download")
            print("     Download + install for Windows/Mac/Linux")
            print("  2. Sign in with Google when asked")
            print("  3. App Store / Play Store → 'Tailscale'")
            print("     Install on your iPhone/iPad → same Google account")
            print("  4. Run bot.tailscale_setup() again")
            print("══════════════════════════════════════════════\n")
            return {"found": False, "ip": None, "commands": {}}

    # Override the HTTP handler to add Siri-specific routes
    def start_http_server(self, port: int = 8080) -> None:  # type: ignore[override]
        """
        Start REST API + Apple Shortcuts / Siri compatible server.

        GET  endpoints: /status /battery /stats /history /predict
                        /siri/status (Siri-friendly spoken response)
        POST endpoints: /clean /pause /resume /dock
                        /siri/clean /siri/dock /siri/pause /siri/resume
        """
        self._require_connected()
        loop = asyncio.get_event_loop()
        bot  = self

        class SiriHandler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _json(self, data: dict, code: int = 200) -> None:
                body = json.dumps(data, default=str).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def _text(self, text: str, code: int = 200) -> None:
                body = text.encode()
                self.send_response(code)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self) -> None:
                self.send_response(200)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
                self.end_headers()

            def _run(self, coro: Any) -> None:
                future = asyncio.run_coroutine_threadsafe(coro, loop)
                future.result(timeout=10)

            def do_GET(self) -> None:
                # Standard routes
                get_routes = {
                    "/":        lambda: {"ok": True, "device": bot.device_name, "state": bot.state},
                    "/status":  lambda: bot.get_status(),
                    "/battery": lambda: {"battery": bot.battery},
                    "/stats":   lambda: bot.cleaning_stats(),
                    "/history": lambda: {"sessions": bot.cleaning_history()},
                    "/predict": lambda: {"prediction": bot.predict_next_clean()},
                    "/health":  lambda: bot.battery_health_score(),
                    "/streak":  lambda: bot.streak_tracker(),
                    "/cost":    lambda: bot.cost_calculator(),
                }
                # Siri-friendly spoken response
                if self.path == "/siri/status":
                    state   = bot.state or "unknown"
                    battery = bot.battery or 0
                    icons   = {
                        "CLEANING":"cleaning right now",
                        "DOCKED":"docked and charging",
                        "IDLE":"idle",
                        "PAUSED":"paused",
                        "RETURNING":"returning to dock",
                    }
                    spoken = (
                        f"Sapna is {icons.get(state, state)} "
                        f"with {battery} percent battery."
                    )
                    self._text(spoken)
                    return

                fn = get_routes.get(self.path)
                if fn:
                    self._json(fn())
                else:
                    self._json({"error": "Not found"}, 404)

            def do_POST(self) -> None:
                actions: Dict[str, Any] = {
                    "/clean":       bot.clean,
                    "/pause":       bot.pause,
                    "/resume":      bot.resume,
                    "/dock":        bot.dock,
                    "/siri/clean":  bot.clean,
                    "/siri/pause":  bot.pause,
                    "/siri/resume": bot.resume,
                    "/siri/dock":   bot.dock,
                }
                fn = actions.get(self.path)
                if fn:
                    try:
                        self._run(fn())
                        action = self.path.strip("/").replace("siri/", "")
                        # Siri-friendly response text
                        responses = {
                            "clean":  "Got it! Sapna is starting to clean now.",
                            "pause":  "Sapna is paused.",
                            "resume": "Sapna is resuming.",
                            "dock":   "Sapna is heading home to charge.",
                        }
                        spoken = responses.get(action, "Done!")
                        if self.path.startswith("/siri/"):
                            self._text(spoken)
                        else:
                            self._json({"ok": True, "action": action, "spoken": spoken})
                    except Exception as exc:
                        self._json({"error": str(exc)}, 500)
                else:
                    self._json({"error": "Not found"}, 404)

        class CustomServer(HTTPServer):
            pass

        srv        = CustomServer(("0.0.0.0", port), SiriHandler)
        srv.deebot = self   # type: ignore
        srv.loop   = loop   # type: ignore
        self._http_server = srv

        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        print(f"🌐 REST + Siri API → http://localhost:{port}")
        print(f"   Siri endpoints: /siri/clean  /siri/dock  /siri/pause  /siri/status")

    # ══════════════════════════════════════════════════════════════
    # VOICE RESPONSES (TTS)
    # ══════════════════════════════════════════════════════════════

    def add_voice_tts(
        self,
        custom_phrases: Optional[Dict[str, str]] = None,
        rate: int = 175,
        volume: float = 0.9,
    ) -> None:
        """
        Add offline text-to-speech voice responses.
        Your computer speaks when Sapna changes state.
        Requires: pip install pyttsx3

        Example:
            bot.add_voice_tts(custom_phrases={
                "CLEANING":  "Yes boss, I am cleaning now",
                "DOCKED":    "Back home, charging up",
                "PAUSED":    "Okay, I stopped",
                "ERROR":     "Help me, I am stuck!",
            })
        """
        try:
            import pyttsx3  # type: ignore
        except ImportError:
            print("❌ pip install pyttsx3")
            return

        default_phrases = {
            "CLEANING":  "Cleaning started",
            "DOCKED":    "Docked and charging",
            "IDLE":      "Standing by",
            "PAUSED":    "Paused",
            "RETURNING": "Returning to dock",
            "ERROR":     "I need help, something went wrong",
        }
        phrases = {**default_phrases, **(custom_phrases or {})}

        engine = pyttsx3.init()
        engine.setProperty("rate",   rate)
        engine.setProperty("volume", volume)

        def speak(new_state: str, old_state: str) -> None:
            text = phrases.get(new_state)
            if text:
                try:
                    engine.say(text)
                    engine.runAndWait()
                except Exception:
                    pass

        self.on_state_change(speak)
        print("🔊 TTS voice responses active")

    async def add_voice_ai(
        self,
        elevenlabs_api_key: str,
        voice_id: str = "21m00Tcm4TlvDq8ikWAM",
        custom_phrases: Optional[Dict[str, str]] = None,
    ) -> None:
        """
        AI voice responses via ElevenLabs (ultra-realistic).
        Free tier: 10,000 chars/month at elevenlabs.io

        voice_id: get from elevenlabs.io/voice-lab
        Default voice: Rachel (natural English)

        Example:
            await bot.add_voice_ai(
                "your_elevenlabs_key",
                voice_id="21m00Tcm4TlvDq8ikWAM",
                custom_phrases={"CLEANING": "On it boss, cleaning now!"}
            )
        """
        try:
            import pygame  # type: ignore
            pygame.mixer.init()
        except ImportError:
            print("❌ pip install pygame")
            return

        default_phrases = {
            "CLEANING":  "Starting to clean now!",
            "DOCKED":    "I'm home and charging.",
            "PAUSED":    "Alright, I've paused.",
            "RETURNING": "On my way back to the dock.",
            "ERROR":     "Uh oh, I'm stuck! I need some help.",
        }
        phrases = {**default_phrases, **(custom_phrases or {})}

        async def speak_ai(new_state: str, old_state: str) -> None:
            text = phrases.get(new_state)
            if not text:
                return
            try:
                async with aiohttp.ClientSession() as s:
                    r = await s.post(
                        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                        headers={
                            "xi-api-key":    elevenlabs_api_key,
                            "Content-Type":  "application/json",
                        },
                        json={
                            "text":     text,
                            "model_id": "eleven_monolingual_v1",
                            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
                        },
                        timeout=aiohttp.ClientTimeout(total=10),
                    )
                    audio = await r.read()
                path = f"sapna_tts_{new_state.lower()}.mp3"
                with open(path, "wb") as f:
                    f.write(audio)
                pygame.mixer.music.load(path)
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():
                    await asyncio.sleep(0.1)
            except Exception as exc:
                log.warning(f"ElevenLabs TTS error: {exc}")

        self.on_state_change(lambda n, o: asyncio.ensure_future(speak_ai(n, o)))
        print("🎙️  AI voice responses active (ElevenLabs)")

    # ══════════════════════════════════════════════════════════════
    # GOOGLE SHEETS LIVE LOGGING
    # ══════════════════════════════════════════════════════════════

    def add_google_sheets(
        self,
        credentials_json: str,
        spreadsheet_id: str,
        sheet_name: str = "Sapna Sessions",
    ) -> None:
        """
        Live-log every session to Google Sheets.
        Requires: pip install gspread oauth2client

        Setup:
        1. Go to console.cloud.google.com
        2. Create project → Enable Google Sheets API
        3. Create Service Account → Download JSON key
        4. Share your Google Sheet with the service account email

        Example:
            bot.add_google_sheets(
                credentials_json="path/to/key.json",
                spreadsheet_id="1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms",
            )
        """
        try:
            import gspread  # type: ignore
            from oauth2client.service_account import ServiceAccountCredentials  # type: ignore
        except ImportError:
            print("❌ pip install gspread oauth2client")
            return

        scope  = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds  = ServiceAccountCredentials.from_json_keyfile_name(credentials_json, scope)
        client = gspread.authorize(creds)

        try:
            sheet = client.open_by_key(spreadsheet_id).worksheet(sheet_name)
        except Exception:
            sheet = client.open_by_key(spreadsheet_id).add_worksheet(
                title=sheet_name, rows=1000, cols=10
            )
            sheet.append_row([
                "Start Time", "End Time", "Duration (min)",
                "Battery Start %", "Battery End %", "Drain %/min",
                "End Reason", "Errors", "Coverage m²", "Cost $"
            ])

        def _log_to_sheets(session: CleanSession) -> None:
            try:
                cost = session.duration_min / 60 * 22 / 1000 * 0.12 if session.duration_min else 0
                sqm  = round((session.duration_min or 0) * 0.8, 1)
                sheet.append_row([
                    session.start_time.isoformat(),
                    session.end_time.isoformat() if session.end_time else "",
                    round(session.duration_min or 0, 1),
                    session.battery_start or "",
                    session.battery_end   or "",
                    round(session.drain_rate or 0, 3),
                    session.end_reason or "",
                    "; ".join(session.errors),
                    sqm,
                    round(cost, 4),
                ])
                print(f"📊 Google Sheets: session logged")
            except Exception as exc:
                log.warning(f"Google Sheets error: {exc}")

        self.on_clean_done(_log_to_sheets)
        print(f"📊 Google Sheets logging active → {spreadsheet_id}")

    # ══════════════════════════════════════════════════════════════
    # NOTION DATABASE LOGGING
    # ══════════════════════════════════════════════════════════════

    def add_notion_log(
        self,
        notion_token: str,
        database_id: str,
    ) -> None:
        """
        Auto-log every session to a Notion database.
        Requires: pip install notion-client

        Setup:
        1. notion.so → Settings → Integrations → Create integration → copy token
        2. Create a database in Notion, share it with your integration
        3. Copy database ID from URL: notion.so/DATABASE_ID?v=...

        Example:
            bot.add_notion_log(
                notion_token="secret_xxx",
                database_id="abc123def456",
            )
        """
        try:
            from notion_client import Client  # type: ignore
        except ImportError:
            print("❌ pip install notion-client")
            return

        notion = Client(auth=notion_token)

        async def _log_to_notion(session: CleanSession) -> None:
            try:
                dur  = round(session.duration_min or 0, 1)
                sqm  = round(dur * 0.8, 1)
                cost = round(dur / 60 * 22 / 1000 * 0.12, 4)
                notion.pages.create(
                    parent={"database_id": database_id},
                    properties={
                        "Name": {
                            "title": [{"text": {"content":
                                f"Clean — {session.start_time.strftime('%Y-%m-%d %H:%M')}"
                            }}]
                        },
                        "Duration (min)":   {"number": dur},
                        "Battery Start %":  {"number": session.battery_start or 0},
                        "Battery End %":    {"number": session.battery_end   or 0},
                        "Drain %/min":      {"number": round(session.drain_rate or 0, 3)},
                        "End Reason":       {"select": {"name": session.end_reason or "unknown"}},
                        "Coverage m²":      {"number": sqm},
                        "Cost $":           {"number": cost},
                        "Errors":           {"number": len(session.errors)},
                        "Date": {
                            "date": {"start": session.start_time.isoformat()}
                        },
                    },
                )
                print("📝 Notion: session logged")
            except Exception as exc:
                log.warning(f"Notion error: {exc}")

        self.on_clean_done(lambda s: asyncio.ensure_future(_log_to_notion(s)))
        print(f"📝 Notion logging active → database: {database_id}")

    # ══════════════════════════════════════════════════════════════
    # ACHIEVEMENT SYSTEM
    # ══════════════════════════════════════════════════════════════

    def _get_achievements_file(self) -> str:
        return "sapna_achievements.json"

    def _load_achievements(self) -> dict:
        path = self._get_achievements_file()
        if os.path.exists(path):
            with open(path, "r") as f:
                return json.load(f)
        return {"unlocked": [], "xp": 0, "level": 1}

    def _save_achievements(self, data: dict) -> None:
        with open(self._get_achievements_file(), "w") as f:
            json.dump(data, f, indent=2)

    async def _check_achievements(self) -> None:
        """Check and unlock achievements after each session."""
        data     = self._load_achievements()
        unlocked = set(data.get("unlocked", []))
        sessions = len(self._all_sessions)
        hours    = self._get_total_clean_hours()
        streak   = self.streak_tracker().get("current_streak", 0)

        ACHIEVEMENTS = [
            ("first_clean",      "🎉 First Clean!",          "Completed your first clean",         1,   sessions >= 1),
            ("ten_cleans",       "🏆 10 Cleans",             "Completed 10 sessions",              10,  sessions >= 10),
            ("fifty_cleans",     "🥇 50 Cleans",             "Completed 50 sessions",              50,  sessions >= 50),
            ("hundred_cleans",   "💯 100 Cleans",            "The century mark",                   100, sessions >= 100),
            ("ten_hours",        "⏱️ 10 Hours Cleaned",      "10 total cleaning hours",            25,  hours >= 10),
            ("fifty_hours",      "⏱️ 50 Hours Cleaned",      "50 total cleaning hours",            75,  hours >= 50),
            ("week_streak",      "🔥 7-Day Streak",          "Cleaned 7 days in a row",            30,  streak >= 7),
            ("month_streak",     "🔥 30-Day Streak",         "Cleaned every day for a month",      150, streak >= 30),
            ("battery_saver",    "🔋 Battery Saver",         "Used smart battery protect",         15,  False),
            ("night_owl",        "🦉 Night Owl",             "Cleaned between midnight and 5am",   20,
             any(s.start_time.hour < 5 for s in self._all_sessions)),
            ("early_bird",       "🐦 Early Bird",            "Cleaned before 6am",                 20,
             any(s.start_time.hour < 6 for s in self._all_sessions)),
        ]

        newly_unlocked = []
        total_xp       = data.get("xp", 0)

        for aid, title, desc, xp, condition in ACHIEVEMENTS:
            if aid not in unlocked and condition:
                unlocked.add(aid)
                newly_unlocked.append({"id": aid, "title": title, "desc": desc, "xp": xp})
                total_xp += xp
                print(f"🏆 Achievement unlocked: {title} (+{xp} XP)")

        # Level calculation (every 100 XP = 1 level)
        level = max(1, total_xp // 100 + 1)
        old_level = data.get("level", 1)

        data["unlocked"] = list(unlocked)
        data["xp"]       = total_xp
        data["level"]    = level
        self._save_achievements(data)

        # Notify on new achievements
        for ach in newly_unlocked:
            msg = (
                f"🏆 Achievement Unlocked: {ach['title']}\n"
                f"{ach['desc']}\n"
                f"+{ach['xp']} XP — Total: {total_xp} XP (Level {level})"
            )
            await self._notify(msg)

        if level > old_level:
            msg = f"⬆️ Sapna leveled up! Now Level {level} 🎉"
            print(msg)
            await self._notify(msg)

    def get_achievements(self) -> dict:
        """
        Get all achievements, XP, and current level.

        Example:
            import json
            print(json.dumps(bot.get_achievements(), indent=2))
        """
        data     = self._load_achievements()
        sessions = len(self._all_sessions)
        hours    = self._get_total_clean_hours()

        ALL_ACHIEVEMENTS = [
            ("first_clean",    "🎉 First Clean!",         "Completed your first clean",        1),
            ("ten_cleans",     "🏆 10 Cleans",            "Completed 10 sessions",             10),
            ("fifty_cleans",   "🥇 50 Cleans",            "Completed 50 sessions",             50),
            ("hundred_cleans", "💯 100 Cleans",           "The century mark",                  100),
            ("ten_hours",      "⏱️ 10 Hours Cleaned",     "10 total cleaning hours",           25),
            ("fifty_hours",    "⏱️ 50 Hours Cleaned",     "50 total cleaning hours",           75),
            ("week_streak",    "🔥 7-Day Streak",         "7 days in a row",                   30),
            ("month_streak",   "🔥 30-Day Streak",        "30 days in a row",                  150),
            ("battery_saver",  "🔋 Battery Saver",        "Used smart battery protect",        15),
            ("night_owl",      "🦉 Night Owl",            "Cleaned midnight–5am",              20),
            ("early_bird",     "🐦 Early Bird",           "Cleaned before 6am",                20),
        ]

        unlocked = set(data.get("unlocked", []))
        result   = []
        for aid, title, desc, xp in ALL_ACHIEVEMENTS:
            result.append({
                "id":       aid,
                "title":    title,
                "desc":     desc,
                "xp":       xp,
                "unlocked": aid in unlocked,
            })

        return {
            "level":    data.get("level", 1),
            "xp":       data.get("xp", 0),
            "next_level_xp": (data.get("level", 1)) * 100,
            "sessions": sessions,
            "hours":    round(hours, 1),
            "achievements": result,
            "unlocked_count": len(unlocked),
            "total_count":    len(ALL_ACHIEVEMENTS),
        }

    # ══════════════════════════════════════════════════════════════
    # CARBON FOOTPRINT TRACKER
    # ══════════════════════════════════════════════════════════════

    def carbon_footprint(
        self,
        grid_carbon_intensity_g_per_kwh: float = 400.0,
        wattage: float = 22.0,
    ) -> dict:
        """
        Calculate CO2 emitted by Sapna's cleaning sessions.
        Grid carbon intensity varies by country/region:
          Iraq avg:  ~600 g CO2/kWh
          EU avg:    ~300 g CO2/kWh
          US avg:    ~400 g CO2/kWh
          Solar/wind: ~0 g CO2/kWh

        Example:
            # Iraq grid
            print(bot.carbon_footprint(grid_carbon_intensity_g_per_kwh=600))
        """
        sessions = [s for s in self._all_sessions if s.duration_min]
        if not sessions:
            return {"total_co2_g": 0, "sessions": 0}

        total_hours  = sum(s.duration_min for s in sessions) / 60.0  # type: ignore
        total_kwh    = wattage / 1000.0 * total_hours
        total_co2_g  = total_kwh * grid_carbon_intensity_g_per_kwh
        avg_co2      = total_co2_g / len(sessions)

        # Equivalents
        km_driven    = total_co2_g / 120.0    # avg car ~120g CO2/km
        phone_charges= total_co2_g / 8.22     # ~8.22g CO2 per phone charge

        return {
            "sessions":                len(sessions),
            "total_hours":             round(total_hours, 2),
            "total_kwh":               round(total_kwh, 4),
            "total_co2_g":             round(total_co2_g, 1),
            "total_co2_kg":            round(total_co2_g / 1000, 3),
            "avg_co2_per_session_g":   round(avg_co2, 1),
            "equivalent_km_driven":    round(km_driven, 2),
            "equivalent_phone_charges":round(phone_charges, 1),
            "grid_intensity":          grid_carbon_intensity_g_per_kwh,
            "tip": (
                "Use solar power mode to reduce this to near zero! ☀️"
                if grid_carbon_intensity_g_per_kwh > 200 else
                "Great — your grid is already low-carbon! 🌿"
            ),
        }

    # ══════════════════════════════════════════════════════════════
    # PET HAIR SEASONAL MODE
    # ══════════════════════════════════════════════════════════════

    def start_pet_hair_mode(
        self,
        extra_clean_months: Optional[List[int]] = None,
        extra_clean_hour:   int = 10,
    ) -> None:
        """
        Automatically increase cleaning frequency during pet shedding seasons.
        Default shedding months: March, April, September, October.

        Example:
            bot.start_pet_hair_mode(
                extra_clean_months=[3, 4, 9, 10],
                extra_clean_hour=10,
            )
        """
        if extra_clean_months is None:
            extra_clean_months = [3, 4, 9, 10]  # Spring + Fall shedding

        async def pet_loop() -> None:
            print(f"🐾 Pet hair mode: extra cleans in months {extra_clean_months}")
            while True:
                now = datetime.datetime.now()
                if now.month in extra_clean_months:
                    # Schedule an extra midday clean
                    nxt = now.replace(hour=extra_clean_hour, minute=0, second=0, microsecond=0)
                    if nxt <= now:
                        nxt += datetime.timedelta(days=1)
                    await asyncio.sleep((nxt - now).total_seconds())
                    if not self._is_silent_hour():
                        print(f"🐾 Shedding season extra clean!")
                        await self._notify("🐾 Shedding season — extra clean scheduled!")
                        await self.clean_if_battery_above(40)
                else:
                    # Check again tomorrow
                    await asyncio.sleep(86400)

        task = asyncio.ensure_future(pet_loop())
        self._schedule_tasks.append(task)
        print(f"🐾 Pet hair seasonal mode active")

    # ══════════════════════════════════════════════════════════════
    # HOME ASSISTANT ENTITY SYNC
    # ══════════════════════════════════════════════════════════════

    async def sync_home_assistant(
        self,
        ha_url: str,
        ha_token: str,
        entity_id: str = "vacuum.sapna",
        sync_interval: float = 10,
    ) -> None:
        """
        Sync Sapna's state to a Home Assistant input_select / vacuum entity.
        Allows HA automations to trigger based on Sapna's state.

        ha_url:   e.g. "http://homeassistant.local:8123"
        ha_token: Long-Lived Access Token from HA profile page

        Example:
            asyncio.ensure_future(
                bot.sync_home_assistant(
                    "http://192.168.1.100:8123",
                    "your_ha_long_lived_token",
                )
            )
        """
        self._require_connected()
        headers = {
            "Authorization": f"Bearer {ha_token}",
            "Content-Type":  "application/json",
        }
        print(f"🏠 Home Assistant sync active → {ha_url} ({entity_id})")

        state_map = {
            "CLEANING":  "cleaning",
            "DOCKED":    "docked",
            "IDLE":      "idle",
            "PAUSED":    "paused",
            "RETURNING": "returning",
            "ERROR":     "error",
        }

        while True:
            await asyncio.sleep(sync_interval)
            try:
                ha_state = state_map.get(self.state or "", "idle")
                payload  = {
                    "state": ha_state,
                    "attributes": {
                        "battery_level":   self.battery or 0,
                        "friendly_name":   self.device_name,
                        "status":          self.state or "unknown",
                        "fan_speed":       "normal",
                        "supported_features": 1,
                    },
                }
                async with aiohttp.ClientSession() as s:
                    await s.post(
                        f"{ha_url}/api/states/{entity_id}",
                        headers=headers,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=5),
                    )
            except Exception as exc:
                log.warning(f"HA sync error: {exc}")

    # ══════════════════════════════════════════════════════════════
    # TELEGRAM BOT CONTROL
    # ══════════════════════════════════════════════════════════════

    def start_telegram_bot(self, token: str) -> None:
        """
        Start a Telegram bot that controls Sapna via chat commands.
        Works from ANYWHERE — no tunnel, no Siri, no setup pain.
        Works perfectly in Iraq 🇮🇶 and every other country.

        Setup (2 min):
          1. Message @BotFather on Telegram → /newbot → get token
          2. Pass the token here
          3. Message your bot any command below

        Commands:
          /clean   → start cleaning
          /dock    → return to dock
          /pause   → pause
          /resume  → resume
          /status  → full status
          /battery → battery %
          /stats   → cleaning stats
          /help    → show all commands

        Example:
            bot.start_telegram_bot("123456:ABCdef...")
            # Then message your bot: /clean
        """
        try:
            from telegram import Update  # type: ignore
            from telegram.ext import (  # type: ignore
                ApplicationBuilder, CommandHandler, ContextTypes
            )
        except ImportError:
            print("❌ pip install python-telegram-bot")
            return

        sapna = self

        async def cmd_clean(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
            await sapna.clean()
            await update.message.reply_text("🧹 Sapna is cleaning!")  # type: ignore

        async def cmd_dock(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
            await sapna.dock()
            await update.message.reply_text("🏠 Sapna is heading home!")  # type: ignore

        async def cmd_pause(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
            await sapna.pause()
            await update.message.reply_text("⏸ Sapna paused.")  # type: ignore

        async def cmd_resume(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
            await sapna.resume()
            await update.message.reply_text("▶ Sapna resumed!")  # type: ignore

        async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
            s = sapna.get_status()
            icons = {
                "CLEANING": "🧹", "DOCKED": "🏠", "IDLE": "💤",
                "PAUSED": "⏸", "RETURNING": "↩️", "ERROR": "⚠️",
            }
            icon  = icons.get(s.get("state") or "", "🤖")
            drain = s.get("drain_rate")
            eta   = s.get("eta_min")
            msg   = (
                f"{icon} *Sapna Status*\n"
                f"━━━━━━━━━━━━━━━\n"
                f"State   : {s.get('state', '—')}\n"
                f"Battery : {s.get('battery', '—')}%\n"
                f"Uptime  : {round(s.get('uptime_min') or 0, 1)} min\n"
                f"Drain   : {drain}%/min\n" if drain else ""
                f"ETA     : {eta} min\n" if eta else ""
                f"Error   : {s.get('error', 'None')}"
            )
            await update.message.reply_text(msg, parse_mode="Markdown")  # type: ignore

        async def cmd_battery(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
            b    = sapna.battery or 0
            icon = "🪫" if b <= 20 else "🔋"
            bar  = "█" * (b // 10) + "░" * (10 - b // 10)
            await update.message.reply_text(f"{icon} Battery: {b}%\n[{bar}]")  # type: ignore

        async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
            s   = sapna.cleaning_stats()
            st  = sapna.streak_tracker()
            msg = (
                f"📊 *Cleaning Stats*\n"
                f"━━━━━━━━━━━━━━━\n"
                f"Sessions : {s.get('total_sessions', 0)}\n"
                f"Total    : {s.get('total_minutes', 0)} min\n"
                f"Avg      : {s.get('avg_duration_min', '—')} min\n"
                f"Streak   : {st.get('message', '—')}"
            )
            await update.message.reply_text(msg, parse_mode="Markdown")  # type: ignore

        async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
            msg = (
                "🤖 *Sapna Bot Commands*\n"
                "━━━━━━━━━━━━━━━━━\n"
                "/clean   — start cleaning\n"
                "/dock    — return to dock\n"
                "/pause   — pause cleaning\n"
                "/resume  — resume cleaning\n"
                "/status  — full status\n"
                "/battery — battery level\n"
                "/stats   — cleaning stats\n"
                "/help    — this menu"
            )
            await update.message.reply_text(msg, parse_mode="Markdown")  # type: ignore

        def _run_bot() -> None:
            import asyncio as _asyncio
            loop = _asyncio.new_event_loop()
            _asyncio.set_event_loop(loop)
            app = ApplicationBuilder().token(token).build()
            app.add_handler(CommandHandler("clean",   cmd_clean))
            app.add_handler(CommandHandler("dock",    cmd_dock))
            app.add_handler(CommandHandler("pause",   cmd_pause))
            app.add_handler(CommandHandler("resume",  cmd_resume))
            app.add_handler(CommandHandler("status",  cmd_status))
            app.add_handler(CommandHandler("battery", cmd_battery))
            app.add_handler(CommandHandler("stats",   cmd_stats))
            app.add_handler(CommandHandler("help",    cmd_help))
            app.run_polling()

        t = threading.Thread(target=_run_bot, daemon=True)
        t.start()
        print("📱 Telegram bot started!")
        print("   Send /help to your bot to see all commands")
        print("   Works from ANYWHERE — no tunnel needed 🌍")

    # ══════════════════════════════════════════════════════════════
    # WHATSAPP BOT CONTROL (via Twilio)
    # ══════════════════════════════════════════════════════════════

    def start_whatsapp_bot(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        your_whatsapp: str,
        port: int = 5000,
    ) -> None:
        """
        Control Sapna by sending WhatsApp messages.
        Uses Twilio WhatsApp sandbox (free to test).

        Setup:
          1. twilio.com → create free account
          2. Console → Messaging → Try it out → WhatsApp sandbox
          3. Send the join code to +1 415 523 8886 on WhatsApp
          4. Get account_sid + auth_token from twilio.com/console
          5. from_number = "whatsapp:+14155238886" (sandbox number)
          6. your_whatsapp = "whatsapp:+YOUR_NUMBER"

        Commands (send as WhatsApp message):
          clean   → start cleaning
          dock    → go home
          pause   → pause
          resume  → resume
          status  → get status
          battery → battery level

        Requires: pip install twilio flask

        Example:
            bot.start_whatsapp_bot(
                account_sid="ACxxx",
                auth_token="your_token",
                from_number="whatsapp:+14155238886",
                your_whatsapp="whatsapp:+9647XXXXXXXXX",
            )
        """
        try:
            from twilio.rest import Client as TwilioClient  # type: ignore
            from twilio.twiml.messaging_response import MessagingResponse  # type: ignore
        except ImportError:
            print("❌ pip install twilio flask")
            return

        try:
            from flask import Flask, request as flask_request  # type: ignore
        except ImportError:
            print("❌ pip install flask")
            return

        sapna      = self
        twilio_cli = TwilioClient(account_sid, auth_token)
        app_flask  = Flask(__name__)

        def send_whatsapp(msg: str) -> None:
            try:
                twilio_cli.messages.create(
                    body=msg, from_=from_number, to=your_whatsapp
                )
            except Exception as exc:
                log.warning(f"WhatsApp send error: {exc}")

        @app_flask.route("/whatsapp", methods=["POST"])
        def whatsapp_webhook():  # type: ignore
            body = flask_request.form.get("Body", "").strip().lower()
            resp = MessagingResponse()
            loop = asyncio.get_event_loop()

            async def run_cmd(coro: Any) -> None:
                await coro

            cmd_map = {
                "clean":  (sapna.clean,  "🧹 Cleaning started!"),
                "dock":   (sapna.dock,   "🏠 Going home!"),
                "pause":  (sapna.pause,  "⏸ Paused!"),
                "resume": (sapna.resume, "▶ Resumed!"),
            }

            if body in cmd_map:
                fn, reply = cmd_map[body]
                asyncio.run_coroutine_threadsafe(run_cmd(fn()), loop).result(timeout=10)
                resp.message(reply)
            elif body == "status":
                s = sapna.get_status()
                resp.message(
                    f"🤖 State: {s.get('state','—')}\n"
                    f"🔋 Battery: {s.get('battery','—')}%\n"
                    f"⏱ Uptime: {round(s.get('uptime_min') or 0, 1)}min"
                )
            elif body == "battery":
                resp.message(f"🔋 Battery: {sapna.battery}%")
            else:
                resp.message(
                    "🤖 Sapna commands:\n"
                    "clean / dock / pause / resume / status / battery"
                )
            return str(resp)

        def _run() -> None:
            app_flask.run(port=port, debug=False, use_reloader=False)

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        print(f"📱 WhatsApp bot started on port {port}")
        print(f"   Point Twilio webhook to: http://YOUR_IP:{port}/whatsapp")
        print(f"   Send 'clean' to your WhatsApp number to test!")

    # ══════════════════════════════════════════════════════════════
    # GOOGLE ASSISTANT via IFTTT
    # ══════════════════════════════════════════════════════════════

    def setup_google_assistant(self, port: int = 8080) -> dict:
        """
        Control Sapna with Google Assistant via IFTTT webhooks.
        Works on Android AND iPhone. Free. No extra app needed.

        Setup guide (5 min):
          1. ifttt.com → Create → If "Google Assistant" → Then "Webhooks"
          2. Google Assistant trigger: "Say a simple phrase"
             Phrase: "clean the house"
             Response: "Starting Sapna!"
          3. Webhooks action:
             URL: http://YOUR_PC_IP:8080/clean  (or CF tunnel URL)
             Method: POST
          4. Repeat for dock/pause/resume

        Make sure start_http_server() or start_siri_server() is running first.

        Returns setup instructions as dict.

        Example:
            info = bot.setup_google_assistant()
            # Then configure IFTTT with the URLs shown
        """
        local_ip = self._get_local_ip()
        base     = f"http://{local_ip}:{port}"

        commands = {
            "clean the house":  f"{base}/clean",
            "dock sapna":       f"{base}/dock",
            "pause sapna":      f"{base}/pause",
            "resume sapna":     f"{base}/resume",
            "sapna status":     f"{base}/status",
        }

        print("\n🎙️  GOOGLE ASSISTANT SETUP (via IFTTT)")
        print("══════════════════════════════════════════════")
        print("1. Go to ifttt.com → Create new Applet")
        print("2. IF: Google Assistant → 'Say a simple phrase'")
        print("3. THEN: Webhooks → 'Make a web request'")
        print("   Method: POST  |  Content Type: application/json")
        print()
        print("Commands to create:")
        for phrase, url in commands.items():
            print(f"  Phrase: \"{phrase}\"")
            print(f"  URL:    {url}")
            print()
        print("💡 Use Cloudflare Tunnel URL for 'Hey Google' anywhere!")
        print("══════════════════════════════════════════════\n")

        return {"base_url": base, "commands": commands}

    # ══════════════════════════════════════════════════════════════
    # ALEXA via WEBHOOK ROUTINE
    # ══════════════════════════════════════════════════════════════

    def setup_alexa(self, port: int = 8080) -> dict:
        """
        Control Sapna with Alexa via a webhook routine.
        Uses the free "Alexa Routines + Make (Integromat) / IFTTT" approach.

        Setup guide:
          Option A — IFTTT (easier):
            Same as Google Assistant setup but choose Alexa trigger instead.
            Alexa skill: "Alexa Triggers" on IFTTT
            Say: "Alexa, trigger clean the house"

          Option B — Amazon Developer (native, advanced):
            1. developer.amazon.com → Alexa Skills Kit → Create Skill
            2. Add a webhook intent that POSTs to your URL
            3. Deploy to your Alexa account

        Returns setup instructions.

        Example:
            info = bot.setup_alexa()
        """
        local_ip = self._get_local_ip()
        base     = f"http://{local_ip}:{port}"

        commands = {
            "Alexa, trigger clean the house":  f"{base}/clean",
            "Alexa, trigger dock sapna":        f"{base}/dock",
            "Alexa, trigger pause sapna":       f"{base}/pause",
            "Alexa, trigger resume sapna":      f"{base}/resume",
        }

        print("\n🔵 ALEXA SETUP (via IFTTT — easiest method)")
        print("══════════════════════════════════════════════")
        print("1. ifttt.com → Create → IF: Amazon Alexa")
        print("   Choose: 'Say a specific phrase'")
        print("   Phrase: 'clean the house'")
        print("2. THEN: Webhooks → Make a web request")
        print("   Method: POST")
        print()
        print("Alexa commands:")
        for phrase, url in commands.items():
            print(f"  \"{phrase}\"")
            print(f"  → POST {url}")
            print()
        print("💡 Use Cloudflare Tunnel URL so it works away from home!")
        print("══════════════════════════════════════════════\n")

        return {"base_url": base, "commands": commands}

    # ══════════════════════════════════════════════════════════════
    # NFC TAG SUPPORT
    # ══════════════════════════════════════════════════════════════

    def generate_nfc_urls(
        self,
        port: int = 8080,
        public_url: Optional[str] = None,
    ) -> dict:
        """
        Generate URLs to write to NFC tags.
        Tap your phone on the tag → Sapna gets the command instantly.
        No voice, no app, just tap.

        Works with any NFC tag (cheap on Amazon, ~$0.50 each).
        Write URL with NFC Tools app (free, iOS + Android).

        Setup:
          1. Buy NFC tags (NTAG213, any brand)
          2. Install "NFC Tools" app (free)
          3. Open app → Write → Add record → URL
          4. Paste the URL for the command you want
          5. Hold phone to tag to write
          6. Done — tap tag to trigger command!

        Tips:
          - Stick "CLEAN" tag near your door (tap when leaving)
          - Stick "DOCK" tag near your bed (tap at night)
          - Use colored tags or stickers to label them

        Example:
            bot.start_http_server(8080)
            urls = bot.generate_nfc_urls(port=8080)
            # Write urls["clean"] to a green NFC tag
            # Write urls["dock"] to a red NFC tag
        """
        base = public_url or f"http://{self._get_local_ip()}:{port}"

        tags = {
            "clean":  f"{base}/clean",
            "dock":   f"{base}/dock",
            "pause":  f"{base}/pause",
            "resume": f"{base}/resume",
            "status": f"{base}/status",
        }

        print("\n📡 NFC TAG URLS")
        print("══════════════════════════════════════════════")
        print("Write these URLs to NFC tags using 'NFC Tools' app:")
        print()
        for cmd, url in tags.items():
            print(f"  🏷️  {cmd.upper():8s} → {url}")
        print()
        print("Recommended tag placement:")
        print("  🟢 CLEAN  → front door (tap when leaving)")
        print("  🔴 DOCK   → bedside table (tap at night)")
        print("  🟡 PAUSE  → wherever you usually are")
        print()
        if not public_url:
            print("⚠️  These only work on home WiFi.")
            print("   Use cloudflare_tunnel=True in start_siri_server() for NFC anywhere!")
        else:
            print("✅ These work ANYWHERE — written to public URL!")
        print("══════════════════════════════════════════════\n")

        return tags

    # ══════════════════════════════════════════════════════════════
    # BEAUTIFUL HOME SCREEN WIDGET / WEB UI
    # ══════════════════════════════════════════════════════════════

    def start_widget_server(self, port: int = 8888) -> None:
        """
        Start a beautiful mobile-optimized web app.
        Add it to your iPhone/iPad/Android home screen as a PWA.
        One tap = full Sapna control panel, looks like a real app.

        ── HOW TO ADD TO HOME SCREEN ────────────────────────────
        iPhone/iPad:
          1. Open Safari (must be Safari, not Chrome)
          2. Go to http://YOUR_TAILSCALE_IP:8888
          3. Tap the Share button (box with arrow pointing up)
          4. Scroll down → tap "Add to Home Screen"
          5. Name it "Sapna" → tap "Add"
          6. Done! Tap the icon on your home screen 🎉

        Android:
          1. Open Chrome
          2. Go to http://YOUR_TAILSCALE_IP:8888
          3. Tap the 3-dot menu (top right)
          4. Tap "Add to Home Screen" or "Install App"
          5. Done! 🎉

        Example:
            bot.start_http_server(8080)   # start API first
            bot.start_widget_server(8888) # then widget
        """
        self._require_connected()
        sapna = self
        loop  = asyncio.get_event_loop()

        HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Sapna">
<meta name="mobile-web-app-capable" content="yes">
<meta name="theme-color" content="#0a0c10">
<title>Sapna</title>
<style>
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
body{background:#0a0c10;color:#dde1ec;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;min-height:100vh;padding:env(safe-area-inset-top,20px) 16px env(safe-area-inset-bottom,20px);max-width:420px;margin:0 auto}

/* HEADER */
.header{display:flex;align-items:center;justify-content:space-between;padding:16px 0 12px}
.logo{display:flex;align-items:center;gap:10px}
.logo-ico{font-size:32px}
.logo-text h1{font-size:20px;font-weight:700;color:#f5a623}
.logo-text p{font-size:11px;color:#6b7280;margin-top:1px}
.live-dot{width:8px;height:8px;border-radius:50%;background:#3ddc84;box-shadow:0 0 8px #3ddc84;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}

/* STATUS CARD */
.status-card{background:linear-gradient(135deg,#12151c,#1a1e2a);border:1px solid #1e2330;border-radius:20px;padding:20px;margin-bottom:16px;position:relative;overflow:hidden}
.status-card::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,#f5a623,#3ddc84,#4d9fff);border-radius:20px 20px 0 0}
.state-row{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px}
.state-badge{font-size:13px;font-weight:700;padding:5px 12px;border-radius:20px;letter-spacing:.04em}
.state-CLEANING{background:#0d2010;color:#3ddc84;border:1px solid #3ddc84}
.state-DOCKED{background:#0d1520;color:#4d9fff;border:1px solid #4d9fff}
.state-IDLE{background:#1a1a1a;color:#6b7280;border:1px solid #2a2a2a}
.state-PAUSED{background:#201500;color:#f5a623;border:1px solid #f5a623}
.state-RETURNING{background:#100d20;color:#b57bee;border:1px solid #b57bee}
.state-ERROR{background:#200d0d;color:#ff4f4f;border:1px solid #ff4f4f}
.state-icon{font-size:36px}

/* BATTERY */
.bat-section{margin-bottom:12px}
.bat-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}
.bat-label{font-size:11px;color:#6b7280;font-weight:600;letter-spacing:.06em;text-transform:uppercase}
.bat-pct{font-size:18px;font-weight:700;color:#3ddc84;font-variant-numeric:tabular-nums}
.bat-track{height:10px;background:#1a1e2a;border-radius:5px;overflow:hidden;border:1px solid #1e2330}
.bat-fill{height:100%;border-radius:5px;transition:width .6s ease,background .3s}

/* META ROW */
.meta-row{display:flex;gap:8px;flex-wrap:wrap}
.meta-chip{background:#0d1018;border:1px solid #1e2330;border-radius:8px;padding:5px 10px;font-size:11px;color:#6b7280;font-family:monospace}
.meta-chip span{color:#dde1ec;font-weight:600}

/* COMMAND GRID */
.cmd-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:16px}
.cmd-btn{border:none;border-radius:16px;padding:18px 12px;font-size:14px;font-weight:700;cursor:pointer;display:flex;flex-direction:column;align-items:center;gap:7px;transition:transform .12s,opacity .15s;position:relative;overflow:hidden;letter-spacing:.02em}
.cmd-btn:active{transform:scale(.93)}
.cmd-btn .ico{font-size:26px}
.cmd-btn.clean{background:linear-gradient(145deg,#0d2010,#163520);color:#3ddc84;border:1px solid #2d6040}
.cmd-btn.dock{background:linear-gradient(145deg,#0d1520,#162030);color:#4d9fff;border:1px solid #2d4060}
.cmd-btn.pause{background:linear-gradient(145deg,#201500,#302000);color:#f5a623;border:1px solid #604020}
.cmd-btn.resume{background:linear-gradient(145deg,#100d20,#181030);color:#b57bee;border:1px solid #402860}
.cmd-btn.wide{grid-column:1/-1;padding:14px}
.cmd-btn.stop{background:linear-gradient(145deg,#200d0d,#300f0f);color:#ff4f4f;border:1px solid #602020}

/* STATS PANEL */
.panel{background:#12151c;border:1px solid #1e2330;border-radius:16px;padding:16px;margin-bottom:12px}
.panel-title{font-size:10px;text-transform:uppercase;letter-spacing:.12em;color:#6b7280;margin-bottom:12px;font-weight:700}
.stat-row{display:flex;justify-content:space-between;align-items:center;padding:7px 0;border-bottom:1px solid #1a1e2a}
.stat-row:last-child{border-bottom:none}
.stat-label{font-size:12px;color:#6b7280}
.stat-value{font-size:13px;font-weight:600;color:#dde1ec;font-family:monospace}
.stat-value.green{color:#3ddc84}
.stat-value.orange{color:#f5a623}
.stat-value.red{color:#ff4f4f}
.stat-value.purple{color:#b57bee}

/* TABS */
.tabs{display:flex;gap:6px;margin-bottom:12px;background:#0d0f14;border-radius:12px;padding:4px}
.tab{flex:1;padding:8px;border:none;background:transparent;color:#6b7280;font-size:12px;font-weight:600;border-radius:9px;cursor:pointer;transition:all .15s}
.tab.active{background:#1a1e2a;color:#f5a623}

/* MAINTENANCE */
.maint-item{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid #1a1e2a}
.maint-item:last-child{border-bottom:none}
.maint-name{font-size:12px;color:#6b7280}
.maint-bar-wrap{flex:1;margin:0 10px;height:6px;background:#1a1e2a;border-radius:3px;overflow:hidden}
.maint-bar{height:100%;border-radius:3px;transition:width .5s}
.maint-pct{font-size:11px;font-weight:700;min-width:32px;text-align:right}

/* TOAST */
.toast{position:fixed;bottom:calc(env(safe-area-inset-bottom,0px) + 24px);left:50%;transform:translateX(-50%) translateY(20px);background:#3ddc84;color:#000;padding:11px 22px;border-radius:14px;font-weight:700;font-size:14px;opacity:0;transition:all .25s;pointer-events:none;white-space:nowrap;z-index:999}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
.toast.error{background:#ff4f4f;color:#fff}

/* REFRESH */
.refresh-row{display:flex;justify-content:space-between;align-items:center;padding:8px 0;margin-bottom:4px}
.refresh-time{font-size:11px;color:#6b7280}
.refresh-btn{background:#1a1e2a;border:1px solid #1e2330;color:#6b7280;border-radius:8px;padding:6px 12px;font-size:11px;cursor:pointer}
.refresh-btn:active{opacity:.7}
</style>
</head>
<body>

<div class="header">
  <div class="logo">
    <div class="logo-ico">🤖</div>
    <div class="logo-text">
      <h1>Sapna</h1>
      <p>Deebot T30C</p>
    </div>
  </div>
  <div class="live-dot" id="dot"></div>
</div>

<!-- STATUS CARD -->
<div class="status-card">
  <div class="state-row">
    <span class="state-badge state-IDLE" id="state-badge">LOADING</span>
    <span class="state-icon" id="state-icon">⏳</span>
  </div>
  <div class="bat-section">
    <div class="bat-header">
      <span class="bat-label">Battery</span>
      <span class="bat-pct" id="bat-pct">—%</span>
    </div>
    <div class="bat-track">
      <div class="bat-fill" id="bat-fill" style="width:0%"></div>
    </div>
  </div>
  <div class="meta-row" id="meta-row">
    <div class="meta-chip">Loading...</div>
  </div>
</div>

<!-- COMMAND GRID -->
<div class="cmd-grid">
  <button class="cmd-btn clean"  onclick="cmd('/clean','🧹 Cleaning started!')">
    <span class="ico">🧹</span>Clean
  </button>
  <button class="cmd-btn dock"   onclick="cmd('/dock','🏠 Going home!')">
    <span class="ico">🏠</span>Dock
  </button>
  <button class="cmd-btn pause"  onclick="cmd('/pause','⏸ Paused!')">
    <span class="ico">⏸</span>Pause
  </button>
  <button class="cmd-btn resume" onclick="cmd('/resume','▶ Resumed!')">
    <span class="ico">▶</span>Resume
  </button>
</div>

<!-- TABS -->
<div class="tabs">
  <button class="tab active" onclick="showTab('stats')">📊 Stats</button>
  <button class="tab"        onclick="showTab('maint')">🔧 Parts</button>
  <button class="tab"        onclick="showTab('streak')">🔥 Streak</button>
</div>

<!-- STATS TAB -->
<div class="panel" id="tab-stats">
  <div class="panel-title">Cleaning Stats</div>
  <div class="stat-row"><span class="stat-label">Total sessions</span><span class="stat-value green" id="s-sessions">—</span></div>
  <div class="stat-row"><span class="stat-label">Total time</span><span class="stat-value" id="s-total">—</span></div>
  <div class="stat-row"><span class="stat-label">Avg session</span><span class="stat-value" id="s-avg">—</span></div>
  <div class="stat-row"><span class="stat-label">Drain rate</span><span class="stat-value orange" id="s-drain">—</span></div>
  <div class="stat-row"><span class="stat-label">ETA remaining</span><span class="stat-value purple" id="s-eta">—</span></div>
  <div class="stat-row"><span class="stat-label">Uptime</span><span class="stat-value" id="s-uptime">—</span></div>
</div>

<!-- MAINTENANCE TAB -->
<div class="panel" id="tab-maint" style="display:none">
  <div class="panel-title">Component Health</div>
  <div class="maint-item">
    <span class="maint-name">Main Brush</span>
    <div class="maint-bar-wrap"><div class="maint-bar" id="m-brush" style="width:0%"></div></div>
    <span class="maint-pct" id="m-brush-pct">—</span>
  </div>
  <div class="maint-item">
    <span class="maint-name">HEPA Filter</span>
    <div class="maint-bar-wrap"><div class="maint-bar" id="m-filter" style="width:0%"></div></div>
    <span class="maint-pct" id="m-filter-pct">—</span>
  </div>
  <div class="maint-item">
    <span class="maint-name">Mop Pad</span>
    <div class="maint-bar-wrap"><div class="maint-bar" id="m-mop" style="width:0%"></div></div>
    <span class="maint-pct" id="m-mop-pct">—</span>
  </div>
</div>

<!-- STREAK TAB -->
<div class="panel" id="tab-streak" style="display:none">
  <div class="panel-title">Habit Tracker</div>
  <div class="stat-row"><span class="stat-label">Current streak</span><span class="stat-value green" id="k-cur">—</span></div>
  <div class="stat-row"><span class="stat-label">Longest streak</span><span class="stat-value orange" id="k-long">—</span></div>
  <div class="stat-row"><span class="stat-label">Total clean days</span><span class="stat-value" id="k-total">—</span></div>
  <div class="stat-row"><span class="stat-label">Last clean</span><span class="stat-value" id="k-last">—</span></div>
  <div class="stat-row"><span class="stat-label">Message</span><span class="stat-value purple" id="k-msg">—</span></div>
</div>

<div class="refresh-row">
  <span class="refresh-time" id="ref-time">—</span>
  <button class="refresh-btn" onclick="refresh()">↻ Refresh</button>
</div>

<div class="toast" id="toast"></div>

<script>
const STATE_ICONS = {CLEANING:'🧹',DOCKED:'🏠',IDLE:'💤',PAUSED:'⏸',RETURNING:'↩️',ERROR:'⚠️'};
const STATE_COLS  = {
  CLEANING:'#3ddc84',DOCKED:'#4d9fff',IDLE:'#6b7280',
  PAUSED:'#f5a623',RETURNING:'#b57bee',ERROR:'#ff4f4f'
};
let online = false;

function showTab(name) {
  ['stats','maint','streak'].forEach(t => {
    document.getElementById('tab-'+t).style.display = t===name ? '' : 'none';
  });
  document.querySelectorAll('.tab').forEach((el,i) => {
    el.classList.toggle('active', ['stats','maint','streak'][i]===name);
  });
}

function toast(msg, ok=true) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast' + (ok ? '' : ' error');
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2200);
}

async function cmd(path, msg) {
  try {
    const r = await fetch(path, {method:'POST'});
    if (!r.ok) throw new Error();
    toast(msg);
    setTimeout(refresh, 1800);
  } catch { toast('❌ Failed — is Sapna connected?', false); }
}

async function refresh() {
  try {
    const [status, stats, streak] = await Promise.all([
      fetch('/status').then(r=>r.json()),
      fetch('/stats').then(r=>r.json()),
      fetch('/streak').then(r=>r.json()),
    ]);

    // Online dot
    online = true;
    document.getElementById('dot').style.background = '#3ddc84';
    document.getElementById('dot').style.boxShadow  = '0 0 8px #3ddc84';

    // State
    const state = status.state || 'IDLE';
    const badge = document.getElementById('state-badge');
    badge.textContent = state;
    badge.className   = 'state-badge state-' + state;
    document.getElementById('state-icon').textContent = STATE_ICONS[state] || '🤖';

    // Battery
    const bat = status.battery || 0;
    document.getElementById('bat-pct').textContent  = bat + '%';
    const fill = document.getElementById('bat-fill');
    fill.style.width = bat + '%';
    fill.style.background = bat > 50
      ? 'linear-gradient(90deg,#3ddc84,#4d9fff)'
      : bat > 20
        ? 'linear-gradient(90deg,#f5a623,#ffaa00)'
        : 'linear-gradient(90deg,#ff4f4f,#ff7070)';

    // Meta chips
    const chips = [];
    if (status.drain_rate) chips.push(`Drain: ${status.drain_rate}%/min`);
    if (status.eta_min)    chips.push(`ETA: ${status.eta_min}min`);
    if (status.uptime_min) chips.push(`Up: ${Math.round(status.uptime_min)}min`);
    document.getElementById('meta-row').innerHTML = chips.length
      ? chips.map(c=>`<div class="meta-chip">${c}</div>`).join('')
      : '<div class="meta-chip">Connected ✅</div>';

    // Stats tab
    document.getElementById('s-sessions').textContent = stats.total_sessions || 0;
    document.getElementById('s-total').textContent    = (stats.total_minutes || 0) + ' min';
    document.getElementById('s-avg').textContent      = (stats.avg_duration_min || '—') + ' min';
    document.getElementById('s-drain').textContent    = status.drain_rate ? status.drain_rate + '%/min' : '—';
    document.getElementById('s-eta').textContent      = status.eta_min ? status.eta_min + ' min' : '—';
    document.getElementById('s-uptime').textContent   = status.uptime_min ? Math.round(status.uptime_min) + ' min' : '—';

    // Streak tab
    if (streak) {
      document.getElementById('k-cur').textContent   = (streak.current_streak || 0) + ' days';
      document.getElementById('k-long').textContent  = (streak.longest_streak || 0) + ' days';
      document.getElementById('k-total').textContent = (streak.total_clean_days || 0) + ' days';
      document.getElementById('k-last').textContent  = streak.last_clean || '—';
      document.getElementById('k-msg').textContent   = streak.message || '—';
    }

    // Maintenance tab (fetch separately)
    fetch('/maintenance').then(r=>r.json()).then(m=>{
      if (!m) return;
      function setBar(id, pct) {
        const bar = document.getElementById('m-'+id);
        const lbl = document.getElementById('m-'+id+'-pct');
        if (!bar || !lbl) return;
        bar.style.width      = pct + '%';
        bar.style.background = pct < 65 ? '#3ddc84' : pct < 85 ? '#f5a623' : '#ff4f4f';
        lbl.textContent      = pct + '%';
        lbl.style.color      = pct < 65 ? '#3ddc84' : pct < 85 ? '#f5a623' : '#ff4f4f';
      }
      setBar('brush',  m.brush?.usage_pct  || 0);
      setBar('filter', m.filter?.usage_pct || 0);
      setBar('mop',    m.mop_pad?.usage_pct|| 0);
    }).catch(()=>{});

    document.getElementById('ref-time').textContent = 'Updated ' + new Date().toLocaleTimeString();

  } catch {
    online = false;
    document.getElementById('dot').style.background = '#ff4f4f';
    document.getElementById('dot').style.boxShadow  = 'none';
    document.getElementById('state-badge').textContent = 'OFFLINE';
    document.getElementById('state-badge').className   = 'state-badge state-ERROR';
    document.getElementById('ref-time').textContent    = 'Offline — is PC running?';
  }
}

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>"""

        class WidgetHandler(BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:
                pass

            def _send(self, body: bytes, ct: str, code: int = 200) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ct)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self) -> None:
                self.send_response(200)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
                self.end_headers()

            def do_GET(self) -> None:
                if self.path in ("/", "/index.html"):
                    self._send(HTML.encode(), "text/html")
                elif self.path == "/status":
                    self._send(json.dumps(sapna.get_status(), default=str).encode(), "application/json")
                elif self.path == "/stats":
                    self._send(json.dumps(sapna.cleaning_stats(), default=str).encode(), "application/json")
                elif self.path == "/streak":
                    self._send(json.dumps(sapna.streak_tracker(), default=str).encode(), "application/json")
                elif self.path == "/maintenance":
                    self._send(json.dumps(sapna.full_maintenance_report(), default=str).encode(), "application/json")
                elif self.path == "/achievements":
                    self._send(json.dumps(sapna.get_achievements(), default=str).encode(), "application/json")
                else:
                    self._send(b'{"error":"not found"}', "application/json", 404)

            def do_POST(self) -> None:
                actions: Dict[str, Any] = {
                    "/clean":  sapna.clean,
                    "/dock":   sapna.dock,
                    "/pause":  sapna.pause,
                    "/resume": sapna.resume,
                }
                fn = actions.get(self.path)
                if fn:
                    try:
                        future = asyncio.run_coroutine_threadsafe(fn(), loop)
                        future.result(timeout=10)
                        action  = self.path.strip("/")
                        replies = {
                            "clean":  "🧹 Cleaning started!",
                            "dock":   "🏠 Going home!",
                            "pause":  "⏸ Paused!",
                            "resume": "▶ Resumed!",
                        }
                        self._send(json.dumps({
                            "ok": True, "action": action,
                            "spoken": replies.get(action, "Done!")
                        }).encode(), "application/json")
                    except Exception as exc:
                        self._send(json.dumps({"error": str(exc)}).encode(), "application/json", 500)
                else:
                    self._send(b'{"error":"not found"}', "application/json", 404)

        class WidgetServer(HTTPServer):
            pass

        srv = WidgetServer(("0.0.0.0", port), WidgetHandler)
        t   = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()

        local_ip = self._get_local_ip()
        print(f"\n📱 HOME SCREEN WIDGET READY")
        print(f"══════════════════════════════════════════════")
        print(f"   URL: http://{local_ip}:{port}")
        print(f"")
        print(f"   iPhone/iPad (Safari only):")
        print(f"   1. Open Safari → go to that URL")
        print(f"   2. Tap Share button (□↑)")
        print(f"   3. Scroll down → 'Add to Home Screen'")
        print(f"   4. Tap 'Add' → done! 🎉")
        print(f"")
        print(f"   Android (Chrome):")
        print(f"   1. Open Chrome → go to that URL")
        print(f"   2. Tap ⋮ menu → 'Add to Home Screen'")
        print(f"   3. Tap 'Add' → done! 🎉")
        print(f"══════════════════════════════════════════════\n")

    # ══════════════════════════════════════════════════════════════
    # WHISPER AI VOICE SERVER
    # ══════════════════════════════════════════════════════════════

    def start_whisper_server(
        self,
        port: int = 9090,
        model: str = "base",
        language: Optional[str] = None,
    ) -> None:
        """
        Start an AI voice command server using OpenAI Whisper.
        Your iPhone (via Scriptable) sends a voice recording →
        Whisper transcribes it → command runs on Sapna.

        Whisper is way more accurate than any built-in speech recognition.
        Works with any accent including Arabic. Runs 100% offline on your PC.

        Models (accuracy vs speed tradeoff):
          "tiny"   → fastest, less accurate  (~40MB)
          "base"   → good balance            (~140MB) ← recommended
          "small"  → more accurate           (~460MB)
          "medium" → very accurate           (~1.5GB)
          "large"  → best accuracy           (~3GB)

        language: force a language e.g. "arabic", "english", None=auto-detect

        Install: pip install openai-whisper torch

        Commands Whisper understands (say any of these):
          "clean" / "start" / "start cleaning"
          "dock"  / "go home" / "charge"
          "pause" / "stop"
          "resume" / "continue"
          "status" / "battery"

        Example:
            bot.start_whisper_server(port=9090, model="base")
            # Then set up Scriptable on iPhone to send voice to this port
        """
        try:
            import whisper  # type: ignore
        except ImportError:
            print("❌ pip install openai-whisper torch")
            print("   (First time downloads the model — ~140MB for 'base')")
            return

        try:
            from flask import Flask, request as freq  # type: ignore
        except ImportError:
            print("❌ pip install flask")
            return

        sapna     = self
        loop      = asyncio.get_event_loop()
        app_flask = Flask(__name__)

        print(f"🎤 Loading Whisper '{model}' model...")
        try:
            wmodel = whisper.load_model(model)
            print(f"✅ Whisper ready!")
        except Exception as exc:
            print(f"❌ Whisper load failed: {exc}")
            return

        # Command keywords → actions
        COMMANDS: Dict[str, Any] = {
            "clean":     sapna.clean,
            "start":     sapna.clean,
            "cleaning":  sapna.clean,
            "dock":      sapna.dock,
            "home":      sapna.dock,
            "charge":    sapna.dock,
            "pause":     sapna.pause,
            "stop":      sapna.pause,
            "resume":    sapna.resume,
            "continue":  sapna.resume,
        }

        def find_command(text: str) -> Optional[str]:
            """Find the best matching command in transcribed text."""
            text = text.lower().strip()
            # Direct match first
            for kw in COMMANDS:
                if kw in text:
                    return kw
            # Fuzzy: check word by word
            words = text.split()
            for word in words:
                for kw in COMMANDS:
                    if word.startswith(kw[:3]):  # first 3 chars match
                        return kw
            return None

        @app_flask.route("/voice", methods=["POST"])
        def voice_endpoint():  # type: ignore
            """Receive audio file → transcribe → run command."""
            if "audio" not in freq.files:
                return json.dumps({"error": "No audio file"}), 400

            audio_file = freq.files["audio"]
            tmp_path   = f"/tmp/sapna_voice_{int(time.time())}.wav"

            try:
                audio_file.save(tmp_path)

                # Transcribe with Whisper
                opts: Dict[str, Any] = {"fp16": False}
                if language:
                    opts["language"] = language

                result = wmodel.transcribe(tmp_path, **opts)
                text   = result["text"].strip()
                print(f"🎤 Heard: '{text}'")

                # Find command
                cmd_key = find_command(text)
                if cmd_key:
                    fn = COMMANDS[cmd_key]
                    asyncio.run_coroutine_threadsafe(fn(), loop).result(timeout=10)
                    responses = {
                        "clean":    "🧹 Cleaning started!",
                        "start":    "🧹 Cleaning started!",
                        "cleaning": "🧹 Cleaning started!",
                        "dock":     "🏠 Going home!",
                        "home":     "🏠 Going home!",
                        "charge":   "🏠 Going home!",
                        "pause":    "⏸ Paused!",
                        "stop":     "⏸ Paused!",
                        "resume":   "▶ Resumed!",
                        "continue": "▶ Resumed!",
                    }
                    reply = responses.get(cmd_key, "Done!")
                    print(f"✅ Command: {cmd_key} → {reply}")
                    return json.dumps({
                        "ok":        True,
                        "heard":     text,
                        "command":   cmd_key,
                        "response":  reply,
                    })
                else:
                    print(f"❓ No command found in: '{text}'")
                    return json.dumps({
                        "ok":      False,
                        "heard":   text,
                        "command": None,
                        "response": f"Heard '{text}' but didn't understand. Try: clean, dock, pause, resume",
                    })

            except Exception as exc:
                print(f"❌ Voice error: {exc}")
                return json.dumps({"error": str(exc)}), 500
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

        @app_flask.route("/voice-text", methods=["POST"])
        def voice_text_endpoint():  # type: ignore
            """Receive transcribed text from Scriptable → run command."""
            try:
                data = freq.get_json(force=True)
                text = (data or {}).get("text", "").strip()
                if not text:
                    return json.dumps({"ok": False, "error": "No text"}), 400
                print(f"🎤 Text command: '{text}'")
                cmd_key = find_command(text)
                if cmd_key:
                    fn = COMMANDS[cmd_key]
                    asyncio.run_coroutine_threadsafe(fn(), loop).result(timeout=10)
                    responses = {
                        "clean":    "🧹 Cleaning started!",
                        "start":    "🧹 Cleaning started!",
                        "cleaning": "🧹 Cleaning started!",
                        "dock":     "🏠 Going home!",
                        "home":     "🏠 Going home!",
                        "charge":   "🏠 Going home!",
                        "pause":    "⏸ Paused!",
                        "stop":     "⏸ Paused!",
                        "resume":   "▶ Resumed!",
                        "continue": "▶ Resumed!",
                    }
                    reply = responses.get(cmd_key, "Done!")
                    return json.dumps({"ok": True, "heard": text, "command": cmd_key, "response": reply})
                else:
                    return json.dumps({"ok": False, "heard": text, "command": None,
                                       "response": f"Didn't understand '{text}'"})
            except Exception as exc:
                return json.dumps({"error": str(exc)}), 500

        @app_flask.route("/ping", methods=["GET"])
        def ping():  # type: ignore
            return json.dumps({"ok": True, "state": sapna.state, "battery": sapna.battery})

        def _run() -> None:
            app_flask.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

        t = threading.Thread(target=_run, daemon=True)
        t.start()

        local_ip = self._get_local_ip()
        print(f"\n🎤 WHISPER VOICE SERVER")
        print(f"══════════════════════════════════════════════")
        print(f"   Listening on: http://{local_ip}:{port}/voice")
        print(f"   Model: {model} | Language: {language or 'auto-detect'}")
        print(f"")
        print(f"   Say any of these commands:")
        print(f"   'clean' / 'dock' / 'pause' / 'resume'")
        print(f"")
        print(f"   Scriptable on iPhone will send audio here →")
        print(f"   Whisper transcribes it → Sapna responds!")
        print(f"══════════════════════════════════════════════\n")

    def generate_scriptable_code(
        self,
        server_ip: str,
        api_port:     int = 8080,
        whisper_port: int = 9090,
    ) -> dict:
        """
        Generate JavaScript code for Scriptable app on iPhone/iPad.
        Creates FOUR scripts:
          1. Sapna Control  — action sheet with clean/dock/pause/resume buttons
          2. Sapna Stats    — battery, state, sessions, streak widget
          3. Sapna Voice    — tap → speak → Sapna responds (uses iOS Dictation, no Whisper needed)
          4. Sapna All-in-One — single script: shows buttons + status + voice in one tap

        WHISPER OFFLINE FIX:
          The voice script now uses iOS built-in Dictation (no Whisper server needed).
          Just speak → iOS transcribes → command sent to your API server.
          Whisper server is optional for higher accuracy.

        Install Scriptable: App Store → search "Scriptable" (free)

        Example:
            info = bot.tailscale_setup(port=8080)
            bot.generate_scriptable_code(
                server_ip=info["ip"],
                api_port=8080,
            )
        """
        base         = f"http://{server_ip}:{api_port}"
        whisper_base = f"http://{server_ip}:{whisper_port}"

        # ─────────────────────────────────────────────────────────
        # SCRIPT 1: CONTROL — action sheet with all buttons
        # ─────────────────────────────────────────────────────────
        control_script = f"""// ============================================
// Sapna Control — by sapna.py
// Paste in Scriptable, name it "Sapna Control"
// ============================================

const BASE = "{base}";

// ── helpers ──────────────────────────────────
async function getStatus() {{
  try {{
    const r = new Request(BASE + "/status");
    r.timeoutInterval = 5;
    return await r.loadJSON();
  }} catch(e) {{ return null; }}
}}

async function runCmd(path, successMsg) {{
  try {{
    const r = new Request(BASE + path);
    r.method = "POST";
    r.timeoutInterval = 8;
    await r.loadString();
    notify("Sapna", successMsg);
  }} catch(e) {{
    notify("Sapna ❌", "Could not reach Sapna.\\nIs your PC running + Tailscale on?");
  }}
}}

function notify(title, body) {{
  const n = new Notification();
  n.title = title;
  n.body  = body;
  n.schedule();
}}

// ── WIDGET MODE (lock screen / home screen) ──
if (config.runsInWidget) {{
  const status = await getStatus();
  const w = new ListWidget();
  w.backgroundColor = new Color("#0a0c10");
  w.url = "scriptable:///run?scriptName=Sapna%20Control";

  const STATE_ICON = {{
    CLEANING:"🧹", DOCKED:"🏠", IDLE:"💤",
    PAUSED:"⏸", RETURNING:"↩️", ERROR:"⚠️", OFFLINE:"📵"
  }};
  const STATE_COL = {{
    CLEANING:"#3ddc84", DOCKED:"#4d9fff", IDLE:"#6b7280",
    PAUSED:"#f5a623", RETURNING:"#b57bee", ERROR:"#ff4f4f", OFFLINE:"#ff4f4f"
  }};

  const state = status?.state || "OFFLINE";
  const bat   = status?.battery ?? null;
  const col   = STATE_COL[state] || "#6b7280";
  const ico   = STATE_ICON[state] || "🤖";

  // Robot icon + name row
  const hRow = w.addStack();
  hRow.layoutHorizontally();
  hRow.centerAlignContent();
  const hIco = hRow.addText("🤖");
  hIco.font = Font.systemFont(14);
  hRow.addSpacer(4);
  const hTxt = hRow.addText("Sapna");
  hTxt.font      = Font.boldSystemFont(13);
  hTxt.textColor = new Color("#f5a623");
  hRow.addSpacer();
  // live dot
  const dotCol = status ? "#3ddc84" : "#ff4f4f";
  const dot = hRow.addText("●");
  dot.font      = Font.systemFont(8);
  dot.textColor = new Color(dotCol);

  w.addSpacer(5);

  // State
  const stRow = w.addStack();
  stRow.layoutHorizontally();
  stRow.centerAlignContent();
  const stIco = stRow.addText(ico);
  stIco.font = Font.systemFont(20);
  stRow.addSpacer(5);
  const stTxt = stRow.addText(state);
  stTxt.font      = Font.boldSystemFont(13);
  stTxt.textColor = new Color(col);

  w.addSpacer(4);

  // Battery
  if (bat !== null) {{
    const batCol = bat > 50 ? "#3ddc84" : bat > 20 ? "#f5a623" : "#ff4f4f";
    const batRow = w.addStack();
    batRow.layoutHorizontally();
    batRow.centerAlignContent();
    const batIco = batRow.addText(bat <= 20 ? "🪫" : "🔋");
    batIco.font = Font.systemFont(11);
    batRow.addSpacer(3);
    const batTxt = batRow.addText(bat + "%");
    batTxt.font      = Font.boldSystemFont(12);
    batTxt.textColor = new Color(batCol);
  }}

  w.addSpacer(4);

  const hint = w.addText("Tap for controls");
  hint.font      = Font.systemFont(9);
  hint.textColor = new Color("#6b7280");

  Script.setWidget(w);

// ── APP MODE (tap widget → show buttons) ─────
}} else {{
  const status = await getStatus();
  const state  = status?.state  || "OFFLINE";
  const bat    = status?.battery ?? "?";

  const a = new Alert();
  a.title   = `🤖 Sapna`;
  a.message = `State: ${{state}}   🔋 ${{bat}}%\\n\\nChoose a command:`;

  a.addAction("🧹  Clean");
  a.addAction("🏠  Dock");
  a.addAction("⏸  Pause");
  a.addAction("▶  Resume");
  a.addDestructiveAction("🔇  Stop All");
  a.addCancelAction("✕ Cancel");

  const i = await a.presentSheet();

  if      (i === 0) await runCmd("/clean",  "🧹 Cleaning started!");
  else if (i === 1) await runCmd("/dock",   "🏠 Going home to charge!");
  else if (i === 2) await runCmd("/pause",  "⏸ Paused!");
  else if (i === 3) await runCmd("/resume", "▶ Resumed!");
  else if (i === 4) await runCmd("/dock",   "⏹ Stopped — returning to dock.");
}}

Script.complete();
"""

        # ─────────────────────────────────────────────────────────
        # SCRIPT 2: STATS WIDGET
        # ─────────────────────────────────────────────────────────
        stats_script = f"""// ============================================
// Sapna Stats Widget — by sapna.py
// Paste in Scriptable, name it "Sapna Stats"
// Best as medium home screen widget
// ============================================

const BASE = "{base}";

async function get(path) {{
  try {{
    const r = new Request(BASE + path);
    r.timeoutInterval = 5;
    return await r.loadJSON();
  }} catch(e) {{ return null; }}
}}

const [status, stats, streak] = await Promise.all([
  get("/status"), get("/stats"), get("/streak")
]);

const w = new ListWidget();
w.backgroundColor = new Color("#0a0c10");
w.setPadding(14, 14, 14, 14);
w.url = "scriptable:///run?scriptName=Sapna%20Control";

const STATE_COL = {{
  CLEANING:"#3ddc84", DOCKED:"#4d9fff", IDLE:"#6b7280",
  PAUSED:"#f5a623", RETURNING:"#b57bee", ERROR:"#ff4f4f"
}};
const STATE_ICON = {{
  CLEANING:"🧹", DOCKED:"🏠", IDLE:"💤",
  PAUSED:"⏸", RETURNING:"↩️", ERROR:"⚠️"
}};

// Header
const hdr = w.addStack();
hdr.layoutHorizontally();
const htxt = hdr.addText("🤖  Sapna Stats");
htxt.font      = Font.boldSystemFont(13);
htxt.textColor = new Color("#f5a623");
hdr.addSpacer();
const dot = hdr.addText(status ? "●" : "○");
dot.font      = Font.systemFont(9);
dot.textColor = new Color(status ? "#3ddc84" : "#ff4f4f");

w.addSpacer(8);

function row(label, val, col="#dde1ec") {{
  const s = w.addStack();
  s.layoutHorizontally();
  const l = s.addText(label);
  l.font      = Font.systemFont(11);
  l.textColor = new Color("#6b7280");
  s.addSpacer();
  const v = s.addText(String(val ?? "—"));
  v.font      = Font.boldSystemFont(11);
  v.textColor = new Color(col);
  w.addSpacer(5);
}}

if (!status) {{
  const t = w.addText("📵 Offline");
  t.font = Font.boldSystemFont(13);
  t.textColor = new Color("#ff4f4f");
}} else {{
  const state  = status.state   || "—";
  const bat    = status.battery ?? 0;
  const sc     = STATE_COL[state]  || "#6b7280";
  const batCol = bat > 50 ? "#3ddc84" : bat > 20 ? "#f5a623" : "#ff4f4f";

  row((STATE_ICON[state]||"🤖") + " State",   state,                         sc);
  row("🔋 Battery",  bat + "%",                                               batCol);
  row("🧹 Sessions", stats?.total_sessions  ?? 0,                             "#3ddc84");
  row("⏱  Total",   (stats?.total_minutes   ?? 0) + " min",                  "#dde1ec");
  row("📊 Avg",      (stats?.avg_duration_min ?? "—") + " min",               "#dde1ec");
  row("🔥 Streak",   streak?.message         ?? "—",                          "#f5a623");
  if (status.drain_rate)
    row("⚡ Drain",  status.drain_rate + "%/min",                             "#b57bee");
  if (status.eta_min)
    row("🕐 ETA",    status.eta_min + " min",                                 "#4d9fff");
}}

Script.setWidget(w);
Script.complete();
"""



        # ─────────────────────────────────────────────────────────
        # SCRIPT 3: VOICE — uses iOS Dictation (no Whisper needed)
        # ─────────────────────────────────────────────────────────
        voice_script = f"""// ============================================
// Sapna Voice Control — by sapna.py
// Paste in Scriptable, name it "Sapna Voice"
// Uses iOS built-in Dictation — NO Whisper server needed!
// ============================================

const BASE = "{base}";

// Command map — what words trigger what
const COMMANDS = {{
  "clean":   {{ path: "/clean",  reply: "🧹 Cleaning started!" }},
  "start":   {{ path: "/clean",  reply: "🧹 Cleaning started!" }},
  "vacuum":  {{ path: "/clean",  reply: "🧹 Cleaning started!" }},
  "dock":    {{ path: "/dock",   reply: "🏠 Going home!" }},
  "home":    {{ path: "/dock",   reply: "🏠 Going home!" }},
  "charge":  {{ path: "/dock",   reply: "🏠 Going home!" }},
  "pause":   {{ path: "/pause",  reply: "⏸ Paused!" }},
  "stop":    {{ path: "/pause",  reply: "⏸ Paused!" }},
  "resume":  {{ path: "/resume", reply: "▶ Resumed!" }},
  "continue":{{ path: "/resume", reply: "▶ Resumed!" }},
  "go":      {{ path: "/resume", reply: "▶ Resumed!" }},
}};

function findCmd(text) {{
  const t = text.toLowerCase();
  for (const [kw, val] of Object.entries(COMMANDS)) {{
    if (t.includes(kw)) return val;
  }}
  return null;
}}

async function runCmd(path) {{
  const r = new Request(BASE + path);
  r.method = "POST";
  r.timeoutInterval = 8;
  await r.loadString();
}}

function notify(title, body) {{
  const n = new Notification();
  n.title = title; n.body = body;
  n.schedule();
}}

// ── WIDGET MODE ───────────────────────────────
if (config.runsInWidget) {{
  const w = new ListWidget();
  w.backgroundColor = new Color("#0a0c10");
  w.url = "scriptable:///run?scriptName=Sapna%20Voice";

  const mic = w.addText("🎤");
  mic.font = Font.systemFont(28);
  mic.centerAlignText();
  w.addSpacer(4);
  const t1 = w.addText("Tap to speak");
  t1.font = Font.boldSystemFont(12);
  t1.textColor = new Color("#f5a623");
  t1.centerAlignText();
  w.addSpacer(2);
  const t2 = w.addText("Sapna Voice");
  t2.font = Font.systemFont(10);
  t2.textColor = new Color("#6b7280");
  t2.centerAlignText();

  Script.setWidget(w);

// ── APP MODE — tap widget to record ──────────
}} else {{
  // Check server reachable first
  try {{
    const ping = new Request(BASE + "/status");
    ping.timeoutInterval = 4;
    await ping.loadJSON();
  }} catch(e) {{
    const a = new Alert();
    a.title   = "❌ Cannot reach Sapna";
    a.message = "Make sure:\\n• Your PC is on\\n• Tailscale is running on PC + iPhone\\n• Python script is running\\n\\nServer: {base}";
    a.addCancelAction("OK");
    await a.present();
    Script.complete();
    return;
  }}

  // Show menu: voice OR buttons
  const menu = new Alert();
  menu.title   = "🤖 Sapna";
  menu.message = "How do you want to control Sapna?";
  menu.addAction("🎤 Voice command");
  menu.addAction("🧹 Clean");
  menu.addAction("🏠 Dock");
  menu.addAction("⏸ Pause");
  menu.addAction("▶ Resume");
  menu.addCancelAction("Cancel");

  const choice = await menu.presentSheet();

  if (choice === 0) {{
    // Voice path — iOS Dictation
    let speech = "";
    try {{
      speech = await Dictation.start();
    }} catch(e) {{
      const a = new Alert();
      a.title   = "❌ Dictation failed";
      a.message = "Enable Dictation in Settings → General → Keyboard → Enable Dictation";
      a.addCancelAction("OK");
      await a.present();
      Script.complete();
      return;
    }}

    if (!speech || !speech.trim()) {{
      const a = new Alert();
      a.title   = "❓ Nothing heard";
      a.message = "Try again and speak clearly.\\nSay: clean, dock, pause, or resume";
      a.addCancelAction("OK");
      await a.present();
      Script.complete();
      return;
    }}

    const cmd = findCmd(speech);
    if (cmd) {{
      try {{
        await runCmd(cmd.path);
        const a = new Alert();
        a.title   = "✅ " + cmd.reply;
        a.message = `Heard: "${{speech}}"`;
        a.addCancelAction("OK");
        await a.present();
        notify("Sapna", cmd.reply);
      }} catch(e) {{
        const a = new Alert();
        a.title   = "❌ Command failed";
        a.message = "Server error: " + String(e);
        a.addCancelAction("OK");
        await a.present();
      }}
    }} else {{
      const a = new Alert();
      a.title   = "❓ Not understood";
      a.message = `Heard: "${{speech}}"\\n\\nTry saying:\\n• "clean"\\n• "dock"\\n• "pause"\\n• "resume"`;
      a.addCancelAction("OK");
      await a.present();
    }}

  }} else if (choice === 1) {{
    await runCmd("/clean");  notify("Sapna", "🧹 Cleaning started!");
  }} else if (choice === 2) {{
    await runCmd("/dock");   notify("Sapna", "🏠 Going home!");
  }} else if (choice === 3) {{
    await runCmd("/pause");  notify("Sapna", "⏸ Paused!");
  }} else if (choice === 4) {{
    await runCmd("/resume"); notify("Sapna", "▶ Resumed!");
  }}
}}

Script.complete();
"""

        siri_voice = """// ============================================
// Sapna Voice Control — Siri Voice Edition (Fixed Async Output)
// Paste in Scriptable, name it "Sapna Voice"
// ============================================

const BASE = "http://100.96.15.116:8080";

const COMMANDS = {
  "clean":    { path: "/clean",  reply: "Cleaning started!" },
  "start":    { path: "/clean",  reply: "Cleaning started!" },
  "vacuum":   { path: "/clean",  reply: "Cleaning started!" },
  "dock":     { path: "/dock",   reply: "Going home!" },
  "home":     { path: "/dock",   reply: "Going home!" },
  "charge":   { path: "/dock",   reply: "Going home!" },
  "pause":    { path: "/pause",  reply: "Paused!" },
  "stop":     { path: "/pause",  reply: "Paused!" },
  "resume":   { path: "/resume", reply: "Resumed!" },
  "continue": { path: "/resume", reply: "Resumed!" },
  "go":       { path: "/resume", reply: "Resumed!" },
  "stats":    { path: "/status", reply: "Checking stats..." },
  "status":   { path: "/status", reply: "Checking stats..." },
  "info":     { path: "/status", reply: "Checking stats..." }
};

function findCmd(text) {
  if (!text) return null;
  const t = text.toLowerCase();
  for (const [kw, val] of Object.entries(COMMANDS)) {
    if (t.includes(kw)) return { key: kw, ...val };
  }
  return null;
}

async function runCmd(path) {
  const r = new Request(BASE + path);
  r.method = "POST";
  r.timeoutInterval = 8;
  await r.loadString();
}

async function fetchStats() {
  const r = new Request(BASE + "/status");
  r.timeoutInterval = 6;
  const data = await r.loadJSON();
  
  if (typeof data === "object" && data !== null) {
    const parts = [];
    if (data.status) parts.push(`Status is ${data.status}`);
    if (data.battery !== undefined) parts.push(`Battery is at ${data.battery}%`);
    if (data.state) parts.push(`State is ${data.state}`);
    return parts.length > 0 ? parts.join(". ") : JSON.stringify(data);
  }
  return String(data);
}

function respond(text) {
  Script.setShortcutOutput(text);
  Script.complete();
}

async function main() {
  // ── WIDGET MODE ───────────────────────────────
  if (config.runsInWidget) {
    const w = new ListWidget();
    w.backgroundColor = new Color("#0a0c10");
    w.url = "scriptable:///run?scriptName=Sapna%20Voice";

    const mic = w.addText("🗣️");
    mic.font = Font.systemFont(28);
    mic.centerAlignText();
    w.addSpacer(4);
    const t1 = w.addText("Siri Voice Control");
    t1.font = Font.boldSystemFont(12);
    t1.textColor = new Color("#f5a623");
    t1.centerAlignText();
    w.addSpacer(2);
    const t2 = w.addText("Sapna Voice");
    t2.font = Font.systemFont(10);
    t2.textColor = new Color("#6b7280");
    t2.centerAlignText();

    Script.setWidget(w);
    Script.complete();
    return;
  }

  // ── SIRI / APP / SHORTCUTS MODE ───────────────
  let input = args.shortcutParameter || args.plainText || "";

  if (!input) {
    if (config.runsWithSiri) {
      respond("Please specify a command like clean, dock, pause, resume, or stats.");
      return;
    } else if (config.runsInApp) {
      const alert = new Alert();
      alert.title = "🤖 Sapna Siri Voice";
      alert.message = "Enter or speak a command (clean, dock, pause, resume, stats):";
      alert.addTextField("Command", "");
      alert.addAction("Run");
      alert.addCancelAction("Cancel");
      const res = await alert.present();
      if (res === -1) {
        Script.complete();
        return;
      }
      input = alert.textFieldValue(0);
    }
  }

  const cmd = findCmd(input);

  if (cmd) {
    try {
      if (cmd.path === "/status") {
        const statsSummary = await fetchStats();
        respond(`Sapna status: ${statsSummary}`);
      } else {
        await runCmd(cmd.path);
        respond(cmd.reply);
      }
    } catch (e) {
      respond("Failed to communicate with Sapna server.");
    }
  } else {
    respond("Unknown command. You can say clean, dock, pause, resume, or stats.");
  }
}

main();"""
        # ─────────────────────────────────────────────────────────
        allinone_script = f"""// ============================================
// Sapna All-in-One — by sapna.py
// Paste in Scriptable, name it "Sapna"
// Widget shows status. Tap = full control menu
// with buttons + voice + stats all in one!
// ============================================

const BASE = "{base}";

const STATE_ICON = {{
  CLEANING:"🧹", DOCKED:"🏠", IDLE:"💤",
  PAUSED:"⏸", RETURNING:"↩️", ERROR:"⚠️"
}};
const STATE_COL = {{
  CLEANING:"#3ddc84", DOCKED:"#4d9fff", IDLE:"#6b7280",
  PAUSED:"#f5a623", RETURNING:"#b57bee", ERROR:"#ff4f4f"
}};
const CMDS = {{
  "clean":   "/clean",  "start":   "/clean",
  "vacuum":  "/clean",  "dock":    "/dock",
  "home":    "/dock",   "charge":  "/dock",
  "pause":   "/pause",  "stop":    "/pause",
  "resume":  "/resume", "continue":"/resume",
}};

function findCmd(text) {{
  const t = text.toLowerCase();
  for (const [k,v] of Object.entries(CMDS)) {{
    if (t.includes(k)) return v;
  }}
  return null;
}}

async function get(path) {{
  try {{
    const r = new Request(BASE + path);
    r.timeoutInterval = 5;
    return await r.loadJSON();
  }} catch(e) {{ return null; }}
}}

async function post(path) {{
  const r = new Request(BASE + path);
  r.method = "POST";
  r.timeoutInterval = 8;
  await r.loadString();
}}

function notify(title, body) {{
  const n = new Notification();
  n.title = title; n.body = body;
  n.schedule();
}}

// ── WIDGET ────────────────────────────────────
if (config.runsInWidget) {{
  const status = await get("/status");
  const state  = status?.state || "OFFLINE";
  const bat    = status?.battery ?? null;
  const col    = STATE_COL[state]  || "#ff4f4f";
  const ico    = STATE_ICON[state] || "📵";

  const w = new ListWidget();
  w.backgroundColor = new Color("#0a0c10");
  w.url = "scriptable:///run?scriptName=Sapna";

  // Header
  const h = w.addStack();
  h.layoutHorizontally();
  h.centerAlignContent();
  const ht = h.addText("🤖  Sapna");
  ht.font      = Font.boldSystemFont(13);
  ht.textColor = new Color("#f5a623");
  h.addSpacer();
  const hd = h.addText(status ? "LIVE ●" : "OFFLINE ○");
  hd.font      = Font.systemFont(9);
  hd.textColor = new Color(status ? "#3ddc84" : "#ff4f4f");

  w.addSpacer(6);

  // Big state
  const sr = w.addStack();
  sr.layoutHorizontally();
  sr.centerAlignContent();
  const si = sr.addText(ico + " ");
  si.font = Font.systemFont(24);
  const st = sr.addText(state);
  st.font      = Font.boldSystemFont(16);
  st.textColor = new Color(col);

  w.addSpacer(5);

  // Battery row
  if (bat !== null) {{
    const bc = bat > 50 ? "#3ddc84" : bat > 20 ? "#f5a623" : "#ff4f4f";
    const br = w.addStack();
    br.layoutHorizontally();
    br.centerAlignContent();
    const bi = br.addText(bat <= 20 ? "🪫 " : "🔋 ");
    bi.font = Font.systemFont(11);
    const bv = br.addText(bat + "%");
    bv.font      = Font.boldSystemFont(13);
    bv.textColor = new Color(bc);
    br.addSpacer();
    // Mini hint
    const hh = br.addText("Tap for controls →");
    hh.font      = Font.systemFont(9);
    hh.textColor = new Color("#6b7280");
  }}

  Script.setWidget(w);

// ── APP MODE — full control menu ──────────────
}} else {{
  // Check server
  const status = await get("/status");

  if (!status) {{
    const a = new Alert();
    a.title   = "❌ Sapna Offline";
    a.message = "Cannot reach your PC.\\n\\nCheck:\\n• PC is on\\n• Tailscale running on PC + iPhone\\n• Python script is running\\n\\nServer: {base}";
    a.addCancelAction("OK");
    await a.present();
    Script.complete();
    return;
  }}

  const state = status.state   || "—";
  const bat   = status.battery ?? "?";
  const drain = status.drain_rate;
  const eta   = status.eta_min;

  let subMsg = `State: ${{state}}   🔋 ${{bat}}%`;
  if (drain) subMsg += `\\nDrain: ${{drain}}%/min`;
  if (eta)   subMsg += `   ETA: ${{eta}}min`;

  const a = new Alert();
  a.title   = "🤖 Sapna Controller";
  a.message = subMsg;

  a.addAction("🧹  Clean");
  a.addAction("🏠  Dock");
  a.addAction("⏸  Pause");
  a.addAction("▶  Resume");
  a.addAction("🎤  Voice command");
  a.addAction("📊  Stats");
  a.addCancelAction("✕ Cancel");

  const choice = await a.presentSheet();

  if (choice === 0) {{
    await post("/clean");
    notify("Sapna", "🧹 Cleaning started!");

  }} else if (choice === 1) {{
    await post("/dock");
    notify("Sapna", "🏠 Going home to charge!");

  }} else if (choice === 2) {{
    await post("/pause");
    notify("Sapna", "⏸ Paused!");

  }} else if (choice === 3) {{
    await post("/resume");
    notify("Sapna", "▶ Resumed!");

  }} else if (choice === 4) {{
    // Voice command
    let speech = "";
    try {{
      speech = await Dictation.start();
    }} catch(e) {{
      const a2 = new Alert();
      a2.title   = "❌ Dictation failed";
      a2.message = "Enable: Settings → General → Keyboard → Enable Dictation";
      a2.addCancelAction("OK");
      await a2.present();
      Script.complete();
      return;
    }}

    if (!speech?.trim()) {{
      const a2 = new Alert();
      a2.title   = "❓ Nothing heard";
      a2.message = "Try again. Say: clean, dock, pause, or resume";
      a2.addCancelAction("OK");
      await a2.present();
    }} else {{
      const cmdPath = findCmd(speech);
      if (cmdPath) {{
        await post(cmdPath);
        const replies = {{
          "/clean":  "🧹 Cleaning started!",
          "/dock":   "🏠 Going home!",
          "/pause":  "⏸ Paused!",
          "/resume": "▶ Resumed!",
        }};
        const reply = replies[cmdPath] || "Done!";
        const a2 = new Alert();
        a2.title   = "✅ " + reply;
        a2.message = `Heard: "${{speech}}"`;
        a2.addCancelAction("OK");
        await a2.present();
        notify("Sapna", reply);
      }} else {{
        const a2 = new Alert();
        a2.title   = "❓ Not understood";
        a2.message = `Heard: "${{speech}}"\\n\\nSay: clean / dock / pause / resume`;
        a2.addCancelAction("OK");
        await a2.present();
      }}
    }}

  }} else if (choice === 5) {{
    // Stats
    const [stats, streak] = await Promise.all([
      get("/stats"), get("/streak")
    ]);
    const a2 = new Alert();
    a2.title   = "📊 Sapna Stats";
    a2.message =
      `🧹 Sessions : ${{stats?.total_sessions ?? 0}}\\n` +
      `⏱  Total    : ${{stats?.total_minutes  ?? 0}} min\\n` +
      `📊 Avg      : ${{stats?.avg_duration_min ?? "—"}} min\\n` +
      `🔥 Streak   : ${{streak?.message ?? "—"}}\\n` +
      `🔋 Battery  : ${{bat}}%`;
    a2.addCancelAction("OK");
    await a2.present();
  }}
}}

Script.complete();
"""

        # ─────────────────────────────────────────────────────────
        # PRINT SETUP GUIDE
        # ─────────────────────────────────────────────────────────
        print("\n📱 SCRIPTABLE SETUP GUIDE")
        print("══════════════════════════════════════════════")
        print("STEP 1: Install Scriptable (free)")
        print("  App Store → search 'Scriptable' → Install")
        print()
        print("STEP 2: Add each script")
        print("  Open Scriptable → tap '+' (top right)")
        print("  Paste the script → tap script name → rename → Done")
        print()
        print("  Scripts to add:")
        print("  • Sapna          (All-in-One — RECOMMENDED, use this one)")
        print("  • Sapna Control  (buttons only)")
        print("  • Sapna Stats    (stats widget)")
        print("  • Sapna Voice    (voice + buttons)")
        print()
        print("STEP 3: Home screen widget")
        print("  Long press home screen → '+' (top left)")
        print("  Search 'Scriptable' → pick size → Add Widget")
        print("  Tap widget → Script: 'Sapna'")
        print()
        print("STEP 4: Lock screen widget")
        print("  Long press lock screen → Customize → Lock Screen")
        print("  Tap widget area below clock → '+' → Scriptable")
        print("  Choose Script: 'Sapna'")
        print()
        print("STEP 5: Enable Dictation (for voice)")
        print("  Settings → General → Keyboard → Enable Dictation → ON")
        print()
        print(f"Your server: {base}")
        print("══════════════════════════════════════════════\n")

        # Save all scripts to files
        scripts = {
            "Sapna.js":         allinone_script,
            "Sapna_Control.js": control_script,
            "Sapna_Stats.js":   stats_script,
            "Sapna_Voice.js":   voice_script,
            "Sapna_Siri.js":    siri_voice,
        }
        for fname, code in scripts.items():
            with open(fname, "w", encoding="utf-8") as f:
                f.write(code)
            print(f"💾 Saved: {fname}")

        print("\n💡 TIP: Just use Sapna.js — it has everything in one script!\n")

        return {
            "allinone_script": allinone_script,
            "control_script":  control_script,
            "stats_script":    stats_script,
            "voice_script":    voice_script,
            "siri_voice":      siri_voice,
            "files_saved":     list(scripts.keys()),
            "api_base":        base,
            "whisper_base":    whisper_base,
        }

    # ══════════════════════════════════════════════════════════════
    # CONTEXT MANAGER
    # ══════════════════════════════════════════════════════════════

    async def __aenter__(self) -> "Deebot":
        await self.connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.disconnect()
