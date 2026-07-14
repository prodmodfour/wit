# Ticket Writing Guide

Good autonomous tickets are small, ordered, and testable.

## Good ticket shape

Each ticket should include:

* a clear title
* a status
* exact scope
* required files or behaviours
* tests or validation expected
* docs expected
* commit instruction

Use `##` headings for tickets. Supported heading styles include:

```text
## 003 — Add user repository
## Ticket 3 — Add user repository
## LP-S5-003 — Add user repository
```

The build loop uses file order: the first ticket heading whose status is `TODO` is next. Keep tickets in the exact execution order you intend. Numeric and prefixed IDs are labels, so mixed families such as `MA-004` followed by `REG-001` remain unambiguous.

Example:

```text
## 003 — Add user repository

Status: TODO

Implement the repository layer for users.

Required:
- create user
- get user by ID
- get user by email
- reject duplicate email at service level
- repository owns database access

Add tests for:
- create/get
- duplicate email
- not found

Update docs if architecture changes.

Run scripts/quality-gate.sh.

Commit when complete.
```

## Avoid vague tickets

Bad:

```text
Make the app better.
```

Good:

```text
Add pagination to the project list endpoint with tests and docs.
```

## One ticket means one change

Do not combine unrelated work.

Bad:

```text
Add auth, deploy to AWS, create docs, and redesign UI.
```

Good:

```text
Add password hashing utilities and tests.
```

## Final ticket

Every project should have a final review ticket that:

* runs the full quality gate
* checks docs
* checks secrets/private files
* checks generated files
* sets top-level `AUTOMATION_STATUS: DONE`
* commits final review
