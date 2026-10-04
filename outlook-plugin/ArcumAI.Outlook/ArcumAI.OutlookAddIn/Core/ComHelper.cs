// Copyright (c) 2026 Nicolas Brianza
// Licensed under the MIT License. See LICENSE file in the project root.
using System;
using System.Runtime.InteropServices;

namespace ArcumAI.OutlookAddIn.Core
{
    internal static class ComHelper
    {
        /// <summary>
        /// Releases each COM object independently, so one failing release (e.g. an RCW
        /// already disconnected from Outlook) does not leak the others in the same finally block.
        /// </summary>
        public static void SafeRelease(params object[] comObjects)
        {
            foreach (object obj in comObjects)
            {
                if (obj == null) continue;
                try
                {
                    if (Marshal.IsComObject(obj))
                        Marshal.ReleaseComObject(obj);
                }
                catch (Exception ex)
                {
                    System.Diagnostics.Debug.WriteLine($"COM release failed: {ex.Message}");
                }
            }
        }
    }
}
