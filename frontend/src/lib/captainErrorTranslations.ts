/**
 * The backend has no per-request language — every error it raises comes
 * back in English regardless of which language the captain app is set
 * to, which is exactly why validation warnings (wrong plate number,
 * "not ready yet", etc.) still read English even after switching to
 * Hindi. Doing this properly (a translated string catalogue on the
 * server, keyed by error code) is a bigger change; this is the pragmatic
 * client-side fix — the handful of messages a captain actually runs
 * into are matched here and swapped for a simple Hindi/Hinglish
 * equivalent. Anything not recognized is shown exactly as the server
 * sent it (English) rather than risk a wrong or garbled "translation".
 */
const STATIC: Record<string, string> = {
  "Booking not found": "बुकिंग नहीं मिली",
  "You are not assigned to this booking": "यह बुकिंग आपको असाइन नहीं है",
  "This booking can no longer be released": "अब यह बुकिंग छोड़ी नहीं जा सकती",
  "This booking is not ready to start heading out": "अभी इस बुकिंग के लिए निकलना शुरू नहीं कर सकते",
  "Vehicle verification happens after you've started heading to the customer": "गाड़ी वेरिफाई सिर्फ निकलने के बाद ही हो सकती है",
  "That registration number doesn't match this booking. Double-check the plate before proceeding — if this really is the wrong vehicle, release the job instead of continuing.":
    "यह गाड़ी नंबर इस बुकिंग से मेल नहीं खाता। नंबर प्लेट फिर से चेक करो — अगर सच में गलत गाड़ी है, तो आगे बढ़ने की बजाय जॉब छोड़ दो।",
  "Reach the customer's location before starting the service": "सर्विस शुरू करने से पहले कस्टमर के पते पर पहुँचो",
  "Verify the vehicle's registration number before starting the service": "सर्विस शुरू करने से पहले गाड़ी नंबर वेरिफाई करो",
  "The service must be in progress before it can be completed": "सर्विस पूरी करने से पहले वह शुरू होनी चाहिए",
  "This booking is flagged for your manager's attention and can't be progressed until they resolve or reschedule it — contact your manager.":
    "यह बुकिंग मैनेजर के पास फ़्लैग है — जब तक वो सुलझा नहीं देते, आगे नहीं बढ़ सकते। मैनेजर से बात करो।",
  "This booking's scheduled window expired too long ago to start now — a manager needs to reschedule or reassign it first.":
    "इस बुकिंग का समय बहुत पहले निकल चुका है — अब मैनेजर को ही नया समय देना होगा।",
};

/** "Too early to start this booking. You can begin heading out 30 minutes
 *  before the scheduled time (in about 12 more minutes)." — the one common
 *  message with a number baked in; matched by its fixed wording either
 *  side of the minute counts and rebuilt in Hindi around them. */
const EARLY_TO_START = /^Too early to start this booking\. You can begin heading out (\d+) minutes before the scheduled time \(in about (\d+) more minutes?\)\.$/;

export function translateCaptainError(message: string, language: "en" | "hi"): string {
  if (language !== "hi" || !message) return message;
  if (STATIC[message]) return STATIC[message];
  const early = message.match(EARLY_TO_START);
  if (early) return `अभी जल्दी है — समय से ${early[1]} मिनट पहले निकल सकते हो (लगभग ${early[2]} मिनट बाकी हैं)।`;
  return message;
}
