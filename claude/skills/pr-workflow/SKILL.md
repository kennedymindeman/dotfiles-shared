---
name: pr-workflow
description: Pull request authoring and code review rules. Use when writing a PR title or body, deciding whether to split a diff into stacked PRs, or dispatching reviewers for a diff or PR.
---

# PR authoring
Title: a standalone imperative sentence. Body: open with exactly 3 plain lines: what merging does, what breaks if it's wrong, and how to undo it. When the change is hard to judge from text, add one `Explainer: <url>` line after them. Put the rest in a `<details>` fold: why (issue link + problem), caveats and intentional exclusions, what as short bullets, then a `## Validation` section (commands + results) on every code PR. Teach what the diff can't; never re-narrate the diff or how you produced it; keep it shorter than the diff on small changes. Past ~200 lines, add a "start reading at" pointer. Prefer stacked single-purpose PRs. Before opening a PR, list the separate concerns in the diff. If there is more than one, split them into stacked PRs or say in the body why they belong together. Never use a line-count limit or a blocking size check.

# Review lanes
When a diff is larger than you'd read line by line, or handles input someone else controls while taking outward or hard-to-undo actions (sending mail or messages, payments, deletes, unsubscribes, public posts), run reviewers in parallel, one per lens. One `reviewer` checks the spec and correctness. A red-team `reviewer` gets the artifact plus a threat statement: who controls which inputs, and what the code can do outbound. It tries to make the code break a stated rule, and confirms each finding with a probe against fakes. Otherwise, one blind reviewer. The threat statement names the attack surface, not expected findings.

# Blind review
Give reviewers the artifact and acceptance criteria without expected findings.
