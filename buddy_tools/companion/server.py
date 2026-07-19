"""Localhost WebSocket fan-out server for companion status events (#115)."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

from buddy_tools.companion.config import CompanionBridgeConfig
from buddy_tools.companion.publisher import CompanionEventPublisher

logger = logging.getLogger(__name__)


class CompanionBridgeServer:
    """Daemon WebSocket server that drains the publisher queue to loopback clients."""

    def __init__(
        self,
        config: CompanionBridgeConfig,
        publisher: CompanionEventPublisher,
        *,
        stop_event: threading.Event,
    ) -> None:
        self.config = config
        self.publisher = publisher
        self.stop_event = stop_event
        self._thread: threading.Thread | None = None
        self._clients: set[Any] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: Any = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run,
            name="companion-bridge-ws",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Companion status bridge starting on %s",
            self.config.url,
        )

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run_server())
        except Exception:
            logger.exception("Companion status bridge server error")
        finally:
            self._loop.close()
            self._loop = None

    async def _run_server(self) -> None:
        import websockets

        self._server = await websockets.serve(
            self._handle_client,
            self.config.host,
            self.config.port,
        )
        logger.info("Companion status bridge ready at %s", self.config.url)

        sender = asyncio.create_task(self._send_loop())
        while not self.stop_event.is_set():
            await asyncio.sleep(0.1)

        sender.cancel()
        try:
            await sender
        except asyncio.CancelledError:
            pass

        for client in list(self._clients):
            try:
                await client.close()
            except Exception:
                pass
        self._clients.clear()

        self._server.close()
        await self._server.wait_closed()
        # Drain any leftover events so producers stay non-blocking after shutdown.
        self.publisher.drain()
        logger.info("Companion status bridge closed")

    async def _handle_client(self, websocket: Any) -> None:
        client_id = id(websocket)
        logger.info("Companion client %s connected", client_id)
        self._clients.add(websocket)
        try:
            for snapshot in self.publisher.snapshot_events():
                await websocket.send(json.dumps(snapshot))
            async for _message in websocket:
                # Status bridge is publish-only; ignore inbound frames.
                pass
        except Exception as exc:
            logger.debug("Companion client %s disconnected: %s", client_id, exc)
        finally:
            self._clients.discard(websocket)
            logger.info("Companion client %s disconnected", client_id)

    async def _send_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                event = self.publisher.try_get()
                if event is None:
                    await asyncio.sleep(0.02)
                    continue
                if not self._clients:
                    # Always drain — drop when nobody is listening.
                    continue
                payload = json.dumps(event)
                await asyncio.gather(
                    *[client.send(payload) for client in list(self._clients)],
                    return_exceptions=True,
                )
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Companion status bridge send loop error")
                await asyncio.sleep(0.05)
