# Official ETF share update strategy

## Publication objective and limits

The target is a verified client report around **00:00 Beijing time**. If complete
official shares and same-day NAV are available by 23:50, the operational budget
is five minutes for detection and five minutes for build, audit and deployment.
This is a target, not an unconditional delivery guarantee. An official probe
timeout, a quality failure, runner queueing or deployment delay can exceed it.
Never publish partial shares, a substituted vendor estimate, or a false date to
meet the clock. The last verified report stays online until its replacement
passes every gate.

GitHub explicitly documents that scheduled events can be delayed or dropped:
<https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule>.
On 2026-09-07/08 the midnight scheduled run first appeared at 02:47 Beijing,
while the afternoon capture had already finished at 21:39. Adding only more
midnight cron expressions does not address that delay.

## Active production chain

Only `capture-etf-order-flow.yml` and `daily-etf-data.yml` contain recurring data
schedules. The Render verification workflow is event driven.

1. The completed afternoon capture starts the official publication workflow via
   `workflow_run`. A failed optional capture also wakes the official workflow.
   Off-peak 18:13 and 20:13 clocks provide additional early entry points.
2. Resolve the latest completed weekday in Beijing, independently of order-flow
   files. Pin that exact date across midnight. Skip an already verified REAL
   date. Holidays are not fabricated; the official exact-date gate decides.
3. An active early runner probes every 15 minutes until 20:00. If data is ready,
   publish immediately. Otherwise the same workflow starts its overnight phase,
   without waiting for another cron event.
4. The overnight runner probes every five minutes until 01:30. Intervals start
   after the prior probe completes. Each probe is bounded at four minutes. Two
   phases keep each job below GitHub's six-hour limit. A late morning/weekend
   invocation still makes an immediate check rather than skipping expired dates.
5. Accept only same-date official SSE and SZSE rows with valid schema, coverage,
   uniqueness, signs and units. A quality error fails immediately. Network,
   unpublished and WAF states retry; only a verified artifact reaches the builder.
6. The builder uses the verified official-share cache, including T-1 history;
   it does not refresh already verified old sessions during publication. Missing
   history is still obtained from the official source. Run all deterministic
   tests, build the v6 snapshot, audit reconciliations and run client tests.
7. Commit the complete report to `main`; Render deploys that commit. A separate
   verification compares the entire public JSON with the committed JSON, rather
   than accepting a matching date alone.
8. Foreground pages check the public snapshot every minute using ETag headers.
   Returning to a phone tab also checks immediately. Unchanged data avoids a
   full download; failed refreshes preserve the displayed verified report.

The requested 22:30, 23:00, 23:30, 00:00, 00:30, 01:00 and 01:30 clocks remain
as fallback entry points. Morning checks at 08:20 and 12:20, plus weekend checks,
continue recovery after late upstream publication. A single concurrency group
keeps one publisher active; redundant queued entries recheck whether work is
still needed. A long-running watcher is waiting for upstream data, not repeatedly
rebuilding the report.

## Diagnosis and data policy

The resolver records the actual Beijing start time and trigger. The watcher logs
the pinned date, each probe result, next check time and deadline. Its final source
status is retained as an Actions artifact for seven days. A missing verified
artifact fails publication rather than silently succeeding. The existing failure
notification is used when configured; upstream unavailability is explicitly
reported in the Actions summary and warning.

Client shares remain the official Shanghai and Shenzhen closing observations.
AKShare is a pinned transport adapter, not an alternative authority. Eastmoney
or other vendor "latest shares" remain audit-only. Existing NAV valuation,
corporate-action adjustments, coverage, reconciliation and conclusion rules are
unchanged by this scheduling repair.
