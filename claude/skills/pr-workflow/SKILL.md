---
name: pr-workflow
description: Pull request authoring and code review rules. Use when writing a PR title or body, deciding whether to split a diff into stacked PRs, dispatching reviewers for a diff or PR, or writing a build brief for work where a client and a server both hold the same state.
---

# PR authoring
Title: a standalone imperative sentence. Body: open with exactly 3 plain lines: what merging does, what breaks if it's wrong, and how to undo it. When the change is hard to judge from text, add one `Explainer: <url>` line after them. Put the rest in a `<details>` fold: why (issue link + problem), caveats and intentional exclusions, what as short bullets, then a `## Validation` section (commands + results) on every code PR. Teach what the diff can't; never re-narrate the diff or how you produced it; keep it shorter than the diff on small changes. Past ~200 lines, add a "start reading at" pointer. Prefer stacked single-purpose PRs. Before opening a PR, list the separate concerns in the diff. If there is more than one, split them into stacked PRs or say in the body why they belong together. Never use a line-count limit or a blocking size check.

# Review lanes
One blind `reviewer` checks the spec and correctness; for a diff that changes only layout, styling, or copy, that one reviewer is all. Add a red-team `reviewer` in parallel when the diff changes stored data, how saved state is loaded, logic that computes results, or takes outward or hard-to-undo actions (sending mail or messages, payments, deletes, unsubscribes, public posts). The red-team `reviewer` gets the artifact plus a threat statement: who controls which inputs, and what the code can do outbound. It tries to make the code break a stated rule, and confirms each finding with a probe against fakes. The threat statement names the attack surface, not expected findings. A finding that affects a small share of cases and loses or corrupts nothing is minor: it goes in a follow-up issue, not another review round. Run a suite that takes over 10 minutes once, on the final commit before merge; during the build and during re-review, run only the checks the diff touches.

# Blind review
Give reviewers the artifact and acceptance criteria without expected findings.

# Failure model
When a client holds state that a server also holds, the build brief states the failure model before review round 1: each request ends ok, refused, or reply lost; one request at a time per resource; resync from the server after a reply is lost; one source of truth for each list.
