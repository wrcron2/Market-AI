"""Ops layer (Trading System v2.4 plan, §6) — standalone ops/tax tooling.

Four small services convert calendar obligations (tax, DST, corporate
actions) from memory into monitored jobs:

  - dst_scheduler.DstScheduler   §6.1  session logic anchored to
                                       America/New_York; Israel time derived
  - ca_daily_job.CaDailyJob      §6.2  corporate-actions daily job; alerts on
                                       open orders near ex-dates
  - w8ben_monitor.W8BenMonitor   §6.3  W-8BEN expiry monitor (Dec 31 year+3,
                                       T-90/T-30 alerts, halt-on-sell after
                                       lapse)
  - tax_exports                  §6.4  Israeli realized-gains ledger +
                                       semi-annual exports + annual Form 1301
                                       pack

G0 survival constraint (plan §9): if Item Zero parks the trading build, THIS
package is what remains — so ops/ must never import execution_lane/ or
fortress/ (or their intent journal). Every service consumes plain data
(dicts, dates, CSV files) and reports through ops.alerts.AlertSink; wiring to
live sources happens outside this package. The constraint is enforced by
tests/test_ops_standalone_no_journal.py.
"""
