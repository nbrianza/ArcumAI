// Copyright (c) 2026 Nicolas Brianza
// Licensed under the MIT License. See LICENSE file in the project root.

namespace ArcumAI.OutlookAddIn.Core
{
    /// <summary>
    /// MAPI property tags for PropertyAccessor.GetProperty/SetProperty.
    /// Suffix 001F = Unicode string, 0040 = time, 0102 = binary.
    /// </summary>
    internal static class MapiProperties
    {
        private const string Base = "http://schemas.microsoft.com/mapi/proptag/";

        public const string ClientSubmitTime              = Base + "0x00390040"; // PR_CLIENT_SUBMIT_TIME
        public const string SentRepresentingName          = Base + "0x0042001F"; // PR_SENT_REPRESENTING_NAME
        public const string SentRepresentingAddrType      = Base + "0x0064001F"; // PR_SENT_REPRESENTING_ADDRTYPE
        public const string SentRepresentingEmailAddress  = Base + "0x0065001F"; // PR_SENT_REPRESENTING_EMAIL_ADDRESS
        public const string ConversationTopic             = Base + "0x0070001F"; // PR_CONVERSATION_TOPIC
        public const string ConversationIndex             = Base + "0x00710102"; // PR_CONVERSATION_INDEX
        public const string SenderName                    = Base + "0x0C1A001F"; // PR_SENDER_NAME
        public const string SenderAddrType                = Base + "0x0C1E001F"; // PR_SENDER_ADDRTYPE
        public const string SenderEmailAddress            = Base + "0x0C1F001F"; // PR_SENDER_EMAIL_ADDRESS
        public const string MessageDeliveryTime           = Base + "0x0E060040"; // PR_MESSAGE_DELIVERY_TIME
        public const string InternetMessageId             = Base + "0x1035001F"; // PR_INTERNET_MESSAGE_ID
        public const string InternetReferences            = Base + "0x1039001F"; // PR_INTERNET_REFERENCES
        public const string InReplyToId                   = Base + "0x1042001F"; // PR_IN_REPLY_TO_ID
        public const string AttachContentId               = Base + "0x3712001F"; // PR_ATTACH_CONTENT_ID
    }
}
