// Copyright (c) 2026 Nicolas Brianza
// Licensed under the MIT License. See LICENSE file in the project root.
using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using Outlook = Microsoft.Office.Interop.Outlook;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using ArcumAI.OutlookAddIn.Core;
using ArcumAI.OutlookAddIn.Core.Transport;

namespace ArcumAI.OutlookAddIn
{
    public partial class ThisAddIn
    {
        private IMcpTransport _transport;
        private PluginConfig _config;
        private int _reconnectAttempt;
        private bool _isShuttingDown;
        private System.Timers.Timer _heartbeatTimer;
        private VirtualLoopbackHandler _loopbackHandler;
        private IPluginLogger _logger;
        private string _pendingIdentifyId;          // tracks the in-flight client/identify request
        private SynchronizationContext _syncContext; // captured on STA thread at startup
        private readonly object _heartbeatLock = new object(); // guards _heartbeatTimer across threads

        private void ThisAddIn_Startup(object sender, System.EventArgs e)
        {
            // 1. Load Configuration
            _config = PluginConfig.Instance;
            _logger = new PluginLogger(_config);
            _syncContext = SynchronizationContext.Current; // Outlook's STA thread context
            if (_syncContext == null)
            {
                // VSTO does not install a SynchronizationContext on the Outlook STA thread.
                // Install one so _syncContext.Post() defers COM cleanup correctly after ItemSend returns.
                var ctx = new System.Windows.Forms.WindowsFormsSynchronizationContext();
                SynchronizationContext.SetSynchronizationContext(ctx);
                _syncContext = ctx;
            }

            if (!_config.Validate(out string validationError))
            {
                _logger.Log("WARNING", $"Invalid configuration: {validationError} - using default values");
            }

            _logger.Log("INFO", $"ArcumAI Plugin started | Server: {_config.ServerUrl} | User: {_config.UserId}");

            try
            {
                _logger.Log("INFO", $"Effective configuration: ServerUrl={_config.ServerUrl}, UserId={_config.UserId}, LogLevel={_config.LogLevel}, EnableVirtualLoopback={_config.EnableVirtualLoopback}");
            }
            catch (Exception ex)
            {
                _logger.Log("WARNING", $"Unable to serialize configuration: {ex.Message}");
            }

            // 2. Setup Transport
            _transport = new WebSocketTransport();
            _transport.MessageReceived += OnMessageFromArcum;
            _transport.Disconnected += OnDisconnected;

            // 3. Connect
            _reconnectAttempt = 0;
            ConnectToArcum();

            // 4. Setup Virtual Loopback
            _loopbackHandler = new VirtualLoopbackHandler(
                this.Application, _transport, _config, _logger.Log);

            if (_config.EnableVirtualLoopback)
            {
                this.Application.ItemSend += Application_ItemSend;
                _loopbackHandler.EnsureContactExists();
                _logger.Log("INFO", $"Virtual Loopback enabled | Target: {_config.ArcumAIEmailAddress}");
            }

            // 5. Hook Quit event (more reliable than Shutdown for VSTO)
            ((Outlook.ApplicationEvents_11_Event)this.Application).Quit += new Outlook.ApplicationEvents_11_QuitEventHandler(Application_Quit);
        }

        private async void ConnectToArcum()
        {
            if (_isShuttingDown) return;

            try
            {
                _logger.Log("INFO", $"Connecting to {_config.GetWebSocketUrl()} (attempt {_reconnectAttempt + 1})...");

                await _transport.ConnectAsync(_config.ServerUrl, _config.UserId);

                _reconnectAttempt = 0;
                _logger.Log("INFO", "Connected successfully");

                await SendIdentify();
                StartHeartbeat();
                _loopbackHandler?.RearmPendingTimeouts();
            }
            catch (Exception ex)
            {
                _logger.Log("ERROR", $"Connection failed: {ex.Message}");
                ScheduleReconnect();
            }
        }

        private async void ScheduleReconnect()
        {
            if (_isShuttingDown || !_config.AutoReconnect) return;

            if (_config.MaxReconnectAttempts != -1 && _reconnectAttempt >= _config.MaxReconnectAttempts)
            {
                _logger.Log("ERROR", $"Reached maximum reconnection limit ({_config.MaxReconnectAttempts}). Stopping attempts.");
                return;
            }

            _reconnectAttempt++;
            int delay = _config.ReconnectDelayMs;

            _logger.Log("WARNING", $"Reconnecting in {delay}ms (attempt {_reconnectAttempt}/{(_config.MaxReconnectAttempts == -1 ? "∞" : _config.MaxReconnectAttempts.ToString())})...");

            await Task.Delay(delay);
            ConnectToArcum();
        }

        private void StartHeartbeat()
        {
            System.Timers.Timer oldTimer;
            lock (_heartbeatLock)
            {
                oldTimer = _heartbeatTimer;
                _heartbeatTimer = null;

                if (_config.HeartbeatIntervalMs > 0)
                {
                    _heartbeatTimer = new System.Timers.Timer(_config.HeartbeatIntervalMs);
                    _heartbeatTimer.Elapsed += (s, e) =>
                    {
                        _ = HeartbeatTickAsync().ContinueWith(
                            t => _logger.Log("ERROR", $"Heartbeat unhandled exception: {t.Exception?.GetBaseException()?.Message}"),
                            TaskContinuationOptions.OnlyOnFaulted);
                    };
                    _heartbeatTimer.AutoReset = true;
                    _heartbeatTimer.Start();
                }
            }
            // Dispose old timer outside the lock to avoid blocking other threads
            if (oldTimer != null) { oldTimer.Stop(); oldTimer.Dispose(); }
        }

        private async Task HeartbeatTickAsync()
        {
            if (_transport != null && _transport.IsConnected)
            {
                try
                {
                    await _transport.SendAsync("{\"method\":\"heartbeat\"}");
                    _logger.Log("DEBUG", "Heartbeat sent");
                }
                catch (Exception ex)
                {
                    _logger.Log("WARNING", $"Heartbeat failed: {ex.Message}");
                    StopHeartbeat();
                    ScheduleReconnect();
                }
            }
            else
            {
                StopHeartbeat();
                ScheduleReconnect();
            }
        }

        private void StopHeartbeat()
        {
            System.Timers.Timer t;
            lock (_heartbeatLock)
            {
                t = _heartbeatTimer;
                _heartbeatTimer = null;
            }
            if (t != null) { t.Stop(); t.Dispose(); }
        }

        private async Task SendIdentify()
        {
            try
            {
                _pendingIdentifyId = Guid.NewGuid().ToString();
                var msg = new JObject
                {
                    ["jsonrpc"] = "2.0",
                    ["method"] = "client/identify",
                    ["id"] = _pendingIdentifyId,
                    ["params"] = new JObject
                    {
                        ["client_type"] = "vsto_outlook",
                        ["client_version"] = "2.0"
                    }
                };
                await _transport.SendAsync(msg.ToString(Formatting.None));
                _logger.Log("INFO", "Sent client/identify to server");
            }
            catch (Exception ex)
            {
                _logger.Log("WARNING", $"Failed to send client/identify: {ex.Message}");
                _pendingIdentifyId = null;
            }
        }

        private void ApplyServerConfig(JObject cfg)
        {
            if (cfg == null)
            {
                _logger.Log("DEBUG", "Config sync: empty config received — using all defaults");
                return;
            }

            _logger.Log("INFO", $"Config sync received from server ({cfg.Count} keys)");

            // Update _config properties — safe from any thread (simple value/reference writes)
            int applied = PluginConfigLoader.ApplyServerValues(_config, cfg);
            Thread.MemoryBarrier(); // ensure all config writes are visible to the STA thread before it reads them

            if (!_config.Validate(out string syncValidationError))
                _logger.Log("WARNING", $"Config sync produced invalid configuration: {syncValidationError}");

            // Persist so the next Outlook start uses these values before the server is reachable.
            try
            {
                PluginConfigLoader.SaveServerConfigCache(cfg);
            }
            catch (Exception ex)
            {
                _logger.Log("WARNING", $"Could not persist server config: {ex.Message}");
            }

            int total = PluginConfigLoader.ServerManagedKeyCount;
            _logger.Log(applied == total ? "INFO" : "WARNING",
                $"Config sync applied: {applied}/{total} keys — " +
                $"MaxAttachment={_config.MaxAttachmentSizeMB}MB, " +
                $"Email={_config.ArcumAIEmailAddress}, " +
                $"Enabled={_config.EnableVirtualLoopback}");

            // COM operations (ItemSend hook, contact creation) must run on Outlook's STA thread.
            // This method is called from OnMessageFromArcum which runs on a thread-pool thread.
            bool loopbackEnabled = _config.EnableVirtualLoopback;
            void ApplyComChanges(object _)
            {
                if (loopbackEnabled)
                {
                    try { this.Application.ItemSend -= Application_ItemSend; } catch { }
                    this.Application.ItemSend += Application_ItemSend;
                    _loopbackHandler?.EnsureContactExists();
                }
                else
                {
                    try { this.Application.ItemSend -= Application_ItemSend; } catch { }
                }
            }

            if (_syncContext != null)
                _syncContext.Post(ApplyComChanges, null);
            else
                ApplyComChanges(null); // already on STA (shouldn't happen in practice)
        }

        private void OnDisconnected(object sender, EventArgs e)
        {
            if (_isShuttingDown) return;

            _logger.Log("WARNING", "Connection lost from server");
            StopHeartbeat();
            _loopbackHandler?.NotifyPendingOnDisconnect();   // toast only, no error emails
            ScheduleReconnect();
        }

        // --- VIRTUAL LOOPBACK: ITEMSEND HANDLER ---

        private void Application_ItemSend(object Item, ref bool Cancel)
        {
            Outlook.MailItem mail = null;
            try
            {
                mail = Item as Outlook.MailItem;
                if (mail == null) return; // Not an email (meeting request, etc.)

                if (!_transport.IsConnected)
                {
                    // Not connected to server: let the email send normally
                    return;
                }

                if (_loopbackHandler.ShouldIntercept(mail))
                {
                    // ArcumAI is the ONLY recipient: full intercept
                    _logger.Log("INFO", $"VirtualLoopback: Intercepting email to ArcumAI: '{mail.Subject}'");
                    Cancel = true;
                    _ = _loopbackHandler.ProcessInterceptedEmail(mail);
                }
                else if (_loopbackHandler.ShouldProcessInParallel(mail))
                {
                    // ArcumAI + real recipients: send normally but also process via AI
                    _logger.Log("INFO", $"VirtualLoopback: Parallel processing (CC to ArcumAI): '{mail.Subject}'");
                    _loopbackHandler.RemoveArcumRecipient(mail);
                    _ = _loopbackHandler.ProcessInterceptedEmail(mail);
                    // Cancel stays false: email is sent to real recipients
                }
            }
            catch (Exception ex)
            {
                _logger.Log("ERROR", $"VirtualLoopback: ItemSend handler error: {ex}");
                // Do NOT cancel on error: let the email send normally
                Cancel = false;
            }
            // NOTE: Do NOT release the mail COM object here - Outlook owns it
        }

        private void Application_Quit()
        {
            _isShuttingDown = true;
            StopHeartbeat();

            // Unsubscribe transport events to prevent duplicate handlers on plugin reload
            if (_transport != null)
            {
                _transport.MessageReceived -= OnMessageFromArcum;
                _transport.Disconnected -= OnDisconnected;
            }

            // Unhook ItemSend to prevent issues during shutdown
            if (_config.EnableVirtualLoopback)
            {
                try { this.Application.ItemSend -= Application_ItemSend; } catch { }
            }

            _logger.Log("INFO", "Outlook closing, disconnecting...");

            if (_transport != null && _transport.IsConnected)
            {
                _ = _transport.SendAsync("{\"method\":\"closing\"}");
            }
        }

        private async void OnMessageFromArcum(object sender, string json)
        {
            try
            {
                _logger.Log("DEBUG", $"RX: {json.Length} bytes");

                if (string.IsNullOrWhiteSpace(json)) return;

                int maxBytes = _config.MaxPayloadSizeMB * 1024 * 1024;
                if (json.Length > maxBytes)
                {
                    _logger.Log("WARNING", $"Rejected oversized payload: {json.Length} bytes (limit: {_config.MaxPayloadSizeMB} MB)");
                    return;
                }

                JObject request = JObject.Parse(json);
                string method = (string)request["method"];
                string id = (string)request["id"];

                // First ack proves this server answers heartbeats: from now on, silence means a dead server.
                if (method == "heartbeat/ack")
                {
                    if (_config.HeartbeatIntervalMs > 0 && _transport.InactivityTimeoutMs == 0)
                    {
                        _transport.InactivityTimeoutMs = _config.HeartbeatIntervalMs * 3;
                        _logger.Log("DEBUG", $"Server acks heartbeats — receive inactivity timeout set to {_transport.InactivityTimeoutMs} ms");
                    }
                    return;
                }

                // Handle virtual_loopback/response (push notification, no id)
                if (method == "virtual_loopback/response")
                {
                    _logger.Log("INFO", "VirtualLoopback: Received AI response from server");
                    _loopbackHandler.HandleServerResponse(request["params"] as JObject);
                    return;
                }

                // Handle client/identify response (has id + result.config, no method)
                JToken result = request["result"];
                if (result != null && !string.IsNullOrEmpty(_pendingIdentifyId) && id == _pendingIdentifyId)
                {
                    _pendingIdentifyId = null;
                    ApplyServerConfig(result["config"] as JObject);
                    return;
                }

                if (string.IsNullOrEmpty(id)) return;

                // JSON-RPC result/error response (e.g. ACK to virtual_loopback/send_email) — not a request.
                // These have no "method" field; nothing to dispatch.
                if (string.IsNullOrEmpty(method)) return;

                // From here on the server is waiting on this id: always answer, even on failure,
                // otherwise it blocks until its bridge timeout.
                var response = new JObject
                {
                    ["jsonrpc"] = "2.0",
                    ["id"] = request["id"]
                };
                try
                {
                    response["result"] = ExecuteRequest(method, request["params"]);
                }
                catch (JsonRpcException ex)
                {
                    _logger.Log("WARNING", $"Rejected request {id}: {ex.Message}");
                    response["error"] = new JObject { ["code"] = ex.Code, ["message"] = ex.Message };
                }
                catch (Exception ex)
                {
                    _logger.Log("ERROR", $"Request {id} ({method}) failed: {ex}");
                    response["error"] = new JObject { ["code"] = JsonRpcException.InternalError, ["message"] = $"Internal error: {ex.Message}" };
                }

                string responseJson = response.ToString(Formatting.None);
                _logger.Log("DEBUG", $"TX: {responseJson.Length} bytes");
                await _transport.SendAsync(responseJson);
            }
            catch (Exception ex)
            {
                _logger.Log("ERROR", $"Error handling message: {ex}");
            }
        }

        private JToken ExecuteRequest(string method, JToken paramsToken)
        {
            if (method != "tools/call")
                throw new JsonRpcException(JsonRpcException.MethodNotFound, $"Method '{method}' not found.");

            if (!(paramsToken is JObject paramsObj))
                throw new JsonRpcException(JsonRpcException.InvalidParams, "tools/call request is missing the 'params' object.");

            string toolName = GetStringArg(paramsObj, "name", null);
            if (string.IsNullOrEmpty(toolName))
                throw new JsonRpcException(JsonRpcException.InvalidParams, "tools/call request is missing 'params.name'.");

            JToken argsToken = paramsObj["arguments"];
            if (argsToken != null && argsToken.Type != JTokenType.Null && !(argsToken is JObject))
                throw new JsonRpcException(JsonRpcException.InvalidParams, "'params.arguments' must be an object.");
            var args = argsToken as JObject ?? new JObject();

            _logger.Log("INFO", $"Executing tool: {toolName}");
            var dataProvider = new OutlookDataProvider(this.Application, _config, _logger.Log);

            switch (toolName)
            {
                case "search_emails":
                    return JToken.FromObject(dataProvider.GetEmails(GetStringArg(args, "query", "")));
                case "get_calendar":
                    return JToken.FromObject(dataProvider.GetCalendar(GetStringArg(args, "filter", "today")));
                default:
                    throw new JsonRpcException(JsonRpcException.MethodNotFound, $"Unknown tool '{toolName}'.");
            }
        }

        private static string GetStringArg(JObject obj, string key, string fallback)
        {
            JToken token = obj[key];
            if (token == null || token.Type == JTokenType.Null) return fallback;
            if (token.Type != JTokenType.String)
                throw new JsonRpcException(JsonRpcException.InvalidParams, $"'{key}' must be a string.");
            return (string)token;
        }

        private sealed class JsonRpcException : Exception
        {
            public const int InvalidParams = -32602;
            public const int MethodNotFound = -32601;
            public const int InternalError = -32603;

            public int Code { get; }

            public JsonRpcException(int code, string message) : base(message)
            {
                Code = code;
            }
        }

        private void ThisAddIn_Shutdown(object sender, System.EventArgs e)
        {
            // Leave empty - critical logic handled in Application_Quit
        }

        #region VSTO generated code
        private void InternalStartup()
        {
            this.Startup += new System.EventHandler(this.ThisAddIn_Startup);
            this.Shutdown += new System.EventHandler(this.ThisAddIn_Shutdown);
        }
        #endregion
    }
}
