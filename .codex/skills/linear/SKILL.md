---
name: linear
description: Update Linear issue state, comments, and PR links during Symphony runs.
---

# Linear

## Goals

- Keep the issue state aligned with actual progress.
- Leave clear review handoff notes.
- Ensure the PR URL is attached to the issue before review.

## Required updates

- Move `Todo` -> `In Progress` when active implementation starts.
- Move `In Progress` -> `In Review` only after commit, push, PR creation, and validation.
- Move to `Done` only after human review and merge are complete.
