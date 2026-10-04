// Copyright (c) 2026 Nicolas Brianza
// Licensed under the MIT License. See LICENSE file in the project root.
using System;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace ArcumAI.OutlookAddIn.Core.Transport
{
    public class WebSocketTransport : IMcpTransport
    {
        private ClientWebSocket _ws;
        private CancellationTokenSource _cts;
        private int _disconnectedFired; // 0 = not yet fired; use Interlocked to guard

        public event EventHandler<string> MessageReceived;
        public event EventHandler Disconnected;

        // Fires Disconnected exactly once per connection lifetime.
        private void FireDisconnected()
        {
            if (Interlocked.CompareExchange(ref _disconnectedFired, 1, 0) == 0)
                Disconnected?.Invoke(this, EventArgs.Empty);
        }

        public bool IsConnected => _ws != null && _ws.State == WebSocketState.Open;

        private volatile int _inactivityTimeoutMs;
        public int InactivityTimeoutMs
        {
            get => _inactivityTimeoutMs;
            set => _inactivityTimeoutMs = Math.Max(0, value);
        }

        public async Task ConnectAsync(string baseUri, string userId)
        {
            // Reset previous connection if it exists
            if (_cts != null)
            {
                _cts.Cancel();
                _cts.Dispose();
            }
            if (_ws != null) _ws.Dispose();

            _ws = new ClientWebSocket();
            _cts = new CancellationTokenSource();
            Interlocked.Exchange(ref _disconnectedFired, 0); // reset for new connection
            InactivityTimeoutMs = 0; // enabled again only once the new server proves it acks heartbeats

            string apiKey = PluginConfig.Instance.ApiKey;
            if (!string.IsNullOrEmpty(apiKey))
                _ws.Options.SetRequestHeader("X-API-Key", apiKey);

            // Build the URL, upgrading to wss:// when UseSecureConnection is set
            string effectiveBase = baseUri.TrimEnd('/');
            if (PluginConfig.Instance.UseSecureConnection &&
                effectiveBase.StartsWith("ws://", StringComparison.OrdinalIgnoreCase))
                effectiveBase = "wss://" + effectiveBase.Substring(5);
            var uriString = $"{effectiveBase}/ws/outlook/{userId}";

            try
            {
                // Configurable connection timeout
                int timeoutMs = PluginConfig.Instance.ConnectionTimeoutMs;
                using (var connectCts = new CancellationTokenSource(timeoutMs))
                {
                    await _ws.ConnectAsync(new Uri(uriString), connectCts.Token);
                }

                // Start the listening loop in background (without blocking Outlook)
                _ = ReceiveLoop();
            }
            catch (OperationCanceledException)
            {
                throw new TimeoutException($"Connection timed out after {PluginConfig.Instance.ConnectionTimeoutMs}ms");
            }
        }

        public async Task SendAsync(string message)
        {
            if (!IsConnected) return;

            try
            {
                var bytes = Encoding.UTF8.GetBytes(message);
                await _ws.SendAsync(new ArraySegment<byte>(bytes), WebSocketMessageType.Text, true, CancellationToken.None);
            }
            catch (Exception)
            {
                // Connection lost during send
                FireDisconnected();
            }
        }

        public async Task DisconnectAsync()
        {
            if (_cts != null)
            {
                _cts.Cancel();
            }

            if (_ws != null && _ws.State == WebSocketState.Open)
            {
                try
                {
                    await _ws.CloseAsync(WebSocketCloseStatus.NormalClosure, "Client closing", CancellationToken.None);
                }
                catch
                {
                    // Best effort
                }
            }
        }

        private async Task ReceiveLoop()
        {
            var buffer = new byte[8192];

            try
            {
                while (IsConnected && !_cts.IsCancellationRequested)
                {
                    var result = await ReceiveWithTimeoutAsync(buffer);

                    if (result.MessageType == WebSocketMessageType.Close)
                    {
                        try
                        {
                            await _ws.CloseAsync(WebSocketCloseStatus.NormalClosure, "Server closed", CancellationToken.None);
                        }
                        catch { }
                        break;
                    }

                    if (result.EndOfMessage)
                    {
                        MessageReceived?.Invoke(this, Encoding.UTF8.GetString(buffer, 0, result.Count));
                        continue;
                    }

                    // Multi-frame message: decode once at the end, otherwise a multi-byte UTF-8
                    // character split across two frames is corrupted.
                    using (var fullMessage = new System.IO.MemoryStream())
                    {
                        fullMessage.Write(buffer, 0, result.Count);
                        while (!result.EndOfMessage)
                        {
                            result = await ReceiveWithTimeoutAsync(buffer);
                            fullMessage.Write(buffer, 0, result.Count);
                        }
                        MessageReceived?.Invoke(this, Encoding.UTF8.GetString(fullMessage.GetBuffer(), 0, (int)fullMessage.Length));
                    }
                }
            }
            catch (OperationCanceledException)
            {
                // Voluntary cancellation, not an error
            }
            catch (TimeoutException ex)
            {
                System.Diagnostics.Debug.WriteLine($"Receive loop: {ex.Message}");
                try { _ws.Abort(); } catch { }
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"Receive loop error: {ex.Message}");
            }

            // Notify disconnection to trigger automatic reconnection.
            // Skip if exit was caused by voluntary cancellation (e.g. ConnectAsync disposed us for reconnect).
            if (!_cts.IsCancellationRequested)
                FireDisconnected();
        }

        // Long AI processing does not trip this: the server acks every heartbeat, so data keeps
        // arriving. The timeout is only active once InactivityTimeoutMs is set (first ack seen).
        private async Task<WebSocketReceiveResult> ReceiveWithTimeoutAsync(byte[] buffer)
        {
            var segment = new ArraySegment<byte>(buffer);
            int timeoutMs = InactivityTimeoutMs;
            if (timeoutMs <= 0)
                return await _ws.ReceiveAsync(segment, _cts.Token);

            using (var linked = CancellationTokenSource.CreateLinkedTokenSource(_cts.Token))
            {
                linked.CancelAfter(timeoutMs);
                try
                {
                    return await _ws.ReceiveAsync(segment, linked.Token);
                }
                catch (Exception) when (linked.IsCancellationRequested && !_cts.IsCancellationRequested)
                {
                    // ClientWebSocket may surface the cancellation as OperationCanceledException or WebSocketException
                    throw new TimeoutException($"No data from server for {timeoutMs} ms — treating connection as dead");
                }
            }
        }
    }
}
