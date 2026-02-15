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

        private void ThisAddIn_Startup(object sender, System.EventArgs e)
        {
            // 1. Load Configuration
            _config = PluginConfig.Instance;

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

            // 4. Hook Quit event (more reliable than Shutdown for VSTO)
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

        private void OnDisconnected(object sender, EventArgs e)
        {
            if (_isShuttingDown) return;

            Log("WARNING", "Connection lost from server");
            StopHeartbeat();
            ScheduleReconnect();
        }

        private void Application_Quit()
        {
            _isShuttingDown = true;
            StopHeartbeat();

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

                if (string.IsNullOrEmpty(id)) return;

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
                        resultData = GetEmails(query);
                    }
                    else if (toolName == "get_calendar")
                    {
                        string filter = (string)args["filter"] ?? "today";
                        resultData = GetCalendar(filter);
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

        // --- OUTLOOK FUNCTIONS ---

        private List<string> GetEmails(string query)
        {
            var results = new List<string>();
            Outlook.NameSpace session = null;
            Outlook.MAPIFolder inbox = null;
            Outlook.Items items = null;

            try
            {
                session = Application.Session;
                inbox = session.GetDefaultFolder(Outlook.OlDefaultFolders.olFolderInbox);
                items = inbox.Items;
                items.Sort("[ReceivedTime]", true);

                int count = 0;
                int maxResults = _config.MaxEmailResults;
                int previewLen = _config.EmailPreviewLength;

                foreach (object item in items)
                {
                    try
                    {
                        if (item is Outlook.MailItem mail)
                        {
                            if (!string.IsNullOrEmpty(query))
                            {
                                string searchContent = (mail.Subject + " " + mail.SenderName).ToLower();
                                if (!searchContent.Contains(query.ToLower())) continue;
                            }

                            string snippet = mail.Body != null && mail.Body.Length > previewLen
                                ? mail.Body.Substring(0, previewLen) + "..."
                                : mail.Body;
                            results.Add($"[{mail.ReceivedTime:g}] FROM: {mail.SenderName} | SUBJECT: {mail.Subject} | PREVIEW: {snippet}");
                            count++;
                        }
                    }
                    finally
                    {
                        Marshal.ReleaseComObject(item);
                    }
                    if (count >= maxResults) break;
                }

                Log("INFO", $"GetEmails: found {results.Count} emails for query '{query}'");
            }
            catch (Exception ex)
            {
                results.Add($"Error reading emails: {ex.Message}");
                Log("ERROR", $"GetEmails error: {ex.Message}");
            }
            finally
            {
                if (items != null) Marshal.ReleaseComObject(items);
                if (inbox != null) Marshal.ReleaseComObject(inbox);
                if (session != null) Marshal.ReleaseComObject(session);
            }
            return results;
        }

        private List<string> GetCalendar(string filter)
        {
            var results = new List<string>();
            Outlook.NameSpace session = null;
            Outlook.MAPIFolder calendar = null;
            Outlook.Items items = null;
            Outlook.Items restrictedItems = null;

            try
            {
                session = Application.Session;
                calendar = session.GetDefaultFolder(Outlook.OlDefaultFolders.olFolderCalendar);
                items = calendar.Items;
                items.Sort("[Start]");
                items.IncludeRecurrences = true;

                DateTime start, end;
                if (filter == "tomorrow")
                {
                    start = DateTime.Today.AddDays(1);
                    end = DateTime.Today.AddDays(2);
                }
                else if (filter == "week")
                {
                    start = DateTime.Today;
                    end = DateTime.Today.AddDays(7);
                }
                else
                {
                    start = DateTime.Today;
                    end = DateTime.Today.AddDays(1);
                }

                string filterString = $"[Start] >= '{start:g}' AND [Start] < '{end:g}'";
                restrictedItems = items.Restrict(filterString);

                foreach (object item in restrictedItems)
                {
                    try
                    {
                        if (item is Outlook.AppointmentItem appt)
                        {
                            results.Add($"[{appt.Start:t} - {appt.End:t}] {appt.Subject} @ {appt.Location}");
                        }
                    }
                    finally
                    {
                        Marshal.ReleaseComObject(item);
                    }
                }

                Log("INFO", $"GetCalendar: found {results.Count} appointments for filter '{filter}'");
            }
            catch (Exception ex)
            {
                results.Add($"Calendar error: {ex.Message}");
                Log("ERROR", $"GetCalendar error: {ex.Message}");
            }
            finally
            {
                if (restrictedItems != null) Marshal.ReleaseComObject(restrictedItems);
                if (items != null) Marshal.ReleaseComObject(items);
                if (calendar != null) Marshal.ReleaseComObject(calendar);
                if (session != null) Marshal.ReleaseComObject(session);
            }

            if (results.Count == 0) results.Add("No appointments found.");
            return results;
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
