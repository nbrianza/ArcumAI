# ArcumAI Outlook Plugin Configuration Guide

This document explains all configuration options for the ArcumAI Outlook Plugin.

## Configuration Priority

The plugin loads configuration in the following order:
1. **config.json** (in `%APPDATA%\ArcumAI\Outlook\` or plugin directory)
2. **App.config** (in plugin installation directory)
3. **Default values** (hardcoded fallbacks)

## Configuration Files Location

### config.json
- **Primary location**: `%APPDATA%\ArcumAI\Outlook\config.json`
- **Fallback location**: `{PluginInstallDir}\config.json`

### App.config
- **Location**: `{PluginInstallDir}\ArcumAI.OutlookAddIn.dll.config`

---

## Configuration Options

### 🔌 Connection Settings

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `ServerUrl` | string | `ws://localhost:8080` | WebSocket server URL |
| `UseSecureConnection` | bool | `false` | Use WSS (secure) instead of WS |
| `UserId` | string | *auto-detect* | User identifier (leave empty for Windows username) |

**Examples:**
```json
// Local development
"ServerUrl": "ws://localhost:8080"

// Production with SSL
"ServerUrl": "wss://arcumai.company.com"
"UseSecureConnection": true
```

---

### 🔄 Reconnection Settings

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `AutoReconnect` | bool | `true` | Automatically reconnect on disconnect |
| `ReconnectDelayMs` | int | `5000` | Delay between reconnection attempts (ms) |
| `MaxReconnectAttempts` | int | `10` | Max attempts before giving up (-1 = infinite) |
| `ConnectionTimeoutMs` | int | `30000` | Timeout for initial connection (ms) |
| `HeartbeatIntervalMs` | int | `30000` | Interval for keepalive pings (0 = disabled) |

**Recommendations:**
- **Development**: `MaxReconnectAttempts: 5`, `ReconnectDelayMs: 3000`
- **Production**: `MaxReconnectAttempts: 20`, `ReconnectDelayMs: 5000`
- **Unstable Network**: `MaxReconnectAttempts: -1`, `ReconnectDelayMs: 10000`

---

### ⚡ Request Settings

| Option | Type | Default | Range | Description |
|--------|------|---------|-------|-------------|
| `RequestTimeoutMs` | int | `60000` | 10000-300000 | Timeout for MCP requests (ms) |
| `MaxEmailResults` | int | `10` | 1-100 | Max emails returned per query |
| `EmailPreviewLength` | int | `200` | 50-1000 | Characters in email body preview |

**Performance Tuning:**
```json
// Fast responses (less data)
"MaxEmailResults": 5,
"EmailPreviewLength": 100,
"RequestTimeoutMs": 30000

// Comprehensive results (more data)
"MaxEmailResults": 20,
"EmailPreviewLength": 500,
"RequestTimeoutMs": 120000
```

---

### 📝 Logging Settings

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `EnableLogging` | bool | `true` | Enable logging to file |
| `LogLevel` | string | `INFO` | Minimum log level (DEBUG/INFO/WARNING/ERROR) |
| `LogFilePath` | string | *auto* | Path to log file (empty = default location) |

**Default Log Location**: `%APPDATA%\ArcumAI\Outlook\logs\plugin.log`

**Log Levels:**
- **DEBUG**: Detailed diagnostic information (includes all WebSocket messages)
- **INFO**: General informational messages (connections, requests)
- **WARNING**: Warning messages (connection issues, retries)
- **ERROR**: Error messages only (failures, exceptions)

---

## Configuration Examples

### 🏢 Enterprise Production

```json
{
  "ServerUrl": "wss://arcumai.company.com",
  "UseSecureConnection": true,
  "AutoReconnect": true,
  "MaxReconnectAttempts": 20,
  "ReconnectDelayMs": 5000,
  "EnableLogging": true,
  "LogLevel": "WARNING",
  "MaxEmailResults": 15,
  "EmailPreviewLength": 300,
  "HeartbeatIntervalMs": 30000
}
```

### 💻 Local Development

```json
{
  "ServerUrl": "ws://localhost:8080",
  "UseSecureConnection": false,
  "AutoReconnect": true,
  "MaxReconnectAttempts": 5,
  "ReconnectDelayMs": 3000,
  "EnableLogging": true,
  "LogLevel": "DEBUG",
  "MaxEmailResults": 5,
  "EmailPreviewLength": 150,
  "HeartbeatIntervalMs": 15000
}
```

### 🌐 Remote Office (VPN)

```json
{
  "ServerUrl": "ws://arcumai-server.internal:8080",
  "UseSecureConnection": false,
  "AutoReconnect": true,
  "MaxReconnectAttempts": -1,
  "ReconnectDelayMs": 10000,
  "EnableLogging": true,
  "LogLevel": "INFO",
  "ConnectionTimeoutMs": 60000,
  "RequestTimeoutMs": 90000,
  "HeartbeatIntervalMs": 45000
}
```

### 🚀 High-Performance

```json
{
  "ServerUrl": "ws://localhost:8080",
  "MaxEmailResults": 20,
  "EmailPreviewLength": 500,
  "RequestTimeoutMs": 120000,
  "ConnectionTimeoutMs": 10000,
  "HeartbeatIntervalMs": 15000,
  "EnableLogging": false
}
```

---

## Configuration Validation

The plugin automatically validates configuration on startup. Invalid values will:
1. Log a warning message
2. Fall back to default values
3. Continue operation with safe defaults

**Common Validation Errors:**
- ❌ `ServerUrl` must start with `ws://` or `wss://`
- ❌ `MaxEmailResults` must be between 1-100
- ❌ `EmailPreviewLength` must be between 50-1000
- ❌ `ReconnectDelayMs` must be at least 1000ms
- ❌ `LogLevel` must be DEBUG, INFO, WARNING, or ERROR

---

## Programmatic Configuration

You can also configure the plugin programmatically in code:

```csharp
// Access singleton instance
var config = PluginConfig.Instance;

// Modify settings
config.ServerUrl = "ws://custom-server:9090";
config.MaxEmailResults = 15;

// Validate configuration
if (!config.Validate(out string error))
{
    MessageBox.Show($"Invalid configuration: {error}");
}

// Save to file
config.Save();

// Create sample config file
PluginConfig.CreateSampleConfig();
```

---

## Environment-Specific Configuration

For different environments, create multiple config files:

```
%APPDATA%\ArcumAI\Outlook\
├── config.json              ← Active configuration
├── config.dev.json          ← Development settings
├── config.staging.json      ← Staging settings
└── config.production.json   ← Production settings
```

Manually copy the appropriate file to `config.json` before deployment.

---

## Troubleshooting

### Plugin Not Connecting

1. Check `ServerUrl` is correct
2. Verify server is running
3. Check firewall settings
4. Increase `ConnectionTimeoutMs`
5. Enable `LogLevel: "DEBUG"` to see detailed messages

### Frequent Disconnections

1. Increase `HeartbeatIntervalMs` (e.g., 60000)
2. Set `MaxReconnectAttempts: -1` for infinite retries
3. Increase `ReconnectDelayMs` (e.g., 10000)
4. Check network stability

### Slow Performance

1. Reduce `MaxEmailResults` (e.g., 5)
2. Reduce `EmailPreviewLength` (e.g., 100)
3. Decrease `RequestTimeoutMs` (e.g., 30000)
4. Set `EnableLogging: false`

---

## Security Considerations

### ⚠️ Production Deployments

1. **Always use WSS** (`wss://`) in production
2. **Never expose** config files with credentials
3. **Use empty `UserId`** to auto-detect (don't hardcode usernames)
4. **Restrict log file permissions** to prevent information disclosure
5. **Regularly rotate** log files to prevent disk space issues

### 🔐 Best Practices

- Store sensitive configs in `%APPDATA%` (user-specific)
- Use Group Policy to distribute enterprise settings
- Encrypt `config.json` for highly sensitive environments
- Audit log files for security events

---

## Support

For configuration assistance, check:
- **Log file**: `%APPDATA%\ArcumAI\Outlook\logs\plugin.log`
- **Debug output**: Visual Studio Debug Console
- **Validation**: Run `PluginConfig.Instance.Validate()` in code

---

*Last updated: February 2026*
