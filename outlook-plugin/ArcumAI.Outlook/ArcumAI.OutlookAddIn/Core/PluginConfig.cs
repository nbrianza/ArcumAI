// Copyright (c) 2026 Nicolas Brianza
// Licensed under the MIT License. See LICENSE file in the project root.
using System;
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

            if (!ServerUrl.StartsWith("ws://") && !ServerUrl.StartsWith("wss://"))
            {
                errorMessage = "ServerUrl must start with ws:// or wss://";
                return false;
            }

            bool isLocalhost = ServerUrl.Contains("localhost") || ServerUrl.Contains("127.0.0.1");
            if (!UseSecureConnection && !isLocalhost)
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
