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

                // Extract attachments (base64 encoded, inline attachments skipped)
                var (attachmentsArray, skippedAttachments) = ExtractAttachments(mail);
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
                        ["response_text"] =
                            $"Your email could not be processed because all attachments exceeded " +
                            $"the configured size limits (max {_config.MaxAttachmentSizeMB} MB per file, " +
                            $"{_config.MaxTotalAttachmentsMB} MB total).\n\n" +
                            $"Files that were too large:\n{bullets}\n\n" +
                            "Please compress the files or split them into smaller parts and try again."
                    };
                    _logAction("INFO", $"VirtualLoopback: All attachments skipped for '{subject}' — error reply injected locally");
                    if (_syncContext != null)
                        _syncContext.Post(_ => { SimulateSentItem(mail); CloseComposeWindow(mail); DeleteFromOutbox(mail); CreateResponseEmail(errorResponse); }, null);
                    else
                    { SimulateSentItem(mail); CloseComposeWindow(mail); DeleteFromOutbox(mail); CreateResponseEmail(errorResponse); }
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
                        ["timestamp"] = DateTime.UtcNow.ToString("o"),
                        ["has_attachments"] = hasAttachments,
                        ["cc_recipients"] = JArray.FromObject(ccNames),
                        ["attachments"] = attachmentsArray,
                        ["skipped_attachments"] = JArray.FromObject(skippedAttachments)
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

                // Defer COM cleanup to AFTER ItemSend returns.
                // Running SimulateSentItem / CloseComposeWindow / DeleteFromOutbox
                // synchronously inside the ItemSend handler blocks Outlook's STA thread
                // and can cause a hang (mail.Copy() + inspector.Close() during send is dangerous).
                // With Cancel=true the compose window stays open and the mail COM object
                // remains valid, so the Post delegate can safely access it.
                if (_syncContext != null)
                    _syncContext.Post(_ => { SimulateSentItem(mail); CloseComposeWindow(mail); DeleteFromOutbox(mail); }, null);
                else
                {
                    SimulateSentItem(mail);
                    CloseComposeWindow(mail);
                    DeleteFromOutbox(mail);
                }

                // Send via WebSocket
                string jsonStr = payload.ToString(Formatting.None);
                _logAction("DEBUG", $"VirtualLoopback TX: {(jsonStr.Length > 500 ? jsonStr.Substring(0, 500) + "..." : jsonStr)}");
                await _transport.SendAsync(jsonStr);

                // Show processing notification only after the send succeeded
                if (_config.ShowProcessingNotification)
                {
                    ShowToastNotification("ArcumAI",
                        $"Processing: \"{subject}\"...");
                }

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
        //  ATTACHMENT EXTRACTION
        // ---------------------------------------------------------------

        // MAPI property tag for Content-ID (identifies inline/embedded attachments like email signatures)
        private const string PR_ATTACH_CONTENT_ID = "http://schemas.microsoft.com/mapi/proptag/0x3712001F";

        /// <summary>
        /// Extracts all real (non-inline) attachments from the mail as base64-encoded JObjects.
        /// Returns (extracted, skipped) where skipped contains human-readable descriptions of
        /// files that were excluded due to size limits or read errors.
        /// </summary>
        private (JArray extracted, List<string> skipped) ExtractAttachments(Outlook.MailItem mail)
        {
            var result = new JArray();
            var skipped = new List<string>();
            Outlook.Attachments attachments = null;

            long maxFileBytes = (long)_config.MaxAttachmentSizeMB * 1024 * 1024;
            long maxTotalBytes = (long)_config.MaxTotalAttachmentsMB * 1024 * 1024;
            long totalBytes = 0;

            try
            {
                attachments = mail.Attachments;
                int count = attachments?.Count ?? 0;

                if (count == 0) return (result, skipped);

                _logAction("INFO", $"VirtualLoopback: Found {count} attachment(s), extracting...");

                for (int i = 1; i <= count; i++)
                {
                    Outlook.Attachment att = null;
                    string tempPath = null;

                    try
                    {
                        att = attachments[i];

                        // Skip inline attachments (embedded images used in email signatures/body)
                        try
                        {
                            string contentId = att.PropertyAccessor.GetProperty(PR_ATTACH_CONTENT_ID) as string;
                            if (!string.IsNullOrEmpty(contentId))
                            {
                                _logAction("DEBUG", $"VirtualLoopback: Skipping inline attachment '{att.FileName}' (Content-ID: {contentId})");
                                continue;
                            }
                        }
                        catch
                        {
                            // Property not present means it's a real attachment - proceed
                        }

                        string fileName = att.FileName ?? $"attachment_{i}";
                        long fileSize = att.Size;

                        // Per-file size limit
                        if (fileSize > maxFileBytes)
                        {
                            string reason = $"{fileName} ({fileSize / 1024 / 1024} MB, limit is {_config.MaxAttachmentSizeMB} MB)";
                            _logAction("WARNING", $"VirtualLoopback: Skipping '{fileName}' — exceeds per-file size limit");
                            skipped.Add(reason);
                            continue;
                        }

                        // Total size limit
                        if (totalBytes + fileSize > maxTotalBytes)
                        {
                            string reason = $"{fileName} (total limit of {_config.MaxTotalAttachmentsMB} MB would be exceeded)";
                            _logAction("WARNING", $"VirtualLoopback: Skipping '{fileName}' — total size limit reached");
                            skipped.Add(reason);
                            continue;
                        }

                        // Save to temp file and read bytes
                        tempPath = Path.Combine(Path.GetTempPath(), $"arcumai_{Guid.NewGuid()}_{fileName}");
                        att.SaveAsFile(tempPath);
                        byte[] bytes = File.ReadAllBytes(tempPath);
                        string base64 = Convert.ToBase64String(bytes);

                        // Determine MIME type from extension
                        string mimeType = GetMimeType(fileName);

                        result.Add(new JObject
                        {
                            ["file_name"] = fileName,
                            ["content_type"] = mimeType,
                            ["size_bytes"] = fileSize,
                            ["content_base64"] = base64
                        });

                        totalBytes += fileSize;
                        _logAction("INFO", $"VirtualLoopback: Extracted '{fileName}' ({fileSize / 1024} KB, {mimeType})");
                    }
                    catch (Exception ex)
                    {
                        string name = att != null ? (att.FileName ?? $"#{i}") : $"#{i}";
                        _logAction("WARNING", $"VirtualLoopback: Could not extract attachment '{name}': {ex.Message}");
                        skipped.Add($"{name} (read error: {ex.Message})");
                    }
                    finally
                    {
                        if (att != null) Marshal.ReleaseComObject(att);
                        if (tempPath != null && File.Exists(tempPath))
                        {
                            try { File.Delete(tempPath); } catch { }
                        }
                    }
                }

                _logAction("INFO", $"VirtualLoopback: Extracted {result.Count}/{count} attachment(s) ({totalBytes / 1024} KB total)" +
                    (skipped.Count > 0 ? $", skipped {skipped.Count}" : ""));
            }
            catch (Exception ex)
            {
                _logAction("ERROR", $"VirtualLoopback: Attachment extraction failed: {ex.Message}");
            }
            finally
            {
                if (attachments != null) Marshal.ReleaseComObject(attachments);
            }

            return (result, skipped);
        }

        /// <summary>
        /// Returns a MIME type string for common file extensions.
        /// </summary>
        private static string GetMimeType(string fileName)
        {
            string ext = Path.GetExtension(fileName)?.ToLowerInvariant() ?? "";
            switch (ext)
            {
                case ".pdf":  return "application/pdf";
                case ".docx": return "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
                case ".doc":  return "application/msword";
                case ".xlsx": return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
                case ".xls":  return "application/vnd.ms-excel";
                case ".pptx": return "application/vnd.openxmlformats-officedocument.presentationml.presentation";
                case ".txt":  return "text/plain";
                case ".csv":  return "text/csv";
                case ".md":   return "text/markdown";
                case ".msg":  return "application/vnd.ms-outlook";
                case ".eml":  return "message/rfc822";
                case ".png":  return "image/png";
                case ".jpg":
                case ".jpeg": return "image/jpeg";
                case ".gif":  return "image/gif";
                case ".tiff":
                case ".tif":  return "image/tiff";
                default:      return "application/octet-stream";
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

                // Set sent timestamp via MAPI property (SentOn is read-only in Outlook interop)
                try
                {
                    copy.PropertyAccessor.SetProperty(
                        "http://schemas.microsoft.com/mapi/proptag/0x00390040", // PR_CLIENT_SUBMIT_TIME
                        DateTime.Now);
                }
                catch (Exception ex)
                {
                    _logAction("DEBUG", $"VirtualLoopback: Could not set sent time: {ex.Message}");
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
        private void CloseComposeWindow(Outlook.MailItem mail)
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
        /// Removes the intercepted email from the Outbox if Outlook queued it there
        /// before the ItemSend cancellation took effect.
        /// </summary>
        private void DeleteFromOutbox(Outlook.MailItem originalMail)
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
