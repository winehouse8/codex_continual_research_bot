# Agent Guide

## Purpose

This repository is built by Codex sessions orchestrated through Symphony.

Agents should treat the repository documents as the primary source of product
context:

- `SPEC.md`: product requirements and operating model
- `ARCH.md`: architecture and module boundaries
- `TASKS.md`: current implementation roadmap

## Rules

- Keep changes tightly scoped to the Linear issue.
- Prefer small, reviewable changes.
- Update docs when implementation changes architecture or runtime behavior.
- Run the narrowest useful validation for the change.
- When behavior changes, record validation evidence in the issue workflow.

## Git / PR Rules

- Work on an issue-specific branch.
- Commit logically with a clear commit message.
- Push branch changes to origin.
- Create or update a GitHub PR for the branch.
- Keep PR title/body aligned with the actual scope of the branch.

## Branch Naming

Use:

`linear-<issue-id>-<short-slug>`

Example:

`linear-DEE-5-python-bootstrap`

## Review Handoff

Before moving an issue to `In Review`, make sure:

- code changes are committed
- branch is pushed
- a PR exists
- the PR URL is included in the Linear issue
- relevant validation has been run
