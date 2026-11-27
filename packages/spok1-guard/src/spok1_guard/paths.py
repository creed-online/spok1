"""Path rules: where agents may write freely, what is protected, what is secret.

Paths are normalised to a comparable string form that works for Windows and POSIX
paths on any OS (so the tests run identically on Linux CI and the Windows laptop):
    C:\\Users\\A\\x  →  c:/users/a/x      /c/Users/A/x (Git Bash) → c:/users/a/x
"""

from __future__ import annotations

import fnmatch
import posixpath
import re

from spok1_guard.policy import Policy

_GITBASH_DRIVE = re.compile(r"^/([a-zA-Z])(/|$)")
_WIN_DRIVE = re.compile(r"^([a-zA-Z]):(/|$)")


def norm(path: str, cwd: str = "", home: str = "") -> str:
    """Normalise a path for comparison. Relative paths resolve against `cwd`."""
    p = path.strip().strip("\"'").replace("\\", "/")
    if p.startswith("~") and home and (len(p) == 1 or p[1] == "/"):
        p = home.replace("\\", "/") + p[1:]
    m = _GITBASH_DRIVE.match(p)
    if m:
        p = f"{m.group(1)}:/{p[m.end() :]}"
    is_abs = p.startswith("/") or bool(_WIN_DRIVE.match(p))
    if not is_abs and cwd:
        p = norm(cwd, home=home).rstrip("/") + "/" + p
    windows = bool(_WIN_DRIVE.match(p))
    if windows:
        drive, rest = p[:2], p[2:] or "/"
        p = drive.lower() + posixpath.normpath(rest)
        p = p.lower()  # Windows paths are case-insensitive
    else:
        p = posixpath.normpath(p) if p else p
    return p


def is_within(path: str, root: str) -> bool:
    root = root.rstrip("/")
    return path == root or path.startswith(root + "/")


SECRET_NAME_PATTERNS = (
    ".env",
    "*.env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa*",
    "id_ed25519*",
    "id_ecdsa*",
    ".credentials.json",
    "credentials.json",
    "*credentials*.json",
    "*secret*.json",
    "*token*.json",
    "service-account*.json",
    ".git-credentials",
    ".netrc",
    "hosts.yml",  # gh CLI token store
)
SECRET_NAME_EXCEPTIONS = (".env.example", "*.env.example", ".env.sample", ".env.template")
SECRET_DIRS = ("~/.ssh", "~/.aws", "~/.azure", "~/.gnupg", "~/.config/gh")


class PathContext:
    def __init__(
        self,
        cwd: str,
        home: str,
        temp_dirs: list[str] | None = None,
        policy: Policy | None = None,
        policy_path: str = "",
        guard_dir: str = "",
        managed_settings: str = "",
    ) -> None:
        self.cwd = cwd
        self.home = home
        self.temp_dirs = temp_dirs or []
        self.policy = policy or Policy()
        self.policy_path = policy_path
        self.guard_dir = guard_dir
        self.managed_settings = managed_settings

    def n(self, path: str) -> str:
        return norm(path, self.cwd, self.home)

    # ── roots ──────────────────────────────────────────────────────────

    def worktree_root(self) -> str | None:
        """The agent worktree the session runs in, if any (`…/.claude/worktrees/<name>`)."""
        c = self.n(self.cwd)
        marker = "/.claude/worktrees/"
        idx = c.find(marker)
        if idx == -1:
            return None
        rest = c[idx + len(marker) :]
        name = rest.split("/", 1)[0]
        return c[: idx + len(marker)] + name if name else None

    def scratch_roots(self) -> list[str]:
        roots = [self.n(t) for t in self.temp_dirs if t]
        roots += [self.n(r) for r in self.policy.extra_roots]
        return roots

    def project_root(self) -> str:
        return self.n(self.cwd)

    def brain_append_roots(self) -> list[str]:
        """Brain paths any session may add to (PRD §4: inbox/, jobs/, feedback.md)."""
        brain = self.n("~/brain")
        return [
            brain + "/inbox",
            brain + "/jobs",
            brain + "/feedback.md",
            self.n("~/.spok1/plans"),  # the manager's plan files (run-request step 3)
        ]

    def in_worktree(self, path: str) -> bool:
        """Inside an agent worktree: the session's own, or any `<repo>/.claude/worktrees/<name>/`
        (coders create worktrees inside the target repo, which may differ from the session cwd)."""
        p = self.n(path)
        wt = self.worktree_root()
        if wt and is_within(p, wt):
            return True
        marker = "/.claude/worktrees/"
        idx = p.find(marker)
        if idx == -1:
            return False
        name = p[idx + len(marker) :].split("/", 1)[0]
        return bool(name) and name not in (".", "..")

    def in_scratch(self, path: str) -> bool:
        p = self.n(path)
        return any(is_within(p, r) for r in self.scratch_roots())

    def in_project(self, path: str) -> bool:
        return is_within(self.n(path), self.project_root())

    # ── protected and secret ──────────────────────────────────────────

    def protected_paths(self) -> list[str]:
        items = [
            "~/.claude/settings.json",
            "~/.claude/settings.local.json",
            "~/.claude.json",
            "~/.claude/hooks",
            "~/brain/guardrails.yaml",
            "~/brain/guardrails.toml",
            "~/brain/grants.yaml",
        ]
        items += [self.policy_path, self.guard_dir, self.managed_settings]
        items += self.policy.extra_protected
        return [self.n(i) for i in items if i]

    def is_protected(self, path: str) -> bool:
        p = self.n(path)
        if any(is_within(p, prot) for prot in self.protected_paths()):
            return True
        name = posixpath.basename(p)
        parent = posixpath.basename(posixpath.dirname(p))
        # any project's Claude Code settings, and managed settings anywhere
        if parent == ".claude" and name in ("settings.json", "settings.local.json"):
            return True
        return name in ("managed-settings.json", "guardrails.toml", "grants.toml")

    def is_secret(self, path: str) -> bool:
        p = self.n(path)
        name = posixpath.basename(p)
        if any(fnmatch.fnmatch(name, pat) for pat in SECRET_NAME_EXCEPTIONS):
            return False
        if any(fnmatch.fnmatch(name, pat) for pat in SECRET_NAME_PATTERNS):
            return True
        if is_within(p, self.n("~/.config/agent")) and name.startswith(".env"):
            return True
        dirs = [self.n(d) for d in SECRET_DIRS] + [self.n(s) for s in self.policy.extra_secrets]
        return any(is_within(p, d) for d in dirs)

    def is_brain_curated(self, path: str) -> bool:
        """Brain files only the VM's learning jobs (or approved T2 actions) change."""
        p = self.n(path)
        brain = self.n("~/brain")
        if not is_within(p, brain) or p == brain:
            return False
        return not any(is_within(p, r) for r in self.brain_append_roots())
