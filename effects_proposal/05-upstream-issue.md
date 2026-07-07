# Filed upstream issue

**Filed 2026-07-07 as https://github.com/PyPSA/PyPSA/issues/1788** (text below).

---

**Title: Generalize cost + CO₂ accounting into first-class "Effects" (N tracked quantities, one objective — no new variables)**

PyPSA already tracks exactly two kinds of quantities, with two different
mechanisms:

- **cost** — always in the model, as the objective, assembled from
  `marginal_cost` / `capital_cost` / `stand_by_cost` / … in
  `define_objective`;
- **anything on a carrier column** (`co2_emissions`, or e.g. PyPSA-Eur's
  synthetic `gas_usage`) — in the model *only* when a `primary_energy`
  GlobalConstraint caps it, and otherwise invisible until post-solve
  reporting (which #1778 / #1784 are adding to `n.statistics` right now).

This issue proposes making that rule uniform and N-ary: a small
**`Effect`** component — a named tracked quantity like `cost`, `co2`,
`land_use`, `water`, `primary_energy` — where

1. components contribute to effects through carrier columns (exactly the
   `carrier_attribute` mechanism `primary_energy` uses today, efficiency
   division and storage-depletion terms included) and through
   per-component coefficients mirroring the two cost attributes
   (`marginal_effect` per unit of operation, `capital_effect` per MW —
   e.g. land use of one specific wind site);
2. **exactly one effect is the objective** (default: `cost` — this is
   *not* a multi-objective proposal);
3. bounds on effects are ordinary GlobalConstraints (one new type,
   `effect_limit`, with the existing `sense` / `constant` /
   `investment_period` fields and `mu` duals — so shadow carbon prices
   work unchanged);
4. an effect can carry a `price` that routes its value into the
   objective — carbon pricing as a declared input instead of the
   `marginal_cost += price × co2_emissions / efficiency` folding that
   PyPSA-Eur does in `add_emission_prices`;
5. **an effect enters the model only if it is the objective, bounded, or
   priced.** Everything else is recomputed post-solve in `n.statistics`
   from its stored coefficients — which is precisely how PyPSA treats CO₂
   today (constraint present → in the model; absent → reporting only),
   and precisely the generalization #1778 describes on the statistics
   side. Effects add **zero decision variables**: they are linear
   expressions of existing flows and capacities, so a continent-scale LP
   stays an LP of the same size unless you actually bound something.

Today's behavior is the default special case: `marginal_cost` /
`capital_cost` *are* the `cost` effect's coefficients; a `primary_energy`
constraint with `carrier_attribute="co2_emissions"` *is* an `effect_limit`
on a built-in `co2` effect. No deprecations, no migration — existing
networks run unchanged and produce identical results.

**What this unlocks with one mechanism** (all of these have live issues or
discussions):

- **per-asset accounting of non-cost quantities.** Every statistic already
  supports `groupby=False` for per-asset drill-down, and `opex` decomposes
  into its seven cost terms — but emissions and other tracked quantities
  have no per-component attribution at all today: the GlobalConstraint
  machinery produces one scalar expression and discards the split.
  `n.statistics.effect("co2", groupby=False)` answers "which asset caused
  what share", built on the same `_aggregate_components` machinery, so
  asset-level and time-resolved views come for free;
- report the CO₂-price share of system cost separately from fuel cost
  (#1377) — `n.statistics.effect("co2")` × price;
- embedded/capacity-based emissions in tCO₂/MW (#1077) — the
  `capital_effect` channel, bounded like any other effect;
- multi-quantity budgets (land use, water, materials, primary energy)
  without one bespoke GlobalConstraint type each — cf. #1566, where
  generalization was already the suggested resolution;
- "minimize CO₂ subject to a cost cap": `objective_effect="co2"` plus an
  `effect_limit` on `cost` — today only possible by hand-rolling the
  objective in `extra_functionality`.

**Where it would live in the codebase** — deliberately boring:

- an `effects` component table (registered like `Carrier`; round-trips
  through IO automatically);
- carrier-derived coefficients: no change at all — existing carrier
  columns, reinterpreted;
- per-component coefficients: plain static columns
  (`marginal_effect_{name}` / `capital_effect_{name}`), set via a
  validated dict kwarg in `n.add` or in bulk on the DataFrame — custom
  static columns already round-trip IO;
- one extracted function `build_effect_expression(n, effect, sns) →
  (capex_terms, opex_terms)` that `define_objective` and
  `define_primary_energy_limit` both delegate to — same term lists, so
  the stochastic/CVaR wrapper composes unchanged;
- an `accounting` attribute per effect encoding the weighting split that
  already exists implicitly: cost uses `snapshot_weightings.objective` ×
  `investment_period_weightings.objective`, physical quantities use
  `.generators`/`.stores` × `.years`.

**Reference implementation.** A complete working implementation exists and
is reviewable as one diff here:
https://github.com/FBumann/PyPSA/pull/2 — including a docs notebook
walking the whole surface
(https://github.com/FBumann/PyPSA/blob/master/docs/examples/effects.ipynb).
Guarantees it demonstrates with tests: the refactor is *expression-identical*
(objective and GlobalConstraint expressions term-for-term unchanged on six
networks incl. stochastic and multi-investment); `effect_limit` ≡
`primary_energy` including `mu` duals; `price` ≡ marginal-cost folding
(also when objective and energy snapshot weightings differ); declared but
unbounded effects provably leave solves untouched; full test suite green.
It slices into three independently reviewable PRs — statistics-only first
(which overlaps #1784 deliberately; happy to converge there), then the
pure refactor, then the user-facing surface — and we're glad to adapt any
of it to fit where #1474 (Properties) and #1778 are heading.

**Non-goals:** multi-objective/Pareto solving; touching power flow;
per-hour rate bounds and effect-on-startup channels (later, if wanted);
non-sum aggregations such as peak-based demand charges (a natural later
extension, but it needs an auxiliary variable, so it stays out of the
LP-invariant core proposal); replacing the sector-coupled `co2 atmosphere`
bus pattern (that's flow tracking with storage and network structure — it
stays).

Is there appetite for this in core, and does the direction fit where
#1474 and #1778 are heading? Happy to open the statistics-only PR as a
first concrete step.
