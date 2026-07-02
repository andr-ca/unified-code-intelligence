# Eval Suite Versioning — Apples-to-Apples Comparability

**Date:** 2026-07-02
**Problem this solves:** the eval suite evolves — golden questions get added, mined facts get
regenerated, scoring parameters get tuned. A score is only meaningful relative to the exact
questions and formulas that produced it; comparing a 94.7 from one golden set against a 91.2 from
another is noise wearing a trend costume. (This bit us in week one: five golden revisions in two
days made the early reports mutually incomparable.)

---

## 1. The three version axes

| Axis | Identifier | Where | Bump when |
| --- | --- | --- | --- |
| **Dataset** | `"version": N` (integer) + content **fingerprint** | each `evals/datasets/<name>.json` | any golden change: entries added/removed/edited, category params (`k`, `loose`, expectations), notes that affect derivation, or a `mined/` refresh the dataset derives from |
| **Scoring spec** | `SCORING_VERSION` (in `run_eval.py`, mirrors `scoring.md`) | `scoring.md` header + runner constant | any formula, weight, matching rule, or vacuity rule changes |
| **Suite** | `suite_fingerprint` (derived) | computed per run | derived — changes whenever any of the above changes |

**Fingerprints are the enforcement; versions are the communication.** The fingerprint is a
SHA-256 (12 hex chars) over the canonicalized dataset JSON **plus the mined reference file it
derives categories from** — so an accidental edit or a `mine_ground_truth.py` refresh is detected
even if someone forgets to bump the version. The version number is for humans and the changelog.

## 2. Rules

1. **Every dataset carries `"version"`.** The runner warns on missing versions.
2. **Any golden change ⇒ bump the dataset version and add a `CHANGELOG.md` entry** (what changed,
   why, expected score impact). Regenerating `mined/` files counts — review the diff, then bump.
3. **Any scoring change ⇒ bump `SCORING_VERSION`** in both `run_eval.py` and the `scoring.md`
   header, changelog entry, and **re-baseline** (old baselines are dead).
4. **Additive evolution is the default.** Prefer adding questions (bump) over editing existing
   ones; edit only when a golden is *wrong* (record the evidence in the changelog, as with the
   COSGN00C completeness correction).
5. **Baselines are per-suite-identity.** `evals/reports/baseline.json` carries the suite block;
   after a version bump, run once, review the report by hand, then copy it over the baseline in
   the same PR that bumped the version.

## 3. How comparison behaves (`--baseline`)

The gate is **comparability-aware** (`compare_baseline` in the runner):

- **Scoring version differs** → nothing is comparable. The gate is skipped entirely with a
  `NOT COMPARABLE` notice; re-baseline required.
- **Per dataset:** gated only when `(version, fingerprint)` match the baseline. A drifted dataset
  prints `drift: <name> v2/abc… -> v3/def… — score X -> Y (informational, not gated)` and is
  excluded from gating until re-baselined.
- **Track-level gate** (`supported`, −1.0 point threshold) applies only on full runs where
  *every* baseline dataset in the track is still comparable; otherwise the track delta is printed
  with a `(partially comparable)` marker.
- **Pre-versioning baselines** (no `suite` block) fall back to score-only gating with a notice.

So: adding questions to `carddemo` never silently moves the gate — the run shows the drift, a
human reviews the new score, and the re-baseline commit adopts it as the new reference point.

## 4. Comparing across versions deliberately

When you *want* a cross-version trend (e.g. "did the DDL extractor improve carddemo even though
the golden grew?"), compare **per-category raw scores on the intersection of questions**, not the
aggregates — or better, run both suite versions from git history against the same code:

```bash
git stash && git checkout <old-tag> -- evals/datasets/  # old questions, new code
PYTHONPATH=src python3 evals/run_eval.py                # apples-to-apples vs old baseline
git checkout HEAD -- evals/datasets/ && git stash pop
```

Datasets are plain JSON in git — the version history *is* the archive.

## 5. Report format additions

Every report (and therefore every baseline) now includes:

```jsonc
{
  "suite": {
    "scoring_version": "1.0",
    "suite_fingerprint": "d41d8cd98f00",
    "datasets": {
      "carddemo": {"version": 2, "fingerprint": "a1b2c3d4e5f6"},
      "shop":     {"version": 2, "fingerprint": "0f9e8d7c6b5a"}
    }
  },
  "tracks": { "…": { "datasets": { "carddemo": { "version": 2, "fingerprint": "a1b2…", … } } } }
}
```

## 6. Version history

See `evals/datasets/CHANGELOG.md`. Summary of the line so far:

- **v1** — initial goldens (Python fixtures migrated; mainframe datasets created from first mining pass).
- **v2** — parser-sprint revisions: `procs` neutral lists in jobs, `maps_to`/`dclgens` mined facts,
  `.asm` members counted as programs, COSGN00C completeness correction (evidence: literal XCTL),
  BANKDATA evidence path fix, data_access table-kind scoping.
- **scoring 1.0** — the `scoring.md` contract as of 2026-07-02 (includes vacuity rules, maps_to §2.10).
