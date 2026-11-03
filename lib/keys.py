"""Name join keys shared by sources that must match display names to ids."""


def strip_parens(value):
    """Remove balanced "( ... )" groups (context-tier qualifiers)."""
    out = []
    depth = 0
    for char in value:
        if char == "(":
            depth += 1
        elif char == ")":
            if depth:
                depth -= 1
        elif not depth:
            out.append(char)
    return "".join(out)


def hyphen_key(value):
    """'GPT 5.6 Luna (> 272K tokens)' -> 'gpt-5-6-luna'."""
    import re

    value = strip_parens(value.replace("*", "").replace("`", ""))
    return re.sub(r"[^A-Za-z0-9]+", "-", value.lower()).strip("-")


def compact_key(value):
    """'GLM-5.3 Flash Free' -> 'glm53flashfree'."""
    import re

    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def name_head(value):
    """'Big Pickle: During its free period...' -> 'Big Pickle'."""
    head = value
    for separator in (" is ", ":", "("):
        position = head.find(separator)
        if position > 0:
            head = head[:position]
    return head
