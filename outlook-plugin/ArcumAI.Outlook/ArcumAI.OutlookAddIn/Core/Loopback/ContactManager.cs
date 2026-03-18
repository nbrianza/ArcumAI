// Copyright (c) 2026 Nicolas Brianza
// Licensed under the MIT License. See LICENSE file in the project root.
using System;
using System.Runtime.InteropServices;
using Outlook = Microsoft.Office.Interop.Outlook;

namespace ArcumAI.OutlookAddIn.Core.Loopback
{
    /// <summary>
    /// Manages the ArcumAI Outlook contact entry.
    /// </summary>
    internal class ContactManager
    {
        private readonly Outlook.Application _outlookApp;
        private readonly PluginConfig _config;
        private readonly Action<string, string> _logAction;

        public ContactManager(
            Outlook.Application outlookApp,
            PluginConfig config,
            Action<string, string> logAction)
        {
            _outlookApp = outlookApp;
            _config = config;
            _logAction = logAction;
        }

        /// <summary>
        /// Ensures an Outlook Contact exists for ArcumAI so users can find it
        /// in the address book and Outlook won't reject the address.
        /// </summary>
        public void EnsureContactExists()
        {
            Outlook.MAPIFolder contactsFolder = null;
            Outlook.Items items = null;
            try
            {
                contactsFolder = _outlookApp.Session.GetDefaultFolder(
                    Outlook.OlDefaultFolders.olFolderContacts);
                items = contactsFolder.Items;

                // Check if contact already exists
                string filter = $"[Email1Address] = '{_config.ArcumAIEmailAddress}'";
                var existing = items.Find(filter);
                if (existing != null)
                {
                    Marshal.ReleaseComObject(existing);
                    _logAction("DEBUG", "VirtualLoopback: ArcumAI contact already exists");
                    return;
                }

                // Create new contact
                Outlook.ContactItem contact = null;
                try
                {
                    contact = (Outlook.ContactItem)_outlookApp.CreateItem(
                        Outlook.OlItemType.olContactItem);
                    contact.FullName = _config.ArcumAIDisplayName;
                    contact.Email1Address = _config.ArcumAIEmailAddress;
                    contact.Email1DisplayName = _config.ArcumAIDisplayName;
                    contact.CompanyName = "ArcumAI";
                    contact.Body = "Virtual AI Assistant - emails to this contact are processed by ArcumAI locally.";
                    contact.Save();
                    _logAction("INFO", $"VirtualLoopback: Created Outlook contact '{_config.ArcumAIDisplayName}' <{_config.ArcumAIEmailAddress}>");
                }
                finally
                {
                    if (contact != null) Marshal.ReleaseComObject(contact);
                }
            }
            catch (Exception ex)
            {
                _logAction("WARNING", $"VirtualLoopback: Could not create ArcumAI contact: {ex.Message}");
            }
            finally
            {
                if (items != null) Marshal.ReleaseComObject(items);
                if (contactsFolder != null) Marshal.ReleaseComObject(contactsFolder);
            }
        }
    }
}
