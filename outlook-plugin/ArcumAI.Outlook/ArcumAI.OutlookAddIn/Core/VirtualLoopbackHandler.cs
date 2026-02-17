using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using Outlook = Microsoft.Office.Interop.Outlook;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace ArcumAI.OutlookAddIn.Core
{
    /// <summary>
    /// Handles the Virtual Loopback feature: intercepts emails sent to ArcumAI,
    /// processes them through the AI server, and injects responses back into Inbox.
    /// </summary>
    public class VirtualLoopbackHandler
    {
        private readonly Outlook.Application _outlookApp;
        private readonly IMcpTransport _transport;
        private readonly PluginConfig _config;
        private readonly Action<string, string> _logAction;

        // Track pending loopback requests for timeout handling
        private readonly ConcurrentDictionary<string, LoopbackRequest> _pendingRequests
            = new ConcurrentDictionary<string, LoopbackRequest>();

        // SynchronizationContext captured at construction (Outlook's STA thread)
        private readonly SynchronizationContext _syncContext;

        public VirtualLoopbackHandler(
            Outlook.Application outlookApp,
            IMcpTransport transport,
            PluginConfig config,
            Action<string, string> logAction)
        {
            _outlookApp = outlookApp;
            _transport = transport;
            _config = config;
            _logAction = logAction;
            _syncContext = SynchronizationContext.Current;
        }

        // ---------------------------------------------------------------
        //  OUTLOOK CONTACT SETUP
        // ---------------------------------------------------------------

        /// <summary>
        /// Ensures an Outlook Contact exists for ArcumAI so users can find it
        /// in the address book and Outlook won't reject the address.
        /// </summary>
        public void EnsureContactExists()
        {
            Outlook.MAPIFolder contactsFolder = null;
            Outlook.Items items = null;
            try
            {
                contactsFolder = _outlookApp.Session.GetDefaultFolder(
                    Outlook.OlDefaultFolders.olFolderContacts);
                items = contactsFolder.Items;

                // Check if contact already exists
                string filter = $"[Email1Address] = '{_config.ArcumAIEmailAddress}'";
                var existing = items.Find(filter);
                if (existing != null)
                {
                    Marshal.ReleaseComObject(existing);
                    _logAction("DEBUG", "VirtualLoopback: ArcumAI contact already exists");
                    return;
                }

                // Create new contact
                Outlook.ContactItem contact = null;
                try
                {
                    contact = (Outlook.ContactItem)_outlookApp.CreateItem(
                        Outlook.OlItemType.olContactItem);
                    contact.FullName = _config.ArcumAIDisplayName;
                    contact.Email1Address = _config.ArcumAIEmailAddress;
                    contact.Email1DisplayName = _config.ArcumAIDisplayName;
                    contact.CompanyName = "ArcumAI";
                    contact.Body = "Virtual AI Assistant - emails to this contact are processed by ArcumAI locally.";
                    contact.Save();
                    _logAction("INFO", $"VirtualLoopback: Created Outlook contact '{_config.ArcumAIDisplayName}' <{_config.ArcumAIEmailAddress}>");
                }
                finally
                {
                    if (contact != null) Marshal.ReleaseComObject(contact);
                }
            }
            catch (Exception ex)
            {
                _logAction("WARNING", $"VirtualLoopback: Could not create ArcumAI contact: {ex.Message}");
            }
            finally
            {
                if (items != null) Marshal.ReleaseComObject(items);
                if (contactsFolder != null) Marshal.ReleaseComObject(contactsFolder);
            }
        }

        // ---------------------------------------------------------------
        //  RECIPIENT ANALYSIS
        // ---------------------------------------------------------------

        /// <summary>
        /// Analyzes recipients to determine how to handle this email.
        /// Returns: (hasArcum, hasRealRecipients, ccNames)
        /// </summary>
        private (bool hasArcum, bool hasReal, List<string> ccNames) AnalyzeRecipients(Outlook.MailItem mail)
        {
            bool hasArcum = false;
            bool hasReal = false;
            var ccNames = new List<string>();

            Outlook.Recipients recipients = null;
            try
            {
                recipients = mail.Recipients;
                string targetAddress = _config.ArcumAIEmailAddress.ToLower();

                for (int i = 1; i <= recipients.Count; i++)
                {
                    Outlook.Recipient recip = null;
                    try
                    {
                        recip = recipients[i];
                        string address = (recip.Address ?? "").ToLower();
                        string name = (recip.Name ?? "").ToLower();

                        if (address == targetAddress ||
                            name == "arcumai" ||
                            name == "arcum ai" ||
                            address.StartsWith("arcumai@"))
                        {
                            hasArcum = true;
                        }
                        else
                        {
                            hasReal = true;
                            // Collect display name for CC disclaimer
                            string displayName = recip.Name ?? recip.Address ?? "Unknown";
                            ccNames.Add(displayName);
                        }
                    }
                    finally
                    {
                        if (recip != null) Marshal.ReleaseComObject(recip);
                    }
                }
            }
            finally
            {
                if (recipients != null) Marshal.ReleaseComObject(recipients);
            }

            return (hasArcum, hasReal, ccNames);
        }

        /// <summary>
        /// Check if the email should be fully intercepted (Cancel=true).
        /// True when ArcumAI is the ONLY recipient.
        /// </summary>
        public bool ShouldIntercept(Outlook.MailItem mail)
        {
            if (!_config.EnableVirtualLoopback) return false;
            if (mail == null) return false;

            var (hasArcum, hasReal, _) = AnalyzeRecipients(mail);
            return hasArcum && !hasReal;
        }

        /// <summary>
        /// Check if the email should be processed in parallel (ArcumAI + real recipients).
        /// The email is sent normally but also processed by ArcumAI.
        /// </summary>
        public bool ShouldProcessInParallel(Outlook.MailItem mail)
        {
            if (!_config.EnableVirtualLoopback) return false;
            if (mail == null) return false;

            var (hasArcum, hasReal, _) = AnalyzeRecipients(mail);
            return hasArcum && hasReal;
        }

        /// <summary>
        /// Remove ArcumAI from recipients before the email is sent to real people.
        /// Called only for parallel processing (CC scenario).
        /// </summary>
        public void RemoveArcumRecipient(Outlook.MailItem mail)
        {
            Outlook.Recipients recipients = null;
            try
            {
                recipients = mail.Recipients;
                string targetAddress = _config.ArcumAIEmailAddress.ToLower();

                // Iterate in reverse for safe removal
                for (int i = recipients.Count; i >= 1; i--)
                {
                    Outlook.Recipient recip = null;
                    try
                    {
                        recip = recipients[i];
                        string address = (recip.Address ?? "").ToLower();
                        string name = (recip.Name ?? "").ToLower();

                        if (address == targetAddress ||
                            name == "arcumai" ||
                            name == "arcum ai" ||
                            address.StartsWith("arcumai@"))
                        {
                            recip.Delete();
                            _logAction("DEBUG", "VirtualLoopback: Removed ArcumAI from recipients list");
                        }
                    }
                    finally
                    {
                        if (recip != null) Marshal.ReleaseComObject(recip);
                    }
                }
                mail.Recipients.ResolveAll();
            }
            finally
            {
                if (recipients != null) Marshal.ReleaseComObject(recipients);
            }
        }

        // ---------------------------------------------------------------
        //  EMAIL PROCESSING (SEND TO SERVER)
        // ---------------------------------------------------------------

        /// <summary>
        /// Process an intercepted email: extract content, send to server via WebSocket.
        /// </summary>
        public async Task ProcessInterceptedEmail(Outlook.MailItem mail)
        {
            string requestId = Guid.NewGuid().ToString();

            try
            {
                // Extract email content
                string subject = mail.Subject ?? "(No Subject)";
                string body = mail.Body ?? "";
                string conversationId = mail.ConversationID ?? Guid.NewGuid().ToString();

                _logAction("INFO", $"VirtualLoopback: Processing email '{subject}' (ID: {requestId})");

                // Extract attachment metadata (Phase 1: text-only, attachments come in Phase 2)
                bool hasAttachments = false;
                Outlook.Attachments attachments = null;
                try
                {
                    attachments = mail.Attachments;
                    hasAttachments = attachments != null && attachments.Count > 0;
                }
                finally
                {
                    if (attachments != null) Marshal.ReleaseComObject(attachments);
                }

                // Collect CC recipient names for disclaimer
                var (_, _, ccNames) = AnalyzeRecipients(mail);

                // Build JSON-RPC message
                var payload = new JObject
                {
                    ["jsonrpc"] = "2.0",
                    ["method"] = "virtual_loopback/send_email",
                    ["id"] = requestId,
                    ["params"] = new JObject
                    {
                        ["subject"] = subject,
                        ["body"] = body,
                        ["conversation_id"] = conversationId,
                        ["timestamp"] = DateTime.UtcNow.ToString("o"),
                        ["has_attachments"] = hasAttachments,
                        ["cc_recipients"] = JArray.FromObject(ccNames),
                        ["attachments"] = new JArray() // Phase 2: will contain base64 data
                    }
                };

                // Track the pending request
                _pendingRequests[requestId] = new LoopbackRequest
                {
                    RequestId = requestId,
                    OriginalSubject = subject,
                    SentAt = DateTime.UtcNow,
                    ConversationId = conversationId
                };

                // Simulate "Sent" status: copy to Sent Items
                SimulateSentItem(mail);

                // Show processing notification
                if (_config.ShowProcessingNotification)
                {
                    ShowToastNotification("ArcumAI",
                        $"Processing: \"{subject}\"...");
                }

                // Send via WebSocket
                string jsonStr = payload.ToString(Formatting.None);
                _logAction("DEBUG", $"VirtualLoopback TX: {(jsonStr.Length > 500 ? jsonStr.Substring(0, 500) + "..." : jsonStr)}");
                await _transport.SendAsync(jsonStr);

                // Start timeout timer
                _ = Task.Run(async () =>
                {
                    await Task.Delay(_config.LoopbackTimeoutMs);
                    if (_pendingRequests.TryRemove(requestId, out var req))
                    {
                        _logAction("WARNING", $"VirtualLoopback: Timeout for request {requestId} ('{req.OriginalSubject}')");
                        InjectResponseOnMainThread(CreateTimeoutResponse(req));
                    }
                });
            }
            catch (Exception ex)
            {
                _logAction("ERROR", $"VirtualLoopback: Error processing email: {ex}");
                _pendingRequests.TryRemove(requestId, out _);
            }
        }

        // ---------------------------------------------------------------
        //  SIMULATE SENT ITEM
        // ---------------------------------------------------------------

        private void SimulateSentItem(Outlook.MailItem originalMail)
        {
            Outlook.NameSpace session = null;
            Outlook.MAPIFolder sentFolder = null;
            Outlook.MailItem copy = null;

            try
            {
                session = _outlookApp.Session;
                sentFolder = session.GetDefaultFolder(Outlook.OlDefaultFolders.olFolderSentMail);

                copy = (Outlook.MailItem)originalMail.Copy();
                copy.Move(sentFolder);

                _logAction("DEBUG", "VirtualLoopback: Email copied to Sent Items");
            }
            catch (Exception ex)
            {
                _logAction("WARNING", $"VirtualLoopback: Could not copy to Sent Items: {ex.Message}");
            }
            finally
            {
                if (copy != null) Marshal.ReleaseComObject(copy);
                if (sentFolder != null) Marshal.ReleaseComObject(sentFolder);
                if (session != null) Marshal.ReleaseComObject(session);
            }
        }

        // ---------------------------------------------------------------
        //  HANDLE SERVER RESPONSE
        // ---------------------------------------------------------------

        /// <summary>
        /// Handle a response from the server for a loopback request.
        /// Called from OnMessageFromArcum when method == "virtual_loopback/response".
        /// </summary>
        public void HandleServerResponse(JObject responseParams)
        {
            if (responseParams == null)
            {
                _logAction("WARNING", "VirtualLoopback: Received null response params");
                return;
            }

            string requestId = responseParams["request_id"]?.ToString() ?? "";

            // Remove from pending (cancel timeout)
            _pendingRequests.TryRemove(requestId, out var originalRequest);

            string subject = responseParams["subject"]?.ToString() ?? "ArcumAI";
            _logAction("INFO", $"VirtualLoopback: Received AI response for '{subject}' (ID: {requestId})");

            InjectResponseOnMainThread(responseParams);
        }

        /// <summary>
        /// Marshal the email creation to Outlook's main STA thread.
        /// </summary>
        private void InjectResponseOnMainThread(JObject responseData)
        {
            if (_syncContext != null)
            {
                _syncContext.Post(_ => CreateResponseEmail(responseData), null);
            }
            else
            {
                // Fallback: direct call (may be on STA thread already)
                CreateResponseEmail(responseData);
            }
        }

        /// <summary>
        /// Create a new MailItem in the Inbox with the AI response.
        /// MUST be called on Outlook's main STA thread.
        /// </summary>
        private void CreateResponseEmail(JObject responseData)
        {
            Outlook.NameSpace session = null;
            Outlook.MAPIFolder inbox = null;
            Outlook.MailItem responseItem = null;
            Outlook.MailItem movedItem = null;

            try
            {
                session = _outlookApp.Session;
                inbox = session.GetDefaultFolder(Outlook.OlDefaultFolders.olFolderInbox);

                // Create a new MailItem
                responseItem = (Outlook.MailItem)_outlookApp.CreateItem(Outlook.OlItemType.olMailItem);

                string subject = responseData["subject"]?.ToString() ?? "ArcumAI";
                responseItem.Subject = "Re: " + subject;

                // Set body (prefer HTML if available)
                string responseHtml = responseData["response_html"]?.ToString();
                string responseText = responseData["response_text"]?.ToString() ?? "(No response)";

                if (!string.IsNullOrEmpty(responseHtml))
                {
                    responseItem.HTMLBody = WrapInEmailHtml(responseHtml);
                }
                else
                {
                    responseItem.Body = responseText;
                }

                // Set as unread
                responseItem.UnRead = true;

                // Set sender display name via MAPI property (SenderName is read-only)
                try
                {
                    responseItem.PropertyAccessor.SetProperty(
                        "http://schemas.microsoft.com/mapi/proptag/0x0042001F", // PR_SENT_REPRESENTING_NAME
                        _config.ArcumAIDisplayName);
                    responseItem.PropertyAccessor.SetProperty(
                        "http://schemas.microsoft.com/mapi/proptag/0x0065001F", // PR_SENT_REPRESENTING_EMAIL_ADDRESS
                        _config.ArcumAIEmailAddress);
                }
                catch
                {
                    // MAPI properties may not be settable in all configurations
                }

                // Set delivery time to now
                try
                {
                    responseItem.PropertyAccessor.SetProperty(
                        "http://schemas.microsoft.com/mapi/proptag/0x0E060040", // PR_MESSAGE_DELIVERY_TIME
                        DateTime.Now);
                }
                catch (Exception ex)
                {
                    _logAction("DEBUG", $"VirtualLoopback: Could not set delivery time: {ex.Message}");
                }

                // Set a unique Message-ID for threading
                try
                {
                    string messageId = $"<arcumai-{Guid.NewGuid()}@local>";
                    responseItem.PropertyAccessor.SetProperty(
                        "http://schemas.microsoft.com/mapi/proptag/0x1035001F", // PR_INTERNET_MESSAGE_ID
                        messageId);
                }
                catch (Exception ex)
                {
                    _logAction("DEBUG", $"VirtualLoopback: Could not set Message-ID: {ex.Message}");
                }

                // Add category for visual distinction
                responseItem.Categories = "ArcumAI";

                // Save and move to Inbox
                responseItem.Save();
                movedItem = (Outlook.MailItem)responseItem.Move(inbox);

                _logAction("INFO", $"VirtualLoopback: Response email injected into Inbox: 'Re: {subject}'");

                // Show notification
                if (_config.ShowProcessingNotification)
                {
                    ShowToastNotification("ArcumAI",
                        $"Response ready: \"Re: {subject}\"");
                }
            }
            catch (Exception ex)
            {
                _logAction("ERROR", $"VirtualLoopback: Failed to create response email: {ex}");
            }
            finally
            {
                if (movedItem != null) Marshal.ReleaseComObject(movedItem);
                if (responseItem != null) Marshal.ReleaseComObject(responseItem);
                if (inbox != null) Marshal.ReleaseComObject(inbox);
                if (session != null) Marshal.ReleaseComObject(session);
            }
        }

        // ---------------------------------------------------------------
        //  TIMEOUT RESPONSE
        // ---------------------------------------------------------------

        private JObject CreateTimeoutResponse(LoopbackRequest req)
        {
            return new JObject
            {
                ["request_id"] = req.RequestId,
                ["subject"] = req.OriginalSubject,
                ["conversation_id"] = req.ConversationId,
                ["response_text"] = "The request has timed out. The document may be too large or complex. " +
                    "Please try again with a smaller file or contact your administrator.",
                ["response_html"] = "<p style='color:#e67e22; font-weight:bold;'>" +
                    "Timeout: The request has exceeded the time limit. " +
                    "The document may be too large or complex.</p>"
            };
        }

        // ---------------------------------------------------------------
        //  HTML WRAPPER
        // ---------------------------------------------------------------

        private string WrapInEmailHtml(string content)
        {
            return $@"<html>
<head><style>
    body {{ font-family: Calibri, Arial, sans-serif; font-size: 11pt; color: #333; }}
    .arcumai-header {{ background: #1a1a2e; color: #e94560; padding: 8px 16px;
                      border-radius: 4px; margin-bottom: 16px; font-weight: bold; }}
    .arcumai-body {{ padding: 8px 0; line-height: 1.6; }}
    pre {{ background: #f5f5f5; padding: 12px; border-radius: 4px;
          overflow-x: auto; font-size: 10pt; }}
    code {{ background: #f0f0f0; padding: 2px 6px; border-radius: 3px; }}
</style></head>
<body>
    <div class='arcumai-header'>{_config.ArcumAIDisplayName}</div>
    <div class='arcumai-body'>{content}</div>
</body>
</html>";
        }

        // ---------------------------------------------------------------
        //  TOAST NOTIFICATION
        // ---------------------------------------------------------------

        private void ShowToastNotification(string title, string message)
        {
            try
            {
                var icon = new System.Windows.Forms.NotifyIcon
                {
                    Visible = true,
                    Icon = System.Drawing.SystemIcons.Information,
                    BalloonTipTitle = title,
                    BalloonTipText = message,
                    BalloonTipIcon = System.Windows.Forms.ToolTipIcon.Info
                };
                icon.ShowBalloonTip(3000);

                // Auto-dispose after display
                var timer = new System.Timers.Timer(5000);
                timer.Elapsed += (s, e) =>
                {
                    icon.Visible = false;
                    icon.Dispose();
                    timer.Dispose();
                };
                timer.AutoReset = false;
                timer.Start();
            }
            catch (Exception ex)
            {
                _logAction("DEBUG", $"VirtualLoopback: Toast notification failed: {ex.Message}");
            }
        }
    }

    // ---------------------------------------------------------------
    //  INTERNAL DATA CLASSES
    // ---------------------------------------------------------------

    internal class LoopbackRequest
    {
        public string RequestId { get; set; }
        public string OriginalSubject { get; set; }
        public string ConversationId { get; set; }
        public DateTime SentAt { get; set; }
    }
}
