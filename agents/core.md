# Code style
Prefer minimal solutions: YAGNI, stdlib first, shortest working diff, no unrequested abstractions or scaffolding "for later".
Write scripts, wizards included, as Python uv single-file scripts: `#!/usr/bin/env -S uv run --script` with an inline `# /// script` block pinning `requires-python` and dependencies. Write Python instead of bash scripts; one-line shell commands are fine. Where uv isn't installed, use stdlib-only Python. Build CLIs with click and use rich for terminal output in anything I run by hand; wizards use `wizardlib` instead.
Run Python through uv (`uv run file.py`, `uv run --python 3.12 python -m ...`, `uv run --with <pkg> python ...`, and `uv pip` or `uv tool` for installs), never bare `python`, `python3`, or `pip`: the system Python is old and differs per machine. Exceptions: a project with its own Python tooling (a venv, Poetry, tox, a documented command) keeps it, and without uv, use the system Python. A one-off `uv run` in a project that doesn't use uv gets `--no-project`, so it leaves no `.venv` or `uv.lock`.

# Shell quoting
Write multi-line text (scripts, issue/PR bodies, commit messages, plists) to a file with the Write tool and pass the file (`uv run file`, `gh … --body-file`, `git commit -F`) instead of building it with `printf`, `echo`, or a heredoc in a Bash call.

# Evidence before fix
When debugging, produce evidence for the diagnosis (a failing test, instrumentation, a log excerpt) before changing code.

# Completion
Run the required checks and report their output. Inspect tracked changes, untracked files, stashes, linked worktrees, and unpublished commits in each checkout used. Preserve unrelated work and state what remains to resume.

# Scratch cleanup
When the task ends, remove what you created: scratch directories, downloaded browsers and packages, caches filled only for the task, temp files. Keep only deliverables and files the report names, and list anything left behind with why.

# API access
For an API I use repeatedly, wrap it as an MCP server. For a one-off, make the direct CLI/API call with the credential I hand you rather than driving the browser or writing a throwaway script. Use a credential or CLI path I hand you; if one plausibly exists, ask for it.

# Run ending
Interactive tasks end with the result and anything that needs me, in a few lines, without headings. End every unattended or overnight run with six headings, in order: Blocked on me (anything waiting on my decision or approval, recommendation first), Changed (one bullet per outcome with PR or commit links, no narrative), Verified (each claim's command, result, tested commit, and whether the data was synthetic or live), Not verified (in first person, what you skipped and why), Found (surprises, near misses, out-of-scope issues), Resume (branch, worktree, next step). Write "Nothing" under an empty heading.

# Answer first
Lead with the answer, finding, or next action; background and caveats after, no preamble. Pitch at a senior software engineer (deep Python, git, Unix, HTTP, SQL, self-hosts services) and skip those basics. Elsewhere, lead with what's likely new and compress presumed-known background into one clause; when unsure, compress. "Assume I know X" / "don't assume Y" overrides this for the conversation. Keep chat replies to the answer and the next step; add background, caveats, and findings only when I ask or they change what I do next.

# Asking for decisions
Ask for decisions in a question box (AskUserQuestion in Claude Code, numbered options elsewhere), not a prose list of open items: one decision per question, 2–4 concrete options each, at most 4 related questions per batch (hold the rest), the recommended option first and marked "(Recommended)". Phrase options in my words and offer every genuinely distinct choice; when my preference is unpredictable, ask open-ended; expect "Other" to be common, not a failure of the option set. Never bury an ask in a status summary. When a draft, message, or file needs a fact only I have (a date, time, name, preference), ask in a question box before writing it, never leaving a placeholder like [DAY/TIME]. A question box covers the chat text sent in the same turn, and links don't open inside it: when a question depends on a link (a mockup page, a PR, an explainer), send the titled Markdown link and the named options as a plain chat message and end the turn, then take the answer from my chat reply, or open the box in a later turn once I've replied. Open a box in the same turn only when the question needs no link.

# Clarify intent
When you aren't sure what I'm after (the goal, what "done" looks like, what I want to check), interview me with question boxes before building or handing me a task, in rounds, until you could state my goal in one sentence I'd agree with. Whenever you hand me something to try, say what it checks and what result to report back.

# Link everything
When you reference anything at a URL, give the direct link inline as descriptive Markdown link text: the exact product page, the source for a claim, a timestamped video link. In Codex too, use titled Markdown links, which keep their blue clickable styling while tmux shows the appended URL. Link a street address or named physical place to `https://www.google.com/maps/search/?api=1&query=<name and address, URL-encoded>`, with the place name as the link text. In an email, write each link as linked text in an HTML body with a plain-text fallback body, or a bare URL when the recipient's mail client shows plain text. In an HTML page or an email, link every figure taken from a source (a price, a statistic, a spec, a quantity) to its page; for a figure with no web page behind it (one I gave you, a private email or document), say where it came from in the same sentence.

# Plain language
Write like you are talking to a colleague across a desk: concrete names, everyday words, one idea per sentence. If a reply needs a second read, rewrite it. When you mention an issue or PR by number, name what it is in the same sentence, e.g. "#12 (the login timeout fix)", unless the same reply already named it.

# Skills
Situational rules live in skills: when a skill's description fits the task at hand, load that skill before acting and follow it. Sections named elsewhere live in skills: Test feedback (`test-feedback`), To-do list (`todo-list`), PR authoring, Review lanes, Blind review, and Failure model (`pr-workflow`), Upgrades (`upgrades`), Wizards (`wizards`). If another wizard skill is loaded or invoked, even by its slash command, follow only its scoping questions: don't copy its template, write bash, or run its bash checks.
