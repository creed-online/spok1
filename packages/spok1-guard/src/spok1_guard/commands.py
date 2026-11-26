"""Classify shell and PowerShell commands into risk tiers.

Principles (PRD §6.1):
- Unclear → one tier higher; can't classify → T2; any deletion → T3 (outside a worktree
  or scratch dir); touching the guardrails → FORBIDDEN; printing secrets → FORBIDDEN.
- Commands are classified after unwrapping (sudo, env, timeout, xargs, bash -c, pwsh -c,
  full program paths), so `bash -c 'rm -rf /'` or `/usr/bin/git -C . push -f` don't slip by.
"""

from __future__ import annotations

import posixpath
import re

from spok1_guard import pwsh, shell, sql
from spok1_guard.paths import PathContext, norm
from spok1_guard.tiers import T0_OK, Tier, Verdict, v, worst

MAX_DEPTH = 6
FORBIDDEN_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
_ENV_ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.DOTALL)
_API_KEY_SET = re.compile(
    r"(\$env:|env:|setx\s+|SetEnvironmentVariable\s*\(\s*['\"]|export\s+|set\s+)"
    r"(ANTHROPIC_API_KEY|ANTHROPIC_AUTH_TOKEN)",
    re.IGNORECASE,
)


class Ctx:
    __slots__ = ("cwd", "depth", "notes", "paths")

    def __init__(
        self, paths: PathContext, cwd: str, depth: int = 0, notes: list[str] | None = None
    ) -> None:
        self.paths = paths
        self.cwd = cwd
        self.depth = depth
        self.notes = notes if notes is not None else []

    def child(self) -> Ctx:
        return Ctx(self.paths, self.cwd, self.depth + 1, self.notes)


def _hostname(url: str) -> str:
    from urllib.parse import urlparse  # only when classifying a URL (saves ~7 ms per call)

    return (urlparse(url if "://" in url else "https://" + url).hostname or "").lower()


# ── path helpers ───────────────────────────────────────────────────────

_VAR = re.compile(r"\$env:([A-Za-z_][A-Za-z0-9_]*)|\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?|%([A-Za-z_]+)%")


def expand(word: str, ctx: Ctx) -> str | None:
    """Expand ~ and home/temp variables. None if an unknown variable remains."""
    home = ctx.paths.home

    def repl(m: re.Match[str]) -> str:
        name = (m.group(1) or m.group(2) or m.group(3) or "").upper()
        if name in ("HOME", "USERPROFILE"):
            return home
        if name in ("TMP", "TEMP", "TMPDIR") and ctx.paths.temp_dirs:
            return ctx.paths.temp_dirs[0]
        if name == "PWD":
            return ctx.cwd
        return "\x00"

    out = _VAR.sub(repl, word)
    if "\x00" in out or "__SUBST__" in out or "__SUBEXPR__" in out:
        return None
    return out


def _resolved(word: str, ctx: Ctx) -> str | None:
    expanded = expand(word, ctx)
    if expanded is None:
        return None
    return norm(expanded, ctx.cwd, ctx.paths.home)


def read_target(word: str, ctx: Ctx) -> Verdict:
    path = _resolved(word, ctx)
    if path is not None and ctx.paths.is_secret(path):
        return v(Tier.FORBIDDEN, f"reads a secret file ({posixpath.basename(path)})", "secret.read")
    return T0_OK


def write_target(word: str, ctx: Ctx, what: str = "writes") -> Verdict:
    if word in ("/dev/null", "nul", "NUL", "$null", "/dev/stdout", "/dev/stderr"):
        return T0_OK
    path = _resolved(word, ctx)
    if path is None:
        return v(Tier.T2, f"{what} to a path that can't be resolved ({word})", "path.unknown")
    p = ctx.paths
    if p.is_protected(path):
        return v(
            Tier.FORBIDDEN, f"{what} a protected file ({path}): guardrails/settings", "protected"
        )
    if p.is_secret(path):
        return v(
            Tier.FORBIDDEN, f"{what} a secret file ({posixpath.basename(path)})", "secret.write"
        )
    if p.is_brain_curated(path):
        return v(Tier.T2, f"{what} a curated brain file ({path})", "brain.curated")
    if p.in_worktree(path) or p.in_scratch(path) or p.in_project(path):
        return T0_OK
    if any(path == r or path.startswith(r + "/") for r in (p.n(x) for x in p.brain_append_roots())):
        return T0_OK
    return v(Tier.T2, f"{what} outside the project ({path})", "path.outside")


def delete_target(word: str, ctx: Ctx, what: str = "deletes") -> Verdict:
    path = _resolved(word, ctx)
    if path is None:
        return v(Tier.T3, f"{what} a path that can't be resolved ({word})", "delete.unknown")
    p = ctx.paths
    if p.is_protected(path):
        return v(Tier.FORBIDDEN, f"{what} a protected file ({path})", "protected")
    if path in ("/", p.n("~"), p.n("~/brain")) or re.fullmatch(r"[a-z]:/?", path):
        return v(Tier.T3, f"{what} a root or home directory ({path})", "delete.root")
    if p.in_worktree(path) or p.in_scratch(path):
        return T0_OK
    return v(Tier.T3, f"{what} outside an agent worktree ({path})", "delete.outside")


def _program(word: str) -> str:
    name = posixpath.basename(word.replace("\\", "/")).lower()
    for ext in (".exe", ".cmd", ".bat", ".ps1", ".com"):
        if name.endswith(ext):
            name = name[: -len(ext)]
    return name


def _args(argv: list[str]) -> list[str]:
    return [a for a in argv[1:] if not a.startswith("-")]


def _has(argv: list[str], *flags: str) -> bool:
    for a in argv[1:]:
        if a in flags:
            return True
        if a.startswith("-") and not a.startswith("--"):  # combined short flags: -rf
            letters = set(a[1:])
            for f in flags:
                if len(f) == 2 and f.startswith("-") and f[1] in letters:
                    return True
        for f in flags:
            if f.startswith("--") and a.startswith(f + "="):
                return True
    return False


def _opt_value(argv: list[str], *names: str) -> str | None:
    for i, a in enumerate(argv):
        for name in names:
            if a == name and i + 1 < len(argv):
                return argv[i + 1]
            if a.startswith(name + "=") and name.startswith("--"):
                return a.split("=", 1)[1]
            if (
                name.startswith("-")
                and not name.startswith("--")
                and a.startswith(name)
                and len(a) > len(name)
            ):
                return a[len(name) :]
    return None


# ── shells ─────────────────────────────────────────────────────────────


def classify_bash(command: str, ctx: Ctx) -> Verdict:
    if ctx.depth > MAX_DEPTH:
        return v(Tier.T2, "nesting too deep to classify", "depth")
    if _API_KEY_SET.search(command):
        return v(Tier.FORBIDDEN, "sets ANTHROPIC_API_KEY (would bill the API, not Max)", "apikey")
    try:
        parsed = shell.parse(command)
    except shell.ParseError as exc:
        return v(Tier.T2, f"couldn't parse the shell command ({exc})", "parse")
    verdicts: list[Verdict] = [classify_bash(n, ctx.child()) for n in parsed.nested]
    local = Ctx(ctx.paths, ctx.cwd, ctx.depth, ctx.notes)
    for cmd in parsed.commands:
        for target in cmd.writes:
            verdicts.append(write_target(target, local, "redirects output"))
        for source in cmd.reads:
            verdicts.append(read_target(source, local))
        if cmd.argv:
            verdicts.append(classify_argv(cmd.argv, local, cmd))
            if _program(cmd.argv[0]) in ("cd", "pushd") and len(cmd.argv) > 1:
                new = _resolved(cmd.argv[1], local)
                if new:
                    local.cwd = new
    return worst(verdicts)


def classify_pwsh(script: str, ctx: Ctx) -> Verdict:
    if ctx.depth > MAX_DEPTH:
        return v(Tier.T2, "nesting too deep to classify", "depth")
    if _API_KEY_SET.search(script):
        return v(Tier.FORBIDDEN, "sets ANTHROPIC_API_KEY (would bill the API, not Max)", "apikey")
    try:
        parsed = pwsh.parse(script)
    except pwsh.ParseError as exc:
        return v(Tier.T2, f"couldn't parse the PowerShell command ({exc})", "parse")
    verdicts: list[Verdict] = [classify_pwsh(n, ctx.child()) for n in parsed.nested]
    local = Ctx(ctx.paths, ctx.cwd, ctx.depth, ctx.notes)
    for st in parsed.statements:
        for target in st.writes:
            verdicts.append(write_target(target, local, "redirects output"))
        if st.words:
            verdicts.append(classify_ps_statement(st, local))
    return worst(verdicts)


# ── native programs ────────────────────────────────────────────────────

SAFE_READERS = {
    "ls",
    "dir",
    "cat",
    "head",
    "tail",
    "less",
    "more",
    "grep",
    "egrep",
    "fgrep",
    "rg",
    "ag",
    "wc",
    "sort",
    "uniq",
    "cut",
    "tr",
    "echo",
    "printf",
    "pwd",
    "which",
    "where",
    "whoami",
    "type",
    "file",
    "stat",
    "du",
    "df",
    "date",
    "basename",
    "dirname",
    "realpath",
    "readlink",
    "tree",
    "diff",
    "cmp",
    "md5sum",
    "sha1sum",
    "sha256sum",
    "shasum",
    "jq",
    "yq",
    "true",
    "false",
    "test",
    "[",
    "sleep",
    "uname",
    "hostname",
    "id",
    "ps",
    "column",
    "xxd",
    "od",
    "hexdump",
    "tac",
    "nl",
    "fold",
    "paste",
    "join",
    "comm",
    "seq",
    "cd",
    "pushd",
    "popd",
    "fc",
    "history",
    "lsof",
    "free",
    "uptime",
    "nproc",
    "getconf",
    "locale",
    "tput",
    "clear",
    "systeminfo",
    "ver",
    "tasklist",
    "ipconfig",
    "ping",
    "nslookup",
    "dig",
    "host",
}
TEST_TOOLS = {
    "pytest",
    "ruff",
    "pyright",
    "mypy",
    "black",
    "isort",
    "flake8",
    "pylint",
    "eslint",
    "prettier",
    "tsc",
    "jest",
    "vitest",
    "mocha",
    "playwright",
    "cargo-test",
    "golangci-lint",
}
INTERPRETERS = {"python", "python3", "py", "node", "deno", "ruby", "perl", "php", "lua", "bun"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish"}
POWERSHELLS = {"pwsh", "powershell"}
WRAPPERS = {"timeout", "time", "nice", "nohup", "stdbuf", "command", "builtin", "exec", "noglob"}


def _unwrap(argv: list[str]) -> tuple[list[str], bool, Verdict | None]:
    """Strip env assignments and wrappers. Returns (argv, used_sudo, forbidden_verdict)."""
    used_sudo = False
    i = 0
    while i < len(argv):
        word = argv[i]
        m = _ENV_ASSIGN.match(word)
        if m:
            if m.group(1).upper() in FORBIDDEN_ENV:
                return argv, used_sudo, v(Tier.FORBIDDEN, f"sets {m.group(1)}", "apikey")
            i += 1
            continue
        prog = _program(word)
        if prog in ("sudo", "doas", "runas", "gsudo"):
            used_sudo = True
            i += 1
            while i < len(argv) and argv[i].startswith("-"):
                i += 2 if argv[i] in ("-u", "-g", "-C", "-D") else 1
            continue
        if prog == "env":
            if i + 1 >= len(argv):
                break  # bare `env`: prints the environment
            i += 1
            while i < len(argv) and (argv[i].startswith("-") or _ENV_ASSIGN.match(argv[i])):
                m2 = _ENV_ASSIGN.match(argv[i])
                if m2 and m2.group(1).upper() in FORBIDDEN_ENV:
                    return argv, used_sudo, v(Tier.FORBIDDEN, f"sets {m2.group(1)}", "apikey")
                i += 1
            continue
        if prog in WRAPPERS:
            i += 1
            if prog == "timeout":
                while i < len(argv) and argv[i].startswith("-"):
                    i += 1
                i += 1  # duration
            elif prog == "nice" and i < len(argv) and argv[i] == "-n":
                i += 2
            while i < len(argv) and argv[i].startswith("-") and prog in ("stdbuf", "nice", "time"):
                i += 1
            continue
        break
    return argv[i:], used_sudo, None


def classify_argv(argv_in: list[str], ctx: Ctx, cmd: shell.SimpleCommand | None = None) -> Verdict:
    argv, used_sudo, forbidden = _unwrap(argv_in)
    if forbidden:
        return forbidden
    if not argv:
        return T0_OK
    verdict = _classify_program(argv, ctx, cmd)
    if used_sudo:
        verdict = verdict.at_least(Tier.T2, f"sudo: {verdict.reason}", "sudo")
    return verdict


def _classify_program(argv: list[str], ctx: Ctx, cmd: shell.SimpleCommand | None) -> Verdict:
    prog = _program(argv[0])
    piped_in = bool(cmd and cmd.piped_from)
    heredoc = cmd.heredoc if cmd else None

    if prog in ("export", "set", "setx", "declare", "typeset", "local"):
        for a in argv[1:]:
            name = a.split("=", 1)[0].upper()
            if name in FORBIDDEN_ENV:
                return v(Tier.FORBIDDEN, f"sets {name}", "apikey")
        if prog in ("env", "set", "printenv") and len(argv) == 1:
            return v(Tier.T2, "prints all environment variables (may expose secrets)", "env.print")
        return T0_OK if prog != "setx" else v(Tier.T2, "setx changes persistent env", "setx")
    if prog in ("env", "printenv") and len(argv) <= 1:
        return v(Tier.T2, "prints all environment variables (may expose secrets)", "env.print")

    if prog in SAFE_READERS:
        return worst([read_target(a, ctx) for a in _args(argv)])
    if prog == "tee":
        return worst([write_target(a, ctx) for a in _args(argv)])
    if prog == "sed":
        if _has(argv, "-i", "--in-place") or any(a.startswith("-i") for a in argv[1:]):
            files = _args(argv)[1:]
            return worst([write_target(a, ctx, "edits in place") for a in files] or [T0_OK])
        return worst([read_target(a, ctx) for a in _args(argv)[1:]])
    if prog in ("awk", "gawk", "mawk"):
        if "system(" in " ".join(argv) or "| getline" in " ".join(argv):
            return v(Tier.T2, "awk runs shell commands", "awk.system")
        return worst([read_target(a, ctx) for a in _args(argv)[1:]])
    if prog == "find":
        return _classify_find(argv, ctx)
    if prog == "xargs":
        inner = argv[1:]
        while inner and inner[0].startswith("-"):
            inner = inner[2:] if inner[0] in ("-I", "-n", "-P", "-d", "-L", "-s") else inner[1:]
        if not inner:
            return T0_OK
        inner_v = classify_argv([*inner, "__XARGS__"], ctx)
        return inner_v
    if prog in ("rm", "rmdir", "unlink", "shred", "del", "erase", "rd", "trash", "trash-put"):
        targets = [a for a in _args(argv) if a != "--"]
        if not targets:
            return v(Tier.T3, f"{prog} with no explicit targets", "delete.unknown")
        if "__XARGS__" in targets:
            return v(Tier.T3, f"{prog} on targets read from a pipe (xargs)", "delete.unknown")
        return worst([delete_target(t, ctx) for t in targets])
    if prog in ("cp", "install", "ln", "rsync", "scp", "robocopy", "xcopy", "copy"):
        args = _args(argv)
        if prog in ("rsync", "scp") and any(
            ":" in a and not re.match(r"^[a-zA-Z]:", a) for a in args
        ):
            return v(Tier.T2, f"{prog} to/from a remote host", "remote.copy")
        if len(args) < 2:
            return v(Tier.T2, f"{prog} without a clear destination", "copy.unknown")
        verdicts = [read_target(a, ctx) for a in args[:-1]] + [write_target(args[-1], ctx)]
        if prog == "rsync" and _has(argv, "--delete", "--delete-after", "--delete-before"):
            verdicts.append(delete_target(args[-1], ctx, "rsync --delete removes files in"))
        return worst(verdicts)
    if prog in ("mv", "move", "ren", "rename"):
        args = _args(argv)
        if len(args) < 2:
            return v(Tier.T2, f"{prog} without a clear destination", "move.unknown")
        verdicts = [delete_target(a, ctx, "moves away") for a in args[:-1]]
        verdicts.append(write_target(args[-1], ctx))
        return worst(verdicts)
    if prog in ("mkdir", "touch", "md"):
        return worst([write_target(a, ctx) for a in _args(argv)] or [T0_OK])
    if prog in ("chmod", "chown", "chgrp", "attrib", "icacls", "takeown", "setfacl"):
        targets = _args(argv)[1:] if prog in ("chmod", "chown", "chgrp") else _args(argv)
        out: list[Verdict] = []
        for t in targets:
            w = write_target(t, ctx, "changes permissions on")
            out.append(
                w if w.tier >= Tier.T2 else v(Tier.T1, f"{prog} inside the project", "perm.local")
            )
        return worst(out or [v(Tier.T2, f"{prog} without targets", "perm.unknown")])
    if prog == "dd":
        of = next((a[3:] for a in argv if a.startswith("of=")), None)
        if of is None or of.startswith("/dev/"):
            return v(Tier.T3, "dd to a device or unknown output", "dd")
        return write_target(of, ctx)
    if prog.startswith("mkfs") or prog in (
        "fdisk",
        "parted",
        "format",
        "diskpart",
        "wipefs",
        "cipher",
    ):
        return v(Tier.T3, f"{prog} can destroy disks", "disk")
    if prog in ("shutdown", "reboot", "halt", "poweroff", "init"):
        return v(Tier.T3, f"{prog} stops the machine", "power")
    if prog in ("systemctl", "service"):
        sub = _args(argv)[:1]
        if sub and sub[0] in (
            "status",
            "show",
            "list-units",
            "list-timers",
            "is-active",
            "is-enabled",
            "cat",
        ):
            return T0_OK
        return v(Tier.T2, f"{prog} changes services", "service")
    if prog in ("kill", "pkill", "killall", "taskkill", "skill"):
        return v(Tier.T2, f"{prog} stops processes", "kill")
    if prog in ("crontab", "schtasks", "at"):
        if _has(argv, "-l", "/query"):
            return T0_OK
        if _has(argv, "-r", "/delete"):
            return v(Tier.T3, f"{prog} deletes scheduled tasks", "cron.delete")
        return v(Tier.T2, f"{prog} changes scheduled tasks", "cron")

    if prog in SHELLS:
        script = _opt_value(argv, "-c")
        if script is not None:
            return classify_bash(script, ctx.child())
        if heredoc is not None:
            return classify_bash(heredoc, ctx.child())
        if piped_in:
            return v(Tier.T2, f"pipes data into {prog} (runs code from a pipe)", "pipe.shell")
        return v(Tier.T2, f"runs a {prog} script", "script")
    if prog in POWERSHELLS:
        script = _opt_value(argv, "-Command", "-command", "-c", "-C")
        if script is not None:
            idx = argv.index(script) if script in argv else len(argv)
            rest = " ".join(argv[idx:]) if idx < len(argv) else script
            return classify_pwsh(rest, ctx.child())
        if any(a.lower() in ("-encodedcommand", "-enc", "-e", "-ec") for a in argv):
            return v(Tier.T2, "PowerShell -EncodedCommand (opaque)", "pwsh.encoded")
        return v(Tier.T2, "runs a PowerShell script", "script")
    if prog == "cmd":
        rest = _opt_value(argv, "/c", "/C", "/k", "/K")
        if rest is not None:
            idx = argv.index(rest)
            return classify_bash(" ".join(argv[idx:]), ctx.child()).at_least(Tier.T0)
        return v(Tier.T2, "runs cmd.exe", "script")
    if prog in ("eval", "source", "."):
        return v(Tier.T2, f"{prog} runs code that can't be inspected", "eval")

    if prog in INTERPRETERS:
        return _classify_interpreter(prog, argv, ctx, piped_in, heredoc)
    if prog in TEST_TOOLS:
        if prog in ("eslint", "prettier", "black", "isort", "ruff") and any(
            a in ("--fix", "--write", "-w", "format") for a in argv
        ):
            return T0_OK  # rewrites files in the project only
        return T0_OK
    if prog in ("make", "just", "task", "mage"):
        targets = _args(argv)
        if targets and all(
            t in ("test", "tests", "lint", "check", "fmt", "format", "build", "typecheck")
            for t in targets
        ):
            return T0_OK
        return v(Tier.T2, f"{prog} runs build targets that can't be inspected", "make")

    if prog == "git":
        return classify_git(argv, ctx)
    if prog == "gh":
        return classify_gh(argv)
    if prog == "az":
        return classify_az(argv)
    if prog in ("terraform", "tofu"):
        return classify_terraform(argv)
    if prog in ("kubectl", "helm", "oc"):
        return classify_kube(argv)
    if prog in ("docker", "podman", "docker-compose", "nerdctl"):
        return classify_docker(argv)
    if prog in ("npm", "pnpm", "yarn", "npx", "pnpx", "bunx"):
        return classify_js(prog, argv)
    if prog in ("pip", "pip3", "uv", "uvx", "pipx", "poetry", "conda", "mamba"):
        return classify_pypkg(prog, argv, ctx)
    if prog in ("curl", "wget", "http", "https", "httpie", "xh", "aria2c"):
        return classify_http(prog, argv, ctx)
    if prog in ("ssh", "sftp", "mosh", "telnet", "nc", "ncat", "socat"):
        return v(Tier.T2, f"{prog} opens a remote connection", "remote")
    if prog in (
        "sqlite3",
        "psql",
        "mysql",
        "mariadb",
        "mongosh",
        "mongo",
        "redis-cli",
        "sqlcmd",
        "duckdb",
    ):
        return classify_sql_client(prog, argv, heredoc, piped_in)
    if prog == "claude":
        return classify_claude(argv)
    if prog in ("open", "start", "xdg-open", "explorer"):
        return v(Tier.T2, f"{prog} opens an app or URL", "open")
    if prog in ("spok1",):
        sub = _args(argv)[:1]
        if sub and sub[0] in ("version", "validate-brain", "audit-plan", "notify"):
            return T0_OK
        return v(Tier.T2, f"spok1 {sub[0] if sub else ''} changes the setup", "spok1")
    if prog in ("code", "cursor", "subl", "vim", "nvim", "nano", "notepad"):
        return v(Tier.T2, f"{prog} opens an editor", "editor")
    if prog == "__subst__":
        return v(Tier.T2, "runs the output of a command substitution", "subst.exec")
    return v(Tier.T2, f"unrecognised program '{prog}'", "unknown")


def _classify_find(argv: list[str], ctx: Ctx) -> Verdict:
    roots: list[str] = []
    for a in argv[1:]:
        if a.startswith("-") or a in ("(", ")", "!"):
            break
        roots.append(a)
    roots = roots or ["."]
    verdicts = [read_target(r, ctx) for r in roots]
    if "-delete" in argv:
        verdicts += [delete_target(r, ctx, "find -delete removes files under") for r in roots]
    for flag in ("-exec", "-execdir", "-ok", "-okdir"):
        if flag in argv:
            i = argv.index(flag) + 1
            inner: list[str] = []
            while i < len(argv) and argv[i] not in (";", "+", "\\;"):
                inner.append(argv[i])
                i += 1
            inner = [roots[0] if a == "{}" else a for a in inner]
            if inner:
                verdicts.append(classify_argv(inner, ctx))
    for flag in ("-fprint", "-fprintf", "-fls"):
        if flag in argv:
            i = argv.index(flag) + 1
            if i < len(argv):
                verdicts.append(write_target(argv[i], ctx))
    return worst(verdicts)


def _classify_interpreter(
    prog: str, argv: list[str], ctx: Ctx, piped_in: bool, heredoc: str | None
) -> Verdict:
    if _has(argv, "--version", "-V") and len(argv) == 2:
        return T0_OK
    module = _opt_value(argv, "-m")
    if module is not None:
        mod = module.split(".")[0]
        if mod in (
            "pytest",
            "ruff",
            "pyright",
            "mypy",
            "black",
            "isort",
            "unittest",
            "doctest",
            "compileall",
            "py_compile",
            "json.tool",
            "flake8",
            "pylint",
            "spok1_guard",
        ):
            return T0_OK
        if mod == "pip":
            idx = argv.index(module) if module in argv else 0
            return classify_pypkg("pip", ["pip", *argv[idx + 1 :]], ctx)
        if mod == "http":
            return v(Tier.T2, "python -m http.server serves files over the network", "serve")
        if mod in ("venv", "virtualenv"):
            return T0_OK
        if mod == "spok1_cli":
            return classify_argv(["spok1", *argv[argv.index(module) + 1 :]], ctx)
        return v(Tier.T2, f"runs python module {module}", "interp.module")
    if _opt_value(argv, "-c", "-e", "--eval", "-p", "--print") is not None:
        return v(Tier.T2, f"runs inline {prog} code", "interp.inline")
    if heredoc is not None or (len(argv) > 1 and argv[1] == "-") or (piped_in and len(argv) == 1):
        return v(Tier.T2, f"runs {prog} code from stdin", "interp.stdin")
    if len(argv) == 1:
        return v(Tier.T2, f"starts an interactive {prog}", "interp.repl")
    return v(
        Tier.T2, f"runs a {prog} script ({_args(argv)[0] if _args(argv) else ''})", "interp.script"
    )


# ── git ────────────────────────────────────────────────────────────────

GIT_READ = {
    "status",
    "log",
    "diff",
    "show",
    "blame",
    "rev-parse",
    "ls-files",
    "ls-tree",
    "describe",
    "shortlog",
    "grep",
    "fetch",
    "help",
    "version",
    "cat-file",
    "merge-base",
    "name-rev",
    "for-each-ref",
    "count-objects",
    "check-ignore",
    "show-ref",
    "var",
    "whatchanged",
    "rev-list",
    "range-diff",
    "ls-remote",
    "check-attr",
    "verify-commit",
    "annotate",
}
GIT_LOCAL = {
    "add",
    "commit",
    "switch",
    "merge",
    "pull",
    "apply",
    "am",
    "cherry-pick",
    "revert",
    "mv",
    "notes",
    "init",
    "sparse-checkout",
    "restore",
}


def classify_git(argv: list[str], ctx: Ctx) -> Verdict:
    i = 1
    work_dir = ctx.cwd
    while i < len(argv) and argv[i].startswith("-"):
        if argv[i] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"):
            if argv[i] == "-C" and i + 1 < len(argv):
                work_dir = _resolved(argv[i + 1], ctx) or work_dir
            i += 2
        else:
            i += 1
    if i >= len(argv):
        return T0_OK
    sub = argv[i]
    rest = argv[i:]

    def has(*flags: str) -> bool:
        return _has(rest, *flags)

    if sub in GIT_READ:
        return T0_OK
    if sub == "config":
        if (
            has("--get", "--get-all", "--list", "-l", "--get-regexp", "--show-origin")
            or len(_args(rest)) <= 1
        ):
            return T0_OK
        return v(Tier.T2, "git config changes settings", "git.config")
    if sub == "branch":
        if has("-d", "-D", "--delete"):
            return v(Tier.T3, "git branch delete", "git.branch.delete")
        if has(
            "-m", "-M", "--move", "-c", "-C", "--copy", "-f", "--force", "-u", "--set-upstream-to"
        ):
            return v(Tier.T1, "git branch rename/copy/upstream", "git.branch")
        return T0_OK if not _args(rest) else v(Tier.T1, "git branch create", "git.branch")
    if sub == "tag":
        if has("-d", "--delete"):
            return v(Tier.T3, "git tag delete", "git.tag.delete")
        return (
            T0_OK
            if not _args(rest) and not has("-a", "-s", "-m")
            else v(Tier.T1, "git tag create", "git.tag")
        )
    if sub == "stash":
        action = _args(rest)[:1]
        if action and action[0] in ("drop", "clear"):
            return v(Tier.T3, f"git stash {action[0]} deletes stashed work", "git.stash.delete")
        if action and action[0] in ("list", "show"):
            return T0_OK
        return v(Tier.T1, "git stash", "git.stash")
    if sub == "push":
        refs = _args(rest)
        if has("-f", "--force", "--force-with-lease", "--force-if-includes", "--mirror") or any(
            r.startswith("+") for r in refs
        ):
            return v(Tier.T3, "git push --force rewrites remote history", "git.push.force")
        if has("-d", "--delete", "--prune") or any(r.startswith(":") for r in refs):
            return v(Tier.T3, "git push deletes a remote branch or tag", "git.push.delete")
        return v(Tier.T2, "git push (outward-facing)", "git.push")
    if sub == "reset":
        if has("--hard", "--merge", "--keep"):
            return v(Tier.T3, "git reset --hard discards work", "git.reset.hard")
        return v(Tier.T1, "git reset (soft/mixed)", "git.reset")
    if sub == "clean":
        if has("-n", "--dry-run"):
            return T0_OK
        return v(Tier.T3, "git clean deletes untracked files", "git.clean")
    if sub == "checkout":
        if "--" in rest or "." in _args(rest) or has("-f", "--force"):
            return v(Tier.T2, "git checkout that discards local changes", "git.checkout.discard")
        return v(Tier.T1, "git checkout", "git.checkout")
    if sub in ("rebase", "filter-branch", "filter-repo", "replace"):
        if sub == "rebase" and has("--abort", "--quit", "--show-current-patch"):
            return v(Tier.T1, "git rebase --abort", "git.rebase.abort")
        return v(Tier.T3, f"git {sub} rewrites history", "git.rewrite")
    if sub == "commit":
        if has("--amend"):
            return v(Tier.T2, "git commit --amend rewrites the last commit", "git.amend")
        return v(Tier.T1, "git commit (local)", "git.commit")
    if sub == "restore":
        if has("--staged", "-S") and not has("--worktree", "-W"):
            return v(Tier.T1, "git restore --staged", "git.restore.staged")
        return v(Tier.T2, "git restore discards local changes", "git.restore")
    if sub == "remote":
        action = _args(rest)[:1]
        if not action or action[0] in ("show", "get-url", "-v"):
            return T0_OK
        if action[0] in ("remove", "rm"):
            return v(Tier.T3, "git remote remove", "git.remote.delete")
        return v(Tier.T2, f"git remote {action[0]}", "git.remote")
    if sub == "worktree":
        action = _args(rest)[:1]
        if action and action[0] == "list":
            return T0_OK
        if action and action[0] in ("remove", "prune"):
            return v(Tier.T3, f"git worktree {action[0]}", "git.worktree.delete")
        return v(Tier.T1, "git worktree add", "git.worktree")
    if sub == "clone":
        args = _args(rest)
        dest = args[1] if len(args) > 1 else None
        if dest:
            w = write_target(dest, Ctx(ctx.paths, work_dir, ctx.depth, ctx.notes), "clones into")
            return w if w.tier > Tier.T0 else T0_OK
        return T0_OK
    if sub in ("gc", "prune", "reflog"):
        if sub == "reflog" and (not _args(rest) or _args(rest)[0] == "show"):
            return T0_OK
        if (
            has("--prune=now", "--aggressive")
            or sub == "prune"
            or "expire" in rest
            or "delete" in rest
        ):
            return v(Tier.T3, f"git {sub} permanently drops objects", "git.gc")
        return v(Tier.T1, f"git {sub}", "git.gc")
    if sub in ("submodule", "lfs", "bisect", "update-ref", "update-index", "symbolic-ref"):
        if sub == "update-ref" and has("-d"):
            return v(Tier.T3, "git update-ref -d deletes a ref", "git.ref.delete")
        return v(Tier.T2, f"git {sub}", "git.other")
    if sub in GIT_LOCAL:
        return v(Tier.T1, f"git {sub} (local)", "git.local")
    return v(Tier.T2, f"unrecognised git command '{sub}'", "git.unknown")


# ── GitHub, Azure, Terraform, Kubernetes, Docker ──────────────────────


def classify_gh(argv: list[str]) -> Verdict:
    args = _args(argv)
    if not args:
        return T0_OK
    group, action = args[0], (args[1] if len(args) > 1 else "")
    if group == "auth" and action == "token":
        return v(Tier.FORBIDDEN, "gh auth token prints a secret", "secret.print")
    if group == "api":
        method = (_opt_value(argv, "-X", "--method") or "").upper()
        if method == "DELETE":
            return v(Tier.T3, "gh api DELETE", "gh.api.delete")
        if method in ("POST", "PUT", "PATCH") or _has(
            argv, "-f", "-F", "--field", "--raw-field", "--input"
        ):
            return v(Tier.T2, f"gh api {method or 'POST'} (changes GitHub)", "gh.api.write")
        return T0_OK
    if action in ("delete", "remove", "rm", "delete-asset") or (
        group == "repo" and action == "archive"
    ):
        return v(Tier.T3, f"gh {group} {action}", "gh.delete")
    if action in ("view", "list", "status", "checks", "diff", "watch", "download") or group in (
        "search",
        "status",
    ):
        return T0_OK
    if group == "repo" and action == "clone":
        return T0_OK
    if group == "auth" and action == "status":
        return T0_OK
    if group in ("secret", "variable") and action == "list":
        return T0_OK
    return v(Tier.T2, f"gh {group} {action} (changes GitHub)".strip(), "gh.write")


def classify_az(argv: list[str]) -> Verdict:
    words = [a.lower() for a in _args(argv)]
    text = " ".join(words)
    if "get-access-token" in words or (
        ("secret" in words or "key" in words) and "show" in words and "keyvault" in words
    ):
        return v(Tier.FORBIDDEN, "az command prints a secret", "secret.print")
    if any(w in ("delete", "purge", "remove") for w in words):
        return v(Tier.T3, f"az {text} (deletes cloud resources)", "az.delete")
    if (
        not words
        or words[-1] in ("show", "list", "get", "exists", "version", "wait")
        or "show" in words
        or "list" in words
        or text.startswith("account show")
    ):
        if words and words[0] in ("login", "logout"):
            return v(Tier.T2, "az login/logout", "az.auth")
        return T0_OK
    return v(Tier.T2, f"az {text} (changes cloud resources)", "az.write")


def classify_terraform(argv: list[str]) -> Verdict:
    args = _args(argv)
    sub = args[0] if args else ""
    if sub == "destroy" or (sub == "apply" and _has(argv, "-destroy")):
        return v(Tier.T3, "terraform destroy", "tf.destroy")
    if sub == "state" and len(args) > 1 and args[1] == "rm":
        return v(Tier.T3, "terraform state rm", "tf.state.rm")
    if sub == "workspace" and len(args) > 1 and args[1] == "delete":
        return v(Tier.T3, "terraform workspace delete", "tf.ws.delete")
    if sub == "force-unlock":
        return v(Tier.T3, "terraform force-unlock", "tf.unlock")
    if sub in (
        "plan",
        "validate",
        "fmt",
        "show",
        "output",
        "version",
        "init",
        "providers",
        "graph",
        "console",
        "",
    ):
        return T0_OK
    if sub == "state" and len(args) > 1 and args[1] in ("list", "show", "pull"):
        return T0_OK
    return v(Tier.T2, f"terraform {sub} (changes infrastructure)", "tf.write")


def classify_kube(argv: list[str]) -> Verdict:
    args = _args(argv)
    sub = args[0] if args else ""
    if sub in ("delete", "uninstall"):
        return v(Tier.T3, f"{_program(argv[0])} {sub}", "kube.delete")
    if sub in (
        "get",
        "describe",
        "logs",
        "top",
        "explain",
        "version",
        "api-resources",
        "diff",
        "status",
        "list",
        "history",
        "template",
        "show",
        "search",
        "cluster-info",
        "auth",
        "",
    ):
        return T0_OK
    if sub == "config" and len(args) > 1 and args[1] in ("view", "get-contexts", "current-context"):
        return T0_OK
    return v(Tier.T2, f"{_program(argv[0])} {sub} (changes the cluster)", "kube.write")


def classify_docker(argv: list[str]) -> Verdict:
    args = [a.lower() for a in _args(argv)]
    if _program(argv[0]) == "docker-compose":
        args = ["compose", *args]
    joined = " ".join(args)
    if any(a in ("rm", "rmi", "prune") for a in args) or "system prune" in joined:
        return v(Tier.T3, f"docker {joined} (deletes containers/images/volumes)", "docker.delete")
    if args[:2] == ["compose", "down"]:
        if _has(argv, "-v", "--volumes", "--rmi"):
            return v(Tier.T3, "docker compose down with volumes/images", "docker.delete")
        return v(Tier.T2, "docker compose down", "docker.write")
    read = (
        "ps",
        "images",
        "logs",
        "inspect",
        "version",
        "info",
        "stats",
        "top",
        "history",
        "port",
        "diff",
        "ls",
        "config",
        "events",
        "search",
    )
    if (
        not args
        or args[-1] in read
        or (args[0] in read)
        or (
            len(args) > 1
            and args[1] in read
            and args[0]
            in ("compose", "image", "container", "network", "volume", "context", "buildx")
        )
    ):
        return T0_OK
    return v(Tier.T2, f"docker {joined} (creates or changes containers)", "docker.write")


# ── package managers ───────────────────────────────────────────────────

JS_SAFE_SCRIPTS = {
    "test",
    "lint",
    "build",
    "typecheck",
    "type-check",
    "check",
    "format",
    "fmt",
    "tsc",
    "coverage",
    "vitest",
    "jest",
    "eslint",
    "prettier",
    "test:unit",
    "test:e2e",
}


def classify_js(prog: str, argv: list[str]) -> Verdict:
    if prog in ("npx", "pnpx", "bunx"):
        return v(Tier.T2, f"{prog} downloads and runs a package", "js.exec")
    args = _args(argv)
    sub = args[0] if args else ""
    if sub in (
        "test",
        "t",
        "tst",
        "ls",
        "list",
        "outdated",
        "why",
        "view",
        "info",
        "show",
        "audit",
        "help",
        "--version",
        "-v",
        "config",
        "root",
        "bin",
        "prefix",
        "doctor",
        "",
    ):
        if sub == "audit" and "fix" in args:
            return v(Tier.T2, f"{prog} audit fix installs packages", "js.install")
        if sub == "config" and len(args) > 1 and args[1] in ("set", "delete"):
            return v(Tier.T2, f"{prog} config change", "js.config")
        return T0_OK
    if sub in ("run", "run-script") and len(args) > 1:
        return (
            T0_OK
            if args[1] in JS_SAFE_SCRIPTS
            else v(Tier.T2, f"{prog} run {args[1]} (project script)", "js.script")
        )
    if sub in JS_SAFE_SCRIPTS:  # pnpm lint, yarn build
        return T0_OK
    if sub in ("unpublish", "deprecate"):
        return v(Tier.T3, f"{prog} {sub}", "js.unpublish")
    if sub in ("publish", "dist-tag", "owner", "access", "login", "logout", "adduser", "token"):
        return v(Tier.T2, f"{prog} {sub} (outward-facing)", "js.publish")
    if sub in (
        "install",
        "i",
        "add",
        "ci",
        "update",
        "up",
        "upgrade",
        "remove",
        "rm",
        "uninstall",
        "un",
        "dedupe",
        "prune",
        "link",
        "unlink",
        "exec",
        "dlx",
        "create",
        "init",
        "x",
        "rebuild",
    ):
        return v(Tier.T2, f"{prog} {sub} (installs or changes packages)", "js.install")
    return v(Tier.T2, f"{prog} {sub} (project script)", "js.script")


def classify_pypkg(prog: str, argv: list[str], ctx: Ctx) -> Verdict:
    args = _args(argv)
    sub = args[0] if args else ""
    if prog in ("uvx", "pipx") and sub != "list":
        return v(Tier.T2, f"{prog} downloads and runs a tool", "py.exec")
    if prog == "uv":
        if sub == "run":
            idx = argv.index("run") + 1
            while idx < len(argv) and argv[idx].startswith("-"):
                idx += (
                    2
                    if argv[idx] in ("--with", "--python", "--package", "-p", "--group", "--extra")
                    else 1
                )
            inner = argv[idx:]
            return classify_argv(inner, ctx) if inner else T0_OK
        if (
            sub
            in (
                "sync",
                "venv",
                "tree",
                "--version",
                "version",
                "build",
                "lock",
                "export",
                "help",
                "",
            )
            or (
                sub == "pip"
                and len(args) > 1
                and args[1] in ("list", "show", "freeze", "check", "tree")
            )
            or (sub == "python" and len(args) > 1 and args[1] in ("list", "find", "dir"))
            or (sub == "tool" and len(args) > 1 and args[1] in ("list", "dir"))
        ):
            return T0_OK
        if sub in ("remove",) or (
            sub == "cache" and len(args) > 1 and args[1] in ("clean", "prune")
        ):
            return v(Tier.T2, f"uv {' '.join(args[:2])}", "py.remove")
        return v(Tier.T2, f"uv {' '.join(args[:2])} (installs or changes packages)", "py.install")
    if sub in (
        "list",
        "show",
        "freeze",
        "check",
        "--version",
        "help",
        "search",
        "inspect",
        "env",
        "info",
        "",
    ):
        return T0_OK
    return v(Tier.T2, f"{prog} {sub} (installs or changes packages)", "py.install")


# ── network, databases, claude ─────────────────────────────────────────


def _host_allowed(url: str, ctx: Ctx) -> bool:
    host = _hostname(url)
    return any(host == h or host.endswith("." + h) for h in ctx.paths.policy.allowed_hosts)


def classify_http(prog: str, argv: list[str], ctx: Ctx) -> Verdict:
    verdicts: list[Verdict] = []
    method = (_opt_value(argv, "-X", "--request", "--method") or "").upper()
    data_flags = (
        "-d",
        "--data",
        "--data-raw",
        "--data-binary",
        "--data-urlencode",
        "--json",
        "-F",
        "--form",
        "-T",
        "--upload-file",
        "--post-data",
        "--post-file",
        "--body-data",
        "--body-file",
    )
    for i, a in enumerate(argv):
        for f in data_flags:
            value: str | None = None
            if a == f and i + 1 < len(argv):
                value = argv[i + 1]
            elif a.startswith(f + "=") and f.startswith("--"):
                value = a.split("=", 1)[1]
            if value is not None:
                method = method or ("PUT" if f in ("-T", "--upload-file") else "POST")
                at = (
                    value.split("@", 1)[1]
                    if "@" in value
                    else (
                        value
                        if f in ("-T", "--upload-file", "--post-file", "--body-file")
                        else None
                    )
                )
                if at:
                    at = at.split(";", 1)[0]
                    verdicts.append(read_target(at, ctx))
    if prog in ("http", "https", "httpie", "xh"):
        args = _args(argv)
        if args and args[0].upper() in ("POST", "PUT", "PATCH", "DELETE"):
            method = args[0].upper()
        elif any("=" in a or ":=" in a for a in args[1:]):
            method = method or "POST"
    out = _opt_value(argv, "-o", "--output", "-O", "--output-document")
    if out and out not in ("-",):
        verdicts.append(write_target(out, ctx, "downloads to"))
    urls = [a for a in argv[1:] if re.match(r"^https?://", a)]
    url = urls[0] if urls else ""
    if method in ("", "GET", "HEAD", "OPTIONS"):
        verdicts.append(T0_OK)
    elif method == "DELETE":
        verdicts.append(v(Tier.T3, f"HTTP DELETE to {url or 'a URL'}", "http.delete"))
    elif url and _host_allowed(url, ctx):
        verdicts.append(
            v(Tier.T1, f"HTTP {method} to allow-listed {_hostname(url)}", "http.allowed")
        )
    else:
        verdicts.append(
            v(
                Tier.T2,
                f"HTTP {method} to {_hostname(url) if url else 'an unknown host'}",
                "http.write",
            )
        )
    return worst(verdicts)


def classify_sql_client(prog: str, argv: list[str], heredoc: str | None, piped_in: bool) -> Verdict:
    if prog == "redis-cli":
        words = [a.upper() for a in _args(argv)]
        if any(w in ("FLUSHALL", "FLUSHDB", "DEL", "UNLINK") for w in words):
            return v(Tier.T3, "redis delete/flush", "redis.delete")
        if any(
            w in ("SET", "HSET", "LPUSH", "RPUSH", "SADD", "ZADD", "EXPIRE", "INCR") for w in words
        ):
            return v(Tier.T2, "redis write", "redis.write")
        return T0_OK if words else v(Tier.T2, "interactive redis-cli", "db.interactive")
    texts: list[str] = []
    for name in ("-c", "--command", "-e", "--execute", "--eval", "-cmd", "-Q", "-q"):
        value = _opt_value(argv, name)
        if value:
            texts.append(value)
    if prog in ("sqlite3", "duckdb"):
        positional = _args(argv)
        texts += positional[1:]
    if heredoc:
        texts.append(heredoc)
    if _opt_value(argv, "-f", "--file", "-i") is not None:
        return v(Tier.T2, f"{prog} runs a SQL file that can't be inspected", "sql.file")
    if not texts:
        if piped_in:
            return v(Tier.T2, f"{prog} runs SQL from a pipe", "sql.pipe")
        return v(Tier.T2, f"interactive {prog} session", "db.interactive")
    return sql.classify_sql(";".join(texts))


def classify_claude(argv: list[str]) -> Verdict:
    lowered = [a.lower() for a in argv]
    if (
        "--dangerously-skip-permissions" in lowered
        or "--allow-dangerously-skip-permissions" in lowered
    ):
        return v(Tier.FORBIDDEN, "--dangerously-skip-permissions is never allowed", "claude.skip")
    mode = _opt_value(argv, "--permission-mode")
    if mode and mode.lower() in ("bypasspermissions", "dontask", "auto"):
        return v(Tier.FORBIDDEN, f"--permission-mode {mode} bypasses approvals", "claude.bypass")
    args = _args(argv)
    if args[:1] == ["setup-token"]:
        return v(
            Tier.FORBIDDEN, "claude setup-token prints a long-lived credential", "secret.print"
        )
    if args[:2] in (["auth", "status"], ["mcp", "list"], ["mcp", "get"]) or _has(
        argv, "--version", "-v", "--help", "-h"
    ):
        return T0_OK
    if _has(argv, "-p", "--print"):
        return v(Tier.T2, "starts a headless claude run (uses Max usage)", "claude.headless")
    return v(Tier.T2, f"claude {' '.join(args[:2])}".strip(), "claude.other")


# ── PowerShell statements ──────────────────────────────────────────────

PS_ALIASES = {
    "rm": "remove-item",
    "del": "remove-item",
    "erase": "remove-item",
    "rd": "remove-item",
    "rmdir": "remove-item",
    "ri": "remove-item",
    "ls": "get-childitem",
    "dir": "get-childitem",
    "gci": "get-childitem",
    "cat": "get-content",
    "gc": "get-content",
    "type": "get-content",
    "cp": "copy-item",
    "copy": "copy-item",
    "cpi": "copy-item",
    "mv": "move-item",
    "move": "move-item",
    "mi": "move-item",
    "ren": "rename-item",
    "rni": "rename-item",
    "ni": "new-item",
    "md": "new-item",
    "mkdir": "new-item",
    "sc": "set-content",
    "ac": "add-content",
    "clc": "clear-content",
    "iwr": "invoke-webrequest",
    "wget": "invoke-webrequest",
    "curl": "invoke-webrequest",
    "irm": "invoke-restmethod",
    "iex": "invoke-expression",
    "icm": "invoke-command",
    "saps": "start-process",
    "start": "start-process",
    "kill": "stop-process",
    "spps": "stop-process",
    "cd": "set-location",
    "sl": "set-location",
    "chdir": "set-location",
    "pushd": "push-location",
    "popd": "pop-location",
    "echo": "write-output",
    "write": "write-output",
    "sls": "select-string",
    "gcm": "get-command",
    "where": "where-object",
    "?": "where-object",
    "%": "foreach-object",
    "foreach": "foreach-object",
    "select": "select-object",
    "sort": "sort-object",
    "measure": "measure-object",
    "tee": "tee-object",
    "pwd": "get-location",
    "gl": "get-location",
    "gp": "get-itemproperty",
    "sp": "set-itemproperty",
    "gi": "get-item",
    "cls": "clear-host",
    "h": "get-history",
    "history": "get-history",
    "fl": "format-list",
    "ft": "format-table",
    "oh": "out-host",
    "sleep": "start-sleep",
}
PS_READ_VERBS = {
    "get",
    "test",
    "find",
    "search",
    "select",
    "measure",
    "show",
    "read",
    "resolve",
    "compare",
    "format",
    "convertto",
    "convertfrom",
    "wait",
    "trace",
    "where",
    "foreach",
    "sort",
    "group",
    "join",
    "split",
    "push",
    "pop",
    "write",
    "out",
    "start-sleep",
    "clear-host",
    "import",
    "use",
    "debug",
    "confirm",
}
PS_WRITE_VERBS = {
    "new",
    "add",
    "set",
    "update",
    "install",
    "register",
    "publish",
    "send",
    "start",
    "enable",
    "disable",
    "export",
    "invoke",
    "connect",
    "grant",
    "copy",
    "move",
    "rename",
    "save",
    "submit",
    "restart",
    "stop",
    "suspend",
    "resume",
    "expand",
    "compress",
    "mount",
    "dismount",
    "block",
    "unblock",
    "protect",
    "unprotect",
    "approve",
    "deny",
    "edit",
    "lock",
    "unlock",
    "deploy",
    "sync",
    "initialize",
    "request",
    "merge",
}
PS_DELETE_VERBS = {"remove", "clear", "uninstall", "unregister", "revoke", "reset", "disconnect"}
PS_PATH_PARAMS = (
    "-path",
    "-literalpath",
    "-filepath",
    "-destination",
    "-outfile",
    "-infile",
    "-destinationpath",
    "-pspath",
    "-target",
)


PS_SWITCHES = (
    "recurse",
    "force",
    "whatif",
    "confirm",
    "verbose",
    "nonewline",
    "append",
    "passthru",
    "usebasicparsing",
    "asjob",
    "wait",
    "noprofile",
    "noninteractive",
    "hidden",
    "raw",
    "directory",
    "file",
    "readonly",
    "skipcertificatecheck",
    "nologo",
    "debug",
)


def _ps_is_switch(key: str) -> bool:
    """`-r`, `-Rec`, `-Force`… PowerShell accepts unique prefixes. An ambiguous prefix
    counts as a switch, so its argument is read as a path: the stricter reading."""
    name = key.lstrip("-")
    return bool(name) and any(s.startswith(name) for s in PS_SWITCHES)


def _ps_params(words: list[str]) -> tuple[dict[str, str], list[str], set[str]]:
    """(named params lowercased → value, positional args, switch flags)."""
    named: dict[str, str] = {}
    positional: list[str] = []
    switches: set[str] = set()
    i = 1
    while i < len(words):
        w = words[i]
        if w.startswith("-") and len(w) > 1 and not w[1:2].isdigit():
            key, _, inline = w.partition(":")
            key = key.lower()
            if inline:
                named[key] = inline
            elif i + 1 < len(words) and not words[i + 1].startswith("-") and not _ps_is_switch(key):
                named[key] = words[i + 1]
                i += 1
            else:
                switches.add(key)
        else:
            positional.append(w)
        i += 1
    return named, positional, switches


def _ps_paths(
    named: dict[str, str], positional: list[str], keys: tuple[str, ...] = ("-path", "-literalpath")
) -> list[str]:
    paths = [named[k] for k in keys if k in named]
    if not paths and positional:
        paths = [positional[0]]
    out: list[str] = []
    for p in paths:
        out += [x.strip() for x in p.split(",") if x.strip()]
    return out


def classify_ps_statement(st: pwsh.Statement, ctx: Ctx) -> Verdict:
    words = st.words
    first = words[0]
    if first.startswith("$") and len(words) >= 2 and words[1] in ("=", "+=", "-="):
        rest = words[2:]
        if (
            not rest
            or rest[0].startswith(("$", "'", '"', "@", "[", "("))
            or rest[0].replace(".", "").isdigit()
            or rest[0] in ("__SUBEXPR__", "__BLOCK__")
        ):
            return T0_OK
        return classify_ps_statement(pwsh.Statement(rest, piped_from=st.piped_from), ctx)
    if first.startswith(("$", "[", "@", "(", "__BLOCK__", "__SUBEXPR__")) or first in (
        "if",
        "else",
        "elseif",
        "foreach",
        "for",
        "while",
        "do",
        "switch",
        "try",
        "catch",
        "finally",
        "return",
        "break",
        "continue",
        "param",
        "function",
        "throw",
        "exit",
    ):
        return T0_OK  # expressions / control flow; nested blocks are classified separately
    raw = first.lower()
    native = _program(first)
    mapped = PS_ALIASES.get(raw)
    is_path = "/" in first or "\\" in first or raw.endswith(".exe")
    if is_path or (mapped is None and "-" not in raw):
        # a native program (git, gh, python …) or a path to an executable
        return classify_argv([native, *words[1:]], ctx)
    if raw in ("curl", "wget"):
        # Windows PowerShell 5.1: an alias for Invoke-WebRequest; PowerShell 7: the real
        # curl.exe. Classify both readings and keep the worse one.
        as_native = classify_argv([raw, *words[1:]], ctx)
        as_cmdlet = classify_ps_statement(
            pwsh.Statement(["Invoke-WebRequest", *words[1:]], piped_from=st.piped_from), ctx
        )
        return worst([as_native, as_cmdlet])
    name = mapped or raw
    named, positional, _switches = _ps_params(words)
    verb = name.split("-", 1)[0]

    if name in ("set-location", "push-location"):
        target = named.get("-path") or (positional[0] if positional else None)
        if target:
            new = _resolved(target, ctx)
            if new:
                ctx.cwd = new
        return T0_OK
    if name in (
        "get-content",
        "select-string",
        "get-item",
        "get-childitem",
        "import-csv",
        "get-filehash",
        "test-path",
        "resolve-path",
    ):
        targets = _ps_paths(named, positional)
        return worst([read_target(t, ctx) for t in targets] or [T0_OK])
    if name == "remove-item":
        targets = _ps_paths(named, positional)
        if not targets:
            return v(Tier.T3, "Remove-Item without explicit targets", "delete.unknown")
        return worst([delete_target(t, ctx) for t in targets])
    if name == "clear-content":
        return worst(
            [delete_target(t, ctx, "clears the contents of") for t in _ps_paths(named, positional)]
            or [v(Tier.T3, "Clear-Content", "delete.unknown")]
        )
    if name in (
        "set-content",
        "add-content",
        "out-file",
        "new-item",
        "tee-object",
        "expand-archive",
        "compress-archive",
        "export-csv",
        "set-itemproperty",
    ):
        keys = ("-path", "-literalpath", "-filepath", "-destinationpath")
        targets = _ps_paths(named, positional, keys)
        if not targets:
            return v(Tier.T2, f"{first} with no clear target", "path.unknown")
        return worst([write_target(t, ctx) for t in targets])
    if name in ("copy-item", "move-item", "rename-item"):
        sources = _ps_paths(named, positional)
        dest = (
            named.get("-destination")
            or named.get("-newname")
            or (positional[1] if len(positional) > 1 else None)
        )
        verdicts: list[Verdict] = []
        if name == "move-item":
            verdicts += [delete_target(s, ctx, "moves away") for s in sources]
        else:
            verdicts += [read_target(s, ctx) for s in sources]
        if dest:
            if name == "rename-item" and sources:
                dest = posixpath.join(posixpath.dirname(sources[0].replace("\\", "/")), dest)
            verdicts.append(write_target(dest, ctx))
        else:
            verdicts.append(v(Tier.T2, f"{first} without a clear destination", "path.unknown"))
        return worst(verdicts)
    if name in ("invoke-webrequest", "invoke-restmethod"):
        method = named.get("-method", "").upper()
        verdicts = []
        if "-body" in named or "-infile" in named or "-form" in named:
            method = method or "POST"
        if "-infile" in named:
            verdicts.append(read_target(named["-infile"], ctx))
        if "-outfile" in named:
            verdicts.append(write_target(named["-outfile"], ctx, "downloads to"))
        url = named.get("-uri") or (positional[0] if positional else "")
        if method in ("", "GET", "HEAD", "OPTIONS", "DEFAULT"):
            verdicts.append(T0_OK)
        elif method == "DELETE":
            verdicts.append(v(Tier.T3, f"HTTP DELETE to {url}", "http.delete"))
        elif url and _host_allowed(url, ctx):
            verdicts.append(v(Tier.T1, f"HTTP {method} to allow-listed host", "http.allowed"))
        else:
            verdicts.append(v(Tier.T2, f"HTTP {method} to {_hostname(url) or url}", "http.write"))
        return worst(verdicts)
    if name in ("invoke-expression", "invoke-command", "start-job", "start-threadjob", "add-type"):
        why = "runs code from a pipe" if st.piped_from else "runs code that can't be inspected"
        return v(Tier.T2, f"{first} {why}", "pwsh.exec")
    if name == "start-process":
        target = named.get("-filepath") or (positional[0] if positional else "")
        args = named.get("-argumentlist", "")
        if target:
            inner = classify_argv([_program(target), *args.replace(",", " ").split()], ctx)
            return inner.at_least(Tier.T2, f"Start-Process {target}: {inner.reason}", "pwsh.start")
        return v(Tier.T2, "Start-Process", "pwsh.start")
    if name in (
        "restart-computer",
        "stop-computer",
        "format-volume",
        "clear-disk",
        "initialize-disk",
        "remove-partition",
    ):
        return v(Tier.T3, f"{first} (machine or disk level)", "power")
    if name == "set-executionpolicy":
        return v(Tier.T2, "changes the PowerShell execution policy", "pwsh.policy")
    if name in (
        "remove-variable",
        "remove-module",
        "remove-job",
        "clear-variable",
        "clear-host",
        "remove-typedata",
        "remove-psdrive",
    ):
        return T0_OK
    if verb in PS_DELETE_VERBS:
        return v(Tier.T3, f"{first} deletes or resets something", "pwsh.delete")
    if verb in PS_READ_VERBS or name in (
        "write-output",
        "write-host",
        "out-null",
        "out-string",
        "out-host",
        "start-sleep",
        "foreach-object",
        "where-object",
    ):
        return T0_OK
    if verb in PS_WRITE_VERBS:
        return v(Tier.T2, f"{first} changes something", "pwsh.write")
    return v(Tier.T2, f"unrecognised PowerShell command '{first}'", "unknown")
