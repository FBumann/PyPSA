# fluxopt → PyPSA core: Effect system proposal (capability A)

**Status (2026-07-06): all four stages implemented as sequential commits on
this fork's master** — so the full system can be judged working before
deciding what to file upstream (e.g. only PR 1, or a rewrite):

1. `Stage 0` — design docs + PoC shim (`poc_effects_shim.py`), equivalence
   checks E1–E4 all passing.
2. `Stage 1` — `Effect` component + `n.statistics.effect` (per-asset,
   time-resolved, IO round-trip). No optimization-side changes.
3. `Stage 2` — pure refactor: `build_cost_terms` +
   `_carrier_contribution_terms` extracted, composed in
   `pypsa/optimization/effects.py::build_effect_expression`. Objective and
   GlobalConstraint expressions proven term-for-term identical on six
   networks (incl. stochastic, multi-invest, non-cyclic storage).
4. `Stage 3` — `effect_limit` GlobalConstraint type, `objective_effect=`
   on `optimize()`, `Effect.price` coupling (≡ PyPSA-Eur emission-price
   folding), `materialized_effects` closure. 18 tests in
   `test/test_effects.py`; full optimization/statistics/IO suites green.

Not implemented (deliberate MVP fence): stochastic-scenario effects,
time-varying prices, per-hour bounds, status/startup channels, direct
per-component `effect_coefficients` container, general contribution DAGs.


Follow-up design pass on the handoff brief "Bringing fluxopt's modeling
paradigm into PyPSA core". Produced 2026-07-06 against PyPSA master
@ b43e70dd (v1.2.4 released), branch `origin/feat/piecewiese`, fluxopt
checkout at `../fluxopt`, and live GitHub state.

- **[00-go-no-go.md](00-go-no-go.md)** — the call: GO, with sequencing
  conditions.
- **[01-design.md](01-design.md)** — grounded technical design: current
  mechanics with file:line references, proposed schema (Effect component,
  coefficient channels, bounds as GlobalConstraints, price coupling),
  materialization-closure algorithm and hook points, results-side
  recomputation, backward-compat proof obligations, PR slicing, answers to
  all seven open questions, risks.
- **[02-discussion-draft.md](02-discussion-draft.md)** — ready-to-post
  GitHub Discussion (Ideas), plus posting notes on timing and audience.
- **[03-poc-plan.md](03-poc-plan.md)** — Stage-0 `extra_functionality` shim
  with four equivalence checks (E1–E4), then the three-PR path.

Key facts that moved from the brief's assumptions to verified ground truth:

| Brief assumed | Verified reality |
|---|---|
| `n.statistics` already reports uncapped emissions | No emissions statistic exists in master; #1778/#1784 are adding a generalized one *now* — convergence opportunity, not prior art |
| #1473/#1603 "extra index level" pattern | Confirmed on `origin/feat/piecewiese`: third per-component container, MultiIndex `(name, attribute)` columns; PR near merge, +315 io.py lines for IO |
| CO₂→cost chains "may exist" in PyPSA-Eur | Two: `add_emission_prices` marginal-cost folding (subsumed by `price`), and the `co2 atmosphere` Bus+Store pattern (explicit non-goal) |
| Uniform weighting | Cost uses `.objective`/`.objective`; physical accounting uses `.generators`/`.stores` × `.years` — encoded as the per-effect `accounting` attribute |
| fluxopt as closure reference | fluxopt materializes *all* effects always; the closure is our contribution, precedented by PyPSA's own cost-vs-CO₂ split |
