# ADR 0036: V2.6 tests a low-volatility BTC derivatives regime gate

## Status

Accepted for a new decision-only Shadow experiment. It is not approved for Paper execution.

## Context

At the 2026-08-30 cutoff, V2.5 had 244 decision cohorts, 70,127 candidate snapshots, and 56
mature selected four-hour outcomes. Selected assets averaged -0.318% at four hours versus -0.110%
for nonselected assets, then -1.059% at 12 hours and -2.644% at 24 hours. The V2.4 same-input
control also remained negative at four hours (-0.161%), so neither the old rule nor V2.5's stricter
non-negative four-hour entry floor justified activation.

The V3 Mark--Index pipeline was healthy: 243/243 signals had a complete
`MARK_INDEX_PROXY` input and no proxy gap. Therefore the observed loss is not explained by the old
optional official-Basis failure.

A descriptive, hypothesis-generating analysis of 42 mature selected decision cohorts found that
the weakest four-hour outcomes clustered in higher-volatility candidates and BTC contexts with
lower funding, lower global long/short ratio, and a more negative Mark--Index Basis. These are
same-sample observations and may be regime-specific; they are not proof of predictive value.

## Decision

- Publish `dynamic-intraday-v2.6` as an immutable schema-3 strategy.
- Keep V2.3 as the only executing Paper lane. V2.6 runs with `execute=false` in a new portfolio and
  experiment.
- Restore the V2.4 lower four-hour entry floor (-3%), because V2.5's zero floor did not improve
  forward performance. Cap positive four-hour momentum at +0.5% to avoid late pump entries.
- Limit entry volatility to 0.3% and the cross-sectional median, and strengthen the nonlinear
  high-score penalty. These thresholds form one pre-registered challenger, not a fitted model.
- Use the existing point-in-time V3 context as a new-entry-only gate. An entry requires:
  funding >= 0.005%, Mark--Index Basis >= -0.05%, and global long/short ratio >= 1.0.
- Missing, stale, incomplete, or below-threshold derivatives context blocks new entries and
  replacements. It never forces an existing holding to be sold and never blocks an exit.
- Keep the maximum holding period at one hour for eventual Paper evaluation. The decision-only
  Shadow still records 4h/12h/24h forward outcomes independently of this setting.
- Persist a complete same-input `v2.5-rule-control` for every V2.6 cohort. The control restores the
  V2.5 spot and SHADOW-overlay rules and cannot place orders.

## Validation

V2.6 must remain Shadow until it spans at least three Seoul calendar days, 192 mature one-hour
cohorts, 96 mature four-hour cohorts, and 30 changed cohorts relative to its V2.5 rule control.
Control coverage must be complete, outcome coverage at least 90% per arm with no more than a
five-point gap, and V2.6-minus-control must be non-negative in both chronological halves. Normal
selected gross-return, selected-spread, score-IC, concentration, derivatives availability, exact
feature-version, and exact Basis-source gates continue to apply.

The initial V2.5-derived thresholds are deliberately frozen. They must not be tuned during the
V2.6 observation window. A passing result permits only an isolated Paper proposal; it does not
authorize live trading.

## Consequences

V2.6 finally lets derivatives data affect selection, but only through a conservative market-wide
entry gate with explicit point-in-time provenance. The same-input control separates the combined
V2.6 rule package from V2.5 prospectively. Because multiple spot and derivatives conditions change
together, a positive result supports the package, not any one component in isolation.
