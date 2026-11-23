---
name: pr-workflow
description: Pull request authoring and code review rules. Use when writing a PR title or body, deciding whether to split a diff into stacked PRs, dispatching reviewers for a diff or PR, or writing a build brief for work where a client and a server both hold the same state.
---

# PR authoring
Title: a standalone imperative sentence. Body: open with exactly 3 plain lines: what merging does, what breaks if it's wrong, and how to undo it. When the change is hard to judge from text, add one `Explainer: <url>` line after them. Put the rest in a `<details>` fold: why (issue link + problem), caveats and intentional exclusions, what as short bullets, then a `## Validation` section (commands + results) on every code PR. Teach what the diff can't; never re-narrate the diff or how you produced it; keep it shorter than the diff on small changes. Past ~200 lines, add a "start reading at" pointer. Prefer stacked single-purpose PRs. Before opening a PR, list the separate concerns in the diff. If there is more than one, split them into stacked PRs or say in the body why they belong together. Never use a line-count limit or a blocking size check.

# Review lanes
One blind `reviewer` checks the spec and correctness; for a diff that changes only layout, styling, or copy, that one reviewer is all. Add a red-team `reviewer` in parallel when the diff changes stored data, how saved state is loaded, logic that computes results, or takes outward or hard-to-undo actions (sending mail or messages, payments, deletes, unsubscribes, public posts). The red-team `reviewer` gets the artifact plus a threat statement: who controls which inputs, and what the code can do outbound. It tries to make the code break a stated rule, and confirms each finding with a probe against fakes. The threat statement names the attack surface, not expected findings.

The reviewer's severity ratings stand, with one exception for the session that reads the review: it may move a finding the reviewer rated major to a follow-up issue, without another review round, when all of these hold: the reviewer measured or estimated that it shows up in under about 2% of realistic cases, it loses or corrupts no data, it opens no security hole, and it fails no test. A finding rated blocker is never downgraded. A review whose only majors were moved this way counts as a pass: the session files the follow-up issue and links it on the line under its pass verdict, the same as when every finding is minor.

A test suite that takes over 10 minutes runs once, on the final commit before merge, run by the builder or the coordinator. Every review round, the first included, runs only the checks the diff touches; the dispatcher says so in the reviewer's brief and names the commit the slow suite last passed on. If that final run fails, the fix goes back to review. Suites under 10 minutes run as usual in every round.

# Blind review
Give reviewers the artifact and acceptance criteria without expected findings.

# Failure model
When a client holds state that a server also holds, the build brief states the failure model before review round 1: each request ends ok, refused, or reply lost; one request at a time per resource; resync from the server after a reply is lost; one source of truth for each list.
