# Free File Fillable Forms (FFFF) — TY2025 Program Limits Reference

**Version:** 0.1
**Last verified:** 2026-04-11
**Owner:** claudetax-2025 skill, `skill/scripts/validate/ffff_limits.py`

FFFF is the IRS's free federal e-file channel for self-directed filers. It is the
primary free federal e-file path this skill targets, because the "Schedule C +
Schedule E + multi-state" profile almost always exceeds the Free File Guided
$89,000 AGI cap. FFFF opened January 26, 2026 for TY2025 and has **no income limit**.

Every numeric limit below is cited to an IRS URL. The compatibility checker in
`skill/scripts/validate/ffff_limits.py` pulls its constants from this document.
If any limit changes, update it here first, then mirror into the Python module.

---

## 1. Access and account requirements

- **No income limit.** FFFF is available regardless of AGI. Source:
  <https://www.irs.gov/newsroom/2026-tax-filing-season-opens-with-several-free-filing-options-available>
- **A US cell phone number capable of receiving SMS is required** for account
  sign-up / sign-in (ID.me or IRS account verification via phone). Source:
  <https://www.irs.gov/e-file-providers/free-file-fillable-forms>
- **First-time filers under age 16 cannot e-file FFFF.** Exact IRS language:
  "Primary taxpayers under the age of 16 who have never filed a tax return with
  the IRS cannot e-file using Free File Fillable Forms or any other do it
  yourself software product." Taxpayers under 16 who have previously filed with
  the IRS are allowed. Source:
  <https://www.irs.gov/filing/free-file-fillable-forms/ind-674-01>

## 2. Instance limits (hard caps)

| Item                                                | Limit      | Source URL |
|-----------------------------------------------------|------------|------------|
| Form W-2 copies                                     | 50         | <https://www.irs.gov/e-file-providers/free-file-fillable-forms-program-limitations-and-available-forms> |
| Schedule E rental properties (page 2 + 10 extra)    | 11         | <https://www.irs.gov/e-file-providers/free-file-fillable-forms-program-limitations-and-available-forms> |
| Form 8829 (Home Office) copies                      | 8          | <https://www.irs.gov/e-file-providers/free-file-fillable-forms-program-limitations-and-available-forms> |
| Form 8283 (Noncash Charitable) copies               | 4          | <https://www.irs.gov/e-file-providers/free-file-fillable-forms-program-limitations-and-available-forms> |
| Form 8082 copies                                    | 4          | <https://www.irs.gov/e-file-providers/free-file-fillable-forms-program-limitations-and-available-forms> |
| Form 8938 (Foreign Assets) — 1 form + N continuation pages | 1 + 25 | <https://www.irs.gov/e-file-providers/free-file-fillable-forms-program-limitations-and-available-forms> |
| Form 4562 per parent schedule                       | 1          | <https://www.irs.gov/e-file-providers/free-file-fillable-forms-program-limitations-and-available-forms> |
| Schedule B line 7b country selections               | 5          | <https://www.irs.gov/e-file-providers/free-file-fillable-forms-program-limitations-and-available-forms> |

Notes on Schedule E: the program "allows one copy of page 2 of Schedule E and
10 additional pages" — so the effective cap is **11 rental real estate
properties** per return. Anything beyond forces either paper filing or
commercial software.

Notes on Form 4562: only one Form 4562 can be associated with each Schedule C /
Schedule E / Schedule F / Form 4835. To associate a second 4562, the user must
"create another Schedule C (...) to associate with the additional Form 4562" —
this is a workaround, not a hard limit, but it can effectively constrain
depreciation-heavy returns.

## 3. Forms NOT supported by FFFF for TY2025

These force either paper filing, Form 8453 transmittal, or commercial software.

| Form / Schedule          | Status                                     | Source URL |
|--------------------------|--------------------------------------------|------------|
| Form 1040-SR             | Not supported — use Form 1040 instead      | <https://www.irs.gov/e-file-providers/free-file-fillable-forms-program-limitations-and-available-forms> |
| Schedule K-1 (1065/1120-S/1041) | Not supported by FFFF                | <https://www.irs.gov/e-file-providers/free-file-fillable-forms-program-limitations-and-available-forms> |
| Form 8915-C              | Not supported                              | <https://www.irs.gov/e-file-providers/free-file-fillable-forms-program-limitations-and-available-forms> |
| Form 8915-D              | Not supported                              | <https://www.irs.gov/e-file-providers/free-file-fillable-forms-program-limitations-and-available-forms> |
| Form 1098-C (vehicle donations over $500) | Not supported — requires Form 8453 paper transmittal | <https://www.irs.gov/e-file-providers/free-file-fillable-forms-program-limitations-and-available-forms> |
| Form 1040-X (prior-year amended beyond current + 2) | Paper only | <https://www.irs.gov/instructions/i1040x> |
| Form 4547 (OBBBA Trump Account election) | Unverified for FFFF TY2025; safest assumption is NOT supported — see TODO in module docstring | <https://www.irs.gov/instructions/i1040x> |

These are the forms with explicit FFFF documentation saying they are unsupported
or that force a non-FFFF path. Statement-election paper-only rules
(§1.263(a), §754, §6013(g)/(h)) also force paper since FFFF has no document
attachment surface.

## 4. Attachment restrictions

FFFF explicitly does not accept document attachments. Exact IRS language:
"This program does not allow you to attach any documents to your return, except
those available through the program."
Source:
<https://www.irs.gov/e-file-providers/free-file-fillable-forms-program-limitations-and-available-forms>

Consequences:
- No PDF of broker statements, signed elections, or supporting schedules.
- Any election that requires a signed statement (§1.263(a) capitalization,
  §754 basis adjustment, §6013(g)/(h) nonresident-spouse) cannot be filed via
  FFFF — it forces paper or commercial software that supports MeF general
  dependency attachments.
- Form 8453 exists as the paper-transmittal mechanism for required attachments
  when the return is otherwise e-filed, but FFFF users cannot invoke it; the
  whole return must go paper if an 8453 attachment is required.

## 5. State return capability

FFFF is **federal-only**. The program does not transmit any state return.
State returns must go through the state DOR portal, state commercial e-file,
or paper. Source:
<https://www.irs.gov/e-file-providers/free-file-fillable-forms-program-limitations-and-available-forms>
(The page makes no mention of state filing; the landscape document
`skill/reference/ty2025-landscape.md` also records this as a known constraint.)

This is not a "blocker" for using FFFF for federal — it's informational: the
skill must render state returns through the appropriate state plugin output,
not through the FFFF bundle.

## 6. Calculation support

FFFF performs **limited arithmetic only**. The product is designed for
taxpayers "comfortable preparing their own taxes using IRS forms and
instructions"; some calculated lines work (e.g., Form 8889 line 13), while
others require the user to compute and type values themselves. There is no
intelligent validation or cross-form propagation beyond basic worksheet math.
Source:
<https://www.irs.gov/e-file-providers/free-file-fillable-forms>

Consequence: the skill must treat FFFF as a **rendering / entry target**, not
a calc engine. Our calc engine (tenforty + patches) produces the numbers; FFFF
is the paste-in destination for line-level values the user transcribes.

## 7. What happens if a return exceeds a limit

Ordered fallback strategy the skill should recommend:

1. **Hard blocker (e.g., >50 W-2s, >11 Schedule E properties, Schedule K-1
   present, Form 1098-C attachment required):** recommend commercial software
   (TurboTax, FreeTaxUSA, TaxAct, etc.) or paper filing. Do not suggest FFFF.
2. **Soft blocker (e.g., Form 4562 multi-association needed):** recommend the
   FFFF workaround (split into multiple Schedule Cs) and note it in the
   generated checklist.
3. **Attachment required:** the entire return goes paper, regardless of how
   simple the main 1040 is. Mark the bundle as "paper only" and generate IRS
   fillable PDFs instead of FFFF entry tables.
4. **State returns present:** informational only — the state return goes
   through its own per-state channel; the federal portion can still use FFFF.

## 8. Changelog

- **2026-04-11** — Initial version. All limits verified against IRS FFFF
  program limitations page snapshot (retrieved via WebFetch this date).
- **Next review trigger:** IRS updates to the FFFF program-limitations page
  after February 2026 (typically 1-2 revisions during filing season as new
  forms are enabled / rules change). Verification should re-run each January
  when the new filing season opens.

## 9. Open questions / TODOs

- **Form 4547 (Trump Account, OBBBA):** not yet called out in any FFFF
  program-limitations page revision verified. Safest assumption for a
  compatibility checker is "NOT supported" until IRS confirms. Revisit when
  the final TY2025 FFFF forms list is published.
- **Form 8606 multi-instance:** not explicitly limited; assume multi-copy
  support for each spouse on MFJ until contradicted.
- **Schedule D + Form 8949 aggregate size:** not explicitly capped, but
  transmit-time schema can reject returns with tens of thousands of 8949
  lines. Worth monitoring.
