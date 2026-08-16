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
    return state


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    # Keep the file from growing forever; 400 entries is well over a year.
    state["sent"] = state["sent"][-400:]
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def sent_keys(state):
    return {entry["deal_key"] for entry in state.get("sent", []) if entry.get("deal_key")}


def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
