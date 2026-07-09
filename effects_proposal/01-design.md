# Effect system for PyPSA — grounded technical design

**Scope:** capability A only (multi-quantity accounting). Piecewise (B) and
discrete investment (C) are explicitly out of scope.
**Grounding:** PyPSA `master` @ `b43e70dd` (v1.2.x dev, released v1.2.4),
branch `origin/feat/piecewiese` (PR #1603), fluxopt
`src/fluxopt/{elements,model_data,model}.py`. All file:line references below
were verified against these sources in July 2026.

---

## 0. What changed relative to the handoff brief

Three findings from the source correct or sharpen the brief:

1. **Master has no emissions statistic.** The brief claimed PyPSA "reports
   uncapped emissions post-solve via `n.statistics`". It does not:
   `co2_emissions` is consumed *only* inside
   `define_primary_energy_limit` (`pypsa/optimization/global_constraints.py:272-480`).
   However, issue **#1778** (FabianHofmann, 2026-07-02) and PR **#1784**
   are adding exactly that — a generalized
   `n.statistics.primary_energy(carrier_attribute=...)`. So the
   "materialize-only-when-bounded, report-in-results" rule is being built
   upstream *right now*, on the results side. Our proposal is the model side
   of the same rule. The pitch gets stronger, but must not claim the
   statistic already exists.
2. **The weighting asymmetry is real and must be first-class.** Cost terms
   use `snapshot_weightings.objective` × `investment_period_weightings.objective`
   (`optimize.py:244-250`, `:323-327`); physical accounting
   (primary_energy, operational_limit) uses `snapshot_weightings.generators`
   / `.stores` × `investment_period_weightings.years`
   (`global_constraints.py:287-298`). An Effect must declare which
   accounting scheme it uses; this is data, not code, in the new design.
3. **fluxopt does *not* implement the materialization closure** — it builds
   every effect always (`model.py:979-1030`). The closure idea is our
   refinement; its precedent is PyPSA's own cost-vs-CO₂ behavior, and it
   should be pitched as such.

---

## 1. Current mechanics (what we generalize)

### 1.1 Objective

Built in one function, `define_objective`
(`pypsa/optimization/optimize.py:139-431`), as two lists of linopy
expressions — `capex_terms` and `opex_terms` — kept separate because
stochastic scenario weighting and CVaR treat them differently
(`optimize.py:360-428`: objective = `E[capex] + (1-ω)·E[opex] + ω·CVaR`).

Terms are driven by a lookup table `pypsa/data/variables.csv` mapping
`(component, variable)` → cost-role flags:

| Legacy attribute | Variable multiplied | Weighting |
|---|---|---|
| `marginal_cost` | `Generator-p`, `Link-p`, `Process-p`, `Store-p`, `StorageUnit-p_dispatch` | snapshot `.objective` × period `.objective` |
| `marginal_cost_storage` | `Store-e`, `StorageUnit-state_of_charge` | same |
| `spill_cost` | `StorageUnit-spill` | same |
| `stand_by_cost` | `{c}-status` (committables) | same |
| `start_up_cost` / `shut_down_cost` | `{c}-start_up` / `-shut_down` | none (per event) |
| `capital_cost` → `periodized_cost` (`pypsa/costs.py:102-206`) | `{c}-{nom_attr}` | per-period activity × period `.objective` |
| `marginal_cost_quadratic` | variable² | snapshot × period `.objective` |

Coefficients are fetched uniformly via the xarray accessor
`c.da[attr]` → `_as_dynamic` (`pypsa/components/array.py:218-300`), which
merges static columns and time-varying `_t` overrides.

### 1.2 GlobalConstraints

Seven hardcoded handlers, dispatched in fixed order at `optimize.py:806-812`
(no registry). The ones that are *scalar accounting constraints* — i.e.
"accumulate a linear quantity over the network, cap it":

- **`primary_energy`** (`global_constraints.py:272-480`): accumulates
  `carriers[carrier_attribute]` (default `co2_emissions`, but **any carrier
  column** — PyPSA-Eur uses a synthetic `gas_usage` attribute). Semantics:
  generators contribute `p × carrier_coeff / efficiency × w_snapshot × w_years`;
  non-cyclic StorageUnits/Stores contribute the SOC/energy *depletion*
  (initial − final), with per-period branching on
  `state_of_charge_initial_per_period` / `e_initial_per_period` and explicit
  `NotImplementedError` guards for continuous depletion with year-weights ≠ 1
  (`:369-378`, `:421-430`). Supports `investment_period` = one period or NaN
  (all), scenarios, sense/constant.
- **`operational_limit`** (`:483-678`): same skeleton with coefficient ≡ 1
  for one carrier (net production limit).
- **`transmission_volume_expansion_limit`** (`:681-789`):
  Σ `length × nom_var` over extendable Lines/Links by carrier.
- **`transmission_expansion_cost_limit`** (`:792-867`):
  Σ `capital_cost × nom_var`, weighted by period `.objective`.

The remaining three (`tech_capacity_expansion_limit`, per-bus-carrier nominal
limits, `growth_limit`) are *per-bus or per-period-difference* constraints,
not scalar accounting — they stay out of scope (see §7 Q2).

Duals flow back into `GlobalConstraint.mu` via the `GlobalConstraint-{name}`
naming convention (`optimize.py:1095-1099`).

### 1.3 Existing generalization seeds

- `GlobalConstraint.carrier_attribute` already gives N named quantities on
  carriers — but only for the flow-hour channel, and only as a cap.
- The MGA module swaps objectives from a `{component: {attr: coeff}}` weights
  dict (`pypsa/optimization/mga.py:187-271`) — precedent for "objective is a
  configurable weighted quantity".
- `pypsa/optimization/expressions.py` (`StatisticExpressionsAccessor`) builds
  reusable linopy expressions (capex/opex/energy_balance) for MGA and
  reporting — a parallel quantity-accumulation machinery an Effect layer
  would unify with.
- PR #1603 introduces the sanctioned **extra-index-level container**: a third
  per-component data container alongside `static`/`dynamic` (MultiIndex
  `(name, attribute)` columns), plus a package-level mapping CSV
  (`pypsa/data/piecewise.csv`), wired through `n.add`/IO. IO support cost
  ~315 lines in `io.py` — real but bounded.

### 1.4 fluxopt semantics we import (and what we drop)

From the fluxopt spec (see `04-fluxopt-semantics.md` if extracted, or the
research notes): contributions split into a **temporal domain** (per
flow-hour ×Δt, per running-hour ×Δt, per startup — no Δt) and a **lump
domain** (per-size, fixed, one-time-at-build vs recurring-per-active-period).
Cross-effect chaining (`contribution_from`) applies **per-domain and
per-timestep**, with DFS cycle detection. Bounds exist at three grains:
weighted total, per-period, per-hour-rate.

We import: the temporal/lump split (it maps 1:1 onto PyPSA's opex/capex
split), total and per-period bounds, and chaining restricted to the dominant
use case (pricing into the objective) for the MVP. We drop for MVP: per-hour
bounds, per-startup/running-hour effect channels (cost already has them;
generalizing later is mechanical), at-build vs recurring distinction
(belongs to capability C), and fluxopt's always-materialize policy.

---

## 2. Proposed data model

### 2.1 `Effect` component

New component (registered like any other: row in `pypsa/data/components.csv`,
`pypsa/data/component_attrs/effects.csv`, trivial subclass in
`pypsa/components/_types/effects.py`, entries in `_CLASS_MAPPING`
(`pypsa/components/legacy.py:40-58`), `store.py:42-56`,
`network/components.py`; the new `Process` component is the worked template).

`effects.csv` attributes:

| attribute | type | default | meaning |
|---|---|---|---|
| `name` | string | — | e.g. `cost`, `co2`, `land_use` |
| `unit` | string | `""` | label only, never used in math |
| `accounting` | string | `physical` | `cost` → snapshot `.objective` / period `.objective` weighting; `physical` → snapshot `.generators`/`.stores` / period `.years`. Makes §1's weighting asymmetry data. |
| `carrier_attribute` | string | `""` | if set, per-flow-hour coefficients derive from `n.carriers[attr]` with **exact `primary_energy` semantics** (efficiency division, storage depletion terms) |
| `price` | static or series | 0 | routes this effect's value into the objective effect (carbon pricing). MVP form of cross-effect chaining. |
| `mu_*` outputs | float | 0 | shadow prices of any bounds (via GlobalConstraint `mu`, see 2.4) |

Built-in effects, auto-injected (never stored unless customized):
- **`cost`** — `accounting="cost"`; its coefficients *are* the legacy cost
  attributes (§1.1 table). Always the default objective.
- **`co2`** — `accounting="physical"`, `carrier_attribute="co2_emissions"`.
  Declared implicitly whenever any carrier has nonzero `co2_emissions`.

Validation: the objective effect must have `price == 0` (no self-pricing);
with the MVP price-into-objective coupling, cycles are impossible by
construction. If/when general `contribution_from` DAGs are added, port
fluxopt's DFS cycle check (`model_data.py:994-1024` in fluxopt).

### 2.2 Component-level effect coefficients

Two coefficient sources, mirroring what exists today:

**(a) Carrier-derived** (the CO₂ path): `n.carriers` grows arbitrary
effect-coefficient columns exactly as it does today (`co2_emissions`,
PyPSA-Eur's `gas_usage`). An Effect with `carrier_attribute` set picks these
up. No schema change at all — this is today's mechanism, reinterpreted.

**(b) Direct per-component coefficients**, for effects that don't follow
carrier membership (land use per MW, water per MWh of a specific plant).
MVP channels:

| channel | multiplies | analog |
|---|---|---|
| `operational` | the component's `marginal_cost` variable (per §1.1 lookup), per flow-hour | `marginal_cost` |
| `capacity` | the nominal variable, per active period (recurring) | `capital_cost` (un-annuitized; see §7 Q5) |

Storage: a third per-component container `c.effect_coefficients`, following
the PR #1603 pattern — DataFrame with index `effect`, MultiIndex columns
`(name, channel)`. Static-only in the MVP (time-varying coefficients are
covered by the carrier-derived path, whose `carrier_attribute` semantics we
keep static as today; a dynamic variant can follow the piecewise IO
precedent). Ingest via `n.add`:

```python
n.add("Generator", "gas CHP", ...,
      effects={"land_use": {"capacity": 0.4},      # ha/MW
               "water":    {"operational": 1.9}})  # m³/MWh
```

Why not wide `{channel}_{effect}` columns on `.static`? Effect names are
user-defined strings; auto-expanded columns would pollute the static schema,
break `_as_dynamic`'s single-level-column assumption
(`pypsa/components/array.py:255-287`), and diverge from the vocabulary
maintainers just reviewed in #1603. Why not a long-form global table? It
would be the only component keyed by *other components' rows* — no precedent,
poor `n.add` ergonomics.

### 2.3 Objective selection

`n.optimize(objective_effect="co2")`, default `"cost"`; persisted as a
network-level attribute (in `component_attrs/networks.csv`) so it
round-trips. Exactly one effect is the objective — no multi-objective. "Min
CO₂ subject to a cost cap" = `objective_effect="co2"` + an effect bound on
`cost` (2.4). Priced effects (`price != 0`) contribute
`price × effect_value` into the objective effect — this reproduces
PyPSA-Eur's `add_emission_prices` declaratively (see §7 Q4).

### 2.4 Bounds = GlobalConstraints

No new bound mechanism. One new GlobalConstraint type, **`effect_limit`**:
the `carrier_attribute` column names the *effect* (same reuse as PR #1784's
parameter), `sense`/`constant` as today, and the existing
`investment_period` field already gives total (NaN) vs per-period bounds
(`global_constraints.py:314-319`). Duals land in `mu` through the existing
`GlobalConstraint-{name}` plumbing — so "shadow carbon price" works
unchanged. Per-hour rate bounds (fluxopt) are deferred; they would be a
time-indexed constraint like `operational_limit`, added later if demanded.

Legacy bridge: a `primary_energy` constraint with
`carrier_attribute="co2_emissions"` **is** an `effect_limit` on the implicit
`co2` effect — same expression, same dual. The handler can remain untouched
initially and later delegate.

---

## 3. Materialization closure

**Rule:** an effect enters the linopy model iff it is
(i) the objective effect, (ii) named by any `effect_limit` (or legacy
`primary_energy`) GlobalConstraint, or (iii) feeds a materialized effect —
in the MVP that means `price != 0` (feeds the objective, which is always
materialized). All other effects are results-only.

With general chaining later, (iii) becomes reverse-reachability over
contribution edges from the needed set — the transitive closure the brief
specifies. Getting this wrong is a correctness bug (an unbounded effect
feeding the objective must be in the model), so the closure computation gets
its own unit tests independent of the solver.

**"Materialize" means building linopy *expressions*, not variables.** Effects
add zero decision variables; the LP stays an LP. (fluxopt, by contrast,
allocates `effect--temporal/lump/total` variables per effect — we
deliberately don't, matching how `define_objective` and
`define_primary_energy_limit` already work with bare expressions.)

**Hook point:** one new function in `pypsa/optimization/`,

```
build_effect_expression(n, effect, sns) -> (capex_terms, opex_terms)
```

returning the two lists that `define_objective` already works in
(`optimize.py:180-181`) so scenario weighting and CVaR compose unchanged.
Sources folded in per §2: legacy cost attributes (iff `effect == "cost"`),
carrier-derived terms (exact `primary_energy` codepath, factored out of
`global_constraints.py:272-480` into a shared helper), and direct
coefficients. Then:

- `define_objective` becomes: `capex, opex = build_effect_expression(n,
  objective_effect, sns)` plus `Σ price_e × expr_e` for priced effects
  (price applied per-snapshot on the opex part, scalar on the capex part —
  matching fluxopt's per-domain chaining, `model.py:1025-1164`).
- `define_effect_limit` (new eighth handler in the dispatch list at
  `optimize.py:806-812`): `merge(capex + opex)` of the named effect,
  `sense`, `constant`, named `GlobalConstraint-{name}`.

**Interaction with CVaR/stochastic:** unchanged for `objective_effect="cost"`
(the default). For a non-cost objective with `risk_preference` set, raise
`NotImplementedError` in the MVP — CVaR's opex/capex asymmetry is
cost-semantic (`optimize.py:377-425`) and generalizing it is out of scope.

## 4. Results-side recomputation

Every declared effect — in or out of the closure — is reported post-solve:

```python
n.statistics.effect("co2")            # per component/carrier, default groupby
n.statistics.effect("land_use", groupby="country")
```

Built on `_aggregate_components` (`pypsa/statistics/abstract.py:156`) exactly
like `opex` (`pypsa/statistics/expressions.py:1535-1662`), which already does
coefficient × flow × `snapshot_weightings` per component. The carrier-derived
path should share its implementation with PR **#1784**'s
`n.statistics.primary_energy` — ideally `primary_energy(carrier_attribute=X)`
becomes a thin alias of `effect(...)` once both exist. This is the
convergence argument to make in the Discussion: #1778/#1784 is building the
results side of the rule; Effects are the model side; one mechanism should
serve both.

Because coefficients are always stored (on carriers / in
`effect_coefficients`) regardless of materialization, recomputation needs no
solve-time state — it reads solved flows and capacities like every other
statistic.

Per-asset detail comes free: `_aggregate_components` metrics all accept
`groupby=False` (individual assets) and `groupby_time=False` (per-snapshot
series), so `n.statistics.effect("co2", groupby=False)` gives per-component
attribution — something the GlobalConstraint machinery structurally cannot
provide today (it builds one scalar expression and discards the split).
This matters for project-level models with many named converters and is
worth leading with in the Discussion. Known residual gap, out of scope
here: per-component constraint duals have no statistics-level accessor
(reachable via `n.model.constraints`).

## 5. Backward-compatibility equivalence

Claims to prove, each an automated test:

1. **No effects declared** → `n.effects` empty, objective_effect="cost" →
   the code path is *literally today's* `define_objective` inputs (legacy
   attributes through the same lookup); objective value, dispatch, and duals
   identical to master on the bundled examples (ac-dc-meshed,
   storage-hvdc, scigrid-de). Target: identical linopy expressions, not just
   equal objective values — term order preserved by keeping the legacy
   iteration order inside `build_effect_expression(n, "cost", sns)`.
2. **Uncapped, unpriced `co2` effect declared** → solve identical to (1)
   (effect stays out of the model), and `n.statistics.effect("co2")` matches
   a hand computation and PR #1784's `primary_energy` statistic.
3. **CO₂ cap via legacy `primary_energy` GC** vs **`effect_limit` on `co2`**
   → identical objective, dispatch, and `mu`, including a multi-invest case
   with non-cyclic storage (the SOC-depletion terms and the
   `.years`-weighting are where equivalence is most fragile — test them
   explicitly).
4. **Carbon price**: `co2.price = 60` vs the PyPSA-Eur folding
   (`marginal_cost += carrier.map(ep)/efficiency`) → identical objective and
   dispatch on a generators-only network. (Storage-unit dispatch efficiency
   folding differs structurally — document, don't chase bit-equality there.)

---

## 6. Implementation plan (PR slicing)

0. **GitHub Discussion first** (see `02-discussion-draft.md`). No code lands
   without a maintainer champion; FabianHofmann (author of #1778, #1474,
   the #1566 "will be generalized" comment) is the natural first reader,
   with coroa/fneum/brynpickering on the optimization side.
1. **PR 1 — statistics only, no model changes:** `n.statistics.effect` +
   `Effect` component table (registration, IO round-trip — a plain new
   component round-trips automatically, `pypsa/network/io.py:1156-1455`).
   Aligns/merges with #1784. Zero risk to solves; establishes the schema.
2. **PR 2 — refactor without behavior change:** extract
   `build_effect_expression`; `define_objective` and
   `define_primary_energy_limit` delegate to it. Equivalence test 1 & 3
   green. This is the invasive step, kept mechanically verifiable.
3. **PR 3 — the new surface:** `effect_limit` GlobalConstraint type,
   `objective_effect=` parameter, `price` coupling, closure logic,
   `effect_coefficients` container + `n.add` ingest. Tests 2–4.
4. **Later, separate:** per-hour bounds, status/startup channels, general
   `contribution_from` DAGs, time-varying direct coefficients, capability
   B/C interop (piecewise coefficients per segment once #1603 merges), and
   non-sum aggregation channels — the concrete motivating case is
   capacity-based grid fees / demand charges (€/kW on the peak draw), a
   max-over-time effect channel that needs one auxiliary variable and
   therefore sits outside the "no new variables" MVP invariant.

## 7. Answers to the brief's open questions

**Q1 Schema** — §2: Effect component table + carrier columns (existing) +
`effect_coefficients` third container (PR #1603 pattern). Coexists with the
segment index because they are *separate containers*; a segment-indexed
effect coefficient (per-segment emissions) is a later composition, not an
MVP concern.

**Q2 GlobalConstraint bridge** — enumerated §1.2. `primary_energy` and
`operational_limit` (coefficient ≡ 1) are exactly effect bounds;
`transmission_volume/cost_expansion_limit` are effect bounds over
length/capital-cost capacity channels restricted to Lines/Links — expressible
but low-value to migrate; `tech_capacity_expansion_limit`, per-bus-carrier
nominals, and `growth_limit` are per-bus/per-period-difference constraints,
**not** scalar accounting, and stay untouched. Nothing is deprecated; all
seven handlers keep working; at most `primary_energy` internally delegates.

**Q3 Objective plumbing** — single function `define_objective`
(`optimize.py:139-431`), called once at `optimize.py:814`. Invasiveness is
moderate and containable (PR 2 above); the capex/opex list structure and
CVaR/stochastic wrapper are preserved by making the builder return the same
two lists. Non-cost objectives + CVaR: `NotImplementedError`.

**Q4 Cross-effect chains in the wild** — yes, two, both in PyPSA-Eur:
(a) `prepare_network.py::add_emission_prices` folds
`co2_price × co2_emissions / efficiency` into `marginal_cost` — exactly
`co2.price` (we subsume it declaratively, cite it); (b) sector-coupled
networks model CO₂ as a physical `co2 atmosphere` Bus+Store with
`marginal_cost = -co2_price` — that remains valid and untouched (it's flow
tracking, not accounting; Effects don't replace it and shouldn't claim to).
Also cite Discussion #1377 (user asking to see CO₂ cost separately from fuel
cost — `n.statistics.effect` answers it) and #1077 (embedded tCO₂/MW — the
`capacity` channel answers it).

**Q5 Periods & weighting** — the `accounting` attribute (§2.1) encodes the
`.objective`-vs-`.years` and `objective`-vs-`generators/stores` split
verified in §1. One open sub-question to put to maintainers: for the
`capacity` channel of physical effects, per-active-period recurrence uses
plain activity (no annuitization, no `.objective` discounting) — i.e.
`periodized_cost` stays cost-only. Multi-invest storage-depletion guards
(`global_constraints.py:369-378`) carry over verbatim via the shared helper.

**Q6 Migration** — none. `marginal_cost`/`capital_cost` are permanently the
`cost` effect's coefficient columns; the CO₂ GlobalConstraint is permanently
sugar. No deprecations proposed anywhere in this effort.

**Q7 Maintainer appetite** — verified 2026-07-06: no competing or conflicting
issue/PR/discussion exists. Tailwinds: #1778/#1784 (results-side
generalization, active now), #1474 Properties (Effect is a Property in their
own taxonomy), #1566 comment ("will be generalized by #322 and #1474"),
#1377 and #1077 (user demand), #322 (custom constraints as data — effect
bounds are a step in exactly that direction), MGA (objective-swap precedent),
and the v1.1→v2.0 `include_objective_constant` change (objective refactors
are landable). Headwind: #1603 review bandwidth (fneum/coroa/brynpickering
mid-push); sequence the Discussion to *reference* #1603's vocabulary, and
don't ask for review of code before it merges.

## 8. Risks

| Risk | Mitigation |
|---|---|
| PR 2 refactor perturbs objective term order → tiny float diffs → snapshot-test noise downstream (PyPSA-Eur CI) | preserve legacy iteration order for the cost effect; assert expression-level identity, not just value identity |
| Storage-depletion emission semantics silently diverge in the shared helper | equivalence test 3 runs the multi-invest + non-cyclic-storage case from the existing test suite for `primary_energy` |
| `effect_coefficients` IO under-scoped | MVP is static-only; the piecewise branch's +315-line `io.py` precedent bounds the cost; PR 1 lands the Effect table (auto round-trips) before the container is needed |
| Scenario/stochastic interactions (`n.has_scenarios`) | carrier-derived path inherits the scenario handling already in `global_constraints.py:303-473`; direct-coefficient container declares scenarios unsupported initially, like piecewise (`NotImplementedError`, matching #1603's own guard) |
| Scope creep toward fluxopt parity | MVP fence is explicit: no per-hour bounds, no status channels, no DAG chaining, no at-build/recurring split |
