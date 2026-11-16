---
name: wizards
description: Builds a wizard that walks the user through steps only they can perform. Use when a step the user must do by hand has more than one action, or involves a login, credential, or value they type.
---

# Wizards

When a step I must do by hand has more than one action, or involves a login, credential, or value I type, build a wizard instead of a prose list or a bare command block. Write it as a uv Python script (Code style).

- The wizard checks preconditions, shows one step at a time, runs the commands, and verifies the result. Hand me the one command that launches it.
- A single one-line command still gets the plain copy-pasteable block.
- When a wizard walks me through a web flow that shows its own numbered steps (like an AWS "Step 1 to Step 4" sidebar), give each of those steps its own wizard screen, titled with the site's step name, and end each screen with a pause that names the page I should land on next.
- For a credential, the wizard prompts with hidden input, saves to the agreed path with private permissions, and keeps the secret out of chat and shell history.

A wizard covers only the steps that need my hands: a sign-in, a passkey or MFA tap, a secret, or a setting with no API. Do every other step yourself with the CLI or API. If a console step only unlocks access (creating a key, approving a role), have me do just that step, then finish the rest yourself.
