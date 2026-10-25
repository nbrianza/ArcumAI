// Copyright (c) 2026 Nicolas Brianza
// Licensed under the MIT License. See LICENSE file in the project root.
using System;
using System.Net;
using Newtonsoft.Json;

namespace ArcumAI.OutlookAddIn.Core
{
    /// <summary>
    /// Configuration manager for ArcumAI Outlook Plugin.
    /// Supports both app.config and JSON file configuration.
    /// Loading logic is in PluginConfigLoader.
    /// </summary>
    public class PluginConfig
    {
        private static PluginConfig _instance;
        private static readonly object _lock = new object();

        // Configuration Properties
        public string ServerUrl { get; set; }
        public int ReconnectDelayMs { get; set; }
        public int MaxReconnectAttempts { get; set; }
        public int ConnectionTimeoutMs { get; set; }
        public int RequestTimeoutMs { get; set; }
        public int MaxEmailResults { get; set; }
        public int EmailPreviewLength { get; set; }
        public bool EnableLogging { get; set; }
        public string LogLevel { get; set; }
        public string LogFilePath { get; set; }
        public bool UseSecureConnection { get; set; }
        public string UserId { get; set; }
        public string ApiKey { get; set; }
        public bool AutoReconnect { get; set; }
        public int HeartbeatIntervalMs { get; set; }

        // Virtual Loopback Configuration
        public bool EnableVirtualLoopback { get; set; }
        public string ArcumAIEmailAddress { get; set; }
        public string ArcumAIDisplayName { get; set; }
        public int MaxAttachmentSizeMB { get; set; }
        public int MaxTotalAttachmentsMB { get; set; }
        public int MaxPayloadSizeMB { get; set; }
        public int LoopbackTimeoutMs { get; set; }
        public bool ShowProcessingNotification { get; set; }

        // Debug switch (issue #39): when true, plugin.log also contains client content
        // (subjects, attachment file names, search queries). Local setting only, never
        // pushed by the server. Development only.
        public bool LogClientContent { get; set; }

        /// <summary>
        /// Singleton instance with thread-safe initialization.
        /// </summary>
        public static PluginConfig Instance
        {
            get
            {
                if (_instance == null)
                {
                    lock (_lock)
                    {
                        if (_instance == null)
                        {
                            _instance = PluginConfigLoader.LoadConfiguration();
                        }
                    }
                }
                return _instance;
            }
        }

        /// <summary>
        /// Internal constructor — used by PluginConfigLoader.LoadConfiguration()
        /// and JsonConvert.DeserializeObject.
        /// </summary>
        internal PluginConfig()
        {
            PluginConfigLoader.SetDefaults(this);
        }

        /// <summary>
        /// Client data for a log line: the value itself (CR and LF escaped, so it stays on
        /// one line) when LogClientContent is on, otherwise the redacted summary.
        /// Client content must reach plugin.log only through this helper (#39).
        /// </summary>
        internal static string Content(string value, string redacted)
        {
            if (!Instance.LogClientContent)
                return redacted;
            return (value ?? "").Replace("\r", "\\r").Replace("\n", "\\n");
        }

        /// <summary>
        /// Save current configuration to JSON file.
        /// </summary>
        public void Save()
        {
            try
            {
                string configPath = PluginConfigLoader.GetConfigFilePath();
                string directory = System.IO.Path.GetDirectoryName(configPath);

                if (!System.IO.Directory.Exists(directory))
                {
                    System.IO.Directory.CreateDirectory(directory);
                }

                string json = JsonConvert.SerializeObject(this, Formatting.Indented);
                System.IO.File.WriteAllText(configPath, json);
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"Failed to save configuration: {ex.Message}");
                throw;
            }
        }

        /// <summary>
        /// Validate configuration values.
        /// </summary>
        public bool Validate(out string errorMessage)
        {
            errorMessage = null;

            if (string.IsNullOrWhiteSpace(ServerUrl))
            {
                errorMessage = "ServerUrl cannot be empty";
                return false;
            }

            if (!ServerUrl.StartsWith("ws://", StringComparison.OrdinalIgnoreCase) &&
                !ServerUrl.StartsWith("wss://", StringComparison.OrdinalIgnoreCase))
            {
                errorMessage = "ServerUrl must start with ws:// or wss://";
                return false;
            }

            if (IsCleartextRemoteConnection())
            {
                errorMessage = "UseSecureConnection must be true for non-localhost connections";
                return false;
            }

            if (ReconnectDelayMs < 1000)
            {
                errorMessage = "ReconnectDelayMs must be at least 1000ms";
                return false;
            }

            if (MaxReconnectAttempts < -1)
            {
                errorMessage = "MaxReconnectAttempts must be -1 or greater";
                return false;
            }

            if (MaxEmailResults < 1 || MaxEmailResults > 100)
            {
                errorMessage = "MaxEmailResults must be between 1 and 100";
                return false;
            }

            if (EmailPreviewLength < 50 || EmailPreviewLength > 1000)
            {
                errorMessage = "EmailPreviewLength must be between 50 and 1000";
                return false;
            }

            string[] validLogLevels = { "DEBUG", "INFO", "WARNING", "ERROR" };
            if (Array.IndexOf(validLogLevels, (LogLevel ?? "").ToUpper()) == -1)
            {
                errorMessage = "LogLevel must be DEBUG, INFO, WARNING, or ERROR";
                return false;
            }

            // Virtual Loopback validation
            if (EnableVirtualLoopback && string.IsNullOrWhiteSpace(ArcumAIEmailAddress))
            {
                errorMessage = "ArcumAIEmailAddress must be set when Virtual Loopback is enabled";
                return false;
            }

            if (MaxAttachmentSizeMB < 1 || MaxAttachmentSizeMB > 100)
            {
                errorMessage = "MaxAttachmentSizeMB must be between 1 and 100";
                return false;
            }

            if (MaxTotalAttachmentsMB < 1 || MaxTotalAttachmentsMB > 200)
            {
                errorMessage = "MaxTotalAttachmentsMB must be between 1 and 200";
                return false;
            }

            if (LoopbackTimeoutMs < 10000)
            {
                errorMessage = "LoopbackTimeoutMs must be at least 10000ms (10 seconds)";
                return false;
            }

            return true;
        }

        /// <summary>
        /// True when the connection would be cleartext (ws:// and UseSecureConnection false)
        /// to a host that is not loopback. A wss:// address (any letter case) is always
        /// encrypted. Single place of the TLS rule, used by Validate().
        /// </summary>
        internal bool IsCleartextRemoteConnection()
        {
            string url = ServerUrl ?? "";
            bool tls = UseSecureConnection || url.StartsWith("wss://", StringComparison.OrdinalIgnoreCase);
            return !tls && !IsLoopbackServerUrl(url);
        }

        /// <summary>
        /// True only if the parsed host of the URL is exactly "localhost" or a loopback
        /// IP address (127.x.x.x, [::1]). Host names that merely contain "localhost" or
        /// "127.0.0.1" (for example localhost.example.test) and unparsable URLs are not local.
        /// </summary>
        private static bool IsLoopbackServerUrl(string url)
        {
            if (!Uri.TryCreate(url, UriKind.Absolute, out Uri uri))
                return false;

            string host = uri.DnsSafeHost;
            if (string.Equals(host, "localhost", StringComparison.OrdinalIgnoreCase))
                return true;

            return IPAddress.TryParse(host, out IPAddress ip) && IPAddress.IsLoopback(ip);
        }

        /// <summary>
        /// Get the effective WebSocket URL with user ID.
        /// </summary>
        public string GetWebSocketUrl()
        {
            string baseUrl = ServerUrl.TrimEnd('/');
            if (UseSecureConnection && baseUrl.StartsWith("ws://", StringComparison.OrdinalIgnoreCase))
                baseUrl = "wss://" + baseUrl.Substring(5);
            return $"{baseUrl}/ws/outlook/{UserId}";
        }
    }
}
