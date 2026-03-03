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
using ArcumAI.OutlookAddIn.Core.Transport;
using ArcumAI.OutlookAddIn.Core.Loopback;

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
        private readonly AttachmentExtractor _attachmentExtractor;
        private readonly ContactManager _contactManager;
        private readonly OutlookMailFactory _mailFactory;

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
            _attachmentExtractor = new AttachmentExtractor(config, logAction);
            _contactManager = new ContactManager(outlookApp, config, logAction);
            _mailFactory = new OutlookMailFactory(outlookApp, _syncContext, config, logAction);
        }

        // ---------------------------------------------------------------
        //  OUTLOOK CONTACT SETUP
        // ---------------------------------------------------------------

        /// <summary>
        /// Ensures an Outlook Contact exists for ArcumAI so users can find it
        /// in the address book and Outlook won't reject the address.
        /// </summary>
        public void EnsureContactExists() => _contactManager.EnsureContactExists();

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

                // Read PR_CONVERSATION_INDEX from the compose window item.
                // This 22-byte header is the basis Exchange uses to compute ConversationID.
                // We store it so CreateResponseEmail can set a reply-level continuation,
                // causing Exchange to assign the same ConversationID to both emails.
                byte[] conversationIndex = null;
                try
                {
                    object raw = mail.PropertyAccessor.GetProperty(
                        "http://schemas.microsoft.com/mapi/proptag/0x00710102"); // PR_CONVERSATION_INDEX
                    if (raw is byte[] b) conversationIndex = b;
                }
                catch (Exception ex)
                {
                    _logAction("DEBUG", $"MAPI [PR_CONVERSATION_INDEX read]: {ex.Message}");
                }

                // Generate and assign a stable Message-ID for email threading.
                // Stored on the original email so the response can set In-Reply-To.
                string originalMessageId = $"<arcumai-orig-{requestId}@local>";
                try
                {
                    mail.PropertyAccessor.SetProperty(
                        "http://schemas.microsoft.com/mapi/proptag/0x1035001F", // PR_INTERNET_MESSAGE_ID
                        originalMessageId);
                }
                catch (Exception ex)
                {
                    _logAction("DEBUG", $"MAPI [PR_INTERNET_MESSAGE_ID on original]: {ex.Message}");
                }

                _logAction("INFO", $"VirtualLoopback: Processing email '{subject}' (ID: {requestId})");

                // Extract attachments (base64 encoded, inline attachments skipped)
                var (attachmentsArray, skippedAttachments) = _attachmentExtractor.ExtractAttachments(mail);
                bool hasAttachments = attachmentsArray.Count > 0 || skippedAttachments.Count > 0;

                // If all attachments were rejected by size limits, inject the error reply
                // locally — no server round-trip needed. Limits come from server config handshake.
                if (hasAttachments && attachmentsArray.Count == 0)
                {
                    string bullets = string.Join("\n", skippedAttachments.ConvertAll(s => $"  • {s}"));
                    var errorResponse = new JObject
                    {
                        ["request_id"] = requestId,
                        ["subject"] = subject,
                        ["conversation_id"] = conversationId,
                        ["original_message_id"] = originalMessageId,
                        ["response_text"] =
                            $"Your email could not be processed because all attachments exceeded " +
                            $"the configured size limits (max {_config.MaxAttachmentSizeMB} MB per file, " +
                            $"{_config.MaxTotalAttachmentsMB} MB total).\n\n" +
                            $"Files that were too large:\n{bullets}\n\n" +
                            "Please compress the files or split them into smaller parts and try again."
                    };
                    _logAction("INFO", $"VirtualLoopback: All attachments skipped for '{subject}' — error reply injected locally");
                    if (_syncContext != null)
                        _syncContext.Post(_ => { _mailFactory.SimulateSentItem(mail); _mailFactory.DeleteInterceptedItem(mail); _mailFactory.CreateResponseEmail(errorResponse); }, null);
                    else
                        _logAction("ERROR", "VirtualLoopback: _syncContext is null — cannot post COM operations to STA thread. Skipping.");
                    return;
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
                        ["original_message_id"] = originalMessageId,
                        ["timestamp"] = DateTime.UtcNow.ToString("o"),
                        ["has_attachments"] = hasAttachments,
                        ["importance"] = (int)mail.Importance,  // 0=Low, 1=Normal, 2=High
                        ["cc_recipients"] = JArray.FromObject(ccNames),
                        ["attachments"] = attachmentsArray,
                        ["skipped_attachments"] = JArray.FromObject(skippedAttachments)
                    }
                };

                // Payload size guard — runs BEFORE registering _pendingRequests or posting COM cleanup.
                // This ensures an over-size abort has zero side-effects:
                //   Bug 1 fix: no prior COM post exists, so the single combined post below runs exactly once.
                //   Bug 2 fix: _pendingRequests is never touched, so no ghost entry on disconnect.
                string jsonStr = payload.ToString(Formatting.None);
                long payloadMB = System.Text.Encoding.UTF8.GetByteCount(jsonStr) / (1024 * 1024);
                long hardLimitMB = _config.MaxPayloadSizeMB;
                if (payloadMB >= hardLimitMB)
                {
                    _logAction("WARNING", $"VirtualLoopback: Payload {payloadMB} MB >= limit {hardLimitMB} MB — aborted");
                    var sizeErrorResp = new JObject
                    {
                        ["request_id"] = requestId,
                        ["subject"] = subject,
                        ["conversation_id"] = conversationId,
                        ["original_message_id"] = originalMessageId,
                        ["response_text"] =
                            $"The email payload ({payloadMB} MB) exceeds the maximum allowed size " +
                            $"({hardLimitMB} MB). Please reduce the number or size of attachments."
                    };
                    // Single combined post: COM cleanup + error response. Runs exactly once.
                    if (_syncContext != null)
                        _syncContext.Post(_ => { _mailFactory.SimulateSentItem(mail); _mailFactory.DeleteInterceptedItem(mail); _mailFactory.CreateResponseEmail(sizeErrorResp); }, null);
                    else
                        _logAction("ERROR", "VirtualLoopback: _syncContext is null — cannot post COM operations to STA thread. Skipping.");
                    return;  // _pendingRequests never touched — no ghost entry on disconnect
                }
                else if (payloadMB >= hardLimitMB * 7 / 10)
                    _logAction("WARNING", $"VirtualLoopback: Large payload: {payloadMB} MB (limit: {hardLimitMB} MB)");

                // Track the pending request (registered only after payload passes the size guard)
                _pendingRequests[requestId] = new LoopbackRequest
                {
                    RequestId = requestId,
                    OriginalSubject = subject,
                    SentAt = DateTime.UtcNow,
                    ConversationId = conversationId,
                    OriginalMessageId = originalMessageId,
                    ConversationIndex = conversationIndex
                };

                // Defer COM cleanup to AFTER ItemSend returns.
                // Running SimulateSentItem / DeleteInterceptedItem synchronously inside
                // the ItemSend handler blocks Outlook's STA thread and can cause a hang
                // (mail.Copy() + inspector.Close() during send is dangerous).
                // With Cancel=true the compose window stays open and the mail COM object
                // remains valid, so the Post delegate can safely access it.
                if (_syncContext != null)
                    _syncContext.Post(_ => { _mailFactory.SimulateSentItem(mail); _mailFactory.DeleteInterceptedItem(mail); }, null);
                else
                    _logAction("ERROR", "VirtualLoopback: _syncContext is null — cannot post COM operations to STA thread. Skipping.");

                // Send via WebSocket
                _logAction("DEBUG", $"VirtualLoopback TX: {(jsonStr.Length > 500 ? jsonStr.Substring(0, 500) + "..." : jsonStr)}");
                await _transport.SendAsync(jsonStr);

                // Show processing notification only after the send succeeded
                if (_config.ShowProcessingNotification)
                {
                    _mailFactory.ShowToastNotification("ArcumAI",
                        $"Processing: \"{subject}\"...");
                }

                // Start timeout timer
                _ = Task.Run(async () =>
                {
                    try
                    {
                        await Task.Delay(_config.LoopbackTimeoutMs);
                        if (_pendingRequests.TryRemove(requestId, out var req))
                        {
                            _logAction("WARNING", $"VirtualLoopback: Timeout for request {requestId} ('{req.OriginalSubject}')");
                            _mailFactory.InjectResponseOnMainThread(_mailFactory.CreateTimeoutResponse(req), req);
                        }
                    }
                    catch (Exception ex)
                    {
                        _logAction("ERROR", $"VirtualLoopback: Timeout handler error for {requestId}: {ex.Message}");
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

            // Remove from pending (cancel timeout) and pass the stored request to CreateResponseEmail
            // so it can use ConversationIndex for proper Exchange conversation threading.
            _pendingRequests.TryRemove(requestId, out var originalRequest);

            string subject = responseParams["subject"]?.ToString() ?? "ArcumAI";
            _logAction("INFO", $"VirtualLoopback: Received AI response for '{subject}' (ID: {requestId})");

            _mailFactory.InjectResponseOnMainThread(responseParams, originalRequest);
        }

        // ---------------------------------------------------------------
        //  DISCONNECT NOTIFICATION
        // ---------------------------------------------------------------

        /// <summary>
        /// Called when the WebSocket disconnects. Shows a toast if there are pending requests
        /// so the user knows processing is continuing on the server.
        /// Does NOT inject error emails — the server keeps processing and delivers on reconnect.
        /// The 1-hour timeout timer remains as a safety net if the server also goes down.
        /// </summary>
        public void NotifyPendingOnDisconnect()
        {
            int count = _pendingRequests.Count;
            if (count == 0) return;
            _logAction("WARNING",
                $"VirtualLoopback: {count} request(s) still processing — results will be delivered on reconnect");
            _mailFactory.ShowToastNotification("ArcumAI",
                $"{count} request(s) still processing.\n" +
                "Results will be delivered when the connection is restored.");
        }

    }
}
