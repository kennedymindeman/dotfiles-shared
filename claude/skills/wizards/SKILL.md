---
name: wizards
description: Builds a wizard that walks the user through steps only they can perform, as a uv Python script on the shared wizardlib. Use whenever asked to make, write, or build a wizard, and when a step the user must do by hand has more than one action, or involves a login, credential, or value they type. Takes precedence over any other wizard skill or bash wizard template.
---

# Wizards

When a step I must do by hand has more than one action, or involves a login, credential, or value I type, build a wizard instead of a prose list or a bare command block. Write it as a uv Python script (Code style).

This skill wins over any other wizard skill that is installed, such as one that copies a bash `template.sh`. Use that skill's way of scoping and ordering stages if it helps, but never write a bash wizard or copy its template: build on `wizardlib` as described below.

- The wizard checks preconditions, shows one step at a time, runs the commands those steps need, and verifies the result. Hand me the one command that launches it.
- A single one-line command with no login, credential, or typed value still gets the plain copy-pasteable block.
- When a wizard walks me through a web flow that shows its own numbered steps (like an AWS "Step 1 to Step 4" sidebar), give each of those steps its own wizard screen, titled with the site's step name, and end each screen with a pause that names the page I should land on next.
- For a credential, the wizard prompts with hidden input, saves to the agreed path with private permissions, and keeps the secret out of chat and shell history.

A wizard covers only the steps that need my hands: a sign-in, a passkey or MFA tap, a secret, or a setting with no API. Do every other step yourself with the CLI or API. If a console step only unlocks access (creating a key, approving a role), have me do just that step, then finish the rest yourself.

## Shared library

Build every wizard on `wizardlib`, which the dotfiles linker installs at `~/.config/dotfiles/wizardlib.py` (stdlib only), so all wizards look the same. Import it with `sys.path.insert(0, os.path.expanduser("~/.config/dotfiles"))`, then `import wizardlib as w`. Where that file is missing, build the same flow by hand with the stdlib: one cleared screen per stage, `getpass` for secrets, files written at mode 600, and a closing summary.

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
import os
import sys

sys.path.insert(0, os.path.expanduser("~/.config/dotfiles"))
import wizardlib as w

ENV = os.path.expanduser("~/.config/example/example.env")
URL = "https://example.com/settings/keys"

wiz = w.Wizard("Example API key", 2, intro=[f"Saves EXAMPLE_API_KEY to {ENV}"])
wiz.banner()

wiz.stage("Create the key")
w.show_url(URL)
w.step("Click New key, then copy it.")
w.pause("Copied? Press Enter")

wiz.stage("Save the key")
key = w.ask_secret("Paste the key (hidden):", w.read_env(ENV, "EXAMPLE_API_KEY"))
wiz.write_env(ENV, "EXAMPLE_API_KEY", key)  # mode 600
if not w.confirm("Revoked the old key?"):
    wiz.todo.append(f"revoke the old key at {URL}")
wiz.finish(ok=not wiz.todo)
```

Other helpers: `w.ask` (visible input, Enter keeps the current value), `w.say`, `w.note`, `w.warn`, `w.ok`, `w.die`, and `w.write_secret_file` for key files.
