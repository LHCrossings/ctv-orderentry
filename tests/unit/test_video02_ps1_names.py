"""PowerShell variable names are CASE-INSENSITIVE: `$Meta` and `$script:meta` are one variable.

10/15: `$relay = Start-Relay` clobbered `$Relay` (the URL) in record.ps1. 10/18: thumb.ps1's
`$Meta` (the live.json path) was overwritten by `$script:meta` (the status hashtable), so every
status write since 10/15 landed in C:\\Windows\\System32\\System.Collections.Hashtable. Every
`video02_recorder/*.ps1` must spell each variable one way, and must never declare an automatic
variable ($args, $input, $error, $matches, ...) as a PARAMETER: the call binds nothing and the
function runs with an empty list (thumb.ps1 10/16, the damaged-sample harness 10/17). A plain
`$args = @(...)` inside a function body shadows the automatic one and works (record.ps1).
"""

import re
from pathlib import Path

_SCRIPTS = sorted((Path(__file__).resolve().parents[2] / "video02_recorder").glob("*.ps1"))
_VAR = re.compile(r"\$(?:script:|global:|local:|private:)?([A-Za-z_][A-Za-z0-9_]*)")
_AUTOMATIC = {
    "args",
    "input",
    "error",
    "matches",
    "this",
    "psitem",
    "host",
    "pid",
    "profile",
    "home",
    "pwd",
}
_PARAMS = re.compile(r"\bparam\s*\(([^)]*)\)|function\s+[\w-]+\s*\(([^)]*)\)")


def _strip_comments_and_strings(text: str) -> str:
    text = re.sub(r"(?s)<#.*?#>", "", text)
    text = re.sub(r"(?m)#.*$", "", text)
    return re.sub(r"'[^']*'", "''", text)  # single-quoted strings never expand variables


def test_each_variable_has_one_spelling_per_script():
    assert _SCRIPTS, "no recorder scripts found"
    for script in _SCRIPTS:
        spellings: dict[str, set[str]] = {}
        for name in _VAR.findall(_strip_comments_and_strings(script.read_text())):
            spellings.setdefault(name.lower(), set()).add(name)
        clashes = {k: sorted(v) for k, v in spellings.items() if len(v) > 1}
        assert not clashes, f"{script.name}: one variable, several spellings: {clashes}"


def test_no_script_takes_an_automatic_variable_as_a_parameter():
    for script in _SCRIPTS:
        body = _strip_comments_and_strings(script.read_text())
        declared = set()
        for m in _PARAMS.finditer(body):
            for params in m.groups():
                if params:
                    declared.update(n.lower() for n in _VAR.findall(params))
        bad = declared & _AUTOMATIC
        assert not bad, f"{script.name} uses automatic variable(s) as parameters: {sorted(bad)}"


def test_the_case_check_catches_the_thumb_bug():
    text = "$Meta = 'x'\n$script:meta = @{ a = 1 }\nSet-Content $Meta\n"
    spellings: dict[str, set[str]] = {}
    for name in _VAR.findall(_strip_comments_and_strings(text)):
        spellings.setdefault(name.lower(), set()).add(name)
    assert spellings["meta"] == {"Meta", "meta"}
