using System;
using System.Collections.Generic;
using System.IO;
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
            // 1. Carica Configurazione
            _config = PluginConfig.Instance;

            if (!_config.Validate(out string validationError))
            {
                Log("WARNING", $"Configurazione non valida: {validationError} - uso valori di default");
            }

            Log("INFO", $"ArcumAI Plugin avviato | Server: {_config.ServerUrl} | Utente: {_config.UserId}");

            // Dump configurazione completa nel file di log
            try
            {
                string cfgJson = JsonConvert.SerializeObject(_config, Formatting.Indented);
                Log("INFO", $"Configurazione effettiva: {cfgJson}");
            }
            catch (Exception ex)
            {
                Log("WARNING", $"Impossibile serializzare configurazione: {ex.Message}");
            }

            // 2. Setup Trasporto
            _transport = new WebSocketTransport();
            _transport.MessageReceived += OnMessageFromArcum;
            _transport.Disconnected += OnDisconnected;

            // 3. Connessione
            _reconnectAttempt = 0;
            ConnectToArcum();

            // 4. Hook evento Quit (più affidabile di Shutdown per VSTO)
            ((Outlook.ApplicationEvents_11_Event)this.Application).Quit += new Outlook.ApplicationEvents_11_QuitEventHandler(Application_Quit);
        }

        private async void ConnectToArcum()
        {
            if (_isShuttingDown) return;

            try
            {
                Log("INFO", $"Connessione a {_config.GetWebSocketUrl()} (tentativo {_reconnectAttempt + 1})...");

                await _transport.ConnectAsync(_config.ServerUrl, _config.UserId);

                _reconnectAttempt = 0;
                Log("INFO", "Connesso con successo");

                StartHeartbeat();
            }
            catch (Exception ex)
            {
                Log("ERROR", $"Connessione fallita: {ex.Message}");
                ScheduleReconnect();
            }
        }

        private async void ScheduleReconnect()
        {
            if (_isShuttingDown || !_config.AutoReconnect) return;

            if (_config.MaxReconnectAttempts != -1 && _reconnectAttempt >= _config.MaxReconnectAttempts)
            {
                Log("ERROR", $"Raggiunto limite massimo di riconnessione ({_config.MaxReconnectAttempts}). Stop tentativi.");
                return;
            }

            _reconnectAttempt++;
            int delay = _config.ReconnectDelayMs;

            Log("WARNING", $"Riconnessione in {delay}ms (tentativo {_reconnectAttempt}/{(_config.MaxReconnectAttempts == -1 ? "∞" : _config.MaxReconnectAttempts.ToString())})...");

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
                        Log("DEBUG", "Heartbeat inviato");
                    }
                    catch (Exception ex)
                    {
                        Log("WARNING", $"Heartbeat fallito: {ex.Message}");
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

            Log("WARNING", "Connessione persa dal server");
            StopHeartbeat();
            ScheduleReconnect();
        }

        private void Application_Quit()
        {
            _isShuttingDown = true;
            StopHeartbeat();

            Log("INFO", "Outlook in chiusura, disconnessione...");

            if (_transport != null && _transport.IsConnected)
            {
                _ = _transport.SendAsync("{\"method\":\"closing\"}");
            }
        }

        private void OnMessageFromArcum(object sender, string json)
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

                    Log("INFO", $"Esecuzione tool: {toolName}");

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
                _transport.SendAsync(responseJson);
            }
            catch (Exception ex)
            {
                Log("ERROR", $"Errore gestione messaggio: {ex}");
            }
        }

        // --- FUNZIONI OUTLOOK ---

        private List<string> GetEmails(string query)
        {
            var results = new List<string>();
            try
            {
                Outlook.MAPIFolder inbox = Application.Session.GetDefaultFolder(Outlook.OlDefaultFolders.olFolderInbox);
                Outlook.Items items = inbox.Items;
                items.Sort("[ReceivedTime]", true);

                int count = 0;
                int maxResults = _config.MaxEmailResults;
                int previewLen = _config.EmailPreviewLength;

                foreach (object item in items)
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
                        results.Add($"[{mail.ReceivedTime:g}] DA: {mail.SenderName} | OGGETTO: {mail.Subject} | ANTEPRIMA: {snippet}");
                        count++;
                    }
                    if (count >= maxResults) break;
                }

                Log("INFO", $"GetEmails: trovate {results.Count} mail per query '{query}'");
            }
            catch (Exception ex)
            {
                results.Add($"Errore lettura mail: {ex.Message}");
                Log("ERROR", $"GetEmails errore: {ex.Message}");
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

                Log("INFO", $"GetCalendar: trovati {results.Count} appuntamenti per filtro '{filter}'");
            }
            catch (Exception ex)
            {
                results.Add($"Errore calendario: {ex.Message}");
                Log("ERROR", $"GetCalendar errore: {ex.Message}");
            }

            if (results.Count == 0) results.Add("Nessun appuntamento trovato.");
            return results;
        }

        // --- LOGGING ---

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

                string entry = $"{DateTime.Now:yyyy-MM-dd HH:mm:ss.fff} [{level}] {message}{Environment.NewLine}";
                File.AppendAllText(logPath, entry);
            }
            catch
            {
                // Fallback silenzioso per non bloccare Outlook
            }

            System.Diagnostics.Debug.WriteLine($"[ArcumAI {level}] {message}");
        }

        private void ThisAddIn_Shutdown(object sender, System.EventArgs e)
        {
            // Lasciare vuoto - logica critica gestita in Application_Quit
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