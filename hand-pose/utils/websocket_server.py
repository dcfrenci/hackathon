"""
WebSocket server for broadcasting gesture events from the DepthAI pipeline
to the web app.

The server runs in a dedicated asyncio thread so it never blocks the pipeline.
Any thread (e.g. HostNode.process()) can call send_event() safely.

Public API:
    start(host, port)       → start server in a daemon thread
    send_event(event: dict) → thread-safe broadcast to all connected clients
    stop()                  → graceful shutdown
    client_count() -> int   → number of currently connected clients
    on_message(handler)     → register a callback for incoming JSON messages
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Callable, Optional

try:
    import websockets
    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False


# ── Singleton state ───────────────────────────────────────────────────────────

_loop: Optional[asyncio.AbstractEventLoop] = None
_thread: Optional[threading.Thread] = None
_clients: set = set()
_started: bool = False
_lock = threading.Lock()
_message_handler: Optional[Callable[[dict], None]] = None


# ── Internals ─────────────────────────────────────────────────────────────────

async def _handler(websocket) -> None:
    """Register a client, keep it alive, and forward incoming JSON messages
    to the callback registered with on_message()."""
    _clients.add(websocket)
    print(f"[ws] client connected ({len(_clients)} total) from {websocket.remote_address}")
    try:
        async for raw in websocket:
            if _message_handler is None:
                continue
            try:
                msg = json.loads(raw)
            except (TypeError, ValueError):
                continue
            try:
                _message_handler(msg)
            except Exception as exc:  # noqa: BLE001
                print(f"[ws] message handler error: {exc}")
    finally:
        _clients.discard(websocket)
        print(f"[ws] client disconnected ({len(_clients)} remaining)")


def _run_loop(host: str, port: int, ready: threading.Event) -> None:
    """WebSocket thread entry point: create the asyncio loop and serve."""
    global _loop
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)

    async def _serve() -> None:
        async with websockets.serve(_handler, host, port):
            print(f"[ws] listening on ws://{host}:{port}")
            ready.set()
            await asyncio.Future()  # run until the loop is stopped

    try:
        _loop.run_until_complete(_serve())
    except asyncio.CancelledError:
        pass
    finally:
        _loop.close()


async def _broadcast(payload: str) -> None:
    """Send the same payload to all connected clients."""
    if not _clients:
        return
    await asyncio.gather(
        *[ws.send(payload) for ws in list(_clients)],
        return_exceptions=True,
    )


# ── Public API ────────────────────────────────────────────────────────────────

def start(host: str = "0.0.0.0", port: int = 8765, timeout: float = 3.0) -> bool:
    """Start the WebSocket server in a background daemon thread.
    Idempotent: repeated calls are no-ops. Returns True on success."""
    global _thread, _started

    if not _WS_AVAILABLE:
        print("[ws] WARNING: 'websockets' package not installed — run: pip install websockets")
        return False

    with _lock:
        if _started:
            return True

        ready = threading.Event()
        _thread = threading.Thread(
            target=_run_loop,
            args=(host, port, ready),
            daemon=True,
            name="GestureWebSocket",
        )
        _thread.start()

        if not ready.wait(timeout=timeout):
            print(f"[ws] ERROR: server did not start within {timeout}s")
            return False

        _started = True
        return True


def send_event(event: dict) -> None:
    """Broadcast a JSON event to all connected clients.
    Thread-safe; no-op if the server is not running or no clients connected."""
    if not _started or _loop is None or not _clients:
        return
    asyncio.run_coroutine_threadsafe(_broadcast(json.dumps(event)), _loop)


def stop() -> None:
    """Gracefully shut down the WebSocket server."""
    global _started
    if not _started or _loop is None:
        return

    async def _shutdown() -> None:
        for ws in list(_clients):
            await ws.close()
        for task in asyncio.all_tasks(_loop):
            task.cancel()

    asyncio.run_coroutine_threadsafe(_shutdown(), _loop)
    _started = False


def client_count() -> int:
    """Number of currently connected web clients."""
    return len(_clients)


def on_message(handler: Callable[[dict], None]) -> None:
    """Register a callback for incoming JSON messages from clients.
    The callback is invoked from the asyncio thread: synchronise on the caller
    side if shared state with the pipeline is accessed."""
    global _message_handler
    _message_handler = handler
