using System;
using System.Threading.Tasks;

namespace ArcumAI.OutlookAddIn.Core
{
    public interface IMcpTransport
    {
        // Evento scatenato quando il server invia un messaggio
        event EventHandler<string> MessageReceived;

        // Connessione al server
        Task ConnectAsync(string baseUri, string userId);

        // Invio messaggio al server
        Task SendAsync(string message);

        bool IsConnected { get; }
    }
}