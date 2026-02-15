import asyncio
import json
import os
import uuid
from typing import Dict, Any
from fastapi import WebSocket

# Using the server_log defined in src/logger.py
from src.logger import server_log as log

BRIDGE_TIMEOUT = float(os.getenv("BRIDGE_TIMEOUT", "60.0"))

class OutlookBridgeManager:
    def __init__(self):
        # Map: user_id -> active WebSocket
        self.active_connections: Dict[str, WebSocket] = {}

        # Map: request_id -> Future (the "semaphore" for waiting)
        self.pending_requests: Dict[str, asyncio.Future] = {}

    async def connect(self, websocket: WebSocket, user_id: str):
        await websocket.accept()
        self.active_connections[user_id] = websocket
        log.info(f"🔌 Bridge: User '{user_id}' connected via WebSocket.")

    def disconnect(self, user_id: str):
        if user_id in self.active_connections:
            del self.active_connections[user_id]

        # Fail-fast: cancel all pending requests for this user
        failed = []
        for req_id, future in list(self.pending_requests.items()):
            if not future.done():
                future.set_result(f"Outlook connection lost for '{user_id}'.")
                failed.append(req_id)
        for req_id in failed:
            del self.pending_requests[req_id]

        log.info(f"🔌 Bridge: User '{user_id}' disconnected. {len(failed)} pending requests cancelled.")

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

        # --- RX LOGGING ---
        # Log the raw message as soon as it arrives (INFO level)
        log.info(f"📥 MCP RX [{user_id}]: {message}")
        # ---------------------------

        try:
            data = json.loads(message)

            # If it's a response to one of our calls (has an ID)
            if "id" in data:
                req_id = data["id"]
                if req_id in self.pending_requests:
                    future = self.pending_requests[req_id]

                    if "error" in data:
                        log.warning(f"❌ Outlook Error (req {req_id}): {data['error']}")
                        future.set_result(f"❌ Error from Outlook: {data['error']}")
                    else:
                        future.set_result(data.get("result", "OK"))

                    del self.pending_requests[req_id]

            # Push Events (Notifications from client)
            elif "method" in data:
                method = data["method"]
                if method == "heartbeat":
                    log.debug(f"Heartbeat from {user_id}")
                else:
                    log.info(f"🔔 Notification from Outlook ({user_id}): {method}")

        except Exception as e:
            log.error(f"❌ Error parsing message from {user_id}: {e}", exc_info=True)

# Global instance
bridge_manager = OutlookBridgeManager()
