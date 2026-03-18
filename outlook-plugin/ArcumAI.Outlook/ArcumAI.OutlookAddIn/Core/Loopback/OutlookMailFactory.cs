// Copyright (c) 2026 Nicolas Brianza
// Licensed under the MIT License. See LICENSE file in the project root.
using System;
using System.Runtime.InteropServices;
using System.Threading;
using Outlook = Microsoft.Office.Interop.Outlook;
using Newtonsoft.Json.Linq;

namespace ArcumAI.OutlookAddIn.Core.Loopback
{
    /// <summary>
    /// Creates and manipulates Outlook mail items for the Virtual Loopback feature.
    /// Handles Sent Items simulation, compose window cleanup, and response email injection.
    /// Extracted from VirtualLoopbackHandler.
    /// </summary>
    internal class OutlookMailFactory
    {
        private readonly Outlook.Application _outlookApp;
        private readonly SynchronizationContext _syncContext;
        private readonly PluginConfig _config;
        private readonly Action<string, string> _logAction;

        public OutlookMailFactory(
            Outlook.Application outlookApp,
            SynchronizationContext syncContext,
            PluginConfig config,
            Action<string, string> logAction)
        {
            _outlookApp = outlookApp;
            _syncContext = syncContext;
            _config = config;
            _logAction = logAction;
        }

        // ---------------------------------------------------------------
        //  SIMULATE SENT ITEM
        // ---------------------------------------------------------------

        public void SimulateSentItem(Outlook.MailItem originalMail)
        {
            Outlook.NameSpace session = null;
            Outlook.MAPIFolder sentFolder = null;
            Outlook.MailItem copy = null;

            try
            {
                session = _outlookApp.Session;
                sentFolder = session.GetDefaultFolder(Outlook.OlDefaultFolders.olFolderSentMail);

                copy = (Outlook.MailItem)originalMail.Copy();

                // Set sent timestamp via MAPI property (SentOn is read-only in Outlook interop)
                try
                {
                    copy.PropertyAccessor.SetProperty(
                        "http://schemas.microsoft.com/mapi/proptag/0x00390040", // PR_CLIENT_SUBMIT_TIME
                        DateTime.Now);
                }
                catch (Exception ex)
                {
                    _logAction("DEBUG", $"MAPI [PR_CLIENT_SUBMIT_TIME]: {ex.Message}");
                }

                // Exchange Online stores sender addresses in X.500 DN format in the compose window item.
                // Explicitly override with SMTP-format addresses so the conversation view shows the
                // user's friendly display name instead of "/o=First Organization/ou=Exchange
                // Administrative Group(FYDIBOHF23SPDLT)/cn=Recip..." in the Sent Items copy.
                try
                {
                    Outlook.AddressEntry addrEntry = null;
                    Outlook.ExchangeUser exUser = null;
                    try
                    {
                        addrEntry = session.CurrentUser?.AddressEntry;
                        exUser = addrEntry?.GetExchangeUser();
                        string displayName = session.CurrentUser?.Name ?? "";
                        string smtpAddress = exUser?.PrimarySmtpAddress ?? "";

                        if (!string.IsNullOrEmpty(displayName))
                        {
                            copy.PropertyAccessor.SetProperty(
                                "http://schemas.microsoft.com/mapi/proptag/0x0042001F", // PR_SENT_REPRESENTING_NAME
                                displayName);
                            copy.PropertyAccessor.SetProperty(
                                "http://schemas.microsoft.com/mapi/proptag/0x0C1A001F", // PR_SENDER_NAME
                                displayName);
                        }
                        if (!string.IsNullOrEmpty(smtpAddress))
                        {
                            copy.PropertyAccessor.SetProperty(
                                "http://schemas.microsoft.com/mapi/proptag/0x0065001F", // PR_SENT_REPRESENTING_EMAIL_ADDRESS
                                smtpAddress);
                            copy.PropertyAccessor.SetProperty(
                                "http://schemas.microsoft.com/mapi/proptag/0x0C1F001F", // PR_SENDER_EMAIL_ADDRESS
                                smtpAddress);
                            copy.PropertyAccessor.SetProperty(
                                "http://schemas.microsoft.com/mapi/proptag/0x0064001F", // PR_SENT_REPRESENTING_ADDRTYPE
                                "SMTP");
                            copy.PropertyAccessor.SetProperty(
                                "http://schemas.microsoft.com/mapi/proptag/0x0C1E001F", // PR_SENDER_ADDRTYPE
                                "SMTP");
                        }
                    }
                    finally
                    {
                        if (exUser != null) Marshal.ReleaseComObject(exUser);
                        if (addrEntry != null) Marshal.ReleaseComObject(addrEntry);
                    }
                }
                catch (Exception ex)
                {
                    _logAction("DEBUG", $"MAPI [PR_SENDER on sent copy]: {ex.Message}");
                }

                copy.Move(sentFolder);
                _logAction("DEBUG", "VirtualLoopback: Email copied to Sent Items with sent timestamp");
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

        /// <summary>
        /// Closes the Outlook compose window (Inspector) for the intercepted email.
        /// Must be called on the main STA thread (before the first await in ProcessInterceptedEmail).
        /// </summary>
        public void CloseComposeWindow(Outlook.MailItem mail)
        {
            Outlook.Inspectors inspectors = null;
            try
            {
                inspectors = _outlookApp.Inspectors;
                string subject = mail.Subject ?? "";

                for (int i = inspectors.Count; i >= 1; i--)
                {
                    Outlook.Inspector inspector = null;
                    Outlook.MailItem currentMail = null;
                    try
                    {
                        inspector = inspectors[i];
                        currentMail = inspector.CurrentItem as Outlook.MailItem;
                        if (currentMail != null && currentMail.Subject == subject)
                        {
                            inspector.Close(Outlook.OlInspectorClose.olDiscard);
                            _logAction("DEBUG", "VirtualLoopback: Closed compose window");
                            return;
                        }
                    }
                    catch (Exception ex)
                    {
                        _logAction("DEBUG", $"VirtualLoopback: Could not close inspector: {ex.Message}");
                    }
                    finally
                    {
                        if (currentMail != null) Marshal.ReleaseComObject(currentMail);
                        if (inspector != null) Marshal.ReleaseComObject(inspector);
                    }
                }
            }
            catch (Exception ex)
            {
                _logAction("DEBUG", $"VirtualLoopback: Inspector enumeration failed: {ex.Message}");
            }
            finally
            {
                if (inspectors != null) Marshal.ReleaseComObject(inspectors);
            }
        }

        /// <summary>
        /// Closes the compose window (if still open) and deletes the intercepted mail item.
        /// Calling mail.Delete() directly on the reference we hold removes the item from
        /// wherever Outlook put it — Drafts (auto-save), Outbox, or in-memory — and closes
        /// any associated inspector without triggering a "Do you want to save?" prompt.
        /// Must be called on Outlook's main STA thread.
        /// </summary>
        public void DeleteInterceptedItem(Outlook.MailItem mail)
        {
            CloseComposeWindow(mail);   // close inspector first (best-effort)
            try
            {
                mail.Delete();
                _logAction("DEBUG", "VirtualLoopback: Deleted intercepted item");
            }
            catch (Exception ex)
            {
                _logAction("DEBUG", $"VirtualLoopback: Could not delete intercepted item: {ex.Message}");
            }
        }

        /// <summary>
        /// Removes the intercepted email from the Outbox if Outlook queued it there
        /// before the ItemSend cancellation took effect.
        /// </summary>
        public void DeleteFromOutbox(Outlook.MailItem originalMail)
        {
            Outlook.NameSpace session = null;
            Outlook.MAPIFolder outbox = null;
            Outlook.Items items = null;

            try
            {
                session = _outlookApp.Session;
                outbox = session.GetDefaultFolder(Outlook.OlDefaultFolders.olFolderOutbox);
                items = outbox.Items;

                string subject = originalMail.Subject ?? "";

                for (int i = items.Count; i >= 1; i--)
                {
                    Outlook.MailItem item = null;
                    try
                    {
                        item = items[i] as Outlook.MailItem;
                        if (item != null && item.Subject == subject)
                        {
                            item.Delete();
                            _logAction("DEBUG", "VirtualLoopback: Removed intercepted email from Outbox");
                            return;
                        }
                    }
                    catch (Exception ex)
                    {
                        _logAction("DEBUG", $"VirtualLoopback: Error checking Outbox item: {ex.Message}");
                    }
                    finally
                    {
                        if (item != null) Marshal.ReleaseComObject(item);
                    }
                }
            }
            catch (Exception ex)
            {
                _logAction("WARNING", $"VirtualLoopback: Could not clean Outbox: {ex.Message}");
            }
            finally
            {
                if (items != null) Marshal.ReleaseComObject(items);
                if (outbox != null) Marshal.ReleaseComObject(outbox);
                if (session != null) Marshal.ReleaseComObject(session);
            }
        }

        // ---------------------------------------------------------------
        //  RESPONSE EMAIL INJECTION
        // ---------------------------------------------------------------

        /// <summary>
        /// Marshal the email creation to Outlook's main STA thread.
        /// </summary>
        public void InjectResponseOnMainThread(JObject responseData, LoopbackRequest originalRequest = null)
        {
            if (_syncContext != null)
            {
                _syncContext.Post(_ => CreateResponseEmail(responseData, originalRequest), null);
            }
            else
            {
                // Fallback: direct call (may be on STA thread already)
                CreateResponseEmail(responseData, originalRequest);
            }
        }

        /// <summary>
        /// Create a new MailItem in the Inbox with the AI response.
        /// MUST be called on Outlook's main STA thread.
        /// </summary>
        public void CreateResponseEmail(JObject responseData, LoopbackRequest originalRequest = null)
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

                // PR_CONVERSATION_TOPIC is the key Outlook uses to group messages into conversations.
                // For a fresh CreateItem(), setting Subject does NOT automatically normalise it
                // (i.e. strip "Re:"), so the topic ends up as "Re: ..." instead of the bare subject.
                // We must set it explicitly to match the original email's topic.
                try
                {
                    responseItem.PropertyAccessor.SetProperty(
                        "http://schemas.microsoft.com/mapi/proptag/0x0070001F", // PR_CONVERSATION_TOPIC
                        subject); // bare subject, no "Re:" prefix
                }
                catch (Exception ex)
                {
                    _logAction("DEBUG", $"MAPI [PR_CONVERSATION_TOPIC]: {ex.Message}");
                }

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
                catch (Exception ex)
                {
                    _logAction("DEBUG", $"MAPI [PR_SENT_REPRESENTING]: {ex.Message}");
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
                    _logAction("DEBUG", $"MAPI [PR_MESSAGE_DELIVERY_TIME]: {ex.Message}");
                }

                // Set a unique Message-ID for the response
                try
                {
                    string messageId = $"<arcumai-{Guid.NewGuid()}@local>";
                    responseItem.PropertyAccessor.SetProperty(
                        "http://schemas.microsoft.com/mapi/proptag/0x1035001F", // PR_INTERNET_MESSAGE_ID
                        messageId);
                }
                catch (Exception ex)
                {
                    _logAction("DEBUG", $"MAPI [PR_INTERNET_MESSAGE_ID]: {ex.Message}");
                }

                // Set In-Reply-To and References for Outlook conversation threading
                string originalMsgId = responseData["original_message_id"]?.ToString();
                if (!string.IsNullOrEmpty(originalMsgId))
                {
                    try
                    {
                        responseItem.PropertyAccessor.SetProperty(
                            "http://schemas.microsoft.com/mapi/proptag/0x1042001F", // PR_IN_REPLY_TO_ID
                            originalMsgId);
                        responseItem.PropertyAccessor.SetProperty(
                            "http://schemas.microsoft.com/mapi/proptag/0x1039001F", // PR_INTERNET_REFERENCES
                            originalMsgId);
                    }
                    catch (Exception ex)
                    {
                        _logAction("DEBUG", $"MAPI [PR_IN_REPLY_TO_ID/PR_INTERNET_REFERENCES]: {ex.Message}");
                    }
                }

                // Set a reply-level PR_CONVERSATION_INDEX derived from the original compose window item.
                // Exchange Online computes ConversationID from the first 22 bytes of this property.
                // Sharing the same header ensures both the Sent Items copy and this response get the
                // same Exchange ConversationID, so they appear grouped in conversation view.
                byte[] parentIndex = originalRequest?.ConversationIndex;
                if (parentIndex != null && parentIndex.Length >= 22)
                {
                    byte[] replyIndex = CreateReplyConversationIndex(parentIndex);
                    if (replyIndex != null)
                    {
                        try
                        {
                            responseItem.PropertyAccessor.SetProperty(
                                "http://schemas.microsoft.com/mapi/proptag/0x00710102", // PR_CONVERSATION_INDEX
                                replyIndex);
                        }
                        catch (Exception ex)
                        {
                            _logAction("DEBUG", $"MAPI [PR_CONVERSATION_INDEX]: {ex.Message}");
                        }
                    }
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

        public JObject CreateTimeoutResponse(LoopbackRequest req)
        {
            return new JObject
            {
                ["request_id"] = req.RequestId,
                ["subject"] = req.OriginalSubject,
                ["conversation_id"] = req.ConversationId,
                ["original_message_id"] = req.OriginalMessageId ?? "",
                ["response_text"] = "The request has timed out. The document may be too large or complex. " +
                    "Please try again with a smaller file or contact your administrator.",
                ["response_html"] = "<p style='color:#e67e22; font-weight:bold;'>" +
                    "Timeout: The request has exceeded the time limit. " +
                    "The document may be too large or complex.</p>"
            };
        }

        // ---------------------------------------------------------------
        //  CONVERSATION INDEX HELPER
        // ---------------------------------------------------------------

        /// <summary>
        /// Extends a parent conversation index to a reply-level continuation (27 bytes).
        /// Exchange Online derives ConversationID from the first 22 bytes of this property,
        /// so keeping the same header makes the reply join the original email's conversation.
        ///
        /// Format (per MAPI spec):
        ///   Bytes 0-21 : header (version=0x01, 5-byte FILETIME, 16-byte GUID) — copied from parent
        ///   Bytes 22-26: response level block (5 zero bytes = reply, delta=0)
        /// </summary>
        private static byte[] CreateReplyConversationIndex(byte[] parentIndex)
        {
            if (parentIndex == null || parentIndex.Length < 22) return null;

            byte[] result = new byte[27];
            Array.Copy(parentIndex, result, 22);   // header from parent
            // bytes 22-26 stay 0x00: direction=reply, delta=0
            return result;
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

        public void ShowToastNotification(string title, string message)
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
        public string OriginalMessageId { get; set; }
        public byte[] ConversationIndex { get; set; }
        public DateTime SentAt { get; set; }
    }
}
