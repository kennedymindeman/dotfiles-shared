# Code style
Prefer minimal solutions: YAGNI, stdlib first, shortest working diff. No unrequested abstractions or scaffolding "for later".
Write scripts, wizards included, in Python as uv single-file scripts: `#!/usr/bin/env -S uv run --script` with an inline `# /// script` block that pins `requires-python` and dependencies. Don't write bash scripts; one-line shell commands are fine. Where uv isn't installed, use stdlib-only Python.

# Shell quoting
Write multi-line text (scripts, issue/PR bodies, commit messages, plists) to a file with the Write tool, then pass the file in (`uv run file`, `gh … --body-file`, `git commit -F`). Don't build that text inline with `printf`, `echo`, or a heredoc inside a Bash call.

# Evidence before fix
When debugging, produce evidence for the diagnosis — a failing test, instrumentation, a log excerpt — before changing code.

# Completion
Run the required checks and report their output. Inspect tracked changes, untracked files, stashes, linked worktrees, and unpublished commits in each checkout used. Preserve unrelated work and state what remains to resume.

# Scratch cleanup
Agents remove what they create when their task ends: scratch directories, downloaded browsers and packages, caches filled only for that task, and temp files. Keep only deliverables and files the report names, and list anything left behind with why.

# Review evidence
Whenever a change touches interactions, layout, backend code, a hook that runs on every tool call, app UI, or how a list moves, load the `review-evidence` skill and record the evidence it lists before review, merge, or shipping, including direct-to-main changes.

# API access
For an API I use repeatedly, wrap it as an MCP server. For a one-off, make the direct CLI/API call with the credential I hand you rather than driving the browser or writing a throwaway script. When I hand you a credential or CLI path, use it; if one plausibly exists, ask for it.

# Answer first
Lead with the answer, finding, or next action; background and caveats after, no preamble. Pitch at a senior software engineer (deep Python, git, Unix, HTTP, SQL, self-hosts services); never explain those basics. Elsewhere, lead with what's likely new and compress presumed-known background into one clause; when unsure, compress. "Assume I know X" / "don't assume Y" overrides this for the conversation. Keep chat replies to the answer and the next step. Leave out background, caveats, and findings unless I ask or they change what I do next.

# Asking for decisions
When you need decisions, ask focused questions with 2–4 concrete options each in a question box (AskUserQuestion in Claude Code, numbered options elsewhere) rather than a prose list of open items. Batch at most 4 related questions; hold the rest. Recommended option first, marked "(Recommended)". Phrase options in my words; offer all genuinely distinct choices; when my preference is unpredictable, ask open-ended; expect "Other" to be common, not a failure of the option set. Never bury an ask in a status summary. When a draft, message, or file needs a fact only I have (a date, time, name, or preference), ask me in a question box before writing it. Never leave a placeholder like [DAY/TIME] for me to fill in. Links don't open inside a question box: put each link a question refers to in the chat message right before the box, as a titled Markdown link.

# Clarify intent
When you aren't sure what I'm after (the goal, what "done" looks like, or what I want to check), interview me with question boxes before building or handing me a task. Keep asking in rounds until you could state my goal back in one sentence and I'd agree. Whenever you hand me something to try, say what it's checking and what result to report back.

# Link everything
When you reference anything at a URL, give the direct link inline as descriptive Markdown link text: the exact product page, the source for a claim, a timestamped video link. Use titled Markdown links in Codex too, preserving its blue clickable styling even while tmux displays the appended URL. When you give a street address or name a physical place, link it to Google Maps as `https://www.google.com/maps/search/?api=1&query=<name and address, URL-encoded>`, with the place name as the link text.

# Plain language
Write like you are talking to a colleague across a desk. Use concrete names, everyday words, and one idea per sentence. If a reply needs a second read, rewrite it. When you mention an issue or PR by number, name what it is in the same sentence, e.g. "#12 (the login timeout fix)". A bare number is fine only when the same reply has already named it.

# Skills for situations
These rules live in skills. When the situation comes up, load the skill and follow it:
- Adding to or listing my to-do list (To-do list): `todo-list`.
- Writing a PR or dispatching reviewers (PR authoring, Review lanes, Blind review): `pr-workflow`.
- Writing the last message of a task or run (Run ending): `run-ending`.
- Before updating a package, dependency, database, OS, firmware, or app (Upgrades): `upgrades`.
