using System;
using System.Threading.Tasks;

namespace ArcumAI.OutlookAddIn.Core
{
    public interface IMcpTransport
    {
        // Evento scatenato quando il server invia un messaggio
        event EventHandler<string> MessageReceived;

        // Evento scatenato quando la connessione viene persa
        event EventHandler Disconnected;

        // Connessione al server
        Task ConnectAsync(string baseUri, string userId);

        // Invio messaggio al server
        Task SendAsync(string message);

        // Chiusura pulita della connessione
        Task DisconnectAsync();

        bool IsConnected { get; }
    }
}