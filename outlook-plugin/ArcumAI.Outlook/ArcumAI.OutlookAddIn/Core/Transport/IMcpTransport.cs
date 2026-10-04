// Copyright (c) 2026 Nicolas Brianza
// Licensed under the MIT License. See LICENSE file in the project root.
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

        // Max silence (ms) tolerated on the receive side before the connection is treated as dead.
        // 0 disables the check; reset to 0 on every ConnectAsync.
        int InactivityTimeoutMs { get; set; }
    }
}
