using System;
using System.Collections.Generic;
using System.Threading.Tasks;
using Outlook = Microsoft.Office.Interop.Outlook;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using ArcumAI.OutlookAddIn.Core;

namespace ArcumAI.OutlookAddIn
{
    /// <summary>
    /// EXAMPLE: Updated ThisAddIn with Configuration Management
    ///
    /// This is an example showing how to integrate the PluginConfig system.
    /// To use this:
    /// 1. Backup your current ThisAddIn.cs
    /// 2. Copy the relevant sections from this file to your ThisAddIn.cs
    /// 3. Test thoroughly before deploying
    /// </summary>
    public partial class ThisAddIn_ConfigExample
    {
        private IMcpTransport _transport;
        private PluginConfig _config;

        private void ThisAddIn_Startup(object sender, System.EventArgs e)
        {
            // 1. Load Configuration
            _config = PluginConfig.Instance;

            // 2. Validate Configuration
            if (!_config.Validate(out string validationError))
            {
                System.Windows.Forms.MessageBox.Show(
                    $"Invalid configuration: {validationError}\n\nUsing default values.",
                    "ArcumAI Configuration Warning",
                    System.Windows.Forms.MessageBoxButtons.OK,
                    System.Windows.Forms.MessageBoxIcon.Warning
                );
            }

            // 3. Log Configuration (if logging enabled)
            if (_config.EnableLogging)
            {
                LogInfo($"ArcumAI Outlook Plugin starting...");
                LogInfo($"Server URL: {_config.ServerUrl}");
                LogInfo($"User ID: {_config.UserId}");
                LogInfo($"Auto-reconnect: {_config.AutoReconnect}");
                LogInfo($"Max reconnect attempts: {_config.MaxReconnectAttempts}");
            }

            // 4. Setup Transport with Configuration
            _transport = new WebSocketTransport();
            _transport.MessageReceived += OnMessageFromArcum;

            // 5. Connect to Server
            ConnectToArcum();

            // 6. Hook Quit Event for Clean Shutdown
            ((Outlook.ApplicationEvents_11_Event)this.Application).Quit
                += new Outlook.ApplicationEvents_11_QuitEventHandler(Application_Quit);

            LogInfo("Plugin started successfully");
        }

        private async void ConnectToArcum()
        {
            try
            {
                LogInfo($"Connecting to {_config.GetWebSocketUrl()}...");

                await _transport.ConnectAsync(_config.ServerUrl, _config.UserId);

                LogInfo("Connected successfully");
            }
            catch (Exception ex)
            {
                LogError($"Connection failed: {ex.Message}");

                // Handle reconnection based on configuration
                if (_config.AutoReconnect)
                {
                    LogWarning($"Will retry connection in {_config.ReconnectDelayMs}ms");
                    // TODO: Implement reconnection logic (see next section)
                }
            }
        }

        private void OnMessageFromArcum(object sender, string json)
        {
            try
            {
                LogDebug($"Received message: {json}");

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

                    LogInfo($"Executing tool: {toolName}");

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
                        errorMsg = $"Tool '{toolName}' sconosciuto.";
                        LogWarning(errorMsg);
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

                LogDebug($"Sending response: {response.ToString(Formatting.None)}");
                _transport.SendAsync(response.ToString(Formatting.None));
            }
            catch (Exception ex)
            {
                LogError($"Error handling message: {ex.Message}\n{ex.StackTrace}");
            }
        }

        // --- OUTLOOK FUNCTIONS (Updated with Configuration) ---

        private List<string> GetEmails(string query)
        {
            var results = new List<string>();
            try
            {
                Outlook.MAPIFolder inbox = Application.Session.GetDefaultFolder(Outlook.OlDefaultFolders.olFolderInbox);
                Outlook.Items items = inbox.Items;
                items.Sort("[ReceivedTime]", true);

                int count = 0;
                foreach (object item in items)
                {
                    if (item is Outlook.MailItem mail)
                    {
                        if (!string.IsNullOrEmpty(query))
                        {
                            string searchContent = (mail.Subject + " " + mail.SenderName).ToLower();
                            if (!searchContent.Contains(query.ToLower())) continue;
                        }

                        // Use configured preview length
                        string snippet = mail.Body != null && mail.Body.Length > _config.EmailPreviewLength
                            ? mail.Body.Substring(0, _config.EmailPreviewLength) + "..."
                            : mail.Body;

                        results.Add($"[{mail.ReceivedTime:g}] DA: {mail.SenderName} | OGGETTO: {mail.Subject} | ANTEPRIMA: {snippet}");
                        count++;
                    }

                    // Use configured max results
                    if (count >= _config.MaxEmailResults) break;
                }

                LogInfo($"Found {results.Count} emails matching query: '{query}'");
            }
            catch (Exception ex)
            {
                string errorMsg = $"Error reading emails: {ex.Message}";
                results.Add(errorMsg);
                LogError(errorMsg);
            }
            return results;
        }

        private List<string> GetCalendar(string filter)
        {
            var results = new List<string>();
            try
            {
                Outlook.MAPIFolder calendar = Application.Session.GetDefaultFolder(Outlook.OlDefaultFolders.olFolderCalendar);
                Outlook.Items items = calendar.Items;
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
                Outlook.Items restrictedItems = items.Restrict(filterString);

                foreach (Outlook.AppointmentItem appt in restrictedItems)
                {
                    results.Add($"[{appt.Start:t} - {appt.End:t}] {appt.Subject} @ {appt.Location}");
                }

                LogInfo($"Found {results.Count} calendar items for filter: '{filter}'");
            }
            catch (Exception ex)
            {
                string errorMsg = $"Error reading calendar: {ex.Message}";
                results.Add(errorMsg);
                LogError(errorMsg);
            }

            if (results.Count == 0) results.Add("Nessun appuntamento trovato.");
            return results;
        }

        private void Application_Quit()
        {
            LogInfo("Outlook closing, disconnecting...");

            if (_transport != null && _transport.IsConnected)
            {
                _ = _transport.SendAsync("{\"method\":\"closing\"}");
            }
        }

        private void ThisAddIn_Shutdown(object sender, System.EventArgs e)
        {
            // Leave empty or minimal cleanup
        }

        // --- LOGGING FUNCTIONS ---

        private void LogDebug(string message)
        {
            if (_config.EnableLogging && _config.LogLevel == "DEBUG")
            {
                WriteLog("DEBUG", message);
            }
        }

        private void LogInfo(string message)
        {
            if (_config.EnableLogging &&
                (_config.LogLevel == "DEBUG" || _config.LogLevel == "INFO"))
            {
                WriteLog("INFO", message);
            }
        }

        private void LogWarning(string message)
        {
            if (_config.EnableLogging &&
                (_config.LogLevel == "DEBUG" || _config.LogLevel == "INFO" || _config.LogLevel == "WARNING"))
            {
                WriteLog("WARNING", message);
            }
        }

        private void LogError(string message)
        {
            if (_config.EnableLogging)
            {
                WriteLog("ERROR", message);
            }
        }

        private void WriteLog(string level, string message)
        {
            try
            {
                string logPath = _config.LogFilePath;
                if (string.IsNullOrEmpty(logPath))
                {
                    logPath = System.IO.Path.Combine(
                        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                        "ArcumAI", "Outlook", "logs", "plugin.log"
                    );
                }

                string directory = System.IO.Path.GetDirectoryName(logPath);
                if (!System.IO.Directory.Exists(directory))
                {
                    System.IO.Directory.CreateDirectory(directory);
                }

                string logEntry = $"{DateTime.Now:yyyy-MM-dd HH:mm:ss.fff} [{level}] {message}\n";
                System.IO.File.AppendAllText(logPath, logEntry);

                // Also write to debug output
                System.Diagnostics.Debug.WriteLine($"[{level}] {message}");
            }
            catch
            {
                // Fail silently to avoid breaking the plugin
            }
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
