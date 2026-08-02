# K200MQ benchmark and cost attribution

K200MQ reports the configured market-index ticker (normally `KPI200`) as a
**price-return** benchmark.  Its daily returns are calculated from the
supplied close series with `pct_change()` after clipping index observations to
the measured interval.  The first in-period close has no prior in-period
close and therefore produces no return.  The manifest carries the actual
configured source ticker and does not label another ticker as `KPI200`.
No dividends, distributions, withholding taxes, or other adjustments are
invented; therefore this benchmark must not be described as a total-return
index.

Cost attribution sums the actual filled buy and sell notionals in the trade
log.  Commission and slippage are applied to both sides, while transaction
tax is applied to sells only.  `total_cost` is the sum of those filled costs;
it is not a hypothetical cost on requested shares.  Turnover is the sum of
actual buy and sell notionals, with one-way turnover reported as half of that
sum when both sides are available.

The engine writes cumulative filled cost to each portfolio snapshot and to
execution statistics.  Attribution consumes the final snapshot cumulative
cost when a per-fill cost schema is unavailable, rather than silently
reporting zero.
