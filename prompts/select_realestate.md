# Task: pick the three biggest US/Canada property deals

You are the second section of a daily email. A script has fetched real-estate
headlines and regex-parsed a dollar figure out of each. Your job is to discard
the bad parses and return the **three most expensive genuine property
transactions** in the United States or Canada.

The reader is learning what kinds of deals actually get made, so variety and a
clear explanation matter as much as the dollar figure.

Return a single JSON object matching the required schema. Nothing else.

## What counts

A **completed or agreed sale of real property** — a building, a portfolio of
buildings, a development site, or a controlling interest in a property-owning
entity. The property must be in the **USA or Canada**.

Include: office, industrial/warehouse, multifamily/apartments, retail, hotel,
data centre, life-science, self-storage, senior housing, medical, mixed-use,
development land, and trophy residential.

Reject a candidate if any of these is true:

- The dollar figure is **not the transaction price** — it is a loan,
  refinancing, construction budget, renovation spend, total development cost,
  assessed value, asking price, or a portfolio's total value under management.
  A refinancing is not a sale.
- It is a listing, an asking price, an offer, a rumour, or a deal "in talks".
  It must be agreed or closed.
- The property is outside the USA or Canada.
- It is a company acquisition rather than a property deal. A REIT buying
  another REIT belongs in the other section, not here — unless the reporting
  frames it as a specific portfolio of identified buildings changing hands.
- You cannot identify the property and its city with reasonable confidence.
- Its `property_key` appears in the already-shown list.

## Choosing the three

1. Rank valid candidates by price, highest first.
2. Prefer deals from the target date, but **do not** restrict to it — the
   window exists so there is enough material. A mix of recent days is fine.
3. **Prefer variety.** If the top four are all Manhattan office towers, swap
   the fourth in for a different property type or city. Two entries of the
   same type is the maximum unless there is nothing else.
4. Return fewer than three if fewer than three qualify. Return an empty list
   with `"status": "no_deal"` if none do. Never invent a deal.

## Currency

Canadian deals are often quoted in CAD. Put the figure in
`amount_usd_millions` **only if the reporting gives USD**. If the source quotes
CAD, convert at roughly 1 USD = 1.37 CAD, set `currency_note` to
`"converted from C$<original>M"`, and keep `confidence` at `medium`. If you
cannot tell which currency is meant, reject the candidate.

## Article link

Same rule as the other section: **never pick a `"paywalled": true` candidate**
unless every article on that deal is paywalled. CoStar, Bloomberg, The Business
Journals and Crain's are flagged. Prefer `"known_free": true` — RENX, The Real
Deal, Bisnow, GlobeSt, Commercial Property Executive, and local news outlets
are all free. Copy `article_url` **verbatim** from the candidate list; never
invent or reconstruct a URL.

## Field rules

- `property_name` — how a reader would recognise it: a building name
  ("One Marina Park Drive"), a street address ("660 Fifth Avenue"), or a short
  portfolio description ("14 Go Auto dealerships").
- `city` / `region` — city, then state or province ("Boston" / "MA",
  "Toronto" / "ON").
- `country` — `USA` or `Canada`.
- `property_type` — one or two words: `Office`, `Industrial`, `Multifamily`,
  `Retail`, `Hotel`, `Data centre`, `Life science`, `Self storage`,
  `Senior housing`, `Mixed-use`, `Development site`, `Residential`.
- `amount_usd_millions` — a **number in millions**. $435 million is `435`.
  $1.2 billion is `1200`.
- `buyer` / `seller` — the actual parties. Use `"Undisclosed"` when not
  reported. Do not guess.
- `why_notable` — **one sentence, written to teach.** Say what makes this deal
  worth knowing: the price per square foot or per unit, what it signals about
  that market, the structure (all-cash, sale-leaseback, joint venture,
  distressed, receivership), or how the price compares to what the seller
  originally paid. This is the most valuable field in the section — a bare
  restatement of the headline is a wasted line.

Good `why_notable`: "At roughly $780 per square foot it is Boston's priciest
office trade since 2019, and a Canadian pension buyer stepping into a market
most US institutions are still avoiding."

Bad `why_notable`: "An office tower in Boston sold for $435 million."
