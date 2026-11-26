"""A small, conservative POSIX shell parser for risk classification.

It doesn't execute or fully emulate a shell. It splits a command line into simple
commands (argv + redirections), and pulls out nested code ($(...), `...`, heredoc bodies)
so each piece can be classified. Anything it can't parse is reported, so the classifier
can treat it as T2 ("couldn't classify").
"""

from __future__ import annotations

import re

SEPARATORS = {"&&", "||", ";", "|", "|&", "&", "\n", "(", ")", "{", "}"}
_HEREDOC = re.compile(r"(?<!<)<<(?!<)-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")


class ParseError(ValueError):
    pass


class SimpleCommand:
    __slots__ = ("argv", "heredoc", "piped_from", "piped_to", "reads", "writes")

    def __init__(self, argv: list[str]) -> None:
        self.argv = argv
        self.writes: list[str] = []  # redirect targets (> >> &>)
        self.reads: list[str] = []  # < sources
        self.piped_from = False  # receives stdin from a pipe
        self.piped_to = False  # sends stdout into a pipe
        self.heredoc: str | None = None  # body fed on stdin, if any


class Parsed:
    __slots__ = ("commands", "nested")

    def __init__(self, commands: list[SimpleCommand], nested: list[str]) -> None:
        self.commands = commands
        self.nested = nested  # code from $(...) / `...` / <( ) to classify separately


def _extract_heredocs(text: str) -> tuple[str, list[str]]:
    """Remove heredoc bodies; return (text without bodies, bodies in order)."""
    bodies: list[str] = []
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        delims = [m.group(2) for m in _HEREDOC.finditer(line)]
        i += 1
        for delim in delims:
            body: list[str] = []
            while i < len(lines) and lines[i].strip() != delim:
                body.append(lines[i])
                i += 1
            i += 1  # skip the delimiter line
            bodies.append("\n".join(body))
    return "\n".join(out), bodies


def _extract_substitutions(text: str) -> tuple[str, list[str]]:
    """Replace $(...), <(...), >(...) and `...` (outside single quotes) with a placeholder."""
    out: list[str] = []
    nested: list[str] = []
    i, n = 0, len(text)
    in_single = in_double = False
    while i < n:
        c = text[i]
        if c == "\\" and not in_single and i + 1 < n:
            out.append(text[i : i + 2])
            i += 2
            continue
        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double
        if not in_single and c in "$<>" and i + 1 < n and text[i + 1] == "(":
            if c == "$" and i + 2 < n and text[i + 2] == "(":  # $(( arithmetic ))
                end = text.find("))", i + 3)
                if end == -1:
                    raise ParseError("unterminated $((")
                out.append("0")
                i = end + 2
                continue
            depth, j = 1, i + 2
            while j < n and depth:
                if text[j] == "(":
                    depth += 1
                elif text[j] == ")":
                    depth -= 1
                j += 1
            if depth:
                raise ParseError("unterminated $(")
            nested.append(text[i + 2 : j - 1])
            out.append("__SUBST__")
            i = j
            continue
        if not in_single and c == "`":
            end = text.find("`", i + 1)
            if end == -1:
                raise ParseError("unterminated backtick")
            nested.append(text[i + 1 : end])
            out.append("__SUBST__")
            i = end + 1
            continue
        out.append(c)
        i += 1
    if in_single or in_double:
        raise ParseError("unterminated quote")
    return "".join(out), nested


def tokenize(text: str) -> list[tuple[str, str]]:
    """Tokens: ("word", text) or ("op", operator)."""
    tokens: list[tuple[str, str]] = []
    buf: list[str] = []
    has_word = False
    i, n = 0, len(text)

    def flush() -> None:
        nonlocal has_word
        if has_word:
            tokens.append(("word", "".join(buf)))
        buf.clear()
        has_word = False

    while i < n:
        c = text[i]
        if c == "\\":
            if i + 1 < n and text[i + 1] == "\n":
                i += 2  # line continuation
                continue
            if i + 1 < n:
                buf.append(text[i + 1])
                has_word = True
                i += 2
                continue
            i += 1
            continue
        if c == "'":
            end = text.find("'", i + 1)
            if end == -1:
                raise ParseError("unterminated single quote")
            buf.append(text[i + 1 : end])
            has_word = True
            i = end + 1
            continue
        if c == '"':
            j = i + 1
            part: list[str] = []
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n and text[j + 1] in '"\\$`':
                    part.append(text[j + 1])
                    j += 2
                    continue
                part.append(text[j])
                j += 1
            if j >= n:
                raise ParseError("unterminated double quote")
            buf.append("".join(part))
            has_word = True
            i = j + 1
            continue
        if c == "#" and not has_word:
            while i < n and text[i] != "\n":  # comment to end of line
                i += 1
            continue
        if c in " \t\r":
            flush()
            i += 1
            continue
        if text[i : i + 3] == "<<<":
            flush()
            tokens.append(("op", "<<<"))
            i += 3
            continue
        two = text[i : i + 2]
        if two == "<<":
            flush()
            tokens.append(("op", "<<"))
            i += 2
            if i < n and text[i] == "-":
                i += 1
            continue
        if two in ("&&", "||", "|&", ">>", "&>", ">|"):
            flush()
            tokens.append(("op", two))
            i += 2
            continue
        if c in ";|&()\n<>{}":
            # fd-prefixed redirections like 2> or 2>&1
            if c == ">" and has_word and "".join(buf).isdigit():
                buf.clear()
                has_word = False
            else:
                flush()
            if c == ">" and i + 1 < n and text[i + 1] == "&":
                j = i + 2
                while j < n and (text[j].isdigit() or text[j] == "-"):
                    j += 1
                tokens.append(("op", ">&"))
                tokens.append(("word", text[i + 2 : j]))
                i = j
                continue
            if c in "{}" and has_word:
                buf.append(c)
                has_word = True
                i += 1
                continue
            tokens.append(("op", c))
            i += 1
            continue
        buf.append(c)
        has_word = True
        i += 1
    flush()
    return tokens


def parse(command: str) -> Parsed:
    text, bodies = _extract_heredocs(command)
    text, nested = _extract_substitutions(text)
    tokens = tokenize(text)

    commands: list[SimpleCommand] = []
    current = SimpleCommand(argv=[])
    pending_redirect: str | None = None
    pipe_in = False
    body_iter = iter(bodies)

    def finish(next_piped: bool) -> None:
        nonlocal current, pipe_in
        if current.argv or current.writes:
            current.piped_from = pipe_in
            current.piped_to = next_piped
            commands.append(current)
        current = SimpleCommand(argv=[])
        pipe_in = next_piped

    for kind, value in tokens:
        if pending_redirect is not None:
            if kind != "word":
                raise ParseError(f"redirection {pending_redirect} without a target")
            if pending_redirect in (">", ">>", "&>", ">|"):
                current.writes.append(value)
            elif pending_redirect == "<":
                current.reads.append(value)
            elif pending_redirect == "<<":
                current.heredoc = next(body_iter, "")
            elif pending_redirect == "<<<":
                current.heredoc = value
            pending_redirect = None
            continue
        if kind == "op":
            if value in (">", ">>", "&>", ">|", "<", ">&", "<<", "<<<"):
                pending_redirect = value
                continue
            finish(next_piped=value in ("|", "|&"))
            continue
        current.argv.append(value)
    if pending_redirect is not None:
        raise ParseError("dangling redirection")
    finish(next_piped=False)
    return Parsed(commands=commands, nested=nested)
