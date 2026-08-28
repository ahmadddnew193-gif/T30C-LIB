

import ssl
import certifi
import asyncio
import hashlib
import logging
import warnings
import sys

import aiohttp
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

log = logging.getLogger("deebot")


class Deebot:
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

    def __init__(
        self,
        account_id: str,
        password: str,
        country: str,
        device_id: str,
        device_name: str = "Sapna",
    ):
        self.account_id   = account_id
        self.password     = password
        self.country      = country
        self.device_id    = device_id
        self.device_name  = device_name

        self._password_hash = hashlib.md5(password.encode()).hexdigest()
        self._session       = None
        self._authenticator = None
        self._bot           = None
        self._mqtt          = None

        # Public state — updated automatically via MQTT events
        self.state   = None   # e.g. "CLEANING", "DOCKED", "IDLE", "PAUSED"
        self.battery = None   # int 0–100
        self.error   = None   # (code, description) or None

    # ── INTERNAL SETUP ────────────────────────────────────────────

    def _make_ssl_session(self):
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        connector   = aiohttp.TCPConnector(ssl=ssl_context)
        return aiohttp.ClientSession(connector=connector)

    def _find_verification_error(self, exc):
        if isinstance(exc, DeviceVerificationRequiredError):
            return True
        if isinstance(exc, (ExceptionGroup, BaseExceptionGroup)):
            return any(self._find_verification_error(e) for e in exc.exceptions)
        if hasattr(exc, "__cause__") and exc.__cause__:
            return self._find_verification_error(exc.__cause__)
        return False

    # ── EVENT HANDLERS ────────────────────────────────────────────

    async def _on_state(self, e: StateEvent):
        self.state = e.state.name
        icons = {
            "CLEANING":  "🧹",
            "DOCKED":    "🏠",
            "IDLE":      "💤",
            "PAUSED":    "⏸",
            "RETURNING": "↩️",
            "ERROR":     "⚠️",
        }
        log.info(f"{icons.get(self.state, '🤖')} State: {self.state}")

    async def _on_battery(self, e: BatteryEvent):
        self.battery = e.value
        icon = "🪫" if e.value <= 20 else "🔋"
        log.info(f"{icon} Battery: {e.value}%")

    async def _on_error(self, e: ErrorEvent):
        if e.code != 0:
            self.error = (e.code, e.description)
            log.warning(f" Error [{e.code}]: {e.description}")

    # ── PUBLIC API ────────────────────────────────────────────────

    async def verify(self) -> None:
        """
        One-time device verification via email code.
        Call this ONCE on first run — never needed again for the same device_id.

        Raises
        ------
        RuntimeError if verification fails or is rejected by Ecovacs.
        """
        print("\n⚠️  DEVICE VERIFICATION (one-time only)")
        print(f"   Sending code to {self.account_id}...\n")

        session = self._make_ssl_session()
        try:
            rest_config   = create_rest_config(session, device_id=self.device_id, alpha_2_country=self.country)
            authenticator = Authenticator(rest_config, self.account_id, self._password_hash)

            await authenticator.request_device_verification_code()
            print(f"Check your email for the Ecovacs verification code.")

            code = input("   Enter code: ").strip()
            await authenticator.verify_device(code)
            print("✅ Verified! You won't need to do this again.\n")
        finally:
            await session.close()

    async def connect(self) -> None:
        """
        Authenticate and connect to Sapna via MQTT.
        Call this before any command method.

        Raises
        ------
        RuntimeError  if no devices found.
        Exception     if auth fails for a reason other than verification.
        """
        self._session = self._make_ssl_session()

        rest_config        = create_rest_config(self._session, device_id=self.device_id, alpha_2_country=self.country)
        self._authenticator = Authenticator(rest_config, self.account_id, self._password_hash)
        api_client         = ApiClient(self._authenticator)

        try:
            devices = await api_client.get_devices()
        except Exception as e:
            if self._find_verification_error(e):
                raise RuntimeError(
                    "Device not verified. Call await bot.verify() first, then connect() again."
                ) from e
            raise

        if not devices.mqtt:
            raise RuntimeError("No MQTT devices found — is your robot powered on and online?")

        target = next(
            (
                d for d in devices.mqtt
                if (getattr(d, "nick", "") or getattr(d, "name", "")).lower() == self.device_name.lower()
            ),
            devices.mqtt[0],
        )
        log.info(f" Found: {getattr(target, 'nick', target)}")

        self._bot = Device(target, self._authenticator)
        self._bot.events.subscribe(StateEvent,   self._on_state)
        self._bot.events.subscribe(BatteryEvent, self._on_battery)
        self._bot.events.subscribe(ErrorEvent,   self._on_error)

        self._mqtt = MqttClient(
            create_mqtt_config(device_id=self.device_id, country=self.country),
            self._authenticator,
        )
        await self._bot.initialize(self._mqtt)

        await asyncio.sleep(3)   # let initial events flush
        log.info("✅ Connected to Sapna! ")

    async def clean(self) -> None:
        """Start cleaning."""
        self._require_connected()
        log.info("▶ Starting clean...")
        await self._bot.execute_command(CleanV2(CleanAction.START))

    async def pause(self) -> None:
        """Pause current clean job."""
        self._require_connected()
        log.info("⏸ Pausing...")
        await self._bot.execute_command(CleanV2(CleanAction.PAUSE))

    async def resume(self) -> None:
        """Resume a paused clean job."""
        self._require_connected()
        log.info("▶ Resuming...")
        await self._bot.execute_command(CleanV2(CleanAction.RESUME))

    async def dock(self) -> None:
        """Send robot back to dock."""
        self._require_connected()
        log.info(" Returning to dock...")
        await self._bot.execute_command(Charge())

    async def disconnect(self) -> None:
        """Close MQTT connection and HTTP session cleanly."""
        if self._session and not self._session.closed:
            await self._session.close()
        log.info("👋 Disconnected.")

    # ── HELPERS ───────────────────────────────────────────────────

    def _require_connected(self):
        if self._bot is None:
            raise RuntimeError("Not connected. Call await bot.connect() first.")

    # ── CONTEXT MANAGER SUPPORT ───────────────────────────────────

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *_):
        await self.disconnect()






def silence_gibberish():
    """Suppresses aiohttp unclosed session warnings and resource noise."""
    warnings.filterwarnings("ignore", category=ResourceWarning)
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    logging.getLogger("aiohttp").setLevel(logging.CRITICAL)
    logging.getLogger("asyncio").setLevel(logging.CRITICAL)
