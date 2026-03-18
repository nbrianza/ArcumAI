// Copyright (c) 2026 Nicolas Brianza
// Licensed under the MIT License. See LICENSE file in the project root.
using System;
using System.IO;

namespace ArcumAI.OutlookAddIn.Core
{
    internal interface IPluginLogger
    {
        void Log(string level, string message);
    }

    /// <summary>
    /// Writes structured log entries to a rotating file.
    /// Extracted from ThisAddIn.
    /// </summary>
    internal class PluginLogger : IPluginLogger
    {
        private readonly PluginConfig _config;
        private const long MAX_LOG_SIZE = 5 * 1024 * 1024; // 5 MB

        public PluginLogger(PluginConfig config)
        {
            _config = config;
        }

        public void Log(string level, string message)
        {
            if (!_config.EnableLogging) return;

            string[] levels = { "DEBUG", "INFO", "WARNING", "ERROR" };
            int configLevel = Array.IndexOf(levels, _config.LogLevel.ToUpper());
            int msgLevel = Array.IndexOf(levels, level);
            if (msgLevel < configLevel) return;

            try
            {
                string logPath = _config.LogFilePath;
                string directory = Path.GetDirectoryName(logPath);
                if (!Directory.Exists(directory))
                    Directory.CreateDirectory(directory);

                // Log rotation: if file exceeds MAX_LOG_SIZE, rotate
                if (File.Exists(logPath))
                {
                    var info = new FileInfo(logPath);
                    if (info.Length > MAX_LOG_SIZE)
                    {
                        string oldPath = logPath + ".old";
                        if (File.Exists(oldPath)) File.Delete(oldPath);
                        File.Move(logPath, oldPath);
                    }
                }

                string entry = $"{DateTime.Now:yyyy-MM-dd HH:mm:ss.fff} [{level}] {message}{Environment.NewLine}";
                File.AppendAllText(logPath, entry);
            }
            catch
            {
                // Silent fallback to avoid blocking Outlook
            }

            System.Diagnostics.Debug.WriteLine($"[ArcumAI {level}] {message}");
        }
    }
}
