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

        public bool IsConnected => _ws != null && _ws.State == WebSocketState.Open;

        public async Task ConnectAsync(string baseUri, string userId)
        {
            // Reset connessione precedente se esistente
            if (_ws != null) _ws.Dispose();
            _ws = new ClientWebSocket();
            _cts = new CancellationTokenSource();

            // Costruisce l'URL: ws://localhost:8080/ws/outlook/nome_utente
            // TrimEnd serve per sicurezza se l'utente mette lo slash finale nell'URL base
            var uriString = $"{baseUri.TrimEnd('/')}/ws/outlook/{userId}";

            try
            {
                await _ws.ConnectAsync(new Uri(uriString), CancellationToken.None);

                // Avvia il loop di ascolto in background (senza bloccare Outlook)
                _ = ReceiveLoop();
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"Errore connessione WS: {ex.Message}");
                throw; // Rilancia l'errore per gestirlo nel chiamante
            }
        }

        public async Task SendAsync(string message)
        {
            if (!IsConnected) return;

            var bytes = Encoding.UTF8.GetBytes(message);
            await _ws.SendAsync(new ArraySegment<byte>(bytes), WebSocketMessageType.Text, true, CancellationToken.None);
        }

        private async Task ReceiveLoop()
        {
            var buffer = new byte[8192]; // Buffer da 8KB

            try
            {
                while (IsConnected && !_cts.IsCancellationRequested)
                {
                    // Ricezione dati
                    var result = await _ws.ReceiveAsync(new ArraySegment<byte>(buffer), _cts.Token);

                    if (result.MessageType == WebSocketMessageType.Close)
                    {
                        await _ws.CloseAsync(WebSocketCloseStatus.NormalClosure, "Server closed", CancellationToken.None);
                        break;
                    }

                    // Conversione e notifica evento
                    var message = Encoding.UTF8.GetString(buffer, 0, result.Count);
                    MessageReceived?.Invoke(this, message);
                }
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"Errore ricezione loop: {ex.Message}");
            }
        }
    }
}