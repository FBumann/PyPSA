# Upstream PR 1 — ready to file (not yet filed)

**Branch:** `FBumann/PyPSA:effect-statistics` (Stage 1 cherry-picked onto
upstream master `b43e70dd`; 8 tests green).
**File with:**

```bash
gh pr create --repo PyPSA/PyPSA \
  --head FBumann:effect-statistics \
  --title "feat: Effect component and per-component effect statistic" \
  --body-file effects_proposal/04-upstream-pr1.md-body
```

**Hold until:** (1) the Discussion (`02-discussion-draft.md`) is posted and
has a first maintainer response, and (2) coordination with PR #1784
(`n.statistics.primary_energy`) — this PR overlaps it deliberately and
should either build on it or absorb it, decided in that thread, not by
racing it.

---

## Draft PR body (copy below the line into the PR)

Adds a results-side-only building block for generalized quantity
accounting, as discussed in <link to Discussion once posted> and
complementing #1778 / #1784:

- **`Effect` component**: a named tracked quantity (`co2`,
  `primary_energy`, `land_use`, …) with a unit label, an `accounting`
  scheme, and a `carrier_attribute` pointing at the `n.carriers` column
  that defines its per-flow-hour coefficients — the same mechanism the
  `primary_energy` global constraint already reads. Round-trips through
  netCDF/CSV IO automatically.
- **`n.statistics.effect(name)`**: per-component contributions with
  `primary_energy` semantics (generator output ÷ efficiency × carrier
  coefficient, non-cyclic storage/store depletion), weighted per the
  effect's accounting scheme (`physical`: `generators`/`stores` snapshot
  weightings × `years` period weighting; `cost`: `objective` weightings).
  Supports the full statistics API surface: `groupby=False` for per-asset
  attribution, `groupby_time=False` for time series, carrier/bus_carrier
  filters.

**No optimization-side changes.** Declared effects have zero impact on
model building or solves — this PR only makes quantities that today exist
solely inside `define_primary_energy_limit` reportable, including when no
constraint caps them (closes the gap that uncapped emissions are invisible;
cf. #520, #1778, discussion #1377 which asks for exactly this
decomposition).

Relation to #1784: overlapping intent with
`n.statistics.primary_energy(carrier_attribute=...)`; the `effect` statistic
adds the declared-quantity registry (unit, accounting scheme) on top. Happy
to rebase onto #1784 and express `primary_energy` as sugar over `effect`,
or fold this into that PR — whichever the maintainers prefer.

Follow-ups (separate, opt-in, only if wanted): an `effect_limit`
GlobalConstraint type generalizing `primary_energy`, and an
`objective_effect` switch — a working reference implementation exists on
the fork (branch `master`, staged commits with expression-identity tests
proving the current objective and `primary_energy` constraint are exactly
reproduced).
