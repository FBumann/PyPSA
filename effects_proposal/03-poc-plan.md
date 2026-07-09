# Proof-of-concept plan — smallest demonstration of equivalence

**Purpose:** the strongest artifact for the Discussion is a script showing
that built-in `cost` + `co2` effects reproduce an existing PyPSA example's
results exactly, including that an *unbounded* co2 effect stays out of the
model. This is a demonstration, not the implementation — it uses
`extra_functionality` and post-solve checks only, zero changes to PyPSA.

## Stage 0 — pure-shim demo (1 file, no PyPSA changes)

`poc_effects_shim.py`, runnable against installed PyPSA ≥ 1.2:

1. **Fixture:** `pypsa.examples.ac_dc_meshed()` (has gas/wind carriers) with
   `co2_emissions` set on gas; a second fixture with a StorageUnit,
   `cyclic_state_of_charge=False`, to exercise the depletion terms; a third
   with two investment periods to exercise `.years` weighting.

2. **Effect expression builder (shim):** a ~80-line function
   `effect_expression(n, coeff_series, sns)` that replicates
   `define_primary_energy_limit`'s accumulation
   (`pypsa/optimization/global_constraints.py:322-457`): generators
   `p × carrier_coeff / efficiency × w`, non-cyclic storage/store depletion,
   snapshot `.generators`/`.stores` weighting, period `.years` weighting.
   This function *is* the PoC — it demonstrates that the model side of an
   effect is one reusable expression builder.

3. **Equivalence checks:**
   - **E1 (unbounded ⇒ absent):** solve the network with no CO₂
     GlobalConstraint; assert the model contains no constraint mentioning
     emissions; compute `co2_total` post-solve from the same coefficients ×
     solved flows; assert it equals the value obtained by evaluating the
     shim expression at the solution. Baseline objective recorded.
   - **E2 (bound ⇒ identical to primary_energy):** solve once with the
     stock `GlobalConstraint(type="primary_energy", …)`; solve again with
     the GC removed and `extra_functionality` adding
     `m.add_constraints(effect_expression(...), "<=", cap,
     name="GlobalConstraint-co2_shim")`. Assert: objective equal (rtol
     1e-9), dispatch equal, dual of the shim constraint equals the stock
     `mu`. Run on all three fixtures — the multi-invest + non-cyclic-storage
     fixture is the one that catches semantic drift.
   - **E3 (price ⇒ subsumes marginal-cost folding):** generators-only
     fixture; solve A with PyPSA-Eur-style folding
     (`marginal_cost += price × co2/efficiency`); solve B with clean
     marginal costs and `extra_functionality` reassigning
     `m.objective = m.objective + price * effect_expression(...)`
     (opex part). Assert objective and dispatch equal.
   - **E4 (objective swap):** `m.objective = effect_expression(...)` (min
     CO₂) + cost-cap constraint built from the original objective expression
     — the "min CO₂ s.t. cost ≤ 1.05 × optimum" pattern, showing the
     mechanism composes. Sanity-check monotonicity, no golden values.

4. **Output:** a table printed per fixture: objective A/B, max |Δdispatch|,
   dual A/B. Paste into the Discussion.

## Stage 1 — statistics PR (first real code)

`n.statistics.effect(name)` implemented on `_aggregate_components`
(`pypsa/statistics/abstract.py:156`), sharing the coefficient logic with
PR #1784's `primary_energy` statistic (coordinate there rather than
duplicate). Plus the `Effect` component registration (CSV + subclass +
`_CLASS_MAPPING` + `store.py` + `network/components.py`, template:
`Process`). Test: statistic equals shim E1's post-solve value on all
fixtures; IO round-trip test for the effects table (netCDF + CSV dir).

## Stage 2 — refactor PR (no behavior change)

Extract `build_effect_expression`; `define_objective` and
`define_primary_energy_limit` delegate. Gate: the existing full test suite
plus new expression-identity tests (compare `repr`/coefficients of
`m.objective` before/after on the bundled examples — value equality is not
enough to keep PyPSA-Eur snapshot CI quiet).

## Stage 3 — surface PR

`effect_limit` GC type, `objective_effect=` on `optimize()`, `price`
coupling, closure logic (unit-tested standalone), `effect_coefficients`
container + `n.add(effects=...)`. E1–E4 become permanent tests.

## Effort guess

Stage 0: a day. Stage 1: small PR (~300 lines + tests), mostly coordination
with #1784. Stage 2: the delicate one (~-300/+400 lines in
`optimize.py`/`global_constraints.py`), dominated by equivalence-test rigor.
Stage 3: medium (~800 lines), mechanical once 2 lands.
