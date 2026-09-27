# Public repository

This repository and its entire Git history are public. Treat source, tests,
documentation, examples, commit messages, issues, and pull requests as public.

Keep credentials, personal identities and email addresses, private repository
links, host details, work data, personal automation, and standing permissions in
a separate private overlay. Use synthetic fixtures and placeholder identities here.
Never import private Git history or copy private files wholesale into this repo.

Before committing or publishing, review every staged file and commit metadata for
private content. Use a public-safe commit identity. If private data is found, stop
publication and report it without repeating the sensitive value in public text.

Keep the shared setup usable without a private GitHub login or an overlay checkout.
Repository instructions belong here; `agents/` contains rules installed on target
machines and must not assume that the machine's other repositories are public.

## Generic by default

Everything here must make sense on a fresh machine with no overlay, such as a
work laptop. An entry that only matters on one setup belongs in that setup's
overlay, even when its name reveals nothing secret. Examples: a notification
service's token variable, a credential file for one host's VPN, a path used by
personal automation, a model or tool choice for one account.

When shared code needs a value that differs per setup, read it from an overlay
file instead of hardcoding it. The README's overlay section lists the existing
files, such as `profiles/<profile>.json`, `copilot/subagents.json`, and
`copilot/sensitive.json`. Add a new optional overlay file only when none of those
fit, and keep the shared default empty or generic.

Before committing, check each new list entry, default, example, and test fixture:
would it appear in a setup for someone else? If not, move it to the overlay.
