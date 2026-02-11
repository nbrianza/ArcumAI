import asyncio
import json
import os
import uuid
from typing import Dict, Any
from fastapi import WebSocket

# Usiamo il server_log definito in src/logger.py
from src.logger import server_log as log

BRIDGE_TIMEOUT = float(os.getenv("BRIDGE_TIMEOUT", "60.0"))

class OutlookBridgeManager:
    def __init__(self):
        # Mappa: user_id -> WebSocket attivo
        self.active_connections: Dict[str, WebSocket] = {}
        
        # Mappa: request_id -> Future (il "semaforo" per l'attesa)
        self.pending_requests: Dict[str, asyncio.Future] = {}

    async def connect(self, websocket: WebSocket, user_id: str):
        await websocket.accept()
        self.active_connections[user_id] = websocket
        log.info(f"🔌 Bridge: Utente '{user_id}' connesso via WebSocket.")

    def disconnect(self, user_id: str):
        if user_id in self.active_connections:
            del self.active_connections[user_id]

        # Fail-fast: cancel all pending requests for this user
        failed = []
        for req_id, future in list(self.pending_requests.items()):
            if not future.done():
                future.set_result(f"Connessione Outlook persa per '{user_id}'.")
                failed.append(req_id)
        for req_id in failed:
            del self.pending_requests[req_id]

        log.info(f"🔌 Bridge: Utente '{user_id}' disconnesso. {len(failed)} richieste pendenti annullate.")

    async def send_mcp_request(self, user_id: str, tool_name: str, args: dict) -> Any:
        """
        Invia un comando a Outlook e aspetta la risposta (bloccando l'esecuzione qui).
        """
        if user_id not in self.active_connections:
            msg = f"⚠️ Tentativo di uso Outlook fallito: Nessun client connesso per l'utente '{user_id}'."
            log.warning(msg)
            return msg

        # 1. Crea un ID univoco per la richiesta
        request_id = str(uuid.uuid4())
        
        # 2. Prepara il pacchetto JSON-RPC (Standard MCP)
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": args
            },
            "id": request_id
        }

        # 3. Crea la "Promessa" di risposta
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self.pending_requests[request_id] = future

        try:
            # 4. Invia al WebSocket
            ws = self.active_connections[user_id]
            
            # --- MODIFICA LOGGING TX ---
            json_str = json.dumps(payload)
            await ws.send_text(json_str)
            # Log livello INFO per vederlo in server.log con tutto il JSON
            log.info(f"📤 MCP TX [{user_id}]: {json_str}") 
            # ---------------------------

            # 5. Aspetta la risposta
            result = await asyncio.wait_for(future, timeout=BRIDGE_TIMEOUT)
            return result

        except asyncio.TimeoutError:
            if request_id in self.pending_requests:
                del self.pending_requests[request_id]
            err_msg = f"⚠️ Timeout Bridge: Outlook di {user_id} non ha risposto in {BRIDGE_TIMEOUT}s."
            log.error(err_msg)
            return err_msg
            
        except Exception as e:
            err_msg = f"⚠️ Errore Critico Bridge: {str(e)}"
            log.error(err_msg, exc_info=True)
            return err_msg

    async def handle_incoming_message(self, user_id: str, message: str):
        """Riceve le risposte da Outlook e sblocca le richieste in attesa."""
        
        # --- MODIFICA LOGGING RX ---
        # Logghiamo il messaggio grezzo appena arriva (INFO level)
        log.info(f"📥 MCP RX [{user_id}]: {message}")
        # ---------------------------

        try:
            data = json.loads(message)
            
            # Se è una risposta a una nostra chiamata (ha un ID)
            if "id" in data:
                req_id = data["id"]
                if req_id in self.pending_requests:
                    future = self.pending_requests[req_id]
                    
                    if "error" in data:
                        log.warning(f"❌ Outlook Error (req {req_id}): {data['error']}")
                        future.set_result(f"❌ Errore da Outlook: {data['error']}")
                    else:
                        # Rimosso log.debug qui per non duplicare l'info
                        future.set_result(data.get("result", "OK"))
                    
                    del self.pending_requests[req_id]
            
            # Eventi Push (Notifiche dal client)
            elif "method" in data:
                method = data["method"]
                if method == "heartbeat":
                    log.debug(f"Heartbeat da {user_id}")
                else:
                    log.info(f"🔔 Notifica da Outlook ({user_id}): {method}")

        except Exception as e:
            log.error(f"❌ Errore parsing messaggio da {user_id}: {e}", exc_info=True)

# Istanza globale
bridge_manager = OutlookBridgeManager()