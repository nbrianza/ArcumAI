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
        private OutlookDataProvider _dataProvider;
        private string _pendingIdentifyId;          // tracks the in-flight client/identify request
        private SynchronizationContext _syncContext; // captured on STA thread at startup

        private void ThisAddIn_Startup(object sender, System.EventArgs e)
        {
            // 1. Load Configuration
            _config = PluginConfig.Instance;
            _syncContext = SynchronizationContext.Current; // Outlook's STA thread context

            if (!_config.Validate(out string validationError))
            {
                Log("WARNING", $"Invalid configuration: {validationError} - using default values");
            }

            Log("INFO", $"ArcumAI Plugin started | Server: {_config.ServerUrl} | User: {_config.UserId}");

            // Dump full configuration to log file
            try
            {
                string cfgJson = JsonConvert.SerializeObject(_config, Formatting.Indented);
                Log("INFO", $"Effective configuration: {cfgJson}");
            }
            catch (Exception ex)
            {
                Log("WARNING", $"Unable to serialize configuration: {ex.Message}");
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
                this.Application, _transport, _config, Log);
            _dataProvider = new OutlookDataProvider(this.Application, _config, Log);

            if (_config.EnableVirtualLoopback)
            {
                this.Application.ItemSend += Application_ItemSend;
                _loopbackHandler.EnsureContactExists();
                Log("INFO", $"Virtual Loopback enabled | Target: {_config.ArcumAIEmailAddress}");
            }

            // 5. Hook Quit event (more reliable than Shutdown for VSTO)
            ((Outlook.ApplicationEvents_11_Event)this.Application).Quit += new Outlook.ApplicationEvents_11_QuitEventHandler(Application_Quit);
        }

        private async void ConnectToArcum()
        {
            if (_isShuttingDown) return;

            try
            {
                Log("INFO", $"Connecting to {_config.GetWebSocketUrl()} (attempt {_reconnectAttempt + 1})...");

                await _transport.ConnectAsync(_config.ServerUrl, _config.UserId);

                _reconnectAttempt = 0;
                Log("INFO", "Connected successfully");

                await SendIdentify();
                StartHeartbeat();
            }
            catch (Exception ex)
            {
                Log("ERROR", $"Connection failed: {ex.Message}");
                ScheduleReconnect();
            }
        }

        private async void ScheduleReconnect()
        {
            if (_isShuttingDown || !_config.AutoReconnect) return;

            if (_config.MaxReconnectAttempts != -1 && _reconnectAttempt >= _config.MaxReconnectAttempts)
            {
                Log("ERROR", $"Reached maximum reconnection limit ({_config.MaxReconnectAttempts}). Stopping attempts.");
                return;
            }

            _reconnectAttempt++;
            int delay = _config.ReconnectDelayMs;

            Log("WARNING", $"Reconnecting in {delay}ms (attempt {_reconnectAttempt}/{(_config.MaxReconnectAttempts == -1 ? "∞" : _config.MaxReconnectAttempts.ToString())})...");

            await Task.Delay(delay);
            ConnectToArcum();
        }

        private void StartHeartbeat()
        {
            StopHeartbeat();

            if (_config.HeartbeatIntervalMs <= 0) return;

            _heartbeatTimer = new System.Timers.Timer(_config.HeartbeatIntervalMs);
            _heartbeatTimer.Elapsed += async (s, e) =>
            {
                if (_transport != null && _transport.IsConnected)
                {
                    try
                    {
                        await _transport.SendAsync("{\"method\":\"heartbeat\"}");
                        Log("DEBUG", "Heartbeat sent");
                    }
                    catch (Exception ex)
                    {
                        Log("WARNING", $"Heartbeat failed: {ex.Message}");
                        StopHeartbeat();
                        ScheduleReconnect();
                    }
                }
                else
                {
                    StopHeartbeat();
                    ScheduleReconnect();
                }
            };
            _heartbeatTimer.AutoReset = true;
            _heartbeatTimer.Start();
        }

        private void StopHeartbeat()
        {
            if (_heartbeatTimer != null)
            {
                _heartbeatTimer.Stop();
                _heartbeatTimer.Dispose();
                _heartbeatTimer = null;
            }
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
                Log("INFO", "Sent client/identify to server");
            }
            catch (Exception ex)
            {
                Log("WARNING", $"Failed to send client/identify: {ex.Message}");
                _pendingIdentifyId = null;
            }
        }

        private void ApplyServerConfig(JObject cfg)
        {
            if (cfg == null)
            {
                Log("DEBUG", "Config sync: empty config received — using all defaults");
                return;
            }

            // Dump the raw received config for diagnostics
            Log("INFO", $"Config sync received from server:{Environment.NewLine}{cfg.ToString(Formatting.Indented)}");

            // Update _config properties — safe from any thread (simple value/reference writes)
            int applied = 0;
            if (cfg["max_attachment_size_mb"] != null)       { _config.MaxAttachmentSizeMB        = cfg.Value<int>("max_attachment_size_mb");    applied++; }
            if (cfg["max_total_attachments_mb"] != null)     { _config.MaxTotalAttachmentsMB      = cfg.Value<int>("max_total_attachments_mb");  applied++; }
            if (cfg["max_payload_size_mb"] != null)          { _config.MaxPayloadSizeMB           = cfg.Value<int>("max_payload_size_mb");        applied++; }
            if (cfg["arcumai_email"] != null)                { _config.ArcumAIEmailAddress        = cfg.Value<string>("arcumai_email");          applied++; }
            if (cfg["arcumai_display_name"] != null)         { _config.ArcumAIDisplayName         = cfg.Value<string>("arcumai_display_name");   applied++; }
            if (cfg["loopback_timeout_ms"] != null)          { _config.LoopbackTimeoutMs          = cfg.Value<int>("loopback_timeout_ms");       applied++; }
            if (cfg["enable_virtual_loopback"] != null)      { _config.EnableVirtualLoopback      = cfg.Value<bool>("enable_virtual_loopback");  applied++; }
            if (cfg["show_processing_notification"] != null) { _config.ShowProcessingNotification = cfg.Value<bool>("show_processing_notification"); applied++; }

            int total = 8;
            Log(applied == total ? "INFO" : "WARNING",
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

            Log("WARNING", "Connection lost from server");
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
                    Log("INFO", $"VirtualLoopback: Intercepting email to ArcumAI: '{mail.Subject}'");
                    Cancel = true;
                    _ = _loopbackHandler.ProcessInterceptedEmail(mail);
                }
                else if (_loopbackHandler.ShouldProcessInParallel(mail))
                {
                    // ArcumAI + real recipients: send normally but also process via AI
                    Log("INFO", $"VirtualLoopback: Parallel processing (CC to ArcumAI): '{mail.Subject}'");
                    _loopbackHandler.RemoveArcumRecipient(mail);
                    _ = _loopbackHandler.ProcessInterceptedEmail(mail);
                    // Cancel stays false: email is sent to real recipients
                }
            }
            catch (Exception ex)
            {
                Log("ERROR", $"VirtualLoopback: ItemSend handler error: {ex}");
                // Do NOT cancel on error: let the email send normally
                Cancel = false;
            }
            // NOTE: Do NOT release the mail COM object here - Outlook owns it
        }

        private void Application_Quit()
        {
            _isShuttingDown = true;
            StopHeartbeat();

            // Unhook ItemSend to prevent issues during shutdown
            if (_config.EnableVirtualLoopback)
            {
                try { this.Application.ItemSend -= Application_ItemSend; } catch { }
            }

            Log("INFO", "Outlook closing, disconnecting...");

            if (_transport != null && _transport.IsConnected)
            {
                _ = _transport.SendAsync("{\"method\":\"closing\"}");
            }
        }

        private async void OnMessageFromArcum(object sender, string json)
        {
            try
            {
                Log("DEBUG", $"RX: {json}");

                JObject request = JObject.Parse(json);
                string method = (string)request["method"];
                string id = (string)request["id"];

                // Handle virtual_loopback/response (push notification, no id)
                if (method == "virtual_loopback/response")
                {
                    Log("INFO", "VirtualLoopback: Received AI response from server");
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

                object resultData = null;
                string errorMsg = null;

                if (method == "tools/call")
                {
                    string toolName = (string)request["params"]["name"];
                    JToken args = request["params"]["arguments"];

                    Log("INFO", $"Executing tool: {toolName}");

                    if (toolName == "search_emails")
                    {
                        string query = (string)args["query"] ?? "";
                        resultData = _dataProvider.GetEmails(query);
                    }
                    else if (toolName == "get_calendar")
                    {
                        string filter = (string)args["filter"] ?? "today";
                        resultData = _dataProvider.GetCalendar(filter);
                    }
                    else
                    {
                        errorMsg = $"Unknown tool '{toolName}'.";
                        Log("WARNING", errorMsg);
                    }
                }
                var response = new JObject
                {
                    ["jsonrpc"] = "2.0",
                    ["id"] = id
                };

                if (errorMsg != null)
                {
                    response["error"] = new JObject { ["code"] = -32601, ["message"] = errorMsg };
                }
                else
                {
                    response["result"] = JToken.FromObject(resultData);
                }

                string responseJson = response.ToString(Formatting.None);
                Log("DEBUG", $"TX: {responseJson}");
                await _transport.SendAsync(responseJson);
            }
            catch (Exception ex)
            {
                Log("ERROR", $"Error handling message: {ex}");
            }
        }

        // --- LOGGING ---

        private const long MAX_LOG_SIZE = 5 * 1024 * 1024; // 5 MB

        private void Log(string level, string message)
        {
            if (!_config.EnableLogging) return;

            string[] levels = { "DEBUG", "INFO", "WARNING", "ERROR" };
            int configLevel = Array.IndexOf(levels, _config.LogLevel.ToUpper());
            int msgLevel = Array.IndexOf(levels, level);
            if (msgLevel < configLevel) return;

            try
            {
                string logPath = _config.LogFilePath;
                string directory = Path.GetDirectoryName(logPath);
                if (!Directory.Exists(directory))
                    Directory.CreateDirectory(directory);

                // Log rotation: if file exceeds MAX_LOG_SIZE, rotate
                if (File.Exists(logPath))
                {
                    var info = new FileInfo(logPath);
                    if (info.Length > MAX_LOG_SIZE)
                    {
                        string oldPath = logPath + ".old";
                        if (File.Exists(oldPath)) File.Delete(oldPath);
                        File.Move(logPath, oldPath);
                    }
                }

                string entry = $"{DateTime.Now:yyyy-MM-dd HH:mm:ss.fff} [{level}] {message}{Environment.NewLine}";
                File.AppendAllText(logPath, entry);
            }
            catch
            {
                // Silent fallback to avoid blocking Outlook
            }

            System.Diagnostics.Debug.WriteLine($"[ArcumAI {level}] {message}");
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
