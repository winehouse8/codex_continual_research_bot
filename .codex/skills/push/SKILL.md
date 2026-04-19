---
name: push
description: Push branch changes and create or update a GitHub pull request.
---

# Push

## Prerequisites

- `gh auth status` succeeds.
- Validation for the current scope has been run.

## Steps

1. Identify the current branch.
2. Push the branch to `origin`.
3. If push is rejected due to sync drift, run the `pull` skill and retry.
4. Ensure a PR exists for the branch:
   - create one if missing
   - update it if already open
5. Use `.github/pull_request_template.md` to write the PR body.
6. Return the PR URL.
