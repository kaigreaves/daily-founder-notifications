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
    RE_DEALS_PATH,
    deal_key as make_deal_key,
    format_amount,
    load_state,
    now_et,
    property_key,
    re_sent_keys,
    read_json,
    save_state,
    sent_keys,
)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
FROM_NAME = "Daily Deals"

TEMPLATE = (
    "{target_industry} company, {target_name}, was acquired by "
    "{acquirer_industry} company, {acquirer_name}, for ${amount} {unit}. "
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
    """Malformed output. Something is broken - fail loudly."""


class Skipped(Exception):
    """A legitimate reason not to send. Not an error; exit cleanly."""


def clean_industry(value, field):
    label = str(value or "").strip().rstrip(",")
    label = re.sub(r"\s+company$", "", label, flags=re.IGNORECASE).strip()
    if not label:
        raise Rejected("{} is empty".format(field))
    # Three words reads fine ("Oil and Gas company"). Longer gets trimmed
    # rather than rejected - a clumsy label is not a reason to send nothing.
    words = label.split()
    if len(words) > 3:
        label = " ".join(words[:3])
    return label[:1].upper() + label[1:]


def clean_name(value, field):
    name = str(value or "").strip().rstrip(",")
    name = re.sub(r"[\s,]+(inc|corp|corporation|ltd|limited|llc|plc|sa|nv|ag|gmbh)\.?$",
                  "", name, flags=re.IGNORECASE).strip()
    if not name:
        raise Rejected("{} is empty".format(field))
    return name


def validate(deal):
    """Validate one candidate. Raises Skipped (try the next) or Rejected."""
    missing = [f for f in REQUIRED_FIELDS if deal.get(f) in (None, "")]
    if missing:
        raise Rejected("missing field(s): {}".format(", ".join(missing)))

    confidence = str(deal.get("confidence", "high")).lower()
    if confidence not in ACCEPTED_CONFIDENCE:
        raise Skipped(
            "the model rated this deal {!r} confidence, so it is probably not a "
            "real purchase price. Nothing sent.".format(confidence)
        )

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
    amount, unit = format_amount(deal["amount_usd_millions"])
    return TEMPLATE.format(
        target_industry=deal["target_industry"],
        target_name=deal["target_name"],
        acquirer_industry=deal["acquirer_industry"],
        acquirer_name=deal["acquirer_name"],
        amount=amount,
        unit=unit,
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


def load_realestate(path, already_seen):
    """Up to three property deals for the second section. Optional: a missing
    or malformed file drops the section rather than failing the send."""
    if not path or not os.path.exists(path):
        return []
    try:
        doc = read_json(path)
    except (ValueError, OSError) as exc:
        print("WARNING: could not read {}: {}".format(path, exc), file=sys.stderr)
        return []

    if doc.get("status") == "no_deal":
        return []

    out = []
    for d in doc.get("deals", []) or []:
        name = str(d.get("property_name", "")).strip()
        city = str(d.get("city", "")).strip()
        if not name or not city:
            continue
        try:
            amount = float(d.get("amount_usd_millions"))
        except (TypeError, ValueError):
            continue
        if amount <= 0:
            continue
        url = str(d.get("article_url", "")).strip()
        if not url.lower().startswith(("http://", "https://")):
            continue

        key = property_key(name, city)
        if key in already_seen:
            continue

        out.append({
            "key": key,
            "property_name": name,
            "city": city,
            "region": str(d.get("region", "")).strip(),
            "country": str(d.get("country", "")).strip(),
            "property_type": str(d.get("property_type", "")).strip() or "Property",
            "amount_usd_millions": amount,
            "currency_note": str(d.get("currency_note", "")).strip(),
            "buyer": str(d.get("buyer", "")).strip() or "Undisclosed",
            "seller": str(d.get("seller", "")).strip() or "Undisclosed",
            "article_url": url,
            "article_source": str(d.get("article_source", "")).strip(),
            "why_notable": str(d.get("why_notable", "")).strip(),
        })

    out.sort(key=lambda d: d["amount_usd_millions"], reverse=True)
    return out[:3]


def realestate_lines(deals):
    """Plain-text rendering of the property section."""
    lines = []
    for d in deals:
        amount, unit = format_amount(d["amount_usd_millions"])
        where = ", ".join(x for x in (d["city"], d["region"]) if x)
        lines.append("* {} - {}".format(d["property_name"], where))
        lines.append("  {} | ${} {}{}".format(
            d["property_type"], amount, unit,
            "  ({})".format(d["currency_note"]) if d["currency_note"] else ""))
        lines.append("  {} bought from {}".format(d["buyer"], d["seller"]))
        if d["why_notable"]:
            lines.append("  {}".format(d["why_notable"]))
        lines.append("  {}".format(d["article_url"]))
        lines.append("")
    return lines


def realestate_html(deals):
    if not deals:
        return ""
    cards = []
    for d in deals:
        amount, unit = format_amount(d["amount_usd_millions"])
        where = ", ".join(x for x in (d["city"], d["region"], d["country"]) if x)
        note = (' <span style="color:#86868b;">({})</span>'.format(
            escape(d["currency_note"])) if d["currency_note"] else "")
        why = ('<p style="margin:8px 0 0;font-size:14px;line-height:1.45;'
               'color:#3a3a3c;">{}</p>'.format(escape(d["why_notable"]))
               if d["why_notable"] else "")
        cards.append("""
    <div style="border-top:1px solid #e5e5ea;padding:16px 0 4px;">
      <p style="margin:0;font-size:16px;font-weight:600;color:#1d1d1f;">
        <a href="{url}" style="color:#1d1d1f;text-decoration:none;">{name}</a>
      </p>
      <p style="margin:4px 0 0;font-size:13px;color:#86868b;">
        {where} &middot; {ptype}
      </p>
      <p style="margin:8px 0 0;font-size:20px;font-weight:600;color:#0071e3;">
        ${amount} {unit}{note}
      </p>
      <p style="margin:6px 0 0;font-size:14px;color:#3a3a3c;">
        {buyer} &larr; {seller}
      </p>
      {why}
      <p style="margin:10px 0 0;font-size:14px;">
        <a href="{url}" style="color:#0071e3;text-decoration:none;">Read it &rarr;</a>
      </p>
    </div>""".format(
            url=escape(d["article_url"]),
            name=escape(d["property_name"]),
            where=escape(where),
            ptype=escape(d["property_type"]),
            amount=amount, unit=unit, note=note,
            buyer=escape(d["buyer"]), seller=escape(d["seller"]),
            why=why,
        ))

    return """
    <p style="margin:32px 0 4px;font-size:12px;font-weight:700;
              letter-spacing:0.08em;text-transform:uppercase;color:#86868b;">
      Biggest property deals &middot; US &amp; Canada
    </p>{cards}""".format(cards="".join(cards))


def escape(text):
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_message(subject, headline, url, deal, re_deals, sender, recipient):
    """`headline` is the lead sentence; `subject` may differ on days with no
    company deal, where the property section carries the email on its own."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((FROM_NAME, sender))
    msg["To"] = recipient

    meta = ""
    if deal:
        meta = "  \u00b7  ".join(
            b for b in (deal["article_source"], deal["announced_date"]) if b)

    text = [headline, ""]
    if url:
        text += [url, ""]
    if meta:
        text += [meta, ""]
    if re_deals:
        text += ["", "BIGGEST PROPERTY DEALS - US & CANADA", ""]
        text += realestate_lines(re_deals)
    msg.set_content("\n".join(text) + "\n")

    if deal and url:
        lead = """
    <p style="margin:0 0 24px;font-size:19px;line-height:1.45;color:#1d1d1f;">{headline}</p>
    <a href="{url}"
       style="display:block;text-align:center;background:#0071e3;color:#ffffff;
              text-decoration:none;font-size:17px;font-weight:600;
              padding:16px 24px;border-radius:10px;">Read the story &rarr;</a>
    <p style="margin:20px 0 0;font-size:13px;color:#86868b;">{meta}</p>""".format(
            headline=escape(headline), url=escape(url), meta=escape(meta))
    else:
        lead = ('\n    <p style="margin:0;font-size:17px;line-height:1.45;'
                'color:#3a3a3c;">{}</p>'.format(escape(headline)))

    msg.add_alternative(
        """\
<html><body style="margin:0;padding:24px;background:#f5f5f7;
 font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <div style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:14px;
              padding:28px;">{lead}{realestate}
  </div>
</body></html>
""".format(lead=lead, realestate=realestate_html(re_deals)),
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
    ap.add_argument("--realestate", default=RE_DEALS_PATH)
    ap.add_argument("--dry-run", action="store_true",
                    help="Render and validate, but do not send or record.")
    args = ap.parse_args()

    state = load_state()

    # ---- section 1: the company acquisition -----------------------------
    # A missing file means the acquisition selector failed. That is worth
    # knowing, but it is not a reason to withhold the property section too, so
    # carry on with an empty candidate list. The workflow still marks the run
    # failed via its summary step.
    raw = {}
    if not os.path.exists(args.deal):
        print("WARNING: {} does not exist - the acquisition selector did not "
              "produce a result. Continuing with the property section only."
              .format(args.deal), file=sys.stderr)
    else:
        try:
            raw = read_json(args.deal)
        except (ValueError, OSError) as exc:
            print("WARNING: could not read {}: {}".format(args.deal, exc),
                  file=sys.stderr)
            raw = {}

    # The selector returns a ranked list. Older files held a single deal at the
    # top level; accept both so a stale work/ directory still works.
    candidates = raw.get("deals")
    if candidates is None:
        candidates = [raw] if raw.get("target_name") else []

    already = sent_keys(state)
    deal, key = None, None
    notes, newly_skipped = [], []

    if raw.get("status") == "no_deal":
        notes.append(raw.get("reason") or "no qualifying acquisition")

    for i, cand in enumerate(candidates, 1):
        try:
            checked = validate(cand)
        except Skipped as exc:
            notes.append("#{} skipped: {}".format(i, exc))
            continue
        except Rejected as exc:
            notes.append("#{} rejected: {}".format(i, exc))
            # Remember it so tomorrow's selection does not offer it again and
            # stall the whole system on one bad record.
            t, a = cand.get("target_name"), cand.get("acquirer_name")
            if t and a:
                newly_skipped.append(make_deal_key(t, a))
            continue

        candidate_key = make_deal_key(checked["target_name"],
                                      checked["acquirer_name"])
        if candidate_key in already:
            notes.append("#{} already sent: {} <- {}".format(
                i, checked["target_name"], checked["acquirer_name"]))
            continue

        deal, key = checked, candidate_key
        print("Using candidate #{} of {}.".format(i, len(candidates)))
        break

    for n in notes:
        print("  {}".format(n))

    company_note = None
    if deal is None:
        company_note = ("No new acquisition today."
                        if notes else "No qualifying acquisition yesterday.")
        print(company_note)

    # ---- section 2: property deals --------------------------------------
    re_deals = load_realestate(args.realestate, re_sent_keys(state))
    print("Property deals to include: {}".format(len(re_deals)))

    if deal is None and not re_deals:
        print("Nothing to send today. Exiting cleanly.")
        return 0

    # ---- compose ---------------------------------------------------------
    if deal is not None:
        subject = headline = render(deal)
        article_url = resolve_url(deal["article_url"])
    else:
        # The property section carries the email on its own.
        where = ", ".join(d["city"] for d in re_deals)
        subject = "No major acquisition yesterday. {} property deal{} inside: {}.".format(
            len(re_deals), "" if len(re_deals) == 1 else "s", where)
        headline = company_note or "No qualifying acquisition yesterday."
        article_url = None

    print("Subject: {}".format(subject))
    if article_url:
        print("Link:    {}".format(article_url))
    for d in re_deals:
        amount, unit = format_amount(d["amount_usd_millions"])
        print("   RE:   ${} {} - {} ({}, {})".format(
            amount, unit, d["property_name"], d["city"], d["property_type"]))

    if args.dry_run:
        print("\n--dry-run: not sending, not recording.")
        return 0

    sender = os.environ.get("GMAIL_ADDRESS", "").strip()
    # Google shows App Passwords as "abcd efgh ijkl mnop". Those spaces are
    # display formatting, not part of the secret, and SMTP rejects them - so
    # strip all whitespace rather than just the ends.
    password = re.sub(r"\s+", "", os.environ.get("GMAIL_APP_PASSWORD", ""))
    recipient = os.environ.get("NOTIFY_TO", "").strip() or sender

    if not sender or not password:
        print("ERROR: GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set.",
              file=sys.stderr)
        return 1

    msg = build_message(subject, headline, article_url, deal, re_deals,
                        sender, recipient)

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

    # ---- record ----------------------------------------------------------
    if deal is not None and key:
        state["sent"].append({
            "sent_at_et": now_et().isoformat(timespec="seconds"),
            "deal_key": key,
            "target_name": deal["target_name"],
            "acquirer_name": deal["acquirer_name"],
            "amount_usd_millions": deal["amount_usd_millions"],
            "announced_date": deal["announced_date"],
            "article_url": article_url,
            "used_fallback": deal["used_fallback"],
            "message": subject,
        })

    if re_deals:
        state.setdefault("re_sent", []).extend(d["key"] for d in re_deals)

    if newly_skipped:
        skipped = state.setdefault("skipped", [])
        skipped.extend(k for k in newly_skipped if k not in skipped)
        print("Recorded {} deal(s) as unusable so they are not retried."
              .format(len(newly_skipped)))

    save_state(state)
    print("Recorded in state/sent.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
