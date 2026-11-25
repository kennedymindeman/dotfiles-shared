---
name: pr-workflow
description: Pull request authoring and code review rules. Use when writing a PR title or body, deciding whether to split a diff into stacked PRs, dispatching reviewers for a diff or PR, or writing a build brief for work where a client and a server both hold the same state.
---

# PR authoring
Title: a standalone imperative sentence. Body: open with exactly 3 plain lines: what merging does, what breaks if it's wrong, and how to undo it. When the change is hard to judge from text, add one `Explainer: <url>` line after them. Put the rest in a `<details>` fold: why (issue link + problem), caveats and intentional exclusions, what as short bullets, then a `## Validation` section (commands + results) on every code PR. Teach what the diff can't; never re-narrate the diff or how you produced it; keep it shorter than the diff on small changes. Past ~200 lines, add a "start reading at" pointer. Prefer stacked single-purpose PRs. Before opening a PR, list the separate concerns in the diff. If there is more than one, split them into stacked PRs or say in the body why they belong together. Never use a line-count limit or a blocking size check.

# Review lanes
One blind `reviewer` checks the spec and correctness; a diff that changes only layout, styling, or copy gets no other. Add a red-team `reviewer` in parallel when the diff changes stored data, how saved state is loaded, logic that computes results, or takes outward or hard-to-undo actions (sending mail or messages, payments, deletes, unsubscribes, public posts). The red-team `reviewer` gets the artifact plus a threat statement: who controls which inputs, and what the code can do outbound. It tries to make the code break a stated rule, and confirms each finding with a probe against fakes. The threat statement names the attack surface, not expected findings. Each review brief names its lane, `blind` or `red-team`.

The reviewer's severity ratings stand, with two exceptions for the coordinator, the session that reads the review. First, it may move a major finding to a follow-up issue when all of these hold: the reviewer puts it at under about 2% of realistic cases, it loses or corrupts no data, it opens no security hole, and it fails no test. Second, the round cap: from a PR's second review on, the coordinator decides alone to fix only findings that lose or corrupt data, break normal use, expose private data or a security hole, or fail a test, and moves every other finding, whatever its rating, to the follow-up issue.

The pass rule: a review passes when every finding is minor, was moved under these exceptions, or was fixed as Blind review says. The coordinator files moved findings as one follow-up issue, linked on the line under its pass verdict; under the round cap, that line also says `Round cap: review <n>, <count> findings moved`.

The reviewer writes its full findings to a file and returns the verdict, a one-line count by severity, and the path; one without a write tool returns the full report. The builder gets the path and reads the file. Delete a PR's findings files once it merges or closes.

A test suite that takes over 10 minutes runs once, on the final commit before merge, by the builder or coordinator. Every review round runs only the checks the diff touches; the reviewer's brief says so and names the commit the slow suite last passed on. If that final run fails, its fix follows Blind review. Suites under 10 minutes run as usual in every round.

# Blind review
A PR's first review is blind and full: give reviewers the artifact and the ticket's acceptance criteria without expected findings. After the blind review, the coordinator reads the fix diff itself and posts the pass comment for the new head, noting that no reviewer checked the fix. A fix goes back to a blind reviewer only when it changes stored data, how saved state is loaded, logic that computes results, or an outward or hard-to-undo action. That re-review is scoped: its brief gives that lane's earlier findings file and the fix range `<reviewed SHA>..HEAD`; the reviewer confirms each finding fixed, checks the fix diff for regressions, and reports anything else under "Outside this round". A brief may also list files or areas an earlier review verified, each with its commit; the reviewer skips those `git diff <commit>..HEAD` shows unchanged.

The review brief adds no criteria; a criterion found during review goes into the ticket first. Acceptance criteria, in tickets and briefs, state the supported envelope (inputs, rates, platforms, timing) and name what is out of scope. Write an absolute ("any input", "whatever the timing", "never") only for a guarantee whose breach should block a merge.

# Failure model
When a client holds state that a server also holds, the build brief states the failure model before review round 1: each request ends ok, refused, or reply lost; one request at a time per resource; resync from the server after a reply is lost; one source of truth for each list.
