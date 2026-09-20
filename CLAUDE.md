# CLAUDE.md — Shopping Project Instructions

## Project Overview

Django REST API application (orders/payments/points).
Built on Django 5.2 + DRF + PostgreSQL 15 + Redis 7 + Celery + Prometheus + OTEL.

- **Application code**: `shopping/` (orders, payments, points)
- **Project config**: `myproject/` (settings, celery, wsgi, middleware)
- **Payment-recovery library (optional)**: the `selfhealing` package that grew out of this repo was extracted and later renamed to `baldur`; the integration code here targets the pre-rename API. `settings.SELFHEALING_AVAILABLE` gates every hook (app, exception handler, URLs, beat schedule, middleware). The app must boot and pass `shopping/tests/` without the package — see README "결제 복구 계층에 대해"

## Code Rules

- **No guessing**: If uncertain, read existing code first
- **Consistency**: Follow existing naming conventions, import styles, and error handling patterns
- **Code over docs**: When docs/ and code conflict, code is the source of truth
- **Code citation**: Reference relevant file paths and code snippets as `filepath:line_number`

## Restrictions

- Never answer with generalities without reading actual code
- Do not suggest refactoring unless the user explicitly requests it
- Do not write line numbers in code comments
- Do not use document-reference terms (`phase`, `reference`, etc.) in class/function/file names
- The self-healing library is optional: never add an unconditional import of `selfhealing` to production code or to `shopping/tests/`; hooks go behind `settings.SELFHEALING_AVAILABLE`, tests that need the package use `pytest.importorskip("selfhealing")`

## Test Location Rules

- `shopping/tests/` — shopping app tests; must pass without the optional library (this is what CI runs)
- `tests/hybrid/` — Celery worker crash / idempotency tests; `tests/integration/` — Kafka/OTEL, real infra required
- `tests/conftest.py` — shared test fixtures

## Custom Skills

| Skill | Description |
|-------|-------------|
| `/advisor` | Project Q&A, idea evaluation, and counter-proposals |
| `/execute` | Read implementation plan document and implement code |
| `/test` | Write unit/integration tests for specified target |
| `/review` | Checklist-based code review (quality, design, security, etc.) |
| `/verify` | Verify consistency between document, code, and tests, then resolve discrepancies |
