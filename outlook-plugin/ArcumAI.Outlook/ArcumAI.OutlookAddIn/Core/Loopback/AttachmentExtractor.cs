using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using Outlook = Microsoft.Office.Interop.Outlook;
using Newtonsoft.Json.Linq;

namespace ArcumAI.OutlookAddIn.Core
{
    /// <summary>
    /// Extracts and encodes attachments from Outlook mail items.
    /// Filters inline attachments (email signatures) and enforces per-file and total size limits.
    /// </summary>
    internal class AttachmentExtractor
    {
        // MAPI property tag for Content-ID (identifies inline/embedded attachments like email signatures)
        private const string PR_ATTACH_CONTENT_ID = "http://schemas.microsoft.com/mapi/proptag/0x3712001F";

        private readonly PluginConfig _config;
        private readonly Action<string, string> _logAction;

        public AttachmentExtractor(PluginConfig config, Action<string, string> logAction)
        {
            _config = config;
            _logAction = logAction;
        }

        /// <summary>
        /// Extracts all real (non-inline) attachments from the mail as base64-encoded JObjects.
        /// Returns (extracted, skipped) where skipped contains human-readable descriptions of
        /// files that were excluded due to size limits or read errors.
        /// </summary>
        public (JArray extracted, List<string> skipped) ExtractAttachments(Outlook.MailItem mail)
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
        public static string GetMimeType(string fileName)
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
    }
}
