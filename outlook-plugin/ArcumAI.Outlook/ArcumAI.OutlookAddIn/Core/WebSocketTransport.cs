using System;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace ArcumAI.OutlookAddIn.Core
{
    public class WebSocketTransport : IMcpTransport
    {
        private ClientWebSocket _ws;
        private CancellationTokenSource _cts;

        public event EventHandler<string> MessageReceived;
        public event EventHandler Disconnected;

        public bool IsConnected => _ws != null && _ws.State == WebSocketState.Open;

        public async Task ConnectAsync(string baseUri, string userId)
        {
            // Reset connessione precedente se esistente
            if (_cts != null)
            {
                _cts.Cancel();
                _cts.Dispose();
            }
            if (_ws != null) _ws.Dispose();

            _ws = new ClientWebSocket();
            _cts = new CancellationTokenSource();

            // Costruisce l'URL: ws://localhost:8080/ws/outlook/nome_utente
            var uriString = $"{baseUri.TrimEnd('/')}/ws/outlook/{userId}";

            try
            {
                // Timeout configurabile per la connessione
                int timeoutMs = PluginConfig.Instance.ConnectionTimeoutMs;
                using (var connectCts = new CancellationTokenSource(timeoutMs))
                {
                    await _ws.ConnectAsync(new Uri(uriString), connectCts.Token);
                }

                // Avvia il loop di ascolto in background (senza bloccare Outlook)
                _ = ReceiveLoop();
            }
            catch (OperationCanceledException)
            {
                throw new TimeoutException($"Connessione scaduta dopo {PluginConfig.Instance.ConnectionTimeoutMs}ms");
            }
            catch (Exception)
            {
                throw;
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
                // Connessione persa durante invio
                Disconnected?.Invoke(this, EventArgs.Empty);
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
                    var result = await _ws.ReceiveAsync(new ArraySegment<byte>(buffer), _cts.Token);

                    if (result.MessageType == WebSocketMessageType.Close)
                    {
                        try
                        {
                            await _ws.CloseAsync(WebSocketCloseStatus.NormalClosure, "Server closed", CancellationToken.None);
                        }
                        catch { }
                        break;
                    }

                    // Supporto messaggi più grandi di 8KB (multi-frame)
                    if (!result.EndOfMessage)
                    {
                        var fullMessage = new StringBuilder();
                        fullMessage.Append(Encoding.UTF8.GetString(buffer, 0, result.Count));

                        while (!result.EndOfMessage)
                        {
                            result = await _ws.ReceiveAsync(new ArraySegment<byte>(buffer), _cts.Token);
                            fullMessage.Append(Encoding.UTF8.GetString(buffer, 0, result.Count));
                        }

                        MessageReceived?.Invoke(this, fullMessage.ToString());
                    }
                    else
                    {
                        var message = Encoding.UTF8.GetString(buffer, 0, result.Count);
                        MessageReceived?.Invoke(this, message);
                    }
                }
            }
            catch (OperationCanceledException)
            {
                // Cancellazione volontaria, non è un errore
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"Errore ricezione loop: {ex.Message}");
            }

            // Notifica disconnessione per attivare riconnessione automatica
            Disconnected?.Invoke(this, EventArgs.Empty);
        }
    }
}