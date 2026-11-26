"""A small, conservative PowerShell parser for risk classification.

Splits a script into pipeline elements (`;`, newlines, `|`, `&&`, `||`), tokenises each
into words (handling '…', "…", backtick escapes, `&` call operator), and pulls out
script blocks / subexpressions ({ … }, $( … )) so they can be classified separately.
"""

from __future__ import annotations


class ParseError(ValueError):
    pass


class Statement:
    __slots__ = ("piped_from", "piped_to", "words", "writes")

    def __init__(
        self,
        words: list[str],
        writes: list[str] | None = None,
        piped_from: bool = False,
        piped_to: bool = False,
    ) -> None:
        self.words = words
        self.writes = writes or []  # > / >> targets
        self.piped_from = piped_from
        self.piped_to = piped_to


class Parsed:
    __slots__ = ("nested", "statements")

    def __init__(self, statements: list[Statement], nested: list[str]) -> None:
        self.statements = statements
        self.nested = nested


def _scan_block(text: str, start: int, open_c: str, close_c: str) -> int:
    """Index just past the matching close bracket (quote-aware)."""
    depth, i, n = 1, start, len(text)
    quote: str | None = None
    while i < n:
        c = text[i]
        if quote:
            if c == "`":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "'\"":
            quote = c
        elif c == open_c:
            depth += 1
        elif c == close_c:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ParseError(f"unterminated {open_c}")


def parse(script: str) -> Parsed:
    statements: list[Statement] = []
    nested: list[str] = []
    words: list[str] = []
    writes: list[str] = []
    buf: list[str] = []
    has_word = False
    pending_redirect = False
    pipe_in = False
    i, n = 0, len(script)

    def flush() -> None:
        nonlocal has_word, pending_redirect
        if has_word:
            word = "".join(buf)
            if pending_redirect:
                writes.append(word)
                pending_redirect = False
            else:
                words.append(word)
        buf.clear()
        has_word = False

    def end_statement(piped: bool) -> None:
        nonlocal words, writes, pipe_in
        flush()
        if words or writes:
            statements.append(Statement(words, writes, piped_from=pipe_in, piped_to=piped))
        words, writes = [], []
        pipe_in = piped

    while i < n:
        c = script[i]
        if c == "`" and i + 1 < n:  # escape (also line continuation)
            if script[i + 1] != "\n":
                buf.append(script[i + 1])
                has_word = True
            i += 2
            continue
        if c == "'":
            end = i + 1
            while True:
                end = script.find("'", end)
                if end == -1:
                    raise ParseError("unterminated single quote")
                if end + 1 < n and script[end + 1] == "'":  # '' escape
                    end += 2
                    continue
                break
            buf.append(script[i + 1 : end].replace("''", "'"))
            has_word = True
            i = end + 1
            continue
        if c == '"':
            j = i + 1
            part: list[str] = []
            while j < n and script[j] != '"':
                if script[j] == "`" and j + 1 < n:
                    part.append(script[j + 1])
                    j += 2
                    continue
                if script[j] == "$" and j + 1 < n and script[j + 1] == "(":
                    end = _scan_block(script, j + 2, "(", ")")
                    nested.append(script[j + 2 : end - 1])
                    j = end
                    continue
                part.append(script[j])
                j += 1
            if j >= n:
                raise ParseError("unterminated double quote")
            buf.append("".join(part))
            has_word = True
            i = j + 1
            continue
        if c == "#" and not has_word:
            while i < n and script[i] != "\n":
                i += 1
            continue
        if c == "<" and script[i : i + 2] == "<#":  # block comment
            end = script.find("#>", i + 2)
            if end == -1:
                raise ParseError("unterminated <# comment")
            i = end + 2
            continue
        if c == "@" and i + 1 < n and script[i + 1] in "'\"" and script[i + 2 : i + 3] == "\n":
            quote = script[i + 1]  # here-string: @' … '@
            end = script.find("\n" + quote + "@", i + 2)
            if end == -1:
                raise ParseError("unterminated here-string")
            buf.append(script[i + 3 : end])
            has_word = True
            i = end + 3
            continue
        if c == "$" and i + 1 < n and script[i + 1] == "(":
            end = _scan_block(script, i + 2, "(", ")")
            nested.append(script[i + 2 : end - 1])
            buf.append("__SUBEXPR__")
            has_word = True
            i = end
            continue
        if c == "{":
            end = _scan_block(script, i + 1, "{", "}")
            nested.append(script[i + 1 : end - 1])
            buf.append("__BLOCK__")
            has_word = True
            i = end
            continue
        if c in " \t\r":
            flush()
            i += 1
            continue
        if script[i : i + 2] in ("&&", "||"):
            end_statement(piped=False)
            i += 2
            continue
        if c in ";\n":
            end_statement(piped=False)
            i += 1
            continue
        if c == "|":
            end_statement(piped=True)
            i += 1
            continue
        if c == ">" or (c.isdigit() and script[i + 1 : i + 2] == ">" and not has_word):
            flush()
            j = i + (1 if c == ">" else 2)
            while j < n and script[j] in ">&":
                j += 1
            if script[j : j + 1].isdigit() and script[j - 1] == "&":  # 2>&1
                i = j + 1
                continue
            pending_redirect = True
            i = j
            continue
        if c == "&" and not has_word:  # call operator: & "path\to.exe" args
            i += 1
            continue
        buf.append(c)
        has_word = True
        i += 1
    if pending_redirect and not has_word:
        raise ParseError("redirection without a target")
    end_statement(piped=False)
    return Parsed(statements=statements, nested=nested)
