---
name: commit
description: Create a clean git commit for the current issue scope.
---

# Commit

## Steps

1. Review `git status`, `git diff`, and `git diff --staged`.
2. Stage only issue-relevant files.
3. Write a conventional commit message with:
   - short subject
   - summary
   - validation note
4. Create the commit only when the staged diff matches the message.

## Template

```text
feat(scope): short summary

Summary:
- key change
- key change

Validation:
- command run or not run (reason)

Co-authored-by: Codex <codex@openai.com>
```
