using System;
using System.Collections.Generic;
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
        private string _currentUser;

        // URL del server
        private const string SERVER_URL = "ws://localhost:8080";

        private void ThisAddIn_Startup(object sender, System.EventArgs e)
        {
            // 1. Identifica l'utente
            _currentUser = Environment.UserName.ToLower();

            // 2. Setup Trasporto
            _transport = new WebSocketTransport();
            _transport.MessageReceived += OnMessageFromArcum;

            // 3. Connessione
            ConnectToArcum();

            // --- FIX PER SHUTDOWN ---
            // Ci agganciamo all'evento Quit dell'applicazione, che è più affidabile
            ((Outlook.ApplicationEvents_11_Event)this.Application).Quit += new Outlook.ApplicationEvents_11_QuitEventHandler(Application_Quit);
        }

        private async void ConnectToArcum()
        {
            try
            {
                await _transport.ConnectAsync(SERVER_URL, _currentUser);
            }
            catch (Exception)
            {
                // Gestione silenziosa per non bloccare Outlook
            }
        }

        // Questo sostituisce la logica di Shutdown deprecata
        private void Application_Quit()
        {
            if (_transport != null && _transport.IsConnected)
            {
                // Tentativo "Best Effort" di chiudere pulito
                // Nota: non possiamo usare await qui perché Outlook sta chiudendo
                _ = _transport.SendAsync("{\"method\":\"closing\"}");
            }
        }

        private void OnMessageFromArcum(object sender, string json)
        {
            try
            {
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

                _transport.SendAsync(response.ToString(Formatting.None));
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"Errore gestione messaggio: {ex}");
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
                foreach (object item in items)
                {
                    if (item is Outlook.MailItem mail)
                    {
                        if (!string.IsNullOrEmpty(query))
                        {
                            string searchContent = (mail.Subject + " " + mail.SenderName).ToLower();
                            if (!searchContent.Contains(query.ToLower())) continue;
                        }

                        string snippet = mail.Body != null && mail.Body.Length > 100 ? mail.Body.Substring(0, 100) + "..." : mail.Body;
                        results.Add($"[{mail.ReceivedTime:g}] DA: {mail.SenderName} | OGGETTO: {mail.Subject} | ANTEPRIMA: {snippet}");
                        count++;
                    }
                    if (count >= 5) break;
                }
            }
            catch (Exception ex)
            {
                results.Add($"Errore lettura mail: {ex.Message}");
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
            }
            catch (Exception ex)
            {
                results.Add($"Errore calendario: {ex.Message}");
            }

            if (results.Count == 0) results.Add("Nessun appuntamento trovato.");
            return results;
        }

        private void ThisAddIn_Shutdown(object sender, System.EventArgs e)
        {
            // Lasciare vuoto o rimuovere logica critica da qui
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