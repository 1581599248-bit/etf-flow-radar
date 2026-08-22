# Official ETF share update strategy

## Production source policy

Client-facing ETF shares remain the post-clearing observations published by the
Shanghai and Shenzhen exchanges.  AKShare may be used as a maintained transport
adapter only when it calls the same official exchange dataset.  Eastmoney or
other vendor "latest shares" are audit evidence and must never silently replace
the official production value.

## Publication objective

The operational SLO is conditional on authoritative availability:

- if both exchange datasets for the target trade date are available by 00:45
  Beijing time, finish validation and publication by 01:00;
- otherwise keep detecting automatically and publish within ten minutes of the
  first verified availability window;
- never replace the last verified snapshot with a partial or estimated result.

## Workflow design

1. Resolve the target from the independently captured order-flow trade date.
2. Skip immediately when that target is already the published official date.
3. Run two independent short-lived probe lanes for one exact date.
4. Accept a probe only when SSE and SZSE are both present and date, schema,
   coverage, uniqueness, sign and unit checks pass.
5. Reconcile the two probe payloads.  Any disagreement blocks publication.
6. Put the verified cross-section in the immutable transport cache.
7. Run the production pipeline once, followed by the reconciliation audit and
   browser/client tests.
8. Publish atomically or retain the previous verified snapshot.

The dense polling window is 23:00-00:55 every five minutes.  Polling continues
at lower frequency after 01:00 and through the weekend when Friday is missing.

## Failure classes

- `not_published`: normal upstream wait; retry automatically.
- `network`: unreachable, DNS, timeout or connection reset; retry on a new
  scheduled runner and prefer IPv4 addresses before IPv6.
- `rate_limited`: respect exchange WAF backoff and retry later.
- `quality_gate`: schema, unit, duplicate, date or coverage failure; block the
  report and surface the failure instead of retrying a bad payload as valid.

## Same-level alternative-source admission

Exchange-owned subscription or authorized delivery is the first candidate.
Wind, iFinD or Choice can only enter a thirty-trading-day shadow comparison.
Admission requires exact trade dates, at least 99.9% official-universe coverage,
at least 99.9% row-level exact agreement, zero median and P99 share error,
correct corporate-action dates, revision timestamps and a documented delivery
SLA.  Failure of any gate keeps the vendor in audit-only status.
