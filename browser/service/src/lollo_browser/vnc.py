"""The live view's relay (design decision 8): the person's noVNC client speaks
RFB over the page's WebSocket; this relays it to the engine's Xvnc.

- VNC authentication ends here: the relay answers the engine's challenge with
  the password (the engine secret's first 8 characters) and offers the
  person's browser security type None, so the password never reaches a page.
- The client is held to a fixed set of messages: SetPixelFormat,
  SetEncodings (filtered to encodings that add no client message),
  FramebufferUpdateRequest, KeyEvent, PointerEvent and ClientCutText.
  Anything else closes the connection.
- Key, pointer and clipboard messages are dropped unless `allow_input()` is
  true, which it is only while the person has taken over the tab the
  connection was opened for and that tab is the one in front of the display
  (BrowserService.controls). Watching is read-only.
- Shared: every viewer's ClientInit asks to share the display, so one viewer
  never disconnects another."""

from __future__ import annotations

import logging
import struct
from collections.abc import Awaitable, Callable

import anyio
from anyio.abc import ByteStream
from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
from cryptography.hazmat.primitives.ciphers import Cipher, modes

log = logging.getLogger("lollo_browser.vnc")

VERSION = b"RFB 003.008\n"
SEC_NONE, SEC_VNC = 1, 2
MAX_CUT_TEXT = 1024 * 1024
MAX_ENCODINGS = 64

# Encodings that only change what the server sends. Left out on purpose:
# QEMU extended keys, ExtendedDesktopSize, xvp, fence, continuous updates,
# extended clipboard and extended mouse buttons, which make the client send
# messages outside the set above.
_ENCODINGS = {0, 1, 2, 5, 6, 7, 16, 21, 50, -260, -223, -224, -239, -307, 0x574D5664}
_ENCODINGS |= set(range(-32, -22)) | set(range(-256, -246))

CLIENT_SET_PIXEL_FORMAT, CLIENT_SET_ENCODINGS, CLIENT_UPDATE_REQUEST = 0, 2, 3
CLIENT_KEY, CLIENT_POINTER, CLIENT_CUT_TEXT = 4, 5, 6


class RelayError(Exception):
    pass


def vnc_response(password: str, challenge: bytes) -> bytes:
    """RFB's VNC authentication: DES of the challenge with the password (at
    most 8 bytes, zero-padded) as key, each key byte's bits reversed."""
    key = password.encode("latin-1", "replace")[:8].ljust(8, b"\0")
    key = bytes(int(f"{b:08b}"[::-1], 2) for b in key)
    # Single DES, as TripleDES with the key three times.
    encryptor = Cipher(TripleDES(key * 3), modes.ECB()).encryptor()
    return encryptor.update(challenge[:16]) + encryptor.finalize()


class Reader:
    """Exact reads from a stream of chunks."""

    def __init__(self, receive: Callable[[], Awaitable[bytes]]) -> None:
        self._receive = receive
        self._buffer = bytearray()

    async def exactly(self, n: int) -> bytes:
        while len(self._buffer) < n:
            chunk = await self._receive()
            if not chunk:
                raise RelayError("connection closed")
            self._buffer += chunk
        data = bytes(self._buffer[:n])
        del self._buffer[:n]
        return data


async def server_handshake(stream: ByteStream, password: str) -> Reader:
    """Up to SecurityResult with the engine. Returns the reader for the rest."""
    reader = Reader(stream.receive)
    version = await reader.exactly(12)
    if not version.startswith(b"RFB 003.") or not version.endswith(b"\n"):
        raise RelayError("not a VNC server")
    await stream.send(VERSION)
    count = (await reader.exactly(1))[0]
    if count == 0:
        raise RelayError("the VNC server refused the connection")
    types = await reader.exactly(count)
    if SEC_VNC not in types:
        raise RelayError("the VNC server does not offer VNC authentication")
    await stream.send(bytes([SEC_VNC]))
    challenge = await reader.exactly(16)
    await stream.send(vnc_response(password, challenge))
    (result,) = struct.unpack(">I", await reader.exactly(4))
    if result != 0:
        raise RelayError("VNC authentication failed")
    return reader


def filter_encodings(encodings: list[int]) -> list[int]:
    return [e for e in encodings if e in _ENCODINGS]


class ClientFilter:
    """Parses the client's messages after the handshake and decides what reaches
    the engine. `allow_input` is asked for every input message."""

    def __init__(self, reader: Reader, allow_input: Callable[[], bool]) -> None:
        self._reader = reader
        self._allow = allow_input
        self.dropped = 0

    async def next_message(self) -> bytes | None:
        """The bytes to forward for the next message (None: dropped)."""
        r = self._reader
        kind = (await r.exactly(1))[0]
        if kind == CLIENT_SET_PIXEL_FORMAT:
            return bytes([kind]) + await r.exactly(19)
        if kind == CLIENT_SET_ENCODINGS:
            head = await r.exactly(3)
            (count,) = struct.unpack(">H", head[1:])
            if count > MAX_ENCODINGS:
                raise RelayError("too many encodings")
            raw = await r.exactly(4 * count)
            kept = filter_encodings(list(struct.unpack(f">{count}i", raw)))
            return bytes([kind, 0]) + struct.pack(f">H{len(kept)}i", len(kept), *kept)
        if kind == CLIENT_UPDATE_REQUEST:
            return bytes([kind]) + await r.exactly(9)
        if kind == CLIENT_KEY:
            data = bytes([kind]) + await r.exactly(7)
            return self._input(data)
        if kind == CLIENT_POINTER:
            data = bytes([kind]) + await r.exactly(5)
            return self._input(data)
        if kind == CLIENT_CUT_TEXT:
            head = await r.exactly(7)
            (length,) = struct.unpack(">i", head[3:])
            if length < 0 or length > MAX_CUT_TEXT:
                raise RelayError("clipboard message refused")
            data = bytes([kind]) + head + await r.exactly(length)
            return self._input(data)
        raise RelayError(f"client message type {kind} is not relayed")

    def _input(self, data: bytes) -> bytes | None:
        if self._allow():
            return data
        self.dropped += 1
        return None


async def relay(
    ws_receive: Callable[[], Awaitable[bytes]],
    ws_send: Callable[[bytes], Awaitable[None]],
    connect: Callable[[], Awaitable[ByteStream]],
    password: str,
    allow_input: Callable[[], bool],
) -> None:
    """Runs one viewer's session until either side closes."""
    stream = await connect()
    async with stream:
        server = await server_handshake(stream, password)
        client = Reader(ws_receive)
        # The person's side: no authentication, the relay already did it.
        await ws_send(VERSION)
        version = await client.exactly(12)
        if not version.startswith(b"RFB 003."):
            raise RelayError("not a VNC client")
        await ws_send(bytes([1, SEC_NONE]))
        if (await client.exactly(1))[0] != SEC_NONE:
            raise RelayError("the client chose another security type")
        await ws_send(struct.pack(">I", 0))
        await client.exactly(1)  # the client's ClientInit; the display is always shared
        await stream.send(b"\x01")
        filt = ClientFilter(client, allow_input)

        async def engine_to_person() -> None:
            # Bytes the server sent after SecurityResult that the reader holds.
            pending = await _drain(server)
            if pending:
                await ws_send(pending)
            try:
                while True:
                    await ws_send(await stream.receive())
            except anyio.EndOfStream, anyio.ClosedResourceError, anyio.BrokenResourceError, OSError:
                pass
            except Exception as e:  # the person's side went away
                log.info("live view: %s", type(e).__name__)
            tg.cancel_scope.cancel()

        refused: list[RelayError] = []

        async def person_to_engine() -> None:
            try:
                while True:
                    message = await filt.next_message()
                    if message is not None:
                        await stream.send(message)
            except RelayError as e:
                refused.append(e)
            except anyio.EndOfStream, anyio.ClosedResourceError, anyio.BrokenResourceError, OSError:
                pass
            tg.cancel_scope.cancel()

        async with anyio.create_task_group() as tg:
            tg.start_soon(engine_to_person)
            tg.start_soon(person_to_engine)
        if refused:
            raise refused[0]


async def _drain(reader: Reader) -> bytes:
    data = bytes(reader._buffer)
    reader._buffer.clear()
    return data
