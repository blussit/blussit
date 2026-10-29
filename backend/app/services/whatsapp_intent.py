"""
What a WhatsApp customer typed, understood without any AI call.

A small deterministic layer in front of the bot's state machine: English,
Hindi and Hinglish keywords ("kitna", "rate", "book karna hai", "kab
aayega"...) map a free-text line to ONE intent, and a typed car type
("sedan ka rate") is matched against the LIVE vehicle-type names. Pure
functions only — no database, no network — so every rule is unit-testable;
the bot (whatsapp_bot_service) decides what each intent does and re-offers
the step the customer was on, so a question never destroys a half-built
booking.

Plans are never sold in chat: PLAN_BUY replies with the website link
(plan_link). Price lists are built from catalogue rows the bot passes in
(names and prices are admin-managed, nothing here is hard-coded).
"""
import re
import unicodedata

# The public website. The login page restores the page a logged-out visitor
# asked for, so the plans link opens the plans after login (a logged-in
# customer lands on the plans straight away).
SITE_URL = "https://blussit.com"
PLANS_PATH = "/app/subscriptions"

MAX_TEXT_LEN = 4000  # WhatsApp text body cap is 4096

BOOK = "book"
PRICE = "price"
OFFER = "offer"
PLAN_BUY = "plan_buy"
PLAN_STATUS = "plan_status"
CANCEL = "cancel"
RESCHEDULE = "reschedule"
STATUS = "status"
ADDRESS = "address"
TIMING = "timing"
HELP = "help"
HUMAN = "human"
THANKS = "thanks"

# Info intents are answered anywhere (and the current step is re-offered);
# the rest move the customer to another flow.
INFO_INTENTS = {PRICE, OFFER, PLAN_BUY, PLAN_STATUS, ADDRESS, TIMING, HELP, THANKS}


def plan_link() -> str:
    """Where a customer buys / renews a plan: the site's login, then the plans."""
    return f"{SITE_URL}{PLANS_PATH}"


def normalize(text: str) -> str:
    """Lower-case words only: punctuation, emoji and repeated spaces dropped
    ("Hi!!" -> "hi", "Rate kya hai?" -> "rate kya hai")."""
    text = unicodedata.normalize("NFKC", text or "").lower()
    # Letters, digits and combining marks stay (Devanagari vowel signs are marks).
    text = "".join(ch if ch.isalnum() or unicodedata.category(ch) in ("Mn", "Mc") else " " for ch in text)
    return " ".join(text.split())


def _rx(*alts: str) -> re.Pattern:
    return re.compile(r"(?<!\w)(?:" + "|".join(alts) + r")(?!\w)")


_GREETING = re.compile(
    r"^(?:(?:hi+|hii+|hello+|helo+|hlo+|hey+|heya|hy|namaste|namaskar|pranam|menu|start|good (?:morning|afternoon|evening))"
    r"(?: (?:there|ji|team|sir|madam|blussit|bhai|bro))?)$"
)
_HUMAN = _rx(
    "agent", "human", "manager", "supervisor", "executive", "representative", "real person", "customer care",
    "customer support", "support", "call me", "call back", "callback", "talk to", "speak to", "speak with",
    "baat karni", "baat karna", "baat karo", "baat kara", "helpline", "complaint", "complain", "shikayat", "shikayat",
    "refund", "money back", "paisa wapas", "paise wapas", "not happy", "bad service", "worst", "cheat", "fraud", "scam",
    "nahi aaya", "nahi aya", "nahin aaya", "not came", "not arrived", "did not come", "didn t come", "didnt come",
    "never came", "no show", "still waiting", "waiting since", "damage", "damaged", "scratch", "scratches", "stolen",
    "इंसान", "शिकायत", "रिफंड",
)
_THANKS = re.compile(
    r"^(?:(?:ok|okay|k|great|cool|nice|fine|alright|theek hai|thik hai|accha|achha|acha) )?"
    r"(?:thanks?|thank you|thankyou|thx|tysm|ty|shukriya|shukria|dhanyavad|dhanyawad|dhanyvaad|thanku)"
    r"(?: you)?(?: so much| a lot| very much| bhai| ji| sir|, ok| ok)*$"
)
_CANCEL = _rx("cancel", "cancellation", "cancelled", "canceled", "radd", "raddh", "रद्द", "कैंसिल")
_RESCHEDULE = _rx(
    "reschedule", "re schedule", "postpone", "prepone", "change (?:the |my )?(?:date|time|slot|day|timing)",
    "(?:date|time|slot|day|timing) change", "(?:date|time|slot|day|timing) (?:badal\\w*|badlo|badal do)",
    "badal\\w*", "another (?:day|time|slot)", "different (?:day|time|slot)", "shift my", "move my booking",
)
_STATUS = _rx(
    "status", "track", "tracking", "where is", "kahan hai", "kaha hai", "kab aay\\w*", "kab aa\\w*",
    "eta", "arrive", "arriving", "my bookings?", "mera booking", "meri booking", "mere booking", "booking details?",
    "booking status", "upcoming", "next booking", "captain", "booking ka", "bookings", "बुकिंग",
)
_PLAN_WORD = _rx("plans?", "subscriptions?", "subscribe", "subscribed", "monthly", "membership", "member", "pass", "passes",
                 "packages?", "renew\\w*", "autopay", "auto pay", "प्लान")
_PLAN_BUY_VERB = _rx(
    "buy", "purchase", "take", "lena", "lene", "lun", "lu", "kharid\\w*", "khareed\\w*", "new", "renew\\w*", "upgrade",
    "check ?out", "join", "activate", "subscribe", "want", "chahiye", "chahie", "interested",
)
# Reading an existing plan: "washes left", "when does my plan expire", "mera plan".
_PLAN_STATUS_MARK = re.compile(
    r"(?<!\w)(?:left|remaining|remain|balance|bacha|bachi|bache|baki|baaki|expiry|expire\w*|valid\w*|how many|used|"
    r"kitne wash\w*|kitne visits?|kitni wash\w*)(?!\w)|(?<!\w)(?:my|mera|meri|mere) (?:\w+ ){0,2}(?:plans?|pass\w*|subscriptions?|membership)(?!\w)"
)
_TIMING = _rx(
    "timing", "timings", "time", "hours", "open", "close", "closed", "khula", "khulta", "kitne baje", "kab tak", "kitna time",
    "kitni der", "duration", "how long", "lagega", "lagta", "sunday", "holiday", "weekend", "समय",
)
_PRICE_STRONG = _rx(
    "price", "prices", "pricing", "priced", "rate", "rates", "rate list", "rate card", "cost", "costs", "charge", "charges",
    "charged", "fee", "fees", "kimat", "keemat", "qimat", "daam", "dam", "how much", "kitna paisa", "kitne paise",
    "kitne ka", "kitne ki", "kitne mein", "kitne me", "kitna lagega", "kitna lagta", "kitna charge", "kitna rupee",
    "tariff", "amount", "sasta", "cheap", "budget", "price list", "menu card", "rs", "rupees?", "inr", "कीमत", "रेट", "दाम", "कितना",
)
_PRICE_WEAK = _rx("kitna", "kitne", "kitni", "kya hai", "kya rate", "list")
_OFFER = _rx("offers?", "discounts?", "deals?", "coupons?", "promos?", "promo ?code", "cashback", "sale", "launch offer",
             "first wash", "free", "voucher", "ऑफर", "छूट")
_HELP = _rx("help", "madad", "sahayata", "options", "what can you do", "kya kar sakte", "kya kya", "how does this work", "how to use", "guide")
_ADDRESS = _rx(
    "address", "location", "where are you", "kahan", "kaha", "office", "branch", "centre", "center", "area", "areas", "serve",
    "serviceable", "coverage", "pincode", "pin code", "city", "cities", "visit", "shop", "pata",
)
_BOOK = re.compile(
    r"(?<!\w)book(?!ing)(?!\w)|(?<!\w)book (?:a |the |my |one )?(?:wash|service|slot|car|bike)|"
    r"(?:karwana|karvana|karwani|karvani|karwa do|karwao)|(?<!\w)chahiye(?!\w)|(?<!\w)chahie(?!\w)|"
    r"(?:wash|clean|cleaning|detailing|service|slot) (?:please|chahiye|karna|karni|karo|kar do|krna|krni|now|today|tomorrow|aaj|kal)|"
    r"(?:want|need|wanna|looking for|i d like|i would like) (?:a |to |my |the )?(?:car |bike )?(?:wash|clean|cleaning|service|booking|appointment|book)|"
    r"(?<!\w)(?:appointment|schedule a)(?!\w)|(?<!\w)(?:new )?booking (?:karna|karni|krna|krni|karo|kar do|karwana|chahiye|please|now)|"
    r"^(?:car |bike )?(?:wash|washing|cleaning|service)$"
)


def is_greeting(text: str) -> bool:
    return bool(_GREETING.match(normalize(text)))


def detect_intent(text: str) -> str | None:
    """The ONE intent of a free-text line, or None when nothing is recognised.
    Priority: a human / complaint first, then moving an existing booking,
    plans, prices, the rest. A very long message is a story, not a command —
    it is left to a human."""
    t = normalize(text)
    if not t or len(t) > 240:
        return None
    if _THANKS.match(t):
        return THANKS
    if _HUMAN.search(t):
        return HUMAN
    if _PLAN_WORD.search(t):
        # Buying, renewing, a price or a bare "plan" is a purchase (the
        # website's job); "washes left" / "my plan" reads the customer's own.
        if _PLAN_BUY_VERB.search(t):
            return PLAN_BUY
        return PLAN_STATUS if _PLAN_STATUS_MARK.search(t) else PLAN_BUY
    if _CANCEL.search(t):
        return CANCEL
    if _RESCHEDULE.search(t):
        return RESCHEDULE
    if _STATUS.search(t):
        return STATUS
    if _PRICE_STRONG.search(t):
        return PRICE
    if _TIMING.search(t):
        return TIMING
    if _OFFER.search(t):
        return OFFER
    if _BOOK.search(t):
        return BOOK
    if _PRICE_WEAK.search(t):
        return PRICE
    if _HELP.search(t):
        return HELP
    if _ADDRESS.search(t):
        return ADDRESS
    return None


# ----------------------------------------------------------------------
# Typed answers to a button / list step
# ----------------------------------------------------------------------

_YES = {
    "yes", "y", "yep", "yup", "yeah", "confirm", "confirmed", "confirm it", "yes confirm", "yes please", "haan", "han", "ha",
    "haa", "ji", "ji haan", "ji han", "haan ji", "han ji", "ji confirm", "book it", "book kar do", "kar do", "karo", "kardo", "done", "sure",
    "proceed", "go ahead", "theek hai confirm", "ok confirm", "okay confirm",
}
_NO = {"no", "n", "nope", "nah", "nahi", "nahin", "na", "mat karo", "dont", "don t", "do not", "not now", "later", "baad mein"}
_CAR_USE_PLAN = {"use my plan", "use plan", "use it", "my plan", "plan", "use pass", "plan se", "plan se karo"}
_CAR_NORMAL = {"pay normal price", "normal price", "normal", "pay", "pay normal", "without plan", "no plan", "plan nahi"}


def yes_no(text: str) -> str | None:
    t = normalize(text)
    if t in _YES:
        return "yes"
    if t in _NO:
        return "no"
    return None


def plan_answer(text: str) -> str | None:
    """"use"/"normal" for a typed answer at the Use My Plan question."""
    t = normalize(text)
    if t in _CAR_NORMAL or yes_no(t) == "no":
        return "normal"
    if t in _CAR_USE_PLAN or yes_no(t) == "yes":
        return "use"
    return None


_EXTRAS_DONE = {"done", "continue", "skip", "nothing", "no more", "thats all", "that s all", "that is all", "bas", "bas itna", "enough", "next", "proceed", "no thanks"}


def wants_extras_done(text: str) -> bool:
    """"done" / "no more" / "bas" at the add-on list: stop adding extras."""
    return normalize(text) in _EXTRAS_DONE


def wants_coupon(text: str) -> bool:
    return bool(re.search(r"(?<!\w)(?:coupon|promo|promo ?code|voucher|discount code|coupon code)(?!\w)", normalize(text)))


def _words_in(haystack: list[str], needle: list[str]) -> tuple[int, int] | None:
    n = len(needle)
    for i in range(len(haystack) - n + 1):
        if haystack[i:i + n] == needle:
            return i, i + n
    return None


def match_names(text: str, names: dict[str, str]) -> list[str]:
    """Which of `names` ({id: display name}) the text points at. A name matches
    as a whole phrase ("compact suv"), by one of its "/"-separated parts
    ("SUV / XUV" -> "xuv"), or by a long prefix of its first word ("hatch" ->
    "hatchback"). When one match sits inside a longer one ("suv" inside
    "compact suv") only the longer survives. Several equal matches are
    returned together — the caller treats that as ambiguous."""
    words = normalize(text).split()
    if not words:
        return []
    found: list[tuple[str, int, int]] = []  # (id, start, end)
    for key, name in names.items():
        forms = {normalize(name)} | {normalize(part) for part in re.split(r"[/,|]", name)}
        first = normalize(name).split(" ")[0] if normalize(name) else ""
        hit = None
        for form in sorted((f for f in forms if f), key=len, reverse=True):
            hit = _words_in(words, form.split())
            if hit:
                break
        if not hit and len(first) >= 5:
            for i, w in enumerate(words):
                if len(w) >= 5 and first.startswith(w):
                    hit = (i, i + 1)
                    break
        if hit:
            found.append((key, hit[0], hit[1]))
    kept = [
        f for f in found
        if not any(o is not f and o[1] <= f[1] and f[2] <= o[2] and (o[2] - o[1]) > (f[2] - f[1]) for o in found)
    ]
    return [k for k, _, _ in kept]


# ----------------------------------------------------------------------
# Price list text
# ----------------------------------------------------------------------


def money(value: float) -> str:
    return f"₹{value:g}"


def split_messages(blocks: list[str], limit: int = MAX_TEXT_LEN) -> list[str]:
    """Joins blocks with blank lines into as few messages as fit `limit`
    (never cutting a block in the middle — a vehicle type's lines stay together)."""
    out: list[str] = []
    current = ""
    for block in blocks:
        if not block:
            continue
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            out.append(current)
        current = block[:limit]
    if current:
        out.append(current)
    return out
