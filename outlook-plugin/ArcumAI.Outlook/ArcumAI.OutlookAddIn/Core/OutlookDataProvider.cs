// Copyright (c) 2026 Nicolas Brianza
// Licensed under the MIT License. See LICENSE file in the project root.
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using Outlook = Microsoft.Office.Interop.Outlook;

namespace ArcumAI.OutlookAddIn.Core
{
    /// <summary>
    /// Provides access to Outlook data (emails, calendar) for MCP tool calls.
    /// Extracted from ThisAddIn.
    /// </summary>
    internal class OutlookDataProvider
    {
        private readonly Outlook.Application _outlookApp;
        private readonly PluginConfig _config;
        private readonly Action<string, string> _logAction;

        public OutlookDataProvider(
            Outlook.Application outlookApp,
            PluginConfig config,
            Action<string, string> logAction)
        {
            _outlookApp = outlookApp;
            _config = config;
            _logAction = logAction;
        }

        public List<string> GetEmails(string query)
        {
            var results = new List<string>();
            Outlook.NameSpace session = null;
            Outlook.MAPIFolder inbox = null;
            Outlook.Items items = null;

            try
            {
                session = _outlookApp.Session;
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

                _logAction("INFO", $"GetEmails: found {results.Count} emails for query '{query}'");
            }
            catch (Exception ex)
            {
                results.Add($"Error reading emails: {ex.Message}");
                _logAction("ERROR", $"GetEmails error: {ex.Message}");
            }
            finally
            {
                if (items != null) Marshal.ReleaseComObject(items);
                if (inbox != null) Marshal.ReleaseComObject(inbox);
                if (session != null) Marshal.ReleaseComObject(session);
            }
            return results;
        }

        public List<string> GetCalendar(string filter)
        {
            var results = new List<string>();
            Outlook.NameSpace session = null;
            Outlook.MAPIFolder calendar = null;
            Outlook.Items items = null;
            Outlook.Items restrictedItems = null;

            try
            {
                session = _outlookApp.Session;
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

                _logAction("INFO", $"GetCalendar: found {results.Count} appointments for filter '{filter}'");
            }
            catch (Exception ex)
            {
                results.Add($"Calendar error: {ex.Message}");
                _logAction("ERROR", $"GetCalendar error: {ex.Message}");
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
    }
}
