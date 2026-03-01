using System;
using System.IO;
using Newtonsoft.Json;

namespace ArcumAI.OutlookAddIn.Core
{
    /// <summary>
    /// Configuration manager for ArcumAI Outlook Plugin
    /// Supports both app.config and JSON file configuration
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
        /// Singleton instance with thread-safe initialization
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
                            _instance = LoadConfiguration();
                        }
                    }
                }
                return _instance;
            }
        }

        /// <summary>
        /// Private constructor for singleton pattern
        /// </summary>
        private PluginConfig()
        {
            // Set defaults
            SetDefaults();
        }

        /// <summary>
        /// Set default configuration values
        /// </summary>
        private void SetDefaults()
        {
            ServerUrl = "ws://localhost:8080";
            ReconnectDelayMs = 5000;
            MaxReconnectAttempts = 10;
            ConnectionTimeoutMs = 30000;
            RequestTimeoutMs = 60000;
            MaxEmailResults = 10;
            EmailPreviewLength = 200;
            EnableLogging = true;
            LogLevel = "INFO";
            LogFilePath = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                "ArcumAI", "Outlook", "logs", "plugin.log"
            );
            UseSecureConnection = false;
            UserId = Environment.UserName.ToLower();
            AutoReconnect = true;
            HeartbeatIntervalMs = 30000;

            // Virtual Loopback defaults
            EnableVirtualLoopback = true;
            ArcumAIEmailAddress = "arcumai@arcumai.swiss";
            ArcumAIDisplayName = "ArcumAI Assistant";
            MaxAttachmentSizeMB = 25;
            MaxTotalAttachmentsMB = 50;
            MaxPayloadSizeMB = 30;
            LoopbackTimeoutMs = 3600000; // 1 hour for large documents
            ShowProcessingNotification = true;
        }

        /// <summary>
        /// Load configuration from file or use defaults
        /// Priority: config.json > app.config > defaults
        /// </summary>
        private static PluginConfig LoadConfiguration()
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
                config.LoadFromAppConfig();
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"Failed to load from app.config: {ex.Message}");
            }

            return config;
        }

        /// <summary>
        /// Load settings from app.config file
        /// </summary>
        private void LoadFromAppConfig()
        {
            var appSettings = System.Configuration.ConfigurationManager.AppSettings;

            if (appSettings["ServerUrl"] != null)
                ServerUrl = appSettings["ServerUrl"];

            if (appSettings["ReconnectDelayMs"] != null && int.TryParse(appSettings["ReconnectDelayMs"], out int reconnectDelay))
                ReconnectDelayMs = reconnectDelay;

            if (appSettings["MaxReconnectAttempts"] != null && int.TryParse(appSettings["MaxReconnectAttempts"], out int maxAttempts))
                MaxReconnectAttempts = maxAttempts;

            if (appSettings["ConnectionTimeoutMs"] != null && int.TryParse(appSettings["ConnectionTimeoutMs"], out int connTimeout))
                ConnectionTimeoutMs = connTimeout;

            if (appSettings["RequestTimeoutMs"] != null && int.TryParse(appSettings["RequestTimeoutMs"], out int reqTimeout))
                RequestTimeoutMs = reqTimeout;

            if (appSettings["MaxEmailResults"] != null && int.TryParse(appSettings["MaxEmailResults"], out int maxResults))
                MaxEmailResults = maxResults;

            if (appSettings["EmailPreviewLength"] != null && int.TryParse(appSettings["EmailPreviewLength"], out int previewLen))
                EmailPreviewLength = previewLen;

            if (appSettings["EnableLogging"] != null && bool.TryParse(appSettings["EnableLogging"], out bool enableLog))
                EnableLogging = enableLog;

            if (appSettings["LogLevel"] != null)
                LogLevel = appSettings["LogLevel"];

            if (appSettings["LogFilePath"] != null && !string.IsNullOrWhiteSpace(appSettings["LogFilePath"]))
                LogFilePath = appSettings["LogFilePath"];

            if (appSettings["UseSecureConnection"] != null && bool.TryParse(appSettings["UseSecureConnection"], out bool useSecure))
                UseSecureConnection = useSecure;

            if (appSettings["UserId"] != null && !string.IsNullOrWhiteSpace(appSettings["UserId"]))
                UserId = appSettings["UserId"];

            if (appSettings["AutoReconnect"] != null && bool.TryParse(appSettings["AutoReconnect"], out bool autoReconn))
                AutoReconnect = autoReconn;

            if (appSettings["HeartbeatIntervalMs"] != null && int.TryParse(appSettings["HeartbeatIntervalMs"], out int heartbeat))
                HeartbeatIntervalMs = heartbeat;

            // Virtual Loopback
            if (appSettings["EnableVirtualLoopback"] != null && bool.TryParse(appSettings["EnableVirtualLoopback"], out bool enableLb))
                EnableVirtualLoopback = enableLb;

            if (appSettings["ArcumAIEmailAddress"] != null && !string.IsNullOrWhiteSpace(appSettings["ArcumAIEmailAddress"]))
                ArcumAIEmailAddress = appSettings["ArcumAIEmailAddress"];

            if (appSettings["ArcumAIDisplayName"] != null && !string.IsNullOrWhiteSpace(appSettings["ArcumAIDisplayName"]))
                ArcumAIDisplayName = appSettings["ArcumAIDisplayName"];

            if (appSettings["MaxAttachmentSizeMB"] != null && int.TryParse(appSettings["MaxAttachmentSizeMB"], out int maxAttSize))
                MaxAttachmentSizeMB = maxAttSize;

            if (appSettings["MaxTotalAttachmentsMB"] != null && int.TryParse(appSettings["MaxTotalAttachmentsMB"], out int maxTotalAtt))
                MaxTotalAttachmentsMB = maxTotalAtt;

            if (appSettings["MaxPayloadSizeMB"] != null && int.TryParse(appSettings["MaxPayloadSizeMB"], out int maxPayload))
                MaxPayloadSizeMB = maxPayload;

            if (appSettings["LoopbackTimeoutMs"] != null && int.TryParse(appSettings["LoopbackTimeoutMs"], out int lbTimeout))
                LoopbackTimeoutMs = lbTimeout;

            if (appSettings["ShowProcessingNotification"] != null && bool.TryParse(appSettings["ShowProcessingNotification"], out bool showNotif))
                ShowProcessingNotification = showNotif;
        }

        /// <summary>
        /// Get the configuration file path
        /// </summary>
        private static string GetConfigFilePath()
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
        /// Save current configuration to JSON file
        /// </summary>
        public void Save()
        {
            try
            {
                string configPath = GetConfigFilePath();
                string directory = Path.GetDirectoryName(configPath);

                if (!Directory.Exists(directory))
                {
                    Directory.CreateDirectory(directory);
                }

                string json = JsonConvert.SerializeObject(this, Formatting.Indented);
                File.WriteAllText(configPath, json);
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"Failed to save configuration: {ex.Message}");
                throw;
            }
        }

        /// <summary>
        /// Create a sample configuration file with comments
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

UserId (string): User identifier for authentication
  Default: Windows username (auto-detected)

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

        /// <summary>
        /// Validate configuration values
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
            if (Array.IndexOf(validLogLevels, LogLevel.ToUpper()) == -1)
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
        /// Get the effective WebSocket URL with user ID
        /// </summary>
        public string GetWebSocketUrl()
        {
            string baseUrl = ServerUrl.TrimEnd('/');
            return $"{baseUrl}/ws/outlook/{UserId}";
        }
    }
}
