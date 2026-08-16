#!/usr/bin/env python3
"""Pull M&A headlines from free RSS feeds and shortlist the priced deals.

Deliberately stdlib-only: no API keys, no pip install, nothing to rot.

Python does the numeric work (parsing "$4.1 billion" into 4100) and the
coarse filtering. The LLM step that follows only has to validate the top
handful and label industries, which keeps it cheap and hard to derail.

Output: work/candidates.json
"""

import argparse
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET_XML
from datetime import timedelta
from email.utils import parsedate_to_datetime

from common import (
    CANDIDATES_PATH,
    ET,
    LOOKBACK_DAYS,
    iso,
    largest_amount_usd_millions,
    normalize,
    now_et,
    target_date,
    write_json,
)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

GOOGLE_NEWS_QUERIES = [
    '"to acquire" ("billion" OR "million") when:{days}d',
    '"acquired by" ("billion" OR "million") when:{days}d',
    '"acquisition of" ("billion" OR "million") when:{days}d',
    '"merger agreement" ("billion" OR "million") when:{days}d',
    '"all-cash" (acquisition OR takeover) when:{days}d',
    '"takeover" ("billion" OR "million") when:{days}d',
    '"definitive agreement" acquire when:{days}d',
]

# Direct publisher feeds. Any of these may 404 or rename without warning;
# failures are logged and skipped so Google News always carries the run.
STATIC_FEEDS = [
    "https://www.prnewswire.com/rss/financial-services-latest-news/acquisitions-mergers-and-takeovers-list.rss",
    "https://www.globenewswire.com/RssFeed/subjectcode/1-Mergers%20And%20Acquisitions/feedTitle/GlobeNewswire%20-%20Mergers%20and%20Acquisitions",
]

# Title must look like a company changing hands.
ACQUISITION_RE = re.compile(
    r"\b(acquir\w*|acquisition|takeover|to buy|buys|bought|merger|merge[sd]?|"
    r"purchase[sd]?|sold to|sells)\b",
    re.IGNORECASE,
)

# Headlines that carry a dollar figure but are not a company sale.
EXCLUDE_RE = re.compile(
    r"\b(raises|raised|funding round|series [a-z]\b|ipo|valuation|valued at|"
    r"buyback|share repurchase|stake|minority interest|joint venture|"
    r"contract|order|loan|bond|debt offering|dividend|lawsuit|settlement|"
    r"fine[sd]?|revenue|earnings|guidance|forecast|etf|index fund|"
    r"real estate|reit|portfolio of properties|land|acreage)\b",
    re.IGNORECASE,
)

# Under this, it's not going to be the day's biggest deal anyway. Dropping
# small figures kills most parse noise ("$5 million contract" etc).
MIN_AMOUNT_MILLIONS = 50.0

MAX_CANDIDATES = 120


def fetch(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def strip_html(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )
    return re.sub(r"\s+", " ", text).strip()


def parse_feed(xml_bytes):
    """Return [{title, link, source, published, summary}] from an RSS document."""
    items = []
    try:
        root = ET_XML.fromstring(xml_bytes)
    except ET_XML.ParseError as exc:
        print("  ! XML parse error: {}".format(exc), file=sys.stderr)
        return items

    for item in root.iter("item"):
        title = strip_html((item.findtext("title") or ""))
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue

        pub_raw = item.findtext("pubDate") or item.findtext("published") or ""
        published = None
        if pub_raw:
            try:
                published = parsedate_to_datetime(pub_raw).astimezone(ET)
            except (TypeError, ValueError):
                published = None

        source_el = item.find("source")
        source = ""
        if source_el is not None and source_el.text:
            source = source_el.text.strip()
        if not source:
            source = urllib.parse.urlparse(link).netloc.replace("www.", "")

        items.append(
            {
                "title": title,
                "link": link,
                "source": source,
                "published": published,
                "summary": strip_html(item.findtext("description") or "")[:400],
            }
        )
    return items


def google_news_url(query):
    return "https://news.google.com/rss/search?q={}&hl=en-US&gl=US&ceid=US:en".format(
        urllib.parse.quote(query)
    )


def collect(days):
    raw = []
    urls = [google_news_url(q.format(days=days)) for q in GOOGLE_NEWS_QUERIES]
    urls += STATIC_FEEDS

    for url in urls:
        label = url[:95]
        try:
            items = parse_feed(fetch(url))
            print("  + {} item(s)  <- {}".format(len(items), label))
            raw.extend(items)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            print("  ! skipped ({}): {}".format(exc, label), file=sys.stderr)
    return raw


def shortlist(raw, window_start, window_end):
    seen_titles = set()
    out = []

    for item in raw:
        pub = item["published"]
        if pub is None:
            continue
        pub_date = pub.date()
        if not (window_start <= pub_date <= window_end):
            continue

        blob = "{} {}".format(item["title"], item["summary"])
        if not ACQUISITION_RE.search(item["title"]):
            continue
        if EXCLUDE_RE.search(blob):
            continue

        amount = largest_amount_usd_millions(blob)
        if amount is None or amount < MIN_AMOUNT_MILLIONS:
            continue

        key = normalize(item["title"])[:110]
        if key in seen_titles:
            continue
        seen_titles.add(key)

        out.append(
            {
                "title": item["title"],
                "summary": item["summary"],
                "url": item["link"],
                "source": item["source"],
                "published_et": pub.isoformat(),
                "published_date_et": iso(pub_date),
                "parsed_amount_usd_millions": round(amount, 2),
                "is_target_date": pub_date == window_end,
            }
        )

    out.sort(
        key=lambda c: (c["is_target_date"], c["parsed_amount_usd_millions"]),
        reverse=True,
    )
    return out[:MAX_CANDIDATES]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--days", type=int, default=LOOKBACK_DAYS + 1,
        help="How many days of news to pull (default covers the fallback window).",
    )
    ap.add_argument("--out", default=CANDIDATES_PATH)
    args = ap.parse_args()

    now = now_et()
    tdate = target_date(now)
    window_start = tdate - timedelta(days=LOOKBACK_DAYS - 1)

    print("Run time (ET):   {}".format(now.isoformat(timespec="seconds")))
    print("Target date:     {}".format(iso(tdate)))
    print("Fallback window: {} .. {}".format(iso(window_start), iso(tdate)))
    print("Fetching feeds...")

    raw = collect(args.days)
    candidates = shortlist(raw, window_start, tdate)

    on_target = sum(1 for c in candidates if c["is_target_date"])
    print(
        "\n{} raw item(s) -> {} priced candidate(s) ({} from the target date)".format(
            len(raw), len(candidates), on_target
        )
    )
    for c in candidates[:10]:
        print(
            "   ${:>10}M  {}  [{}]".format(
                int(c["parsed_amount_usd_millions"]), c["title"][:78], c["source"]
            )
        )

    payload = {
        "generated_at_et": now.isoformat(timespec="seconds"),
        "target_date": iso(tdate),
        "window_start": iso(window_start),
        "window_end": iso(tdate),
        "candidate_count": len(candidates),
        "candidates": candidates,
    }
    write_json(args.out, payload)
    print("\nWrote {}".format(args.out))

    if not candidates:
        print("No priced candidates found in the window.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
