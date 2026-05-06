// Copyright (c) 2026 Nicolas Brianza
// Licensed under the MIT License. See LICENSE file in the project root.
using System;
using System.IO;
using Newtonsoft.Json;

namespace ArcumAI.OutlookAddIn.Core
{
    /// <summary>
    /// Handles reading and writing PluginConfig from/to JSON and app.config.
    /// Extracted from PluginConfig.
    /// </summary>
    internal static class PluginConfigLoader
    {
        /// <summary>
        /// Load configuration from file or use defaults.
        /// Priority: config.json > app.config > defaults
        /// </summary>
        internal static PluginConfig LoadConfiguration()
        {
            var config = new PluginConfig();

            // Try to load from JSON file first
            string configPath = GetConfigFilePath();
            if (File.Exists(configPath))
            {
                try
                {
                    string json = File.ReadAllText(configPath);
                    var fileConfig = JsonConvert.DeserializeObject<PluginConfig>(json);
                    if (fileConfig != null)
                    {
                        return fileConfig;
                    }
                }
                catch (Exception ex)
                {
                    System.Diagnostics.Debug.WriteLine($"Failed to load config from JSON: {ex.Message}");
                }
            }

            // Try to load from app.config
            try
            {
                LoadFromAppConfig(config);
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"Failed to load from app.config: {ex.Message}");
            }

            return config;
        }

        /// <summary>
        /// Set default configuration values on the given instance.
        /// </summary>
        internal static void SetDefaults(PluginConfig config)
        {
            config.ServerUrl = "ws://localhost:8080";
            config.ReconnectDelayMs = 5000;
            config.MaxReconnectAttempts = 10;
            config.ConnectionTimeoutMs = 30000;
            config.RequestTimeoutMs = 60000;
            config.MaxEmailResults = 10;
            config.EmailPreviewLength = 200;
            config.EnableLogging = true;
            config.LogLevel = "INFO";
            config.LogFilePath = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                "ArcumAI", "Outlook", "logs", "plugin.log"
            );
            config.UseSecureConnection = false;
            config.UserId = Environment.UserName.ToLower();
            config.ApiKey = "";
            config.AutoReconnect = true;
            config.HeartbeatIntervalMs = 30000;

            // Virtual Loopback defaults
            config.EnableVirtualLoopback = true;
            config.ArcumAIEmailAddress = "arcumai@arcumai.swiss";
            config.ArcumAIDisplayName = "ArcumAI Assistant";
            config.MaxAttachmentSizeMB = 25;
            config.MaxTotalAttachmentsMB = 50;
            config.MaxPayloadSizeMB = 30;
            config.LoopbackTimeoutMs = 3600000; // 1 hour for large documents
            config.ShowProcessingNotification = true;
        }

        /// <summary>
        /// Load settings from app.config file into the given instance.
        /// </summary>
        internal static void LoadFromAppConfig(PluginConfig config)
        {
            var appSettings = System.Configuration.ConfigurationManager.AppSettings;

            if (appSettings["ServerUrl"] != null)
                config.ServerUrl = appSettings["ServerUrl"];

            if (appSettings["ReconnectDelayMs"] != null && int.TryParse(appSettings["ReconnectDelayMs"], out int reconnectDelay))
                config.ReconnectDelayMs = reconnectDelay;

            if (appSettings["MaxReconnectAttempts"] != null && int.TryParse(appSettings["MaxReconnectAttempts"], out int maxAttempts))
                config.MaxReconnectAttempts = maxAttempts;

            if (appSettings["ConnectionTimeoutMs"] != null && int.TryParse(appSettings["ConnectionTimeoutMs"], out int connTimeout))
                config.ConnectionTimeoutMs = connTimeout;

            if (appSettings["RequestTimeoutMs"] != null && int.TryParse(appSettings["RequestTimeoutMs"], out int reqTimeout))
                config.RequestTimeoutMs = reqTimeout;

            if (appSettings["MaxEmailResults"] != null && int.TryParse(appSettings["MaxEmailResults"], out int maxResults))
                config.MaxEmailResults = maxResults;

            if (appSettings["EmailPreviewLength"] != null && int.TryParse(appSettings["EmailPreviewLength"], out int previewLen))
                config.EmailPreviewLength = previewLen;

            if (appSettings["EnableLogging"] != null && bool.TryParse(appSettings["EnableLogging"], out bool enableLog))
                config.EnableLogging = enableLog;

            if (appSettings["LogLevel"] != null)
                config.LogLevel = appSettings["LogLevel"];

            if (appSettings["LogFilePath"] != null && !string.IsNullOrWhiteSpace(appSettings["LogFilePath"]))
                config.LogFilePath = appSettings["LogFilePath"];

            if (appSettings["UseSecureConnection"] != null && bool.TryParse(appSettings["UseSecureConnection"], out bool useSecure))
                config.UseSecureConnection = useSecure;

            if (appSettings["UserId"] != null && !string.IsNullOrWhiteSpace(appSettings["UserId"]))
                config.UserId = appSettings["UserId"];

            if (appSettings["ApiKey"] != null)
                config.ApiKey = appSettings["ApiKey"];

            if (appSettings["AutoReconnect"] != null && bool.TryParse(appSettings["AutoReconnect"], out bool autoReconn))
                config.AutoReconnect = autoReconn;

            if (appSettings["HeartbeatIntervalMs"] != null && int.TryParse(appSettings["HeartbeatIntervalMs"], out int heartbeat))
                config.HeartbeatIntervalMs = heartbeat;

            // Virtual Loopback
            if (appSettings["EnableVirtualLoopback"] != null && bool.TryParse(appSettings["EnableVirtualLoopback"], out bool enableLb))
                config.EnableVirtualLoopback = enableLb;

            if (appSettings["ArcumAIEmailAddress"] != null && !string.IsNullOrWhiteSpace(appSettings["ArcumAIEmailAddress"]))
                config.ArcumAIEmailAddress = appSettings["ArcumAIEmailAddress"];

            if (appSettings["ArcumAIDisplayName"] != null && !string.IsNullOrWhiteSpace(appSettings["ArcumAIDisplayName"]))
                config.ArcumAIDisplayName = appSettings["ArcumAIDisplayName"];

            if (appSettings["MaxAttachmentSizeMB"] != null && int.TryParse(appSettings["MaxAttachmentSizeMB"], out int maxAttSize))
                config.MaxAttachmentSizeMB = maxAttSize;

            if (appSettings["MaxTotalAttachmentsMB"] != null && int.TryParse(appSettings["MaxTotalAttachmentsMB"], out int maxTotalAtt))
                config.MaxTotalAttachmentsMB = maxTotalAtt;

            if (appSettings["MaxPayloadSizeMB"] != null && int.TryParse(appSettings["MaxPayloadSizeMB"], out int maxPayload))
                config.MaxPayloadSizeMB = maxPayload;

            if (appSettings["LoopbackTimeoutMs"] != null && int.TryParse(appSettings["LoopbackTimeoutMs"], out int lbTimeout))
                config.LoopbackTimeoutMs = lbTimeout;

            if (appSettings["ShowProcessingNotification"] != null && bool.TryParse(appSettings["ShowProcessingNotification"], out bool showNotif))
                config.ShowProcessingNotification = showNotif;
        }

        /// <summary>
        /// Get the configuration file path.
        /// </summary>
        internal static string GetConfigFilePath()
        {
            // Try user's AppData folder first
            string appDataPath = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                "ArcumAI", "Outlook", "config.json"
            );

            if (File.Exists(appDataPath))
                return appDataPath;

            // Fall back to assembly directory
            string assemblyPath = Path.GetDirectoryName(
                System.Reflection.Assembly.GetExecutingAssembly().Location
            );
            return Path.Combine(assemblyPath, "config.json");
        }

        /// <summary>
        /// Create a sample configuration file with comments.
        /// </summary>
        public static void CreateSampleConfig(string path = null)
        {
            if (path == null)
            {
                path = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                    "ArcumAI", "Outlook", "config.json"
                );
            }

            var config = new PluginConfig();
            string directory = Path.GetDirectoryName(path);

            if (!Directory.Exists(directory))
            {
                Directory.CreateDirectory(directory);
            }

            // Create JSON with comments (stored as a separate readme)
            string json = JsonConvert.SerializeObject(config, Formatting.Indented);
            File.WriteAllText(path, json);

            // Create a readme file
            string readmePath = Path.Combine(directory, "config.README.txt");
            string readme = @"ArcumAI Outlook Plugin Configuration
=====================================

This file explains all configuration options in config.json

CONNECTION SETTINGS:
-------------------
ServerUrl (string): WebSocket server URL
  Default: ws://localhost:8080
  Example: wss://arcumai.example.com (for secure connection)

UseSecureConnection (bool): Use WSS instead of WS
  Default: false
  Required: true for any non-localhost ServerUrl

UserId (string): User identifier for authentication
  Default: Windows username (auto-detected)

ApiKey (string): Shared secret sent as X-API-Key header on every connection.
  Must match WS_API_KEY on the server (.env).
  Default: "" (empty — server-side check disabled when server key is also empty)
  Example: "ApiKey": "change-me-to-a-long-random-string"

RECONNECTION SETTINGS:
---------------------
AutoReconnect (bool): Automatically reconnect on disconnect
  Default: true

ReconnectDelayMs (int): Delay between reconnection attempts (milliseconds)
  Default: 5000 (5 seconds)

MaxReconnectAttempts (int): Maximum reconnection attempts before giving up
  Default: 10
  Set to -1 for infinite attempts

ConnectionTimeoutMs (int): Timeout for initial connection (milliseconds)
  Default: 30000 (30 seconds)

HeartbeatIntervalMs (int): Interval for sending heartbeat pings (milliseconds)
  Default: 30000 (30 seconds)
  Set to 0 to disable heartbeats

REQUEST SETTINGS:
----------------
RequestTimeoutMs (int): Timeout for MCP requests (milliseconds)
  Default: 60000 (60 seconds)

MaxEmailResults (int): Maximum number of emails to return per query
  Default: 10
  Range: 1-100

EmailPreviewLength (int): Number of characters in email body preview
  Default: 200
  Range: 50-1000

LOGGING SETTINGS:
----------------
EnableLogging (bool): Enable logging to file
  Default: true

LogLevel (string): Minimum log level to record
  Default: INFO
  Options: DEBUG, INFO, WARNING, ERROR

LogFilePath (string): Path to log file
  Default: %APPDATA%\ArcumAI\Outlook\logs\plugin.log

EXAMPLE CONFIGURATIONS:
======================

Production (Secure):
{
  ""ServerUrl"": ""wss://arcumai.company.com"",
  ""UseSecureConnection"": true,
  ""ApiKey"": ""change-me-to-a-long-random-string"",
  ""EnableLogging"": true,
  ""LogLevel"": ""WARNING"",
  ""AutoReconnect"": true,
  ""MaxReconnectAttempts"": 20
}

Development (Local):
{
  ""ServerUrl"": ""ws://localhost:8080"",
  ""UseSecureConnection"": false,
  ""EnableLogging"": true,
  ""LogLevel"": ""DEBUG"",
  ""AutoReconnect"": true,
  ""MaxReconnectAttempts"": 5
}

High-Performance:
{
  ""MaxEmailResults"": 20,
  ""EmailPreviewLength"": 300,
  ""RequestTimeoutMs"": 120000,
  ""HeartbeatIntervalMs"": 15000
}
";
            File.WriteAllText(readmePath, readme);
        }
    }
}
