#!/usr/bin/env python3
"""
Roborock MCP Server — Control your Roborock vacuum from Claude.

Exposes tools to start/stop cleaning, dock, get status, and clean specific rooms.
Requires running auth.py first to cache Roborock credentials.
"""

import asyncio
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

# Windows: MQTT requires SelectorEventLoop (ProactorEventLoop doesn't support add_reader/add_writer)
if sys.platform == "win32":
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from mcp.server.fastmcp import FastMCP

from roborock.data import DnDTimer
from roborock.data.containers import UserData
from roborock.devices.device import RoborockDevice
from roborock.devices.device_manager import DeviceManager, UserParams, create_device_manager
from roborock.roborock_typing import RoborockCommand

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).parent / ".cache"
CREDENTIALS_FILE = CACHE_DIR / "credentials.json"
# Only needed if your Roborock account has more than one vacuum — set these to
# pick which one this server controls. Leave unset and the server uses
# whichever single device it finds (or the first one, if there are several).
DEVICE_NICKNAME = os.environ.get("ROBOROCK_DEVICE_NAME", "")
TARGET_MODEL = os.environ.get("ROBOROCK_DEVICE_MODEL", "")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_cached_credentials() -> dict:
    """Load cached credentials from disk."""
    if not CREDENTIALS_FILE.exists():
        raise FileNotFoundError(
            "No cached credentials found. Run 'python auth.py' first to authenticate."
        )
    return json.loads(CREDENTIALS_FILE.read_text())


# ---------------------------------------------------------------------------
# Session — manages the DeviceManager and target device
# ---------------------------------------------------------------------------

class RoborockSession:
    """Holds the DeviceManager and target device reference."""

    def __init__(self):
        self.manager: Optional[DeviceManager] = None
        self.device: Optional[RoborockDevice] = None
        self._rooms: Optional[dict[int, str]] = None  # segment_id -> name
        self._home_data_raw: Optional[dict] = None

    async def connect(self):
        """Authenticate and connect to the target device."""
        creds = _load_cached_credentials()
        self._home_data_raw = creds.get("home_data", {})

        email = creds.get("email") or os.environ.get("ROBOROCK_EMAIL", "")
        user_data = UserData.from_dict(creds["user_data"])
        base_url = creds.get("base_url")

        user_params = UserParams(username=email, user_data=user_data, base_url=base_url)
        # create_device_manager() already performs discovery AND connects each
        # device (cloud MQTT + local LAN where available), including a background
        # reconnect loop. Do NOT call discover_devices() again here — it re-fetches
        # home data over HTTP — and do NOT call device.connect() on the result:
        # both are redundant and the extra connect tears down healthy connections.
        self.manager = await create_device_manager(user_params)

        devices = await self.manager.get_devices()
        if not devices:
            raise RuntimeError("No devices discovered. Check your Roborock account.")

        if DEVICE_NICKNAME or TARGET_MODEL:
            for dev in devices:
                model = dev.product.model or "" if dev.product else ""
                name = dev.name or ""
                if (TARGET_MODEL and model == TARGET_MODEL) or (
                    DEVICE_NICKNAME and name.lower() == DEVICE_NICKNAME.lower()
                ):
                    self.device = dev
                    break

        if self.device is None:
            self.device = devices[0]  # only/first device
            if len(devices) > 1:
                logger.warning(
                    "Multiple devices found and none matched ROBOROCK_DEVICE_NAME/"
                    "ROBOROCK_DEVICE_MODEL; defaulting to %r. Set one of those env "
                    "vars to pick a specific vacuum.",
                    self.device.name,
                )

        logger.info("Target device: %s (duid: %s)", self.device.name, self.device.duid)

    async def close(self):
        """Clean up connections."""
        if self.device and self.device.is_connected:
            try:
                await self.device.close()
            except Exception:
                pass
        if self.manager:
            try:
                await self.manager.close()
            except Exception:
                pass


session = RoborockSession()


@asynccontextmanager
async def roborock_lifespan(server: FastMCP):
    """Connect to Roborock on server start, disconnect on shutdown."""
    try:
        await session.connect()
        logger.info("Connected to %s", session.device.name if session.device else "device")
    except FileNotFoundError as e:
        logger.warning("Auth required: %s", e)
    except Exception as e:
        logger.error("Failed to connect: %s", e, exc_info=True)
    yield
    await session.close()


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP("roborock_mcp", lifespan=roborock_lifespan)


def _device_display_name() -> str:
    """Friendly name for messages: the device's actual name, or a generic fallback."""
    if session.device is not None and session.device.name:
        return session.device.name
    return DEVICE_NICKNAME or "Vacuum"


def _check_connected() -> str | None:
    """Return an error string if not connected, else None."""
    if session.device is None or not session.device.is_connected:
        return (
            "Error: Not connected to Roborock. "
            "Run 'python auth.py' first to authenticate, then restart the server."
        )
    return None


def _send(command: RoborockCommand, params: Any = None):
    """Send a command via the device's v1 command trait."""
    return session.device.v1_properties.command.send(command, params)


# ---------------------------------------------------------------------------
# Fan power / water level helpers
# ---------------------------------------------------------------------------

def _resolve_mode_code(value: str | int, options) -> int:
    """Resolve a friendly mode name ('max') or raw code (104) to a device code.

    `options` is the device's supported list of RoborockModeEnum members
    (e.g. status.fan_speed_options / status.water_mode_options).
    """
    if isinstance(value, int):
        if any(opt.code == value for opt in options):
            return value
        raise ValueError(f"Code {value} not supported by this device. Valid: "
                         f"{[(o.name, o.code) for o in options]}")
    needle = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    for opt in options:
        if opt.name.lower() == needle or opt.value == needle:
            return opt.code
    raise ValueError(f"Unknown mode '{value}'. Valid: {[o.name for o in options]}")


async def _set_motor_modes(*, fan_power: int | None = None, water_box_mode: int | None = None) -> dict:
    """Set fan power and/or water level via SET_CLEAN_MOTOR_MODE.

    Unspecified values are kept at their current setting, mirroring how the
    official app sends the full triple.
    """
    status = session.device.v1_properties.status
    await status.refresh()
    cur_fan = fan_power if fan_power is not None else (status.fan_power or 102)
    cur_water = water_box_mode if water_box_mode is not None else (status.water_box_mode or 200)
    cur_mop = getattr(status, "mop_mode", None)
    params: dict[str, int] = {"fan_power": cur_fan, "water_box_mode": cur_water}
    if cur_mop is not None:
        params["mop_mode"] = cur_mop
    await _send(RoborockCommand.SET_CLEAN_MOTOR_MODE, [params])
    await status.refresh()
    return {"fan_power": status.fan_power, "water_box_mode": status.water_box_mode, "mop_mode": getattr(status, "mop_mode", None)}


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool(
    name="roborock_get_status",
    annotations={
        "title": "Get Vacuum Status",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def roborock_get_status() -> str:
    """Get the vacuum's current status including battery level, cleaning state, and other info.

    Returns:
        str: A formatted status report including battery percentage, current state
             (idle, cleaning, charging, etc.), clean time, clean area, and error info.
    """
    if err := _check_connected():
        return err

    try:
        status = session.device.v1_properties.status
        await status.refresh()

        info = {
            "name": _device_display_name(),
            "model": session.device.product.model or "unknown",
            "battery": f"{status.battery}%" if status.battery is not None else "unknown",
            "state": status.state_name or str(status.state),
            "clean_time": f"{status.clean_time // 60}m {status.clean_time % 60}s" if status.clean_time else "0s",
            "clean_area": f"{status.square_meter_clean_area:.1f} m²" if status.square_meter_clean_area is not None else "0 m²",
            "fan_speed": status.fan_speed_name or str(status.fan_power),
            "water_box": "attached" if status.water_box_status else "not attached",
            "mop_mode": status.mop_route_name or str(status.mop_mode),
            "error": status.error_code_name or "none",
        }

        lines = [f"# {_device_display_name()} Status", ""]
        for key, value in info.items():
            lines.append(f"- **{key.replace('_', ' ').title()}**: {value}")

        return "\n".join(lines)

    except Exception as e:
        return f"Error getting status: {e}"


@mcp.tool(
    name="roborock_start_cleaning",
    annotations={
        "title": "Start Full Clean",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def roborock_start_cleaning() -> str:
    """Start a full cleaning cycle. The vacuum will clean all reachable areas.

    Returns:
        str: Confirmation that cleaning has started, or an error message.
    """
    if err := _check_connected():
        return err

    try:
        await _send(RoborockCommand.APP_START)
        return f"{_device_display_name()} has started cleaning."
    except Exception as e:
        return f"Error starting clean: {e}"


@mcp.tool(
    name="roborock_stop_cleaning",
    annotations={
        "title": "Stop Cleaning",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def roborock_stop_cleaning() -> str:
    """Stop the current cleaning cycle. The vacuum will stop where it is.

    Returns:
        str: Confirmation that cleaning has stopped, or an error message.
    """
    if err := _check_connected():
        return err

    try:
        await _send(RoborockCommand.APP_STOP)
        return f"{_device_display_name()} has stopped cleaning."
    except Exception as e:
        return f"Error stopping clean: {e}"


@mcp.tool(
    name="roborock_pause_cleaning",
    annotations={
        "title": "Pause Cleaning",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def roborock_pause_cleaning() -> str:
    """Pause the current cleaning cycle. The vacuum will pause in place and can be resumed.

    Returns:
        str: Confirmation that cleaning has been paused, or an error message.
    """
    if err := _check_connected():
        return err

    try:
        await _send(RoborockCommand.APP_PAUSE)
        return f"{_device_display_name()} has paused cleaning."
    except Exception as e:
        return f"Error pausing clean: {e}"


@mcp.tool(
    name="roborock_return_to_dock",
    annotations={
        "title": "Return to Dock",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def roborock_return_to_dock() -> str:
    """Send the vacuum back to its charging dock.

    Returns:
        str: Confirmation that the vacuum is heading home, or an error message.
    """
    if err := _check_connected():
        return err

    try:
        await _send(RoborockCommand.APP_CHARGE)
        return f"{_device_display_name()} is returning to the dock."
    except Exception as e:
        return f"Error sending to dock: {e}"


@mcp.tool(
    name="roborock_get_rooms",
    annotations={
        "title": "List Rooms",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def roborock_get_rooms() -> str:
    """List all rooms/segments the vacuum knows about from its map.

    Returns:
        str: A list of rooms with their segment IDs, or an error message.
             Use the room names with roborock_clean_room to clean specific rooms.
    """
    if err := _check_connected():
        return err

    try:
        rooms_trait = session.device.v1_properties.rooms
        await rooms_trait.refresh()
        room_map = rooms_trait.room_map

        if not room_map:
            # Fallback: try raw command + home data
            return await _get_rooms_fallback()

        lines = ["# Rooms", ""]
        rooms_found = {}
        for seg_id, mapping in room_map.items():
            name = mapping.name if hasattr(mapping, "name") else f"Room {seg_id}"
            rooms_found[seg_id] = name
            lines.append(f"- **{name}** (segment {seg_id})")

        session._rooms = rooms_found
        return "\n".join(lines)

    except Exception:
        # Fallback to raw command approach
        try:
            return await _get_rooms_fallback()
        except Exception as e:
            return f"Error getting rooms: {e}"


async def _get_rooms_fallback() -> str:
    """Get rooms via raw GET_ROOM_MAPPING command + home data names."""
    room_mapping = await _send(RoborockCommand.GET_ROOM_MAPPING)

    if not room_mapping:
        return "No room mapping found. The vacuum may need to complete a mapping run first."

    # Resolve names from cached home data
    home_rooms = {}
    if session._home_data_raw:
        for room in session._home_data_raw.get("rooms", []):
            room_id = room.get("id") or room.get("globalId")
            room_name = room.get("name", f"Room {room_id}")
            if room_id:
                home_rooms[str(room_id)] = room_name

    lines = ["# Rooms", ""]
    rooms_found = {}
    for mapping in room_mapping:
        if isinstance(mapping, (list, tuple)) and len(mapping) >= 2:
            segment_id = mapping[0]
            iot_id = str(mapping[1])
            name = home_rooms.get(iot_id, f"Room {segment_id}")
            rooms_found[segment_id] = name
            lines.append(f"- **{name}** (segment {segment_id})")

    if not rooms_found:
        return "Room mapping returned but could not be parsed. Raw: " + json.dumps(room_mapping, default=str)

    session._rooms = rooms_found
    return "\n".join(lines)


@mcp.tool(
    name="roborock_clean_room",
    annotations={
        "title": "Clean Specific Room",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def roborock_clean_room(room_name: str) -> str:
    """Clean a specific room by name. Use roborock_get_rooms to see available rooms first.

    Args:
        room_name: The name of the room to clean (e.g., "Living Room", "Kitchen").
                   Case-insensitive partial matching is supported.

    Returns:
        str: Confirmation that room cleaning has started, or an error message.
    """
    if err := _check_connected():
        return err

    try:
        # Get room mapping if we don't have it cached
        if not session._rooms:
            await roborock_get_rooms()

        if not session._rooms:
            return "Error: No rooms found. The vacuum may need to complete a mapping run first."

        # Find room by name (case-insensitive partial match)
        search = room_name.lower().strip()
        matched_segments = []
        matched_names = []

        for seg_id, name in session._rooms.items():
            if search in name.lower() or name.lower() in search:
                matched_segments.append(int(seg_id))
                matched_names.append(name)

        if not matched_segments:
            available = ", ".join(session._rooms.values())
            return f"Error: No room matching '{room_name}' found. Available rooms: {available}"

        # Start segment cleaning
        await _send(
            RoborockCommand.APP_SEGMENT_CLEAN,
            [{"segments": matched_segments, "repeat": 1}],
        )

        rooms_str = ", ".join(matched_names)
        return f"{_device_display_name()} is now cleaning: {rooms_str}"

    except Exception as e:
        return f"Error starting room clean: {e}"


@mcp.tool(
    name="roborock_locate",
    annotations={
        "title": "Find Vacuum",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def roborock_locate() -> str:
    """Make the vacuum play a sound so you can find it.

    Returns:
        str: Confirmation that the locate sound is playing, or an error message.
    """
    if err := _check_connected():
        return err

    try:
        await _send(RoborockCommand.FIND_ME)
        return f"{_device_display_name()} is playing a sound so you can find him!"
    except Exception as e:
        return f"Error locating: {e}"


@mcp.tool(
    name="roborock_set_fan_power",
    annotations={
        "title": "Set Fan Power (Suction)",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def roborock_set_fan_power(level: str) -> str:
    """Set the fan power / suction level. Works live, even mid-clean.

    Args:
        level: One of the device's supported modes, e.g. "quiet", "balanced",
               "turbo", "max", "max_plus", "smart_mode", or a raw code like 104.
               Call roborock_get_status first to see the current level.

    Returns:
        str: Confirmation with the new fan level, or an error message listing
             valid options.
    """
    if err := _check_connected():
        return err

    try:
        options = session.device.v1_properties.status.fan_speed_options
        code = _resolve_mode_code(level, options)
        result = await _set_motor_modes(fan_power=code)
        return f"{_device_display_name()} fan power set: {result['fan_power']} ({result})"
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error setting fan power: {e}"


@mcp.tool(
    name="roborock_set_water_level",
    annotations={
        "title": "Set Mop Water Level",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def roborock_set_water_level(level: str) -> str:
    """Set the mop water flow level. Works live, even mid-clean.

    Args:
        level: One of the device's supported water modes, e.g. "off", "low",
               "medium", "high", "smart_mode", or a raw code like 203.

    Returns:
        str: Confirmation with the new water level, or an error message listing
             valid options.
    """
    if err := _check_connected():
        return err

    try:
        options = session.device.v1_properties.status.water_mode_options
        code = _resolve_mode_code(level, options)
        result = await _set_motor_modes(water_box_mode=code)
        return f"{_device_display_name()} water level set: {result['water_box_mode']} ({result})"
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error setting water level: {e}"


def _seconds_to_days(seconds: int | None) -> str:
    """Format a seconds duration as a friendly day count, treating negative as 'overdue'."""
    if seconds is None:
        return "unknown"
    days = seconds / 86400
    if days < 0:
        return f"overdue by {abs(days):.1f} days"
    return f"{days:.1f} days left"


@mcp.tool(
    name="roborock_get_consumables",
    annotations={
        "title": "Get Consumable Status",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def roborock_get_consumables() -> str:
    """Get remaining life on replaceable parts (filter, brushes, sensors, etc.).

    Returns:
        str: A formatted report of remaining life for each consumable this
             device reports, or an error message.
    """
    if err := _check_connected():
        return err

    try:
        consumables = session.device.v1_properties.consumables
        await consumables.refresh()

        fields = [
            ("Main brush", consumables.main_brush_time_left),
            ("Side brush", consumables.side_brush_time_left),
            ("Filter", consumables.filter_time_left),
            ("Sensors", consumables.sensor_time_left),
            ("Strainer", consumables.strainer_time_left),
            ("Dust collection bag", consumables.dust_collection_time_left),
            ("Cleaning brush (dock)", consumables.cleaning_brush_time_left),
            ("Mop roller", consumables.mop_roller_time_left),
        ]
        lines = [f"# {_device_display_name()} Consumables", ""]
        for label, seconds_left in fields:
            if seconds_left is None:
                continue  # not supported by this device
            lines.append(f"- **{label}**: {_seconds_to_days(seconds_left)}")

        if len(lines) == 2:
            return "No consumable data reported by this device."
        return "\n".join(lines)
    except Exception as e:
        return f"Error getting consumables: {e}"


@mcp.tool(
    name="roborock_get_clean_history",
    annotations={
        "title": "Get Cleaning History",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def roborock_get_clean_history() -> str:
    """Get lifetime cleaning stats and details of the most recent clean.

    Returns:
        str: A formatted report of total clean time/area/count and the last
             clean's duration and area, or an error message.
    """
    if err := _check_connected():
        return err

    try:
        summary = session.device.v1_properties.clean_summary
        await summary.refresh()

        lines = [f"# {_device_display_name()} Cleaning History", "", "## Lifetime totals"]
        total_time = summary.clean_time or 0
        lines.append(f"- **Total clean time**: {total_time // 3600}h {(total_time % 3600) // 60}m")
        lines.append(
            f"- **Total area cleaned**: {summary.square_meter_clean_area:.1f} m²"
            if summary.square_meter_clean_area is not None
            else "- **Total area cleaned**: unknown"
        )
        lines.append(f"- **Total clean count**: {summary.clean_count or 0}")

        record = summary.last_clean_record
        if record is not None:
            lines.append("")
            lines.append("## Last clean")
            duration = record.duration or 0
            lines.append(f"- **Started**: {record.begin_datetime or 'unknown'}")
            lines.append(f"- **Duration**: {duration // 60}m {duration % 60}s")
            lines.append(
                f"- **Area**: {record.square_meter_area:.1f} m²"
                if record.square_meter_area is not None
                else "- **Area**: unknown"
            )
            lines.append(f"- **Completed**: {'yes' if record.complete else 'no'}")

        return "\n".join(lines)
    except Exception as e:
        return f"Error getting clean history: {e}"


@mcp.tool(
    name="roborock_get_volume",
    annotations={
        "title": "Get Volume",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def roborock_get_volume() -> str:
    """Get the current sound volume level (0-100).

    Returns:
        str: The current volume level, or an error message.
    """
    if err := _check_connected():
        return err

    try:
        volume = session.device.v1_properties.sound_volume
        await volume.refresh()
        return f"{_device_display_name()}'s volume is {volume.volume}."
    except Exception as e:
        return f"Error getting volume: {e}"


@mcp.tool(
    name="roborock_set_volume",
    annotations={
        "title": "Set Volume",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def roborock_set_volume(level: int) -> str:
    """Set the sound volume level.

    Args:
        level: Volume level from 0 (mute) to 100 (loudest).

    Returns:
        str: Confirmation with the new volume, or an error message.
    """
    if err := _check_connected():
        return err
    if not 0 <= level <= 100:
        return "Error: level must be between 0 and 100."

    try:
        volume = session.device.v1_properties.sound_volume
        await volume.set_volume(level)
        return f"{_device_display_name()}'s volume set to {level}."
    except Exception as e:
        return f"Error setting volume: {e}"


@mcp.tool(
    name="roborock_set_child_lock",
    annotations={
        "title": "Set Child Lock",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def roborock_set_child_lock(enabled: bool) -> str:
    """Enable or disable the child lock, which locks the vacuum's physical buttons.

    Args:
        enabled: True to lock the physical buttons, False to unlock them.

    Returns:
        str: Confirmation of the new state, or an error message.
    """
    if err := _check_connected():
        return err

    child_lock = session.device.v1_properties.child_lock
    if child_lock is None:
        return "Error: This device does not support child lock."

    try:
        if enabled:
            await child_lock.enable()
        else:
            await child_lock.disable()
        return f"{_device_display_name()}'s child lock is now {'enabled' if enabled else 'disabled'}."
    except Exception as e:
        return f"Error setting child lock: {e}"


@mcp.tool(
    name="roborock_set_dnd",
    annotations={
        "title": "Set Do Not Disturb",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def roborock_set_dnd(enabled: bool, start_time: str = "22:00", end_time: str = "08:00") -> str:
    """Enable or disable Do Not Disturb, which silences the vacuum during the given hours.

    Args:
        enabled: True to enable Do Not Disturb, False to turn it off.
        start_time: DND start time as "HH:MM" (24-hour), used only when enabling.
        end_time: DND end time as "HH:MM" (24-hour), used only when enabling.

    Returns:
        str: Confirmation of the new state, or an error message.
    """
    if err := _check_connected():
        return err

    try:
        dnd = session.device.v1_properties.dnd
        if not enabled:
            await dnd.clear_dnd_timer()
            return f"{_device_display_name()}'s Do Not Disturb is now disabled."

        start_hour, start_minute = (int(p) for p in start_time.split(":"))
        end_hour, end_minute = (int(p) for p in end_time.split(":"))
        await dnd.set_dnd_timer(
            DnDTimer(
                start_hour=start_hour,
                start_minute=start_minute,
                end_hour=end_hour,
                end_minute=end_minute,
                enabled=1,
            )
        )
        return f"{_device_display_name()}'s Do Not Disturb is now enabled from {start_time} to {end_time}."
    except ValueError:
        return "Error: start_time/end_time must be in 'HH:MM' 24-hour format."
    except Exception as e:
        return f"Error setting Do Not Disturb: {e}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
