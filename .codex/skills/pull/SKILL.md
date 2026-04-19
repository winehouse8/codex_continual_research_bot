---
name: pull
description: Sync the current branch with origin/main using merge-based update.
---

# Pull

## Steps

1. Ensure the working tree is clean or commit current work.
2. Enable rerere if available.
3. Fetch `origin`.
4. Pull the current branch with `--ff-only`.
5. Merge `origin/main` into the current branch.
6. Resolve conflicts carefully and rerun validation.
