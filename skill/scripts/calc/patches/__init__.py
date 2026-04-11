"""Calc patch layer — our own code that supplements tenforty.

Each module here implements a calc hot spot that tenforty's high-level API
doesn't cover. Patches are applied in a known order by the engine after tenforty
returns. See skill/reference/cp4-tenforty-verification.md for the gaps this
layer fills (CTC, OBBBA senior deduction, Form 4547, Schedule 1-A, QBI 8995-A,
multi-state apportionment).

Each patch module exports a well-known function signature — see individual
modules for the specific contract.
"""
