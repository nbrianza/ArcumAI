from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Dict, Any
from fastapi import WebSocket

# Using the server_log defined in src/logger.py
from src.logger import server_log as log
from src.bridge.pending_results import PendingResultStore
from src.bridge.loopback_queue import _EmailTask, _UserQueue
from src.bridge.loopback_processor import LoopbackProcessor

BRIDGE_TIMEOUT = float(os.getenv("BRIDGE_TIMEOUT", "60.0"))
LOOPBACK_TIMEOUT = float(os.getenv("LOOPBACK_TIMEOUT", "3600.0"))


class OutlookBridgeManager:
    def __init__(self):
        # Map: user_id -> active WebSocket
        self.active_connections: Dict[str, WebSocket] = {}

        # Map: request_id -> Future (the "semaphore" for waiting)
        self.pending_requests: Dict[str, asyncio.Future] = {}

        # Map: user_id -> client_type (set during client/identify handshake)
        self.client_types: Dict[str, str] = {}

        # Per-user priority queues — survive client disconnects
        self._user_queues: dict[str, _UserQueue] = {}

        # Global concurrency cap across all users
        from src.config import LOOPBACK_MAX_CONCURRENT, PENDING_RESULTS_DIR
        self._ai_semaphore = asyncio.Semaphore(LOOPBACK_MAX_CONCURRENT)

        # Directory for storing results when client is offline
        self._temp_dir = Path(PENDING_RESULTS_DIR)
        self._pending = PendingResultStore(self._temp_dir)
        self._processor = LoopbackProcessor(self.active_connections, self._pending)

    async def connect(self, websocket: WebSocket, user_id: str):
        await websocket.accept()
        self.active_connections[user_id] = websocket
        log.info(f"🔌 Bridge: User '{user_id}' connected via WebSocket.")

    def disconnect(self, user_id: str):
        self.active_connections.pop(user_id, None)
        self.client_types.pop(user_id, None)

        # Fail-fast: cancel pending MCP tool-call requests (search_emails, get_calendar)
        failed = []
        for req_id, future in list(self.pending_requests.items()):
            if not future.done():
                future.set_result(f"Outlook connection lost for '{user_id}'.")
                failed.append(req_id)
        for req_id in failed:
            del self.pending_requests[req_id]

        # Loopback queue: keep the worker running — it will store results to temp files
        # so they are delivered when the client reconnects.
        if user_id in self._user_queues:
            remaining = self._user_queues[user_id].queue.qsize()
            if remaining > 0:
                log.info(f"Queue[{user_id}]: client disconnected with {remaining} item(s) still "
                         f"queued — continuing processing, results stored to temp files")

        log.info(f"🔌 Bridge: User '{user_id}' disconnected. {len(failed)} MCP request(s) cancelled.")

    async def send_mcp_request(self, user_id: str, tool_name: str, args: dict) -> Any:
        """
        Sends a command to Outlook and waits for the response (blocking execution here).
        """
        if user_id not in self.active_connections:
            msg = f"⚠️ Outlook usage attempt failed: No client connected for user '{user_id}'."
            log.warning(msg)
            return msg

        # 1. Create a unique ID for the request
        request_id = str(uuid.uuid4())

        # 2. Prepare the JSON-RPC packet (MCP Standard)
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": args
            },
            "id": request_id
        }

        # 3. Create the response "Promise"
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self.pending_requests[request_id] = future

        try:
            # 4. Send to WebSocket
            ws = self.active_connections[user_id]

            # --- TX LOGGING ---
            json_str = json.dumps(payload)
            await ws.send_text(json_str)
            # Log at INFO level to see it in server.log with full JSON
            log.info(f"📤 MCP TX [{user_id}]: {json_str}")
            # ---------------------------

            # 5. Wait for the response
            result = await asyncio.wait_for(future, timeout=BRIDGE_TIMEOUT)
            return result

        except asyncio.TimeoutError:
            if request_id in self.pending_requests:
                del self.pending_requests[request_id]
            err_msg = f"⚠️ Bridge Timeout: Outlook for {user_id} did not respond within {BRIDGE_TIMEOUT}s."
            log.error(err_msg)
            return err_msg

        except Exception as e:
            err_msg = f"⚠️ Critical Bridge Error: {str(e)}"
            log.error(err_msg, exc_info=True)
            return err_msg

    async def handle_incoming_message(self, user_id: str, message: str):
        """Receives responses from Outlook and unblocks pending requests."""

        try:
            data = json.loads(message)

            # Skip verbose logging for heartbeats
            if data.get("method") == "heartbeat":
                log.debug(f"Heartbeat from {user_id}")
                return

            # RX logging (truncate large messages with attachments)
            log_preview = message[:500] + "..." if len(message) > 500 else message
            log.info(f"MCP RX [{user_id}]: {log_preview}")

            # Client identification handshake — client declares its type, server pushes config
            if data.get("method") == "client/identify":
                request_id = data.get("id", str(uuid.uuid4()))
                client_type = data.get("params", {}).get("client_type", "unknown")
                client_version = data.get("params", {}).get("client_version", "?")
                self.client_types[user_id] = client_type
                config_block = self._build_client_config(client_type)
                response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"status": "ok", "config": config_block}
                }
                ws = self.active_connections.get(user_id)
                if ws:
                    await ws.send_text(json.dumps(response))
                if config_block:
                    log.info(f"Bridge: '{user_id}' identified as '{client_type}' v{client_version} — config pushed ({len(config_block)} keys)")
                else:
                    log.warning(f"Bridge: '{user_id}' identified as unknown client_type '{client_type}' — no config pushed")
                # Deliver any results that completed while the client was offline
                asyncio.create_task(self._pending.deliver(user_id, self.active_connections))
                return

            # Virtual Loopback: email sent to ArcumAI by the plugin
            if data.get("method") == "virtual_loopback/send_email":
                request_id = data.get("id", str(uuid.uuid4()))
                params = data.get("params", {})
                log.info(f"VirtualLoopback: Email received from {user_id} | Subject: '{params.get('subject', '?')}'")

                # Send immediate acknowledgment
                ack = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"status": "processing", "message": "Email received, processing..."}
                }
                ws = self.active_connections.get(user_id)
                if ws:
                    await ws.send_text(json.dumps(ack))

                # Enqueue for priority processing (don't block the WebSocket loop)
                importance = params.get("importance", 1)  # 0=Low, 1=Normal, 2=High
                asyncio.create_task(
                    self._enqueue_email(user_id, request_id, params, importance)
                )
                return

            # Response to one of our tool calls (has an ID matching a pending request)
            if "id" in data:
                req_id = data["id"]
                if req_id in self.pending_requests:
                    future = self.pending_requests[req_id]

                    if "error" in data:
                        log.warning(f"Outlook Error (req {req_id}): {data['error']}")
                        future.set_result(f"Error from Outlook: {data['error']}")
                    else:
                        future.set_result(data.get("result", "OK"))

                    del self.pending_requests[req_id]
                    return

            # Push Events (Notifications from client)
            if "method" in data:
                method = data["method"]
                if method == "closing":
                    log.info(f"Client {user_id} closing")
                else:
                    log.info(f"Notification from Outlook ({user_id}): {method}")

        except Exception as e:
            log.error(f"Error parsing message from {user_id}: {e}", exc_info=True)

    # ------------------------------------------------------------------
    #  VIRTUAL LOOPBACK: PROCESS EMAIL FROM PLUGIN
    # ------------------------------------------------------------------

    def _build_client_config(self, client_type: str) -> dict:
        """Return the config block to push for the given client_type.
        Returns an empty dict for unknown types (client uses its own defaults)."""
        if client_type == "vsto_outlook":
            from src.config import (
                VSTO_MAX_ATTACHMENT_MB, VSTO_MAX_TOTAL_MB, VSTO_MAX_PAYLOAD_MB,
                VSTO_ARCUMAI_EMAIL, VSTO_ARCUMAI_DISPLAY_NAME,
                VSTO_LOOPBACK_TIMEOUT_MS, VSTO_ENABLE_VIRTUAL_LOOPBACK,
                VSTO_SHOW_NOTIFICATION,
            )
            return {
                "max_attachment_size_mb":       VSTO_MAX_ATTACHMENT_MB,
                "max_total_attachments_mb":     VSTO_MAX_TOTAL_MB,
                "max_payload_size_mb":          VSTO_MAX_PAYLOAD_MB,
                "arcumai_email":                VSTO_ARCUMAI_EMAIL,
                "arcumai_display_name":         VSTO_ARCUMAI_DISPLAY_NAME,
                "loopback_timeout_ms":          VSTO_LOOPBACK_TIMEOUT_MS,
                "enable_virtual_loopback":      VSTO_ENABLE_VIRTUAL_LOOPBACK,
                "show_processing_notification": VSTO_SHOW_NOTIFICATION,
            }
        # Future client types: add elif blocks here
        return {}

    async def _process_loopback_email(self, user_id: str, request_id: str, params: dict):
        """Delegate to LoopbackProcessor. See src/bridge/loopback_processor.py."""
        return await self._processor._process_loopback_email(user_id, request_id, params)

    # ------------------------------------------------------------------
    #  QUEUE MANAGEMENT
    # ------------------------------------------------------------------

    async def _enqueue_email(self, user_id: str, request_id: str, params: dict, importance: int):
        """Enqueue an email for processing. Checks for cached result first (deduplication)."""
        conv_id = params.get("conversation_id", "")

        # Deduplication: if a cached result exists for this conversation
        # (user resent the email thinking it was lost), deliver immediately.
        # Guard: only check when conv_id is non-empty — an empty string would
        # false-positive against any other pending result that also has no conv_id (Bug 3 fix).
        cached = self._pending.find(user_id, conv_id) if conv_id else None
        if cached:
            log.info(f"Queue[{user_id}]: duplicate request conv_id={conv_id[:16]}... — delivering cached result")
            ws = self.active_connections.get(user_id)
            if ws:
                push = {"jsonrpc": "2.0", "method": "virtual_loopback/response",
                        "params": cached["response"]}
                await ws.send_text(json.dumps(push))
                self._pending.delete(user_id, conv_id)
            return

        # Map Outlook importance to queue priority: High(2)→0, Normal(1)→1, Low(0)→2
        priority = 2 - max(0, min(2, importance))

        if user_id not in self._user_queues:
            uq = _UserQueue(user_id)
            self._user_queues[user_id] = uq
            uq.worker_task = asyncio.create_task(self._queue_worker(user_id))
        else:
            uq = self._user_queues[user_id]
            # Restart worker if it exited unexpectedly (e.g. external task cancellation)
            if uq.worker_task is None or uq.worker_task.done():
                log.warning(f"Queue[{user_id}]: worker not running — restarting")
                uq.worker_task = asyncio.create_task(self._queue_worker(user_id))
        uq.sequence += 1
        await uq.queue.put(_EmailTask(
            priority=priority, sequence=uq.sequence,
            user_id=user_id, request_id=request_id, params=params
        ))
        log.info(f"Queue[{user_id}]: enqueued seq={uq.sequence} priority={priority} "
                 f"subject='{params.get('subject', '?')}' depth={uq.queue.qsize()}")

    async def _queue_worker(self, user_id: str):
        """Per-user worker: pulls tasks from the priority queue and processes them
        one at a time, bounded by the global AI semaphore. Never cancelled on disconnect
        — stores results to temp files if the client is offline."""
        uq = self._user_queues[user_id]
        while True:
            try:
                task = await uq.queue.get()
                log.info(f"Queue[{user_id}]: processing seq={task.sequence} priority={task.priority} "
                         f"(waiting for AI slot...)")
                async with self._ai_semaphore:
                    log.info(f"Queue[{user_id}]: AI slot acquired for seq={task.sequence}")
                    await self._process_loopback_email(task.user_id, task.request_id, task.params)
                uq.queue.task_done()
            except asyncio.CancelledError:
                log.info(f"Queue[{user_id}]: worker stopped")
                break
            except Exception as e:
                log.error(f"Queue[{user_id}]: worker error: {e}", exc_info=True)
                # Never kill the worker — keep processing next items



# Global instance
bridge_manager = OutlookBridgeManager()
