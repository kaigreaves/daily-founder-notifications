#!/usr/bin/env python3
"""Validate work/deal.json, render the notification, and email it.

This is the gate. If the LLM step produced anything malformed, low-confidence,
or already sent, nothing goes out and the run fails loudly rather than sending
a wrong deal.

The notification *is* the subject line - that is what iOS shows on the lock
screen. The body exists only to give you something big to tap.

Usage:
    python3 scripts/send_notification.py            # send for real
    python3 scripts/send_notification.py --dry-run  # print only, touch nothing
"""

import argparse
import os
import re
import smtplib
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from email.utils import formataddr

from common import (
    DEAL_PATH,
    deal_key as make_deal_key,
    format_millions,
    load_state,
    now_et,
    read_json,
    save_state,
    sent_keys,
)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
FROM_NAME = "Daily Deals"

TEMPLATE = (
    "{target_industry} company, {target_name}, was acquired by "
    "{acquirer_industry} company, {acquirer_name}, for ${amount} million. "
    "Tap to read more."
)

URL_LIMIT = 2000

REQUIRED_FIELDS = (
    "target_name",
    "target_industry",
    "acquirer_name",
    "acquirer_industry",
    "amount_usd_millions",
    "article_url",
)

ACCEPTED_CONFIDENCE = {"high", "medium"}


class Rejected(Exception):
    """The deal is not fit to send."""


def clean_industry(value, field):
    label = str(value or "").strip().rstrip(",")
    label = re.sub(r"\s+company$", "", label, flags=re.IGNORECASE).strip()
    if not label:
        raise Rejected("{} is empty".format(field))
    if len(label.split()) > 2:
        raise Rejected("{} is not a short label: {!r}".format(field, label))
    return label[:1].upper() + label[1:]


def clean_name(value, field):
    name = str(value or "").strip().rstrip(",")
    name = re.sub(r"[\s,]+(inc|corp|corporation|ltd|limited|llc|plc|sa|nv|ag|gmbh)\.?$",
                  "", name, flags=re.IGNORECASE).strip()
    if not name:
        raise Rejected("{} is empty".format(field))
    return name


def validate(deal):
    status = deal.get("status", "ok")
    if status == "no_deal":
        return None

    missing = [f for f in REQUIRED_FIELDS if deal.get(f) in (None, "")]
    if missing:
        raise Rejected("missing field(s): {}".format(", ".join(missing)))

    confidence = str(deal.get("confidence", "high")).lower()
    if confidence not in ACCEPTED_CONFIDENCE:
        raise Rejected("confidence is {!r}; refusing to send".format(confidence))

    try:
        amount = float(deal["amount_usd_millions"])
    except (TypeError, ValueError):
        raise Rejected("amount_usd_millions is not a number: {!r}".format(
            deal.get("amount_usd_millions")))
    if amount <= 0:
        raise Rejected("amount_usd_millions must be positive, got {}".format(amount))

    url = str(deal["article_url"]).strip()
    if not url.lower().startswith(("http://", "https://")):
        raise Rejected("article_url is not a URL: {!r}".format(url))
    if len(url) > URL_LIMIT:
        raise Rejected("article_url exceeds {} chars".format(URL_LIMIT))

    return {
        "target_name": clean_name(deal["target_name"], "target_name"),
        "target_industry": clean_industry(deal["target_industry"], "target_industry"),
        "acquirer_name": clean_name(deal["acquirer_name"], "acquirer_name"),
        "acquirer_industry": clean_industry(deal["acquirer_industry"], "acquirer_industry"),
        "amount_usd_millions": amount,
        "article_url": url,
        "article_source": str(deal.get("article_source", "")).strip(),
        "announced_date": str(deal.get("announced_date", "")).strip(),
        "used_fallback": bool(deal.get("used_fallback", False)),
        "confidence": confidence,
    }


def render(deal):
    return TEMPLATE.format(
        target_industry=deal["target_industry"],
        target_name=deal["target_name"],
        acquirer_industry=deal["acquirer_industry"],
        acquirer_name=deal["acquirer_name"],
        amount=format_millions(deal["amount_usd_millions"]),
    )


def resolve_url(url, timeout=10):
    """Follow redirects to a publisher URL. Google News links often won't
    resolve without JS; falling back to the original is fine."""
    if "news.google.com" not in urllib.parse.urlparse(url).netloc:
        return url
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            final = resp.geturl()
        if final and "news.google.com" not in final and len(final) <= URL_LIMIT:
            return final
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        pass
    return url


def escape(text):
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_message(subject, url, deal, sender, recipient):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((FROM_NAME, sender))
    msg["To"] = recipient

    meta_bits = [b for b in (deal["article_source"], deal["announced_date"]) if b]
    meta = "  ·  ".join(meta_bits)

    msg.set_content(
        "{}\n\n{}\n\n{}\n".format(subject, url, meta or "")
    )

    msg.add_alternative(
        """\
<html><body style="margin:0;padding:24px;background:#f5f5f7;
 font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <div style="max-width:520px;margin:0 auto;background:#ffffff;border-radius:14px;
              padding:28px;">
    <p style="margin:0 0 24px;font-size:19px;line-height:1.45;color:#1d1d1f;">{headline}</p>
    <a href="{url}"
       style="display:block;text-align:center;background:#0071e3;color:#ffffff;
              text-decoration:none;font-size:17px;font-weight:600;
              padding:16px 24px;border-radius:10px;">Read the story &rarr;</a>
    <p style="margin:20px 0 0;font-size:13px;color:#86868b;">{meta}</p>
  </div>
</body></html>
""".format(headline=escape(subject), url=escape(url), meta=escape(meta)),
        subtype="html",
    )
    return msg


def send_email(msg, sender, password):
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context, timeout=30) as server:
        server.login(sender, password)
        server.send_message(msg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deal", default=DEAL_PATH)
    ap.add_argument("--dry-run", action="store_true",
                    help="Render and validate, but do not send or record.")
    args = ap.parse_args()

    if not os.path.exists(args.deal):
        print("ERROR: {} does not exist - the selection step did not run."
              .format(args.deal), file=sys.stderr)
        return 1

    raw = read_json(args.deal)

    try:
        deal = validate(raw)
    except Rejected as exc:
        print("ERROR: refusing to send - {}".format(exc), file=sys.stderr)
        print("Deal payload was: {}".format(raw), file=sys.stderr)
        return 1

    if deal is None:
        print("No qualifying deal in the 7-day window: {}".format(
            raw.get("reason", "no reason given")))
        print("Nothing sent, by design.")
        return 0

    state = load_state()
    key = make_deal_key(deal["target_name"], deal["acquirer_name"])
    if key in sent_keys(state):
        print("Already sent this deal ({}). Nothing to do.".format(key))
        return 0

    subject = render(deal)
    article_url = resolve_url(deal["article_url"])

    print("Subject: {}".format(subject))
    print("Link:    {}".format(article_url))
    print("Source:  {}".format(deal["article_source"] or "unknown"))
    print("Key:     {}".format(key))
    print("Fallback used: {}".format(deal["used_fallback"]))

    if args.dry_run:
        print("\n--dry-run: not sending, not recording.")
        return 0

    sender = os.environ.get("GMAIL_ADDRESS", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    recipient = os.environ.get("NOTIFY_TO", "").strip() or sender

    if not sender or not password:
        print("ERROR: GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set.",
              file=sys.stderr)
        return 1

    msg = build_message(subject, article_url, deal, sender, recipient)

    try:
        send_email(msg, sender, password)
    except smtplib.SMTPAuthenticationError as exc:
        print("ERROR: Gmail rejected the login. Use a 16-character App Password, "
              "not your Google account password. ({})".format(exc), file=sys.stderr)
        return 1
    except (smtplib.SMTPException, ssl.SSLError, OSError) as exc:
        print("ERROR: could not send mail: {}".format(exc), file=sys.stderr)
        return 1

    print("Sent to {}".format(recipient))

    state["sent"].append(
        {
            "sent_at_et": now_et().isoformat(timespec="seconds"),
            "deal_key": key,
            "target_name": deal["target_name"],
            "acquirer_name": deal["acquirer_name"],
            "amount_usd_millions": deal["amount_usd_millions"],
            "announced_date": deal["announced_date"],
            "article_url": article_url,
            "used_fallback": deal["used_fallback"],
            "message": subject,
        }
    )
    save_state(state)
    print("Recorded in state/sent.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
