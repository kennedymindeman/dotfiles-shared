#!/usr/bin/env python3
"""Install shared configuration and an optional overlay for the selected profile."""

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from dotfiles_profile import resolve_profile

HOOK_MARK = "dotfiles:relink-hook"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def headings(text):
    # Skip fenced code so shell comments are not taken for headings.
    found, fenced = {}, False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        elif not fenced and re.match(r"#+ .+$", line):
            # Key "# prAuthoring" and "# PR authoring" alike so old copies match.
            key = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", line).lower().split()
            found.setdefault(" ".join(key), line)
    return found


def read_json(path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def is_redirecting_link(path):
    try:
        details = os.lstat(path)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(details.st_mode):
        return True
    return bool(
        os.name == "nt"
        and details.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


class Installer:
    def __init__(
        self, repo, home, profile, platform, overlay=None, copilot_home=None
    ):
        self.repo, self.home = repo, home
        self.profile, self.platform, self.overlay = profile, platform, overlay
        selected_copilot_home = copilot_home or home / ".copilot"
        if not selected_copilot_home.is_absolute():
            raise ValueError("COPILOT_HOME must be an absolute path")
        if is_redirecting_link(selected_copilot_home):
            raise ValueError(
                "review symlinked configuration directory before installing: "
                f"{selected_copilot_home}"
            )
        self.copilot_home = selected_copilot_home.resolve(strict=False)
        self.state_path = self.target(".config/dotfiles/state.json")
        backups = self.target(".config/dotfiles/backups")
        if is_redirecting_link(backups):
            raise ValueError(f"refusing symlinked backup directory: {backups}")
        marker = self.target(".dotfiles-env")
        if marker.is_symlink():
            raise ValueError(f"refusing symlinked profile marker: {marker}")
        if self.state_path.is_symlink():
            raise ValueError(f"refusing symlinked state: {self.state_path}")
        self.old = read_json(self.state_path, {"files": {}})
        if not isinstance(self.old, dict) or not isinstance(
            self.old.get("files"), dict
        ):
            raise TypeError(f"invalid ownership state: {self.state_path}")
        previous_copilot_home = Path(
            self.old.get("copilot_home", home / ".copilot")
        ).resolve(strict=False)
        if self.old["files"] and os.path.normcase(
            os.path.abspath(previous_copilot_home)
        ) != os.path.normcase(os.path.abspath(self.copilot_home)):
            raise ValueError(
                "COPILOT_HOME changed since the previous install; review the old "
                f"managed configuration at {previous_copilot_home} before relinking"
            )
        self.records = {}
        self.changes = []
        self.legacy = (
            read_json(overlay / "profiles/legacy.json", {})
            if overlay and profile == "home"
            else {}
        )

    def target(self, relative):
        relative_path = Path(relative)
        if (
            relative_path.is_absolute()
            or ".." in relative_path.parts
            or relative_path == Path(".")
        ):
            raise ValueError(f"invalid installation path: {relative}")
        if relative_path.parts[0] == ".copilot":
            root = self.copilot_home
            parts = relative_path.parts[1:]
        else:
            root = self.home
            parts = relative_path.parts
        path = root.joinpath(*parts)
        parent = root
        for part in parts[:-1]:
            parent /= part
            if is_redirecting_link(parent):
                raise ValueError(
                    f"review symlinked configuration directory before installing: {parent}"
                )
        return path

    def owned(self, relative, path):
        record = self.old["files"].get(relative, {})
        if path.is_symlink():
            return record.get("link") == os.readlink(path)
        return path.is_file() and record.get("sha256") == digest(path.read_bytes())

    def legacy_link(self, path, source):
        roots = [self.repo]
        if self.overlay:
            roots.append(self.overlay)
        return path.is_symlink() and any(
            path.resolve() == (root / source).resolve() for root in roots
        )

    def file(self, relative, source, root=None, data=None, skip_unowned=False):
        """Install source; with data, write those bytes as an owned copy instead.
        With skip_unowned, warn and keep an unowned or changed file instead of stopping."""
        path = self.target(relative)
        for name in (
            ".dotfiles-env", ".config/dotfiles/state.json", ".copilot/settings.json",
            ".config/dotfiles/backups", *self.records,
        ):
            configured = self.target(name)
            if path == configured or path in configured.parents or configured in path.parents:
                raise ValueError(f"reserved or already configured path: {path}")
        source_path = (root or self.repo) / source
        if not source_path.exists():
            raise ValueError(f"missing configured source: {source_path}")
        generated = data is not None
        use_link = self.platform != "windows" and not generated
        if not generated and not use_link:
            data = source_path.read_bytes()
        record = {"link": str(source_path)} if use_link else {"sha256": digest(data)}
        exists = path.exists() or path.is_symlink()
        identical = (
            path.is_symlink() and os.readlink(path) == str(source_path)
            if use_link
            else path.is_file()
            and not path.is_symlink()
            and path.read_bytes() == data
        )
        if (
            exists
            and not identical
            and not self.owned(relative, path)
            and not self.legacy_link(path, source)
        ):
            if skip_unowned:
                # Keep the old record so a profile switch doesn't flag the file.
                if relative in self.old["files"]:
                    self.records[relative] = self.old["files"][relative]
                print(
                    f"warning: keeping {path}, which dotfiles did not write or which "
                    "changed since; move it aside after review and rerun to replace it",
                    file=sys.stderr,
                )
                return
            raise ValueError(
                f"preserving unowned configuration: {path}; move it aside after review and rerun"
            )
        self.records[relative] = record
        if not identical:
            self.changes.append(
                (relative, "bytes", data)
                if generated
                else (relative, "link" if use_link else "copy", source_path)
            )

    def block(self, relative, content, markdown=False, legacy_source=None, lua=False):
        path = self.target(relative)
        start, end = (
            ("<!-- dotfiles:begin -->", "<!-- dotfiles:end -->")
            if markdown
            else ("# dotfiles:begin", "# dotfiles:end")
        )
        if lua:
            start, end = "-- dotfiles:begin", "-- dotfiles:end"
        if path.is_symlink():
            old_agents_link = (
                self.overlay
                and relative == ".codex/AGENTS.md"
                and path.resolve() == (self.home / "AGENTS.md").resolve()
                # A dangling link held nothing; an earlier run may have removed
                # ~/AGENTS.md before replacing this link.
                and (
                    not path.exists()
                    or digest(path.read_bytes()) in self.legacy.get("AGENTS.md", [])
                )
            )
            if not old_agents_link and (
                not legacy_source or not self.legacy_link(path, legacy_source)
            ):
                raise ValueError(f"preserving unowned symlink: {path}")
            original = ""
        else:
            original = path.read_text(encoding="utf-8-sig") if path.exists() else ""
        before = original
        if digest(original.encode()) in self.legacy.get(relative, []):
            original = ""
        if (
            self.overlay and self.profile == "home"
            and relative == ".claude/CLAUDE.md"
            and original
            == f"@{self.overlay}/agents/core.md\n@{self.overlay}/agents/home.md\n"
        ):
            original = ""
        if self.overlay and self.profile == "home":
            # Adopt only exact legacy Windows stubs generated by the old installer.
            private_path = str(self.overlay).replace(chr(92), "/")
            if relative == ".gitconfig":
                for value in (
                    private_path + "/gitconfig",
                    json.dumps(private_path + "/gitconfig"),
                ):
                    original = original.replace(
                        "[include]\n\tpath = " + value + "\n", ""
                    )
            profile_source = str(
                self.overlay / "powershell/Microsoft.PowerShell_profile.ps1"
            )
            if original.strip() in (
                f'. "{profile_source}"',
                f'. "{profile_source.replace(chr(47), chr(92))}"',
            ):
                original = ""
        old_overlay = self.old.get("overlay")
        if (
            old_overlay
            and (old_overlay != str(self.overlay) or self.old.get("profile") != self.profile)
            and old_overlay.replace(chr(92), "/") in original.replace(chr(92), "/")
        ):
            # The managed block itself is replaced below; check only user-owned text.
            outside = re.sub(
                re.escape(start) + r"\n.*?\n" + re.escape(end),
                "",
                original,
                flags=re.DOTALL,
            )
            if old_overlay.replace(chr(92), "/") in outside.replace(chr(92), "/"):
                raise ValueError(
                    f"review remaining reference to private configuration: {path}"
                )
        replacement = start + "\n" + content.rstrip() + "\n" + end
        pattern = re.compile(re.escape(start) + r"\n.*?\n" + re.escape(end), re.DOTALL)
        matches = pattern.findall(original)
        if start in original or end in original:
            prior = self.old["files"].get(relative, {}).get("block")
            if (
                len(matches) != 1
                or original.count(start) != 1
                or original.count(end) != 1
            ):
                raise ValueError(f"malformed managed block: {path}")
            if matches[0] != replacement and digest(matches[0].encode()) != prior:
                raise ValueError(f"preserving edited managed block: {path}")
            updated = pattern.sub(lambda _: replacement, original)
        else:
            updated = (
                original.rstrip("\n")
                + ("\n\n" if original else "")
                + replacement
                + "\n"
            )
        self.records[relative] = {
            "block": digest(replacement.encode()),
            "markers": [start, end],
        }
        if lua and original and not matches:
            raise ValueError(
                f"preserving existing WezTerm configuration: {path}; review it before linking"
            )
        outside_rules = pattern.sub("", original).strip()
        managed, outside = headings(content), headings(outside_rules)
        repeated = sorted(managed[key] for key in managed.keys() & outside.keys())
        if markdown and repeated:
            # Warn only: the text outside the block belongs to the user.
            print(
                f"warning: {path} repeats managed rules outside the dotfiles block "
                f"({', '.join(repeated)}); remove the stale copy after review",
                file=sys.stderr,
            )
        if self.profile == "work" and markdown and outside_rules:
            raise ValueError(
                f"review existing global agent instructions before using work: {path}"
            )
        if path.is_symlink() or updated != before:
            self.changes.append((relative, "bytes", updated.encode()))

    def retire(self):
        for relative, record in self.old["files"].items():
            if relative in self.records:
                continue
            path = self.target(relative)
            if not path.exists() and not path.is_symlink():
                continue
            if "block" in record and path.is_file() and not path.is_symlink():
                start, end = record["markers"]
                original = path.read_text(encoding="utf-8-sig")
                pattern = re.compile(
                    re.escape(start) + r"\n.*?\n" + re.escape(end), re.DOTALL
                )
                matches = pattern.findall(original)
                if len(matches) != 1 or digest(matches[0].encode()) != record["block"]:
                    raise ValueError(f"preserving edited profile block: {path}")
                self.changes.append(
                    (relative, "bytes", pattern.sub("", original).encode())
                )
            elif self.owned(relative, path):
                self.changes.append((relative, "remove", None))
            else:
                raise ValueError(
                    f"profile switch needs review of modified file: {path}"
                )

    def retire_home_agents(self):
        # Copilot and Codex also read AGENTS.md from the working directory, so a
        # ~/AGENTS.md doubled the rules in sessions started in the home directory.
        # Take back what earlier links wrote there; leave user text alone.
        self.old["files"].pop("AGENTS.md", None)
        path = self.target("AGENTS.md")
        if not path.is_file() or path.is_symlink():
            return
        text = path.read_text(encoding="utf-8-sig")
        pattern = re.compile(
            r"<!-- dotfiles:begin -->\n.*?\n<!-- dotfiles:end -->\n?", re.DOTALL
        )
        # The old installer's exact output counts as managed, as in block().
        if digest(text.encode()) in self.legacy.get("AGENTS.md", []):
            text = ""
        elif not pattern.search(text):
            return
        rest = pattern.sub("", text).rstrip("\n")
        self.changes.append(
            ("AGENTS.md", "bytes", (rest + "\n").encode())
            if rest.strip()
            else ("AGENTS.md", "remove", None)
        )

    def apply(self, dry_run):
        self.retire()
        # Validate every planned write before changing any configuration.
        for relative in [
            *(item[0] for item in self.changes),
            ".config/dotfiles/state.json",
            ".dotfiles-env",
        ]:
            path = self.target(relative)
            if (
                path.exists()
                and not path.is_symlink()
                and (not path.is_file() or not path.stat().st_mode & stat.S_IWUSR)
            ):
                raise ValueError(f"preserving non-writable configuration: {path}")
            parent = path.parent
            while not parent.exists():
                parent = parent.parent
            if not parent.is_dir() or not os.access(parent, os.W_OK):
                raise ValueError(f"configuration directory is not writable: {parent}")
        for relative, action, _ in self.changes:
            print(f"{action}: {self.target(relative)}")
        hooks = [
            Path(name) for name, text in self.hooks.items()
            if not Path(name).is_file() or Path(name).read_bytes() != text.encode()
        ]
        # A recorded hook the user has since changed is no longer ours to remove.
        stale = [
            Path(name) for name, sha in self.old.get("hooks", {}).items()
            if name not in self.hooks
            and Path(name).is_file()
            and digest(Path(name).read_bytes()) == sha
        ]
        for path in hooks:
            print(f"hook: {path}")
        for path in stale:
            print(f"remove: {path}")
        if dry_run:
            return
        backup = None
        for relative, action, value in self.changes:
            path = self.target(relative)
            if path.exists() or path.is_symlink():
                if backup is None:
                    directory = self.home / ".config/dotfiles/backups"
                    if is_redirecting_link(directory):
                        raise ValueError(
                            f"refusing symlinked backup directory: {directory}"
                        )
                    directory.mkdir(parents=True, exist_ok=True)
                    backup = Path(tempfile.mkdtemp(prefix="install-", dir=directory))
                saved = backup / relative
                saved.parent.mkdir(parents=True, exist_ok=True)
                if path.is_symlink():
                    saved.symlink_to(
                        os.readlink(path), target_is_directory=path.is_dir()
                    )
                else:
                    shutil.copy2(path, saved)
                path.unlink()
            if action == "link":
                path.parent.mkdir(parents=True, exist_ok=True)
                path.symlink_to(value, target_is_directory=value.is_dir())
            elif action == "copy":
                atomic_write(path, value.read_bytes())
            elif action == "bytes":
                atomic_write(path, value)
        for path in hooks:
            atomic_write(path, self.hooks[str(path)].encode())
            path.chmod(0o755)
        for path in stale:
            path.unlink()
        state = {
            "profile": self.profile,
            "files": self.records,
            "copilot_home": str(self.copilot_home),
            "copilot_subagents": self.subagents,
            "hooks": {name: digest(text.encode()) for name, text in self.hooks.items()},
        }
        if self.overlay:
            state["overlay"] = str(self.overlay)
        atomic_write(self.state_path, (json.dumps(state, indent=2) + "\n").encode())
        atomic_write(self.home / ".dotfiles-env", (self.profile + "\n").encode())
        if backup:
            print(f"previous configuration: {backup}")
        print(
            f"linked {self.profile}; restart shells and tmux to load the selected profile"
        )


def configured_files(repo, platform, profile="shared"):
    path = repo / "profiles" / f"{profile}.json"
    manifest = read_json(path)
    if not isinstance(manifest, dict):
        raise ValueError(f"profile requires a manifest object: {path}")
    result = dict(manifest.get("files", {}))
    result.update(manifest.get("windows" if platform == "windows" else "unix", {}))
    result.update(manifest.get(platform, {}))
    return result


def overlay_rules(overlay, name):
    """Return an optional overlay rules file, prefixed with a blank line."""
    path = overlay / "agents" / name if overlay else None
    return "\n" + path.read_text(encoding="utf-8") if path and path.is_file() else ""


def merge_tables(base, extra):
    """Merge TOML tables recursively; any other overlay value replaces the shared one."""
    merged = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            value = merge_tables(merged[key], value)
        merged[key] = value
    return merged


def toml_key(key):
    return key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else toml_value(key)


def toml_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        # JSON string escapes are valid in TOML basic strings; TOML also forbids a raw DEL.
        return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(
            f"{toml_key(k)} = {toml_value(v)}" for k, v in value.items()
        ) + " }"
    raise ValueError(f"unsupported TOML value: {value!r}")


def toml_dump(table, path=()):
    """Write what tomllib read back as TOML: herdr's config has no include."""
    is_table_list = lambda v: isinstance(v, list) and v and all(isinstance(i, dict) for i in v)
    lines = [
        f"{toml_key(k)} = {toml_value(v)}"
        for k, v in table.items()
        if not isinstance(v, dict) and not is_table_list(v)
    ]
    for key, value in table.items():
        name = ".".join(toml_key(part) for part in (*path, key))
        if isinstance(value, dict):
            body = toml_dump(value, (*path, key))
            # A table holding only subtables needs no header of its own.
            lines += ["", body] if body.startswith("[") else ["", f"[{name}]", body]
        elif is_table_list(value):
            for item in value:
                lines += ["", f"[[{name}]]", toml_dump(item, (*path, key))]
    return "\n".join(lines).strip("\n")


def herdr_config(repo, overlay, profile, platform):
    """Return shared herdr config bytes, merged with the overlay's
    herdr.<profile>.toml and then herdr.<profile>.<platform>.toml."""
    import tomllib

    shared = (repo / "herdr/config.toml").read_bytes()
    extras = [
        path
        for path in (
            overlay / f"herdr.{profile}.toml",
            overlay / f"herdr.{profile}.{platform}.toml",
        )
        if path.is_file()
    ] if overlay else []
    if not extras:
        return shared
    merged = tomllib.loads(shared.decode("utf-8"))
    for extra in extras:
        try:
            merged = merge_tables(merged, tomllib.loads(extra.read_text(encoding="utf-8")))
        except tomllib.TOMLDecodeError as error:
            raise ValueError(f"invalid TOML in {extra}: {error}") from error
    return (toml_dump(merged) + "\n").encode()


def check_home_integrations(installer, powershell_profile=None):
    """Review external registrations when leaving home or changing overlays."""
    leaving_home = installer.profile == "work" and installer.old.get("profile") == "home"
    overlay = installer.old.get("overlay")
    if not leaving_home and (
        not overlay
        or (overlay == str(installer.overlay) and installer.old.get("profile") == installer.profile)
    ):
        return
    import tomllib

    for relative, keys in (
        (".claude.json", ("mcpServers",)),
        (".copilot/mcp-config.json", ("mcpServers",)),
        (".codex/config.toml", ("mcp_servers", "plugins")),
    ):
        path = installer.target(relative)
        if not leaving_home or not path.exists():
            continue
        config = (
            tomllib.loads(path.read_text(encoding="utf-8"))
            if path.suffix == ".toml"
            else read_json(path)
        )
        if any(config.get(key) for key in keys):
            raise ValueError(
                f"review and remove personal MCP/plugin registrations before switching to work: {path}"
            )
    if overlay:
        startup = [
            installer.home / name
            for name in (
                ".zshenv",
                ".zprofile",
                ".zlogin",
                ".zshrc",
                ".bash_profile",
                ".bash_login",
                ".profile",
                ".bashrc",
            )
        ]
        profile_dirs = [
            installer.home / "Documents" / name
            for name in ("PowerShell", "WindowsPowerShell")
        ]
        if powershell_profile:
            profile_dirs.append(Path(powershell_profile).parent)
        for directory in profile_dirs:
            startup.extend(directory.glob("*profile.ps1"))
        for path in startup:
            if not path.is_file():
                continue
            outside = re.sub(
                r"# dotfiles:begin\n.*?\n# dotfiles:end",
                "",
                path.read_text(encoding="utf-8-sig"),
                flags=re.DOTALL,
            )
            if overlay.replace(chr(92), "/") in outside.replace(chr(92), "/"):
                raise ValueError(
                    f"review remaining reference to private configuration: {path}"
                )
        for directory in ("Library/LaunchAgents", ".config/systemd/user"):
            root = installer.home / directory
            for path in root.rglob("*") if root.exists() else ():
                if path.is_file() and overlay.encode() in path.read_bytes():
                    raise ValueError(
                        f"stop and disable the previous overlay service before switching: {path}"
                    )


def relink_hooks(repo, overlay, platform, recorded):
    """Return the post-merge hooks to install, keyed by path.

    The overlay gets the hook too, since it may hold this checkout as a
    submodule. The hook always exits 0 so a pull never fails.
    """
    script = repo / ("link.ps1" if platform == "windows" else "link.sh")
    command = ("pwsh -NoProfile -File " if platform == "windows" else "sh ") + (
        shlex.quote(str(script).replace(chr(92), "/"))
    )
    relink = (
        f"{command} || echo "
        + shlex.quote(f"dotfiles: relink failed; fix the error above, then run: {command}")
        + " >&2\nexit 0\n"
    )
    # A hook's GIT_DIR would point rev-parse at the repository being pulled.
    git_env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    hooks = {}
    for checkout in (repo, overlay):
        if not checkout or not (checkout / ".git").exists():
            continue
        hook = f"#!/bin/sh\n# {HOOK_MARK}\n"
        if checkout != repo and repo.is_relative_to(checkout):
            inner = repo.relative_to(checkout).as_posix()
            entry = subprocess.run(
                ["git", "--literal-pathspecs", "-C", str(checkout), "ls-files", "--stage", "--", inner],
                capture_output=True, text=True, env=git_env, check=False,
            ).stdout
            if entry.startswith("160000 "):
                # A pull runs this hook before it updates submodules, so the
                # relink would use the old copy. A submodule on a branch is
                # being worked on and is left alone.
                top, sub = (shlex.quote(str(p).replace(chr(92), "/")) for p in (checkout, repo))
                hook += (
                    "(unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE\n"
                    f" git -C {sub} symbolic-ref -q HEAD >/dev/null ||\n"
                    f" git --literal-pathspecs -C {top} submodule update --init -- {shlex.quote(inner)}) || echo "
                    + shlex.quote(f"dotfiles: could not update {inner}; relinking with the copy on disk")
                    + " >&2\n"
                )
        hook += relink
        found = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "--git-path", "hooks/post-merge"],
            capture_output=True, text=True, env=git_env, check=False,
        )
        raw = checkout / found.stdout.strip()
        path = raw.resolve()
        shared_dir = subprocess.run(
            ["git", "-C", str(checkout), "config", "--get", "core.hooksPath"],
            capture_output=True, text=True, env=git_env, check=False,
        ).stdout.strip()
        if shared_dir:
            # That folder serves other repositories too; a hook there would
            # relink after their pulls.
            print(
                f"warning: no relink hook for {checkout}: core.hooksPath is set to {shared_dir}",
                file=sys.stderr,
            )
        elif found.returncode:
            print(f"warning: no relink hook for {checkout}: {found.stderr.strip()}", file=sys.stderr)
        elif raw.is_symlink() or (
            path.exists()
            and path.read_bytes() != hook.encode()
            and digest(path.read_bytes()) != recorded.get(str(path))
        ):
            # Ours only while it is byte for byte what a link wrote: a symlink,
            # another tool's hook, or our hook edited since is the user's.
            print(
                f"warning: keeping {path}, which dotfiles did not write or which changed since; "
                f"pulls in {checkout} will not relink",
                file=sys.stderr,
            )
        else:
            hooks[str(path)] = hook
    return hooks


def plan(
    repo,
    home,
    profile,
    platform,
    overlay=None,
    powershell_profile=None,
    wezterm_user_config=None,
    copilot_home=None,
):
    additions = configured_files(overlay, platform, profile) if overlay else {}
    installer = Installer(
        repo, home, profile, platform, overlay, copilot_home=copilot_home
    )
    check_home_integrations(installer, powershell_profile)
    previous = home / ".dotfiles-env"
    if (
        profile == "work"
        and previous.exists()
        and previous.read_text(encoding="utf-8").strip() == "home"
        and not installer.state_path.exists()
    ):
        raise ValueError(
            "legacy home installation has no ownership record; migrate with the private home installer and review personal MCP/services before switching to work"
        )
    for relative, source in configured_files(repo, platform).items():
        installer.file(relative, source)
    # Herdr reads %APPDATA%\herdr on Windows and ~/.config/herdr elsewhere. It
    # writes its own config from Settings, so this is always a copy, never a link,
    # and a config herdr rewrote skips only herdr instead of stopping the link.
    installer.file(
        "AppData/Roaming/herdr/config.toml"
        if platform == "windows"
        else ".config/herdr/config.toml",
        "herdr/config.toml",
        data=herdr_config(repo, overlay, profile, platform),
        skip_unowned=True,
    )
    git = "[include]\n\tpath = " + json.dumps(
        str(repo / "gitconfig").replace(chr(92), "/")
    )
    if overlay and (overlay / f"gitconfig.{profile}").is_file():
        git += "\n[include]\n\tpath = " + json.dumps(
            str(overlay / f"gitconfig.{profile}").replace(chr(92), "/")
        )
    installer.block(".gitconfig", git, legacy_source="gitconfig")
    rules = (repo / "agents/core.md").read_text(encoding="utf-8")
    if profile == "work":
        rules += "\n" + (repo / "agents/work.md").read_text(encoding="utf-8")
    rules += overlay_rules(overlay, f"{profile}.md")
    installer.block(".codex/AGENTS.md", rules, markdown=True)
    installer.retire_home_agents()
    # Claude-only mechanics (its tools, hooks, permission modes) stay out of
    # the harness-neutral files above.
    claude_rules = rules + overlay_rules(overlay, f"{profile}.claude.md")
    installer.block(".claude/CLAUDE.md", claude_rules, markdown=True)
    installer.block(
        ".copilot/copilot-instructions.md",
        (repo / "copilot/copilot-instructions.md").read_text(encoding="utf-8") + "\n" + rules,
        markdown=True,
    )

    if platform == "windows":
        if profile == "work":
            for configured_terminal in (
                wezterm_user_config,
                os.environ.get("WEZTERM_CONFIG_FILE"),
            ):
                if (
                    configured_terminal
                    and Path(configured_terminal).resolve()
                    != (repo / "wezterm.lua").resolve()
                ):
                    raise ValueError(
                        "review and remove the existing WEZTERM_CONFIG_FILE environment override before using work"
                    )
        # The entry script supplies PowerShell's actual CurrentUserCurrentHost path.
        if not powershell_profile:
            raise ValueError("Windows requires --powershell-profile from link.ps1")
        relative = str(Path(powershell_profile).relative_to(home))
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        content = f"$env:DOTFILES_ENV = '{profile}'\n"
        content += (
            f"$env:DOTFILES_PRIVATE_DIR = {quote(overlay)}\n"
            if overlay
            else "Remove-Item Env:DOTFILES_PRIVATE_DIR -ErrorAction SilentlyContinue\n"
        )
        content += f"$env:STARSHIP_CONFIG = {quote(repo / 'starship.toml')}\n"
        content += f"$env:WEZTERM_CONFIG_FILE = {quote(repo / 'wezterm.lua')}\n"
        content += f". {quote(repo / 'powershell/Microsoft.PowerShell_profile.ps1')}"
        if overlay and (overlay / f"powershell/profile.{profile}.ps1").is_file():
            content += f"\n. {quote(overlay / f'powershell/profile.{profile}.ps1')}"
        installer.block(relative, content)
        # WezTerm loads this without depending on a shell's environment.
        installer.block(
            ".wezterm.lua",
            f"return dofile({json.dumps(str(repo / 'wezterm.lua').replace(chr(92), '/'))})",
            lua=True,
        )
    else:
        exports = f"export DOTFILES_ENV={shlex.quote(profile)}\n"
        if overlay:
            exports += f"export DOTFILES_PRIVATE_DIR={shlex.quote(str(overlay))}\n"
        else:
            exports += "unset DOTFILES_PRIVATE_DIR\n"
        zsh = exports + f". {shlex.quote(str(repo / 'zshrc'))}"
        bash = exports + f". {shlex.quote(str(repo / 'copilot/shell-defaults.sh'))}"
        if overlay and (overlay / f"zshrc.{profile}").is_file():
            zsh += f"\n. {shlex.quote(str(overlay / f'zshrc.{profile}'))}"
        if overlay and (overlay / f"bashrc.{profile}").is_file():
            bash += f"\n. {shlex.quote(str(overlay / f'bashrc.{profile}'))}"
        installer.block(
            ".zshrc",
            zsh,
            legacy_source="zshrc",
        )
        installer.block(
            ".bashrc",
            bash,
        )
        tmux = "source-file " + json.dumps(str(repo / "tmux.conf"))
        if overlay and (overlay / f"tmux.{profile}.conf").is_file():
            tmux += "\nsource-file " + json.dumps(str(overlay / f"tmux.{profile}.conf"))
        installer.block(".tmux.conf", tmux, legacy_source="tmux.conf")

    spec = importlib.util.spec_from_file_location(
        "copilot_settings", repo / "bin/apply-copilot-settings.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    settings_path = installer.target(".copilot/settings.json")
    if settings_path.is_symlink():
        raise ValueError(f"preserving symlinked Copilot settings: {settings_path}")
    settings = read_json(settings_path, {})
    subagents = read_json(overlay / "copilot/subagents.json", {}) if overlay else {}
    agents = settings.get("subagents", {}).get("agents", {})
    for name, fields in installer.old.get("copilot_subagents", {}).items():
        for field, value in fields.items():
            if field in subagents.get(name, {}):
                continue
            if agents.get(name, {}).get(field) != value:
                raise ValueError(
                    f"review modified personal Copilot subagent before profile switch: {name}.{field}"
                )
            del agents[name][field]
        if name in agents and not agents[name]:
            del agents[name]
    installer.subagents = subagents
    updated = module.build_settings(
        settings,
        subagents,
        str(home),
        "windows" if platform == "windows" else "unix",
        copilot_home=str(installer.copilot_home),
        sensitive_paths=(
            module.load_sensitive(overlay / "copilot/sensitive.json")["paths"]
            if overlay
            else ()
        ),
    )
    updated["footer"]["showUsername"] = True
    data = (json.dumps(updated, indent=2) + "\n").encode()
    # Copilot settings are a merge, not a file owned wholesale by this installer.
    if not settings_path.exists() or settings_path.read_bytes() != data:
        installer.changes.append((".copilot/settings.json", "bytes", data))
    # Additions cannot replace shared files, generated blocks, or ownership state.
    for relative, source in additions.items():
        installer.file(relative, source, overlay)
    installer.hooks = relink_hooks(repo, overlay, platform, installer.old.get("hooks", {}))
    return installer


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("work", "home"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--overlay", type=Path)
    parser.add_argument("--powershell-profile")
    parser.add_argument("--wezterm-user-config")
    args = parser.parse_args(argv)
    try:
        home = Path.home()
        copilot_home = Path(os.environ.get("COPILOT_HOME", home / ".copilot"))
        # Without --profile, rerun with what the last link recorded.
        state = read_json(home / ".config/dotfiles/state.json", {})
        if not args.profile and isinstance(state, dict) and state.get("profile"):
            args.profile = state["profile"]
            if not args.overlay and state.get("overlay"):
                args.overlay = Path(state["overlay"])
                if not args.overlay.is_dir():
                    raise ValueError(
                        f"recorded overlay no longer exists: {args.overlay}; restore "
                        "it, or relink with --profile and the --overlay to use"
                    )
        profile = resolve_profile(args.profile, home)
        platform = "windows" if os.name == "nt" else sys.platform
        installer = plan(
            Path(__file__).resolve().parent,
            home,
            profile,
            platform,
            args.overlay.resolve() if args.overlay else None,
            args.powershell_profile,
            args.wezterm_user_config,
            copilot_home,
        )
        installer.apply(args.dry_run)
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.exit(1, f"link: {error}\n")


if __name__ == "__main__":
    main()
