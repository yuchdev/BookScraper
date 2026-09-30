# License compatibility policy

Deterministic ruling for the license step of `/dep-audit`. The tiers below
default to a **proprietary / commercially-licensed** distribution model, where
strong copyleft is a real distribution risk, not a theoretical one. **Adjust
this section to your own project's licensing model** — e.g. an open-source
project under a compatible license, or an internal-only tool with no
distribution, may reasonably downgrade some

⛔ Block entries to
⚠️ Flag or
✅ Allow 

(AGPL's network-copyleft trigger is the one exception worth keeping
strict even for SaaS-only, no-distribution products). Classify every direct
dependency's license into one of the three tiers below and report accordingly.

## Tiers

### ✅ Allow (permissive — no action)

MIT, MIT-0 (OSI-approved MIT variant without the attribution clause — strictly
more permissive than MIT; ruled Allow on that basis), BSD-2-Clause, BSD-3-Clause,
Apache-2.0, ISC, PSF/Python-2.0, MPL-2.0
(file-level copyleft — fine for a dependency we don't modify), Unlicense, CC0,
Zlib. These impose only attribution/notice obligations (MIT-0 imposes none).
Ensure NOTICE/attribution is preserved where required but take no further action.

### ⚠️ Flag (review before shipping)

- **LGPL-2.1 / LGPL-3.0** — usable only if dynamically linked and the user can
  relink; for a Python dep this is usually OK but must be confirmed not vendored
  or statically bundled. Flag for `security-auditor` / legal note.
- **MPL-2.0 when we modify the source** — file-level copyleft triggers.
- **Weak/unusual permissive with extra clauses** (e.g. BSD-4-Clause advertising
  clause, EPL, CDDL) — compatible but note the obligation.
- **Dual-licensed** where one option is copyleft — record which option we elect.

### ⛔ Block (incompatible with proprietary distribution)

- **GPL-2.0 / GPL-3.0** (strong copyleft).
- **AGPL-3.0** (network copyleft — the most dangerous; triggers even for SaaS use).
- **SSPL**, **BUSL/Commons-Clause**, and other source-available/non-OSI licenses.
- **No license / UNKNOWN / "proprietary"** on a third-party package — treat as
  block until identified; an unlicensed dep grants us no rights.

## Ruling procedure

1. Enumerate **direct** deps from `pyproject.toml` (transitive deps inherit the
   obligation but the direct dep is where we act).
2. Identify each license: prefer `pip-licenses`; fall back to the package's
   metadata / PyPI classifier; last resort `WebSearch` the package + "license".
3. Assign a tier. Any ⛔ is a **blocking** finding; any ⚠️ is a warning that needs
   a human/security-auditor decision; ✅ is silent.
4. For a ⛔ or ⚠️, propose the remedy: a permissively-licensed replacement, or an
   isolation boundary (separate process / optional extra) if the dep is essential.

## Output rows

```
### Licence risks
| Package | Licence | Tier | Action |
| some-agpl-lib | AGPL-3.0 | ⛔ Block | replace with <permissive alt> before release |
| some-lgpl-lib | LGPL-3.0 | ⚠️ Flag | confirm dynamic link only; security-auditor sign-off |
```

Report `UNKNOWN` licenses explicitly — a silent omission reads as "all clear"
when it is actually "unclassified".
