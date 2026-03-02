using System;
using System.Threading.Tasks;

namespace ArcumAI.OutlookAddIn.Core.Transport
{
    public interface IMcpTransport
    {
        // Event triggered when the server sends a message
        event EventHandler<string> MessageReceived;

        // Event triggered when the connection is lost
        event EventHandler Disconnected;

        // Connect to the server
        Task ConnectAsync(string baseUri, string userId);

        // Send a message to the server
        Task SendAsync(string message);

        // Gracefully close the connection
        Task DisconnectAsync();

        bool IsConnected { get; }
    }
}
