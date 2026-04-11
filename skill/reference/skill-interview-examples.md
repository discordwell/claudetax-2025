# SKILL.md Interview — Worked Examples

This document shows how the interview flow in `skill/SKILL.md` reads in practice across several typical TY2025 taxpayer profiles. Each example walks Claude (you, the interviewer) through a representative conversation, the running `taxpayer_info.json` you build up, and the pipeline handoff.

These examples are primarily a **regression check on the prompt design** — if the SKILL.md interview cannot smoothly handle these profiles, the prompt is incomplete. They are also a soft target for prompt-engineering review: tighten any phase that reads awkwardly here.

All taxpayer names, SSNs, and addresses are fictional.

---

## Example 1 — Single W-2 wage earner, standard deduction (the happy path)

**Profile.** Alex Doe, age 35, single, one W-2 from Acme Corp, no investments, no self-employment, no dependents, lives in Springfield IL all year, takes the standard deduction.

This is the same shape as `skill/fixtures/simple_w2_standard/input.json`.

### Transcript sketch

> **Claude:** Hi — I'm the Claude tax prep skill. I'll walk you through your federal and state income tax return for TY2025 and produce a bundle of filing artifacts. I do not transmit anything to the IRS — you'll submit through Free File Fillable Forms, your state's DOR portal, commercial software, or paper. Ready to start? Are we filing for tax year 2025?
>
> **Alex:** Yes, 2025.
>
> **Claude:** Great. What's your filing status — single, married filing jointly, married filing separately, head of household, or qualifying surviving spouse?
>
> **Alex:** Single.
>
> **Claude:** Got it. Let me get your basic identity. Legal first and last name?
>
> **Alex:** Alex Doe.
>
> **Claude:** SSN?
>
> **Alex:** 111-22-3333.
>
> **Claude:** Date of birth?
>
> **Alex:** 1990-01-15.
>
> *(Claude notes silently that Alex is 35 — well under 65, so the OBBBA senior deduction does NOT apply.)*
>
> **Claude:** Occupation? Are you legally blind?
>
> **Alex:** Software engineer. No.
>
> **Claude:** Address — street, city, state, ZIP?
>
> **Alex:** 1 Test Lane, Springfield IL 62701.
>
> *(Claude notes IL is not Maryland, so no `county` is needed.)*
>
> **Claude:** Any dependents on the return?
>
> **Alex:** No.
>
> **Claude:** Now let's walk through your TY2025 income. Did you receive any of these — W-2 wages, 1099 interest, 1099 dividends, broker investment sales, self-employment / 1099-NEC, rental real estate, retirement distributions, Social Security, unemployment / state refund, or Schedule K-1?
>
> **Alex:** Just one W-2 from Acme Corp.
>
> **Claude:** I can ingest the W-2 PDF automatically — drop it in `~/TaxData/Doe/ty2025/input_pdfs/` and I'll extract the boxes. Or I can ask you the numbers directly. Which would you prefer?
>
> **Alex:** I'll just give you the numbers.
>
> **Claude:** Employer name and EIN?
>
> **Alex:** Acme Corp, EIN 12-3456789.
>
> **Claude:** Box 1 wages and Box 2 federal withholding?
>
> **Alex:** $65,000 wages, $7,500 federal withholding.
>
> **Claude:** Boxes 3 and 4 — Social Security wages and SS tax — usually equal Box 1 for non-tipped employees. Box 4 is 6.2% of Box 3.
>
> **Alex:** Box 3 is also $65,000. Box 4 is $4,030.
>
> **Claude:** Boxes 5 and 6 — Medicare wages and Medicare tax?
>
> **Alex:** $65,000 and $942.50.
>
> **Claude:** Any tips, dependent care benefits, Box 12 codes, retirement plan box 13, anything in Box 14? Multi-state W-2?
>
> **Alex:** No to all of those.
>
> **Claude:** Now itemized vs standard deduction. The TY2025 single standard deduction is $15,750 (post-OBBBA). You'd only itemize if you have large state and local taxes, mortgage interest, big charitable gifts, or substantial medical expenses. Want to walk through the itemized list to compare?
>
> **Alex:** No, just take the standard deduction.
>
> **Claude:** State residency — full-year resident of IL?
>
> **Alex:** Yes, all 365 days.
>
> *(Claude notes IL is in the wave-3 wired state plugins. Hand-rolled flat 4.95%.)*
>
> **Claude:** OBBBA items — you're 35, so no senior deduction. Software engineer is not a tipped occupation; do you have employer-attested OBBBA overtime (FLSA half-time premium) on your W-2?
>
> **Alex:** No.
>
> **Claude:** Did you make a Form 4547 Trump Account election for TY2025?
>
> **Alex:** No.
>
> **Claude:** Foreign accounts over $10K USD aggregate, or any foreign trust transactions?
>
> **Alex:** No.
>
> **Claude:** FFFF compatibility check — single W-2, no K-1, no rentals, no Schedule C, no Form 4547, no foreign attachments. You're well within FFFF limits. Federal can go through Free File Fillable Forms; IL state goes through MyTax Illinois separately.
>
> **Claude:** Here's the assembled `taxpayer_info.json`:
>
> ```json
> {
>   "schema_version": "0.1.0",
>   "tax_year": 2025,
>   "filing_status": "single",
>   "taxpayer": {
>     "first_name": "Alex",
>     "last_name": "Doe",
>     "ssn": "111-22-3333",
>     "date_of_birth": "1990-01-15",
>     "is_blind": false,
>     "occupation": "Software engineer"
>   },
>   "address": {
>     "street1": "1 Test Lane",
>     "city": "Springfield",
>     "state": "IL",
>     "zip": "62701",
>     "country": "US"
>   },
>   "w2s": [
>     {
>       "employer_name": "Acme Corp",
>       "employer_ein": "12-3456789",
>       "employee_is_taxpayer": true,
>       "box1_wages": "65000.00",
>       "box2_federal_income_tax_withheld": "7500.00",
>       "box3_social_security_wages": "65000.00",
>       "box4_social_security_tax_withheld": "4030.00",
>       "box5_medicare_wages": "65000.00",
>       "box6_medicare_tax_withheld": "942.50"
>     }
>   ],
>   "itemize_deductions": false,
>   "state_returns": [
>     {"state": "IL", "residency": "resident", "days_in_state": 365}
>   ]
> }
> ```
>
> Look right? If yes I'll write it to `~/TaxData/Doe/ty2025/taxpayer_info.json` and call `run_pipeline`.

After `run_pipeline` returns, Claude reads `result.canonical_return.computed` and reports:

- Total income $65,000
- AGI $65,000 (no above-the-line adjustments)
- Standard deduction $15,750
- Taxable income $49,250
- Federal tax (computed via tenforty), payments $7,500
- Refund or amount owed
- IL state tax (computed via the IL plugin)
- Rendered PDFs: `form_1040.pdf`
- FFFF: clean — no blockers

---

## Example 2 — Self-employed Schedule C with home office, rental property, MFJ couple, MD county special-case

**Profile.** Jamie and Sam Rivera, married filing jointly, both age 42. Jamie has a freelance design business (Schedule C with home office), Sam has a W-2 from a hospital. They own one rental property in addition to their primary residence. They live in Howard County, Maryland. They have one qualifying child dependent.

This is the kind of return that exercises CP8-D (`Address.county`) AND multiple income sources AND a Schedule C plus a Schedule E in the same return.

### Transcript sketch

> **Claude:** Hi — I'm the Claude tax prep skill. I'll walk you through your federal and Maryland income tax returns for TY2025. I do not transmit returns to the IRS — you'll submit through Free File Fillable Forms or commercial software, plus the Maryland DOR portal for the state return. Ready? Tax year 2025?
>
> **Jamie:** Yes.
>
> **Claude:** Filing status?
>
> **Jamie:** Married filing jointly.
>
> **Claude:** I'll need both spouses. Primary taxpayer first — full name, SSN, DOB, occupation, blind y/n?
>
> **Jamie:** Jamie Rivera, 222-33-4444, 1983-06-12, freelance graphic designer, no.
>
> **Claude:** And the spouse?
>
> **Jamie:** Sam Rivera, 333-44-5555, 1983-09-30, registered nurse, no.
>
> *(Both 42 — no senior deduction.)*
>
> **Claude:** Dependents?
>
> **Jamie:** One — our daughter Riley, 444-55-6666, born 2018-03-04. She lived with us all 12 months. Qualifying child, not a student, not disabled, no one else is claiming her.
>
> **Claude:** Address — street, city, state, ZIP, and **because you're in Maryland, I need the county** for the local income tax piggyback.
>
> **Jamie:** 14 Rivers Edge Lane, Columbia MD 21044, Howard County.
>
> *(Claude normalizes to `"howard"` for `address.county`. Howard's TY2025 local rate is 3.20% — significantly higher than the 2.25% nonresident default, so getting the county right matters. This is exactly the CP8-D case.)*
>
> **Claude:** Now let's walk through TY2025 income. Yes/no for each — W-2 wages, 1099 interest, 1099 dividends, broker sales, self-employment / 1099-NEC, rental real estate, retirement distributions, Social Security, unemployment, K-1?
>
> **Jamie:** W-2 yes (Sam's hospital job), 1099-INT yes (a small bank account), self-employment yes (my design business), rental yes (one property). Nothing else.
>
> **Claude:** Sam's W-2 first. Employer, EIN, box 1, box 2?
>
> *(...standard W-2 walkthrough; Sam is the employee_is_taxpayer=false flag because Jamie is primary...)*
>
> **Claude:** Bank 1099-INT — payer name and box 1 interest?
>
> **Jamie:** Capital One, $112 interest.
>
> *(Below the $1,500 threshold for forced Schedule B, but the engine handles that.)*
>
> **Claude:** Now your Schedule C. Whose business — yours or Sam's? Business name, principal business or profession, NAICS code if you know it, EIN if you have one?
>
> **Jamie:** Mine. "Rivera Design Studio." Graphic and web design. NAICS 541430. No EIN, just my SSN.
>
> **Claude:** Material participation y/n; cash or accrual; started this year y/n; did you make any payments requiring a 1099?
>
> **Jamie:** Material participation yes, cash, established years ago, no 1099-required payments.
>
> **Claude:** Gross receipts (line 1)?
>
> **Jamie:** $84,200.
>
> **Claude:** Returns and allowances, COGS, other income?
>
> **Jamie:** All zero.
>
> **Claude:** Now Schedule C Part II expenses — I'll walk every line. Line 8 advertising?
>
> **Jamie:** $1,200.
>
> *(Continue line by line through the table in SKILL.md Phase 3f. Most lines are zero; populated lines: line 9 car/truck $2,800 (standard mileage), line 17 legal/professional $400, line 18 office expense $620, line 22 supplies $1,150, line 25 utilities $0 (covered under home office), line 27a other $300 for software subscriptions.)*
>
> **Claude:** Home office (line 30)?
>
> **Jamie:** I use 200 sqft as a dedicated office. Use the simplified method.
>
> **Claude:** Simplified method: $5/sqft × 200 = $1,000. Setting `line30_home_office_expense` to $1,000.
>
> **Claude:** Now Schedule E for the rental. Property address?
>
> **Jamie:** 88 Tenant Street, Baltimore MD 21218. Single-family. Rented all 365 days, no personal use.
>
> **Claude:** Rents received and per-line expenses?
>
> *(...full Schedule E walk: rents $24,000, mortgage interest $9,200, property tax $3,400, insurance $1,800, repairs $1,150, depreciation $5,800 (engine handles MACRS), management fees $1,920...)*
>
> **Claude:** Just one rental — well under the FFFF cap of 11 properties.
>
> **Claude:** Itemized vs standard. MFJ standard deduction is $31,500 in TY2025. With Howard County local tax, MD state income tax, real estate tax on your primary home, mortgage interest on your primary home, plus charitable contributions, you're likely going to itemize. Want to walk through Schedule A?
>
> **Jamie:** Yes.
>
> **Claude:** **Medical expenses — I have to flag something here.** Only the portion of unreimbursed medical and dental expenses that EXCEEDS 7.5% of your adjusted gross income is deductible. So if your AGI ends up around $135K, the first ~$10,125 of medical expenses doesn't count. The calc engine applies this floor automatically — this is the CP8-A medical-floor fix in `skill/scripts/calc/engine.py`. Without it, tenforty would over-deduct your medical by the floor amount and that's a real-money correctness bug. Enter your **gross** unreimbursed medical total; the engine subtracts the 7.5%-of-AGI floor itself.
>
> **Jamie:** OK. We had $11,400 in unreimbursed medical.
>
> **Claude:** Setting `medical_and_dental_total` to $11,400. The engine will apply the floor at compute time.
>
> *(...continue through Schedule A: state and local income tax (will hit SALT cap with the local), real estate tax, mortgage interest, charitable cash gifts $3,200, no non-cash...)*
>
> **Claude:** State residency — full-year MD residents?
>
> **Jamie:** Yes.
>
> *(MD plugin is wired in wave 4. Will use `address.county = "howard"` for the 3.20% local rate.)*
>
> **Claude:** OBBBA items. Both 42, so no senior deduction. Neither of you is in a tipped occupation. Sam — does the hospital report any OBBBA-qualifying overtime in W-2 box 14 with the structured tag?
>
> **Jamie:** No, we never saw anything like that.
>
> **Claude:** Then no OBBBA overtime deduction. Form 4547 Trump Account election?
>
> **Jamie:** No.
>
> **Claude:** Foreign accounts or trusts?
>
> **Jamie:** No.
>
> **Claude:** FFFF compatibility — one W-2, one Schedule C, one Schedule E, no K-1, no Form 4547. You're within FFFF limits. Federal can use FFFF; the MD return goes through iFile / Maryland DOR.
>
> **Claude:** *(prints assembled JSON, confirms write path, runs `run_pipeline`, then summarizes from `result.canonical_return.computed` — total income, AGI, deduction taken with note that Schedule A beat standard, QBI deduction (Schedule C with positive net qualifies), taxable income, federal tax, MD state tax, refund/owed, rendered PDFs `form_1040.pdf` + `schedule_a.pdf` + `schedule_c.pdf` + `schedule_se.pdf`)*

---

## Example 3 — Retired couple on Social Security and IRA distributions, age 67/65

**Profile.** Pat and Lee Chen, MFJ, ages 67 and 65 (so the OBBBA senior deduction applies for BOTH spouses), retired. Income is Social Security plus 1099-R IRA distributions plus a small amount of bank interest. They itemize because of property tax and charitable giving. No state tax (lives in Florida). No dependents.

This profile exercises (a) the OBBBA senior deduction auto-detect logic for two filers, (b) the no-income-tax state plugin path, (c) Social Security taxability, and (d) itemized with the medical-floor warning.

### Transcript highlights

- Filing status: `mfj`. Both spouses' DOB triggers `is_age_65_or_older` derivation in Phase 6a — Claude says: "I noticed both of you are 65 or older — TY2025 has a new $6,000 OBBBA senior deduction per filer that I'll apply automatically." (The engine populates `adjustments.senior_deduction_obbba` via the `obbba_senior` patch.)
- Address: Sarasota FL 34236. No `county` needed (FL is not MD).
- Income: 1099-INT $480 (small money market). Two 1099-Rs (one IRA each), Pat $42,000 gross, Lee $28,000 gross, both fully taxable, distribution code 7 (normal). Two SSA-1099s, Pat box 5 $34,200, Lee box 5 $26,400.
- Itemized: medical $14,800 (Claude issues the CP8-A medical floor warning explicitly — for an AGI in the ~$80K-$90K range, the first ~$6,000-$6,750 of medical doesn't count), real estate tax $5,200 (their home is paid off so no mortgage interest), charitable cash $9,400.
- State: FL is in the no-income-tax batch. Claude tells them: "Florida has no state income tax, so no MD-style local return, but I'll still create a `state_returns` row so the no-income-tax plugin produces the artifact noting no return is required."
- OBBBA: senior deduction auto-applied for both. Neither in tipped occupation. No overtime. No Form 4547.
- FFFF: clean — well within limits, no blockers. Federal goes through FFFF.
- Pipeline output: rendered `form_1040.pdf` + `schedule_a.pdf`. Schedule B is required because total interest is below $1,500, BUT they also need to disclose foreign accounts (they don't have any, so no Schedule B needed unless interest+dividends > $1,500 — engine decides).
- Final summary stresses: "About 85% of your Social Security is taxable at this income level," and "the OBBBA senior deduction added $12,000 to your standard-deduction equivalent — but since you're itemizing, the senior deduction stacks above the itemized total via Schedule 1."

---

## Example 4 — Single filer with W-2 plus tipped restaurant work, OBBBA Schedule 1-A (variant)

**Profile.** Casey Brooks, single, age 28, two W-2s — one from a restaurant (server, with employer-attested OBBBA tips in Box 14), one from a part-time bookstore job (no overtime). No dependents. Lives in Texas (no state income tax). Standard deduction.

Highlights:

- Phase 3a — first W-2 (restaurant): Claude asks specifically about Box 14 OBBBA-attested tips. Casey confirms: "Yeah, the manager said they updated the W-2 to include the OBBBA Box 14 amount — it says $4,800." Claude sets `box14_qualified_tips_obbba` to 4800. Bookstore W-2 has no tips and no overtime.
- Phase 6b — Schedule 1-A: Claude says: "I see your restaurant W-2 has $4,800 in employer-attested qualified tips. The OBBBA Schedule 1-A patch in `skill/scripts/calc/obbba_schedule_1a.py` will pick that up automatically and populate the qualified tips deduction. You don't need to enter it manually."
- TX is in the no-income-tax batch — same path as the FL example.
- FFFF clean.
- Pipeline output: `form_1040.pdf` only (standard deduction, no Schedule A; no SE earnings, no Schedule SE; interest below threshold so no Schedule B).
- Result summary calls out: "Your OBBBA Schedule 1-A qualified tips deduction reduced your AGI by $4,800."

---

## Example 5 — Schedule K-1 present (FFFF blocker fast-fail)

**Profile.** Morgan Patel, single, age 45. Has a W-2 from a tech company plus a Schedule K-1 from a tech-startup partnership.

The interesting part of this example is the **fast-fail on the FFFF blocker** as soon as the K-1 surfaces in Phase 3k.

> **Morgan:** I also have a K-1 from a partnership.
>
> **Claude:** Stop — I need to flag this immediately. **Schedule K-1 is a hard blocker for Free File Fillable Forms.** FFFF does not support K-1s at all. Your options are: (a) commercial software like FreeTaxUSA, TurboTax, TaxAct, or Cash App Taxes, all of which support K-1s and most of which have a free tier for federal; or (b) paper filing the federal return. I'll still build out the canonical return and produce the federal PDFs — they're valid for paper filing or for transcribing into commercial software — but FFFF is off the table. Want to continue?

Then Claude completes the K-1 walk (source name, source EIN, source type partnership, ordinary business income, §199A items, etc.) and finishes the rest of the interview as normal. The `notes[]` list ends up with one entry: `"FFFF blocker: Schedule K-1 present (source: TechCo Partners). Recommend commercial software or paper file."`

The `validation_report["ffff"]` block from `validate.ffff_limits` will mirror this hard violation when `compute()` runs.

---

## What these examples demonstrate

| Capability                                       | Examples |
|--------------------------------------------------|----------|
| Single W-2, standard deduction, happy path       | 1        |
| MFJ with multiple income sources                 | 2        |
| Schedule C with home office                      | 2        |
| Schedule E rental                                | 2        |
| Itemized deductions with CP8-A medical warning   | 2, 3     |
| MD `address.county` (CP8-D) collection           | 2        |
| OBBBA senior deduction auto-detect               | 3        |
| OBBBA Schedule 1-A tips deduction                | 4        |
| No-income-tax state path                         | 3, 4     |
| Schedule K-1 FFFF fast-fail                      | 5        |
| Foreign account / FBAR prompt                    | (every interview asks; no positives in these examples) |
| Form 4547 Trump Account election                 | (every interview asks; no positives in these examples) |

If a future taxpayer profile is not covered by one of these patterns, add a sixth example here rather than mutating the existing ones.
