> **Superseded:** filed as upstream issue PyPSA/PyPSA#1788 (2026-07-07) instead of a Discussion — see 05-upstream-issue.md for the final text.

# Draft GitHub Discussion post (Ideas)

> Ready to post at github.com/PyPSA/PyPSA/discussions (category: Ideas),
> after a final read. ~800 words. Mentions are deliberate: FabianHofmann
> (#1778, #1474), coroa/fneum/brynpickering (#1603/#1473 context).

---

**Title: Generalize cost + CO₂ accounting into first-class "Effects" (N tracked quantities, one objective — no MILP, no new variables)**

PyPSA already tracks exactly two kinds of quantities, with two different
mechanisms:

- **cost** — always in the model, as the objective, assembled from
  `marginal_cost` / `capital_cost` / `stand_by_cost` / … in
  `define_objective`;
- **anything on a carrier column** (`co2_emissions`, or e.g. PyPSA-Eur's
  synthetic `gas_usage`) — in the model *only* when a `primary_energy`
  GlobalConstraint caps it, and otherwise invisible until post-solve
  reporting (which #1778 / #1784 are adding to `n.statistics` right now).

This discussion proposes making that rule uniform and N-ary: a small
**`Effect`** component — a named tracked quantity like `cost`, `co2`,
`land_use`, `water`, `primary_energy` — where

1. components contribute to effects through the channels that already exist
   for cost (per flow-hour, per MW of capacity) and through carrier columns
   (exactly the `carrier_attribute` mechanism `primary_energy` uses today,
   efficiency division and storage-depletion terms included);
2. **exactly one effect is the objective** (default: `cost` — this is *not*
   a multi-objective proposal);
3. bounds on effects are ordinary GlobalConstraints (one new type,
   `effect_limit`, with the existing `sense` / `constant` /
   `investment_period` fields and `mu` duals — so shadow carbon prices work
   unchanged);
4. an effect can carry a `price` that routes its value into the objective —
   which is carbon pricing as a declared input instead of the
   `marginal_cost += price × co2_emissions / efficiency` folding that
   PyPSA-Eur does in `add_emission_prices`;
5. **an effect enters the model only if it is the objective, bounded, or
   priced.** Everything else is recomputed post-solve in `n.statistics` from
   its stored coefficients — which is precisely how PyPSA treats CO₂ today
   (constraint present → in the model; absent → statistics), and precisely
   the generalization #1778 describes on the statistics side. Effects add
   **zero decision variables**: they are linear expressions of existing
   flows and capacities, so a continent-scale LP stays an LP of the same
   size unless you actually bound something.

Today's behavior is the default special case: `marginal_cost` /
`capital_cost` *are* the `cost` effect's coefficients; a `primary_energy`
constraint with `carrier_attribute="co2_emissions"` *is* an `effect_limit`
on a built-in `co2` effect. No deprecations, no migration — existing
networks run unchanged and produce identical results (we'd prove that with
expression-level equivalence tests, not just matching objective values).

**What this unlocks with one mechanism** (all of these have live issues or
discussions):

- **per-asset accounting of non-cost quantities.** Every statistic already
  supports `groupby=False` for per-asset drill-down, and `opex` decomposes
  into its seven cost terms — but emissions and other tracked quantities
  have no per-component attribution at all today: the GlobalConstraint
  machinery produces one scalar expression and discards the split.
  `n.statistics.effect("co2", groupby=False)` answers "which converter
  caused what share", built on the same `_aggregate_components` machinery,
  so asset-level and time-resolved views come for free;
- report the CO₂-price share of system cost separately from fuel cost
  (#1377) — `n.statistics.effect("co2")` × price;
- embedded/capacity-based emissions in tCO₂/MW (#1077) — the capacity
  channel, bounded like any other effect;
- multi-quantity budgets (land use, water, materials, primary energy)
  without one bespoke GlobalConstraint type each — cf. #1566, where
  generalization was already the suggested resolution;
- "minimize CO₂ subject to a cost cap": `objective_effect="co2"` plus an
  `effect_limit` on `cost` — today only possible by hand-rolling the
  objective in `extra_functionality`;
- one shared implementation for #1784-style statistics and the model-side
  expressions, instead of the current situation where
  `define_primary_energy_limit` and reporting code each reimplement
  coefficient × flow × weighting.

**Where it would live in the codebase** — deliberately boring:

- an `effects` component table (registered like `Carrier`; round-trips
  through IO automatically);
- carrier-derived coefficients: no change at all — existing carrier columns,
  reinterpreted;
- direct per-component coefficients: a container following the
  segment-table pattern from #1603/#1473, so the two efforts share one data-
  model vocabulary;
- one extracted function `build_effect_expression(n, effect, sns) →
  (capex_terms, opex_terms)` that `define_objective` and
  `define_primary_energy_limit` both delegate to — same lists, so the
  stochastic/CVaR wrapper composes unchanged;
- an `accounting` attribute per effect encoding the weighting split that
  already exists implicitly: cost uses `snapshot_weightings.objective` ×
  `investment_period_weightings.objective`, physical quantities use
  `.generators`/`.stores` × `.years`.

**Non-goals:** multi-objective/Pareto solving; touching power flow; per-hour
rate bounds and effect-on-startup channels (later, if wanted); non-sum
aggregations such as peak-based demand charges (a max-over-time channel is a
natural later extension of the same expression builder, but it needs an
auxiliary variable, so it stays out of the LP-invariant MVP); replacing the
sector-coupled `co2 atmosphere` bus pattern (that's flow tracking with
storage and network structure — it stays).

We're happy to do the work: first a statistics-only PR
(`n.statistics.effect`, converging with #1784), then a pure refactor
extracting `build_effect_expression` with equivalence tests proving
bit-identical objectives, then the new surface behind it. Before any of
that — does this direction fit where #1474 (Properties) and #1778 are
heading, and is there appetite for it in core? Design notes with file-level
detail are ready if useful.

---

> Posting notes (not part of the post):
> - Post *after* #1603 merges or at least after its next review round —
>   don't compete for coroa/fneum/brynpickering attention mid-push.
> - If FabianHofmann engages, offer the PR-1 statistics convergence with
>   #1784 as the concrete first step — it's small, unthreatening, and
>   establishes the Effect table.
> - FBumann is already a participant in #1473 (linopy PR links,
>   2026-02-02) — post from that account for continuity.
