# CLAUDE.md — Shopping Project Instructions

## Project Overview

Django REST API application (orders/payments/points) with Self-Healing capabilities.
Built on Django 5.2 + DRF + PostgreSQL 15 + Redis 7 + Celery + Kafka + Prometheus + OTEL + K8s.

- **Application code**: `shopping/` (orders, payments, points)
- **Project config**: `myproject/` (settings, celery, wsgi, middleware)
- **Self-Healing framework**: installed as pip dependency from [selfhealing-python](https://github.com/gotoUSA/selfhealing-python)

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
- selfhealing framework code is maintained in a separate repo — do not modify it here

## Test Location Rules

- `shopping/tests/` — shopping app unit/integration tests
- `tests/hybrid/` — shopping + selfhealing combined integration tests
- `tests/conftest.py` — shared test fixtures

## Custom Skills

| Skill | Description |
|-------|-------------|
| `/advisor` | Project Q&A, idea evaluation, and counter-proposals |
| `/execute` | Read implementation plan document and implement code |
| `/test` | Write unit/integration tests for specified target |
| `/review` | Checklist-based code review (quality, design, security, etc.) |
| `/verify` | Verify consistency between document, code, and tests, then resolve discrepancies |
