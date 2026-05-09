"""
WebSocket server per trasmettere eventi gesture dalla pipeline DepthAI alla web app.

Il server gira in un thread asyncio dedicato (così non blocca la pipeline).
Da qualsiasi thread (es. il process() del HostNode) puoi chiamare `send_event()`
per fare il broadcast a tutti i client connessi.

API pubblica:
    start(host="0.0.0.0", port=8765)   → avvia il server in un thread daemon
    send_event(event: dict)            → broadcast thread-safe agli iscritti
    stop()                             → ferma il server (chiamato a fine pipeline)
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Optional

try:
    import websockets
    from websockets.server import WebSocketServerProtocol  # type: ignore
    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False


# ── Stato globale (singleton) ─────────────────────────────────────────────────

_loop: Optional[asyncio.AbstractEventLoop] = None
_thread: Optional[threading.Thread] = None
_clients: set = set()
_started: bool = False
_lock = threading.Lock()
_message_handler = None  # callback(dict) — chiamato sui messaggi in arrivo


# ── Internals ─────────────────────────────────────────────────────────────────

async def _handler(websocket):
    """Registra il client, tienilo connesso e inoltra i messaggi JSON in arrivo
    al callback registrato con `on_message`."""
    _clients.add(websocket)
    print(f"[ws] client connesso ({len(_clients)} totali) "
          f"da {websocket.remote_address}")
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
                print(f"[ws] errore nel message handler: {exc}")
    finally:
        _clients.discard(websocket)
        print(f"[ws] client disconnesso ({len(_clients)} rimanenti)")


def _run_loop(host: str, port: int, ready: threading.Event):
    """Entry point del thread WebSocket: crea il loop asyncio e serve."""
    global _loop
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)

    async def _serve():
        async with websockets.serve(_handler, host, port):
            print(f"[ws] server in ascolto su ws://{host}:{port}")
            ready.set()
            # Tieni il server attivo finché _loop non viene stoppato
            await asyncio.Future()

    try:
        _loop.run_until_complete(_serve())
    except asyncio.CancelledError:
        pass
    finally:
        _loop.close()


async def _broadcast(payload: str):
    """Manda lo stesso payload a tutti i client connessi."""
    if not _clients:
        return
    await asyncio.gather(
        *[ws.send(payload) for ws in list(_clients)],
        return_exceptions=True,
    )


# ── API pubblica ──────────────────────────────────────────────────────────────

def start(host: str = "0.0.0.0", port: int = 8765, timeout: float = 3.0) -> bool:
    """
    Avvia il server WebSocket in background.
    Idempotente: chiamate ripetute non fanno nulla.
    Ritorna True se il server è partito correttamente.
    """
    global _thread, _started

    if not _WS_AVAILABLE:
        print("[ws] WARNING: pacchetto 'websockets' non installato — "
              "esegui: pip install websockets")
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
            print(f"[ws] ERROR: il server non è partito entro {timeout}s")
            return False

        _started = True
        return True


def send_event(event: dict) -> None:
    """
    Manda un evento JSON a tutti i client connessi.
    Thread-safe: può essere chiamato da qualsiasi thread (es. HostNode.process()).
    Se il server non è partito o non ci sono client, è una no-op.
    """
    if not _started or _loop is None or not _clients:
        return

    payload = json.dumps(event)
    asyncio.run_coroutine_threadsafe(_broadcast(payload), _loop)


def stop() -> None:
    """Ferma il server WebSocket. Chiamabile a fine pipeline."""
    global _started
    if not _started or _loop is None:
        return

    async def _shutdown():
        for ws in list(_clients):
            await ws.close()
        # Annulla tutti i task pending in modo che il loop esca
        for task in asyncio.all_tasks(_loop):
            task.cancel()

    asyncio.run_coroutine_threadsafe(_shutdown(), _loop)
    _started = False


def client_count() -> int:
    """Numero di client web attualmente connessi."""
    return len(_clients)


def on_message(handler) -> None:
    """Registra un callback per i messaggi JSON in arrivo dai client.
    Il callback viene invocato dal thread del loop asyncio: se serve toccare
    stato condiviso con la pipeline, sincronizzare lato chiamante."""
    global _message_handler
    _message_handler = handler
