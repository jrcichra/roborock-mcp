"""Tests for the lazy reconnect path in server.py (_ensure_connected).

The real session needs a live device, an MQTT channel and network, so these
tests swap in a stand-in exposing only what _ensure_connected touches: `device`,
`_connect_lock` and `connect()`.
"""

import asyncio

import pytest

import server
from server import _ensure_connected, _not_connected_error, AuthRequired


class _Disconnected:
    is_connected = False


class _Connected:
    is_connected = True


class FakeSession:
    """Stand-in for the module-level RoborockSession."""

    def __init__(self, *, device=None, connect=None):
        self.device = device
        self._connect_lock = asyncio.Lock()
        self._connect_impl = connect

    async def connect(self):
        if self._connect_impl is not None:
            await self._connect_impl()
            # A successful real connect() leaves a live device on the session.
            self.device = _Connected()


def install(monkeypatch, session: FakeSession) -> FakeSession:
    """Point server.py's module-level `session` at a fake one."""
    monkeypatch.setattr(server, "session", session)
    return session


def counting_connect():
    """A connect() stand-in plus the log it appends to on each real attempt."""
    calls: list[str] = []

    async def connect():
        # Await before recording, so several callers can arrive together and
        # only one of them should get through the lock.
        await asyncio.sleep(0.01)
        calls.append("connect")

    return calls, connect


class TestEnsureConnected:
    @pytest.mark.asyncio
    async def test_connected_device_never_reconnects(self, monkeypatch):
        install(monkeypatch, FakeSession(device=_Connected()))
        assert await _ensure_connected() is None

    @pytest.mark.asyncio
    async def test_disconnected_device_reconnects_and_succeeds(self, monkeypatch):
        calls, connect = counting_connect()
        session = install(monkeypatch, FakeSession(device=_Disconnected(), connect=connect))
        assert await _ensure_connected() is None
        assert calls == ["connect"]

    @pytest.mark.asyncio
    async def test_parallel_calls_share_one_reconnect(self, monkeypatch):
        # A client may fire several tools at once while disconnected (this is
        # what the MCP adapter does); each must not trigger its own connect().
        calls, connect = counting_connect()
        session = install(monkeypatch, FakeSession(device=_Disconnected(), connect=connect))
        results = await asyncio.gather(*(_ensure_connected() for _ in range(4)))
        assert results == [None, None, None, None]
        assert calls == ["connect"]

    @pytest.mark.asyncio
    async def test_missing_credentials_points_at_the_login_tools(self, monkeypatch):
        async def no_creds():
            raise FileNotFoundError("No cached credentials found.")

        session = install(monkeypatch, FakeSession(device=_Disconnected(), connect=no_creds))
        error = await _ensure_connected()
        assert error is not None
        assert "roborock_login_request_code" in error

    @pytest.mark.asyncio
    async def test_other_connect_failures_still_report(self, monkeypatch):
        async def failing():
            raise RuntimeError("Roborock rate-limited us; not retrying for another 59 min.")

        session = install(monkeypatch, FakeSession(device=_Disconnected(), connect=failing))
        error = await _ensure_connected()
        assert error is not None
        assert "59 min" in error

    @pytest.mark.asyncio
    async def test_needs_auth_points_at_the_login_tools(self, monkeypatch):
        async def failing():
            raise AuthRequired("No ROBOROCK_PASSWORD set for this account.")

        session = install(monkeypatch, FakeSession(device=_Disconnected(), connect=failing))
        error = await _ensure_connected()
        assert error is not None
        assert "roborock_login_request_code" in error

    @pytest.mark.asyncio
    async def test_connect_that_yields_no_device_still_errors(self, monkeypatch):
        # connect() normally either sets session.device or raises; if it ever
        # returns quietly, tools must not run against a missing device.
        session = install(monkeypatch, FakeSession(device=None, connect=None))
        error = await _ensure_connected()
        assert error is not None
        assert error.startswith("Error: Not connected to Roborock.")


class TestNotConnectedError:
    def test_message_names_the_login_tools(self):
        error = _not_connected_error("No usable cached credentials.")
        assert error.startswith("Error: Not connected to Roborock.")
        assert "roborock_login_request_code" in error
        assert "roborock_login_with_code" in error
