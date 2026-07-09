# Go / no-go read

**Call: GO — with sequencing conditions.** After reading the real code, no
fundamental blocker exists, and the upstream tailwinds are unusually strong.
Confidence: high on technical feasibility, medium-high on core acceptance.

## Why go

1. **The generalization claim survives contact with the source.** The
   objective is one table-driven function (`define_objective`,
   `optimize.py:139-431`) whose terms are exactly the `cost` effect's
   channels; `primary_energy` already accumulates an *arbitrary* carrier
   column (`carrier_attribute`) — PyPSA even ships the generic default in
   `global_constraints.csv`. The Effect system genuinely is "make the
   existing rule uniform", not a parallel subsystem.
2. **Maintainers are independently converging on the same rule.**
   #1778/#1784 (July 2026, active) generalize post-solve accounting over
   `carrier_attribute`; #1474 "Properties" is a maintainer-owned track for
   exactly this kind of non-physical component; FabianHofmann resolved #1566
   with "will be generalized by #322 and #1474". We verified there is **no
   competing proposal** for effects/multi-quantity/objective selection.
3. **User demand exists in their tracker** (#1377 CO₂-vs-fuel cost split,
   #1077 embedded emissions per MW, #1475 penalties/subsidies examples).
4. **No new variables ⇒ no performance argument against it.** The closure
   rule means a model with no bounds and default objective is *identical*
   to today's, provably.
5. **Objective refactors are landable in this project** — precedent:
   `include_objective_constant` (v1.1→v2.0 behavior change, PR #1509) and
   the MGA objective-swap machinery.

## Where it could still fail, and what that would look like

- **The refactor PR (Stage 2) is the choke point.** `define_objective` also
  carries stochastic scenarios and CVaR; `define_primary_energy_limit`
  carries multi-invest storage-depletion edge cases with explicit
  `NotImplementedError` guards. If maintainers judge the extraction too
  risky for its benefit, the effort stalls *there* — not on the concept.
  Mitigation: expression-identity tests, and shipping value first via the
  statistics-only PR.
- **Bandwidth, not appetite.** The optimization reviewers
  (coroa, fneum, brynpickering, FabianHofmann) are mid-#1603. Posting the
  Discussion during that push risks a polite silence that reads as
  rejection. Condition: sequence after #1603's merge or next review round.
- **Schema bikeshed.** The `effect_coefficients` container (extra index
  level) is the most contestable choice. It's deliberately the #1603
  pattern, and it's not needed until Stage 3 — carrier-derived effects
  cover CO₂/primary-energy/gas-usage with zero schema change. If the
  container is rejected, the MVP still stands on carrier columns alone.

## Conditions attached to GO

1. Discussion before code; a named maintainer champion before Stage 2.
2. Stage 1 (statistics + Effect table) is offered as a convergence with
   #1784, not a competitor — coordinate in that PR's thread first.
3. MVP fence stays fixed (no per-hour bounds, no status channels, no DAG
   chaining, no CVaR-on-non-cost) — every one of these is where "effects"
   drifts into "port fluxopt", which is the framing that gets rejected.
4. Capabilities B (piecewise) and C (discrete investment) stay out of every
   conversation in this push.
