# Configuration System - Quick Start Guide

This guide will help you integrate the new configuration management system into your Outlook plugin.

## ✅ What Was Created

1. **`Core/PluginConfig.cs`** - Configuration manager class
2. **`config.json`** - Default configuration file
3. **`App.config`** - Alternative configuration (XML format)
4. **`config.README.md`** - Comprehensive configuration documentation
5. **`ThisAddIn.EXAMPLE.cs`** - Example implementation
6. **Updated `.csproj`** - Added necessary references

## 🚀 Integration Steps

### Step 1: Update Your ThisAddIn.cs

Add configuration loading in the `ThisAddIn_Startup` method:

```csharp
private PluginConfig _config;

private void ThisAddIn_Startup(object sender, System.EventArgs e)
{
    // Load configuration
    _config = PluginConfig.Instance;

    // Validate configuration
    if (!_config.Validate(out string error))
    {
        MessageBox.Show($"Config error: {error}", "Warning");
    }

    // Use configuration values
    _transport = new WebSocketTransport();
    await _transport.ConnectAsync(_config.ServerUrl, _config.UserId);

    // ... rest of your code
}
```

### Step 2: Replace Hardcoded Values

**Before:**
```csharp
private const string SERVER_URL = "ws://localhost:8080";
_currentUser = Environment.UserName.ToLower();
if (count >= 5) break;  // Max 5 emails
```

**After:**
```csharp
// Use configuration instead
string serverUrl = _config.ServerUrl;
string userId = _config.UserId;
if (count >= _config.MaxEmailResults) break;
```

### Step 3: Add Logging (Optional but Recommended)

Copy the logging methods from `ThisAddIn.EXAMPLE.cs`:

```csharp
private void LogInfo(string message)
{
    if (_config.EnableLogging &&
        (_config.LogLevel == "DEBUG" || _config.LogLevel == "INFO"))
    {
        WriteLog("INFO", message);
    }
}

private void WriteLog(string level, string message)
{
    string logPath = _config.LogFilePath;
    // ... see example file for full implementation
}
```

Then use throughout your code:
```csharp
LogInfo("Connecting to server...");
LogError($"Connection failed: {ex.Message}");
```

### Step 4: Build and Test

```bash
# In Visual Studio
1. Build > Rebuild Solution
2. Check for compilation errors
3. Run in Debug mode
4. Verify connection works
```

## 📝 Configuration File Locations

The plugin will look for `config.json` in these locations (in order):

1. **`%APPDATA%\ArcumAI\Outlook\config.json`** ← **Recommended**
2. `{PluginInstallDir}\config.json`

### Create Configuration File

**Option A: Manual Creation**

Create `%APPDATA%\ArcumAI\Outlook\config.json`:

```json
{
  "ServerUrl": "ws://localhost:8080",
  "UserId": "",
  "AutoReconnect": true,
  "MaxReconnectAttempts": 10,
  "EnableLogging": true,
  "LogLevel": "INFO",
  "MaxEmailResults": 10
}
```

**Option B: Programmatic Creation**

Add to your startup code (run once):
```csharp
PluginConfig.CreateSampleConfig();
```

This creates both `config.json` and `config.README.txt` in `%APPDATA%\ArcumAI\Outlook\`.

## ⚙️ Common Configuration Scenarios

### Development Environment

```json
{
  "ServerUrl": "ws://localhost:8080",
  "EnableLogging": true,
  "LogLevel": "DEBUG",
  "MaxReconnectAttempts": 5,
  "ReconnectDelayMs": 3000
}
```

### Production Environment

```json
{
  "ServerUrl": "wss://arcumai.company.com",
  "UseSecureConnection": true,
  "EnableLogging": true,
  "LogLevel": "WARNING",
  "MaxReconnectAttempts": 20,
  "ReconnectDelayMs": 5000
}
```

### Testing (Minimal Logging)

```json
{
  "ServerUrl": "ws://localhost:8080",
  "EnableLogging": false,
  "MaxEmailResults": 5,
  "RequestTimeoutMs": 30000
}
```

## 🔧 Testing Your Integration

### 1. Verify Configuration Loading

Add this to your startup:

```csharp
var config = PluginConfig.Instance;
MessageBox.Show(
    $"Config loaded:\n" +
    $"Server: {config.ServerUrl}\n" +
    $"User: {config.UserId}\n" +
    $"Logging: {config.EnableLogging}",
    "Configuration Test"
);
```

### 2. Test Connection

```csharp
try
{
    await _transport.ConnectAsync(config.ServerUrl, config.UserId);
    LogInfo("Connection successful!");
}
catch (Exception ex)
{
    LogError($"Connection failed: {ex.Message}");
}
```

### 3. Check Log Files

Navigate to: `%APPDATA%\ArcumAI\Outlook\logs\plugin.log`

You should see entries like:
```
2026-02-06 14:30:00.123 [INFO] ArcumAI Outlook Plugin starting...
2026-02-06 14:30:00.456 [INFO] Server URL: ws://localhost:8080
2026-02-06 14:30:01.789 [INFO] Connected successfully
```

## 🐛 Troubleshooting

### Config Not Loading

**Problem**: Plugin uses default values instead of your config.json

**Solution**:
1. Check file location: `%APPDATA%\ArcumAI\Outlook\config.json`
2. Verify JSON syntax (use https://jsonlint.com/)
3. Check file permissions (must be readable)
4. Enable DEBUG logging to see load attempts

### Connection Fails

**Problem**: Plugin can't connect to server

**Solution**:
1. Verify `ServerUrl` in config
2. Check server is running: `python main_nice.py`
3. Test WebSocket: Use browser extension (Simple WebSocket Client)
4. Check firewall settings
5. Review logs for detailed error messages

### Validation Errors

**Problem**: "Invalid configuration" message on startup

**Solution**:
1. Check all values are within valid ranges:
   - `MaxEmailResults`: 1-100
   - `EmailPreviewLength`: 50-1000
   - `ReconnectDelayMs`: ≥ 1000
2. Verify `ServerUrl` starts with `ws://` or `wss://`
3. Check `LogLevel` is DEBUG, INFO, WARNING, or ERROR

## 📚 Advanced Features

### Dynamic Configuration Updates

```csharp
// Modify configuration at runtime
var config = PluginConfig.Instance;
config.MaxEmailResults = 15;
config.LogLevel = "DEBUG";

// Save changes
config.Save();

// Reload configuration
PluginConfig._instance = null;  // Force reload
config = PluginConfig.Instance;
```

### Environment-Specific Configs

```csharp
// Detect environment and load appropriate config
string environment = Environment.GetEnvironmentVariable("ARCUMAI_ENV") ?? "production";
string configPath = $"config.{environment}.json";

// Load manually
string json = File.ReadAllText(configPath);
var config = JsonConvert.DeserializeObject<PluginConfig>(json);
```

### Encrypted Configuration (Future)

For sensitive environments, consider encrypting config files:

```csharp
// TODO: Implement encryption
string encrypted = EncryptConfig(config);
File.WriteAllText("config.encrypted", encrypted);
```

## 📦 Deployment

### Option 1: Include Config in Installer

Add to your `.vsto` installer:
1. Copy `config.json` to installation directory
2. Add post-install script to copy to `%APPDATA%`

### Option 2: Group Policy Distribution

For enterprise deployments:
1. Create a GPO to deploy `config.json`
2. Target: `%APPDATA%\ArcumAI\Outlook\`
3. Set file permissions: Read-only for users

### Option 3: Manual Distribution

Provide users with:
1. `config.json` template
2. Instructions to place in `%APPDATA%\ArcumAI\Outlook\`
3. Customization guide

## 🎯 Next Steps

1. ✅ Review `config.README.md` for all configuration options
2. ✅ Study `ThisAddIn.EXAMPLE.cs` for full integration example
3. ✅ Test in development environment first
4. ✅ Deploy to staging/test users
5. ✅ Roll out to production

## 📖 Additional Resources

- **Full Documentation**: See `config.README.md`
- **Code Example**: See `ThisAddIn.EXAMPLE.cs`
- **Config Schema**: See `PluginConfig.cs` class properties

## 🆘 Need Help?

Check the log file first:
```
%APPDATA%\ArcumAI\Outlook\logs\plugin.log
```

Enable DEBUG logging for maximum detail:
```json
{
  "EnableLogging": true,
  "LogLevel": "DEBUG"
}
```

---

**Version**: 1.0
**Last Updated**: February 2026
**Author**: ArcumAI Team
