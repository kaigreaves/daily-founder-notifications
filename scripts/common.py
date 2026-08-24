"""Shared helpers: time windows, money parsing, text normalization, state file I/O."""

import json
import os
import re
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK_DIR = os.path.join(REPO_ROOT, "work")
STATE_PATH = os.path.join(REPO_ROOT, "state", "sent.json")

CANDIDATES_PATH = os.path.join(WORK_DIR, "candidates.json")
DEAL_PATH = os.path.join(WORK_DIR, "deal.json")

# Second section of the email: US/Canada property transactions.
RE_CANDIDATES_PATH = os.path.join(WORK_DIR, "re_candidates.json")
RE_DEALS_PATH = os.path.join(WORK_DIR, "realestate.json")

# How far back the fallback search reaches when yesterday has no usable deal.
LOOKBACK_DAYS = 7


def now_et():
    return datetime.now(tz=ET)


def target_date(now=None):
    """The day we report on: yesterday, in Eastern time."""
    now = now or now_et()
    return (now - timedelta(days=1)).date()


def iso(d):
    return d.isoformat()


# --------------------------------------------------------------------------
# Money parsing
# --------------------------------------------------------------------------

_MULTIPLIERS = {
    "trillion": 1_000_000.0,
    "tn": 1_000_000.0,
    "billion": 1_000.0,
    "bn": 1_000.0,
    "b": 1_000.0,
    "million": 1.0,
    "mn": 1.0,
    "m": 1.0,
}

# Matches "$4.1 billion", "US$322 million", "$1.2B", "$322M".
_MONEY_RE = re.compile(
    r"(?:US)?\$\s?(\d[\d,]*(?:\.\d+)?)\s*"
    r"(trillion|billion|million|tn|bn|mn|b|m)\b",
    re.IGNORECASE,
)


def parse_amounts_usd_millions(text):
    """Return every USD figure found in `text`, expressed in millions."""
    out = []
    for raw, unit in _MONEY_RE.findall(text or ""):
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        mult = _MULTIPLIERS.get(unit.lower())
        if mult:
            out.append(value * mult)
    return out


def largest_amount_usd_millions(text):
    amounts = parse_amounts_usd_millions(text)
    return max(amounts) if amounts else None


def format_millions(value):
    """322.0 -> '322'   4100.0 -> '4,100'"""
    return "{:,}".format(int(round(float(value))))


def format_amount(millions):
    """Split a figure in millions into (number, unit) for the template.

    Anything at or above a billion reads as billions - "$60,000 million" is
    technically correct and completely unreadable.

        322.0   -> ('322', 'million')
        920.0   -> ('920', 'million')
        1050.0  -> ('1.05', 'billion')
        4100.0  -> ('4.1', 'billion')
        60000.0 -> ('60', 'billion')
    """
    millions = float(millions)
    if millions >= 1000:
        billions = millions / 1000.0
        text = "{:,.2f}".format(billions).rstrip("0").rstrip(".")
        return text, "billion"
    return format_millions(millions), "million"


# --------------------------------------------------------------------------
# Text / dedup keys
# --------------------------------------------------------------------------

_PUNCT_RE = re.compile(r"[^a-z0-9 ]+")
_WS_RE = re.compile(r"\s+")

# Suffixes stripped so "BluCorp, Inc." and "BluCorp" collapse to one key.
_CORP_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd",
    "limited", "llc", "lp", "llp", "plc", "sa", "nv", "ag", "gmbh", "ab",
    "as", "oyj", "spa", "holdings", "holding", "group", "the",
}


def normalize(text):
    text = unicodedata.normalize("NFKD", text or "")
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = _PUNCT_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def company_key(name):
    tokens = [t for t in normalize(name).split() if t not in _CORP_SUFFIXES]
    return " ".join(tokens) or normalize(name)


def deal_key(target, acquirer):
    """Stable identity for a deal, order-independent of phrasing."""
    return "{}|{}".format(company_key(target), company_key(acquirer))


def property_key(name, city):
    """Stable identity for a property transaction.

    Keyed on the property plus its city because building names repeat across
    markets - plenty of cities have a "One Marina Park" or a "Centre Square".
    """
    return "{}|{}".format(normalize(name), normalize(city))


# --------------------------------------------------------------------------
# Paywalls
# --------------------------------------------------------------------------

# Outlets that hard-paywall most business coverage. A link the reader cannot
# open is worse than a link to a smaller outlet.
PAYWALLED_SOURCES = {
    "bloomberg", "wsj", "wall street journal", "ft", "financial times",
    "nytimes", "new york times", "the information", "barrons", "barron's",
    "economist", "the economist", "washingtonpost", "washington post",
    "seekingalpha", "seeking alpha", "businessinsider", "business insider",
    "insider", "telegraph", "law360", "politico pro",
    "crain's", "crains", "american banker", "modern healthcare", "statnews",
    "stat news", "the athletic", "puck", "axios pro", "globes", "nikkei",
    "handelsblatt", "les echos", "mergermarket", "pitchbook",
    # The UK Times specifically. A bare "the times" also matches The Times of
    # India, The Irish Times, and the Seattle Times, none of which belong here.
    "thetimes", "times of london", "sunday times",
    # Real-estate trade press that hard-paywalls.
    "costar", "bizjournals", "business journals", "real estate alert",
    "green street", "trepp", "the deal",
}

# Reliably free, reputable, and widely read. Used to rank the good options.
FREE_SOURCES = {
    "reuters", "apnews", "ap news", "associated press", "cnbc", "bbc",
    "techcrunch", "the verge", "theverge", "axios", "yahoo", "marketwatch",
    "investing.com", "cnn", "npr", "guardian", "the guardian", "forbes",
    "fortune", "businesswire", "business wire", "prnewswire", "pr newswire",
    "globenewswire", "zdnet", "ars technica", "engadget", "quartz",
    "fierce", "endpoints", "benzinga", "thestreet", "aljazeera", "dw",
    # Free real-estate trade press, used for the property section.
    "renx", "therealdeal", "the real deal", "bisnow", "globest", "rejournals",
    "connect cre", "connectcre", "multi housing news", "multifamily dive",
    "commercial property executive", "rebusinessonline", "storeys",
    "mercury news", "nbc", "abc", "cbs", "patch", "spectrum news",
}


# Normalized once so entries written with dots or apostrophes ("investing.com",
# "barron's") can still match text that has been stripped of punctuation.
_PAYWALLED_NORM = {normalize(n) for n in PAYWALLED_SOURCES}
_FREE_NORM = {normalize(n) for n in FREE_SOURCES}


def _matches(source, url, names):
    """Whole-word matching.

    Substring matching is wrong here: "ft" appears inside "Microsoft", which
    would flag half the tech press as paywalled. Single-word names must match a
    whole token; multi-word names are matched as a phrase.
    """
    blob = normalize("{} {}".format(source or "", url or ""))
    tokens = set(blob.split())
    for name in names:
        if not name:
            continue
        if " " in name:
            if name in blob:
                return True
        elif name in tokens:
            return True
    return False


def is_paywalled(source, url=""):
    return _matches(source, url, _PAYWALLED_NORM)


def is_known_free(source, url=""):
    return _matches(source, url, _FREE_NORM)


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------

def load_state():
    if not os.path.exists(STATE_PATH):
        return {"sent": []}
    with open(STATE_PATH, "r", encoding="utf-8") as fh:
        try:
            state = json.load(fh)
        except json.JSONDecodeError:
            return {"sent": []}
    state.setdefault("sent", [])
    state.setdefault("re_sent", [])
    state.setdefault("skipped", [])
    return state


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    # Keep the file from growing forever; 400 entries is well over a year.
    state["sent"] = state["sent"][-400:]
    state["re_sent"] = state.get("re_sent", [])[-600:]
    state["skipped"] = state.get("skipped", [])[-200:]
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def sent_keys(state):
    return {entry["deal_key"] for entry in state.get("sent", []) if entry.get("deal_key")}


def re_sent_keys(state):
    """Property deals already shown, so each day surfaces new ones."""
    return {k for k in state.get("re_sent", []) if k}


def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
