"""CI enforcement of the repository policy checks.

Run from anywhere: ``BASE_REF=origin/main python .github/scripts/policy_check.py``.
BASE_REF is only set for pull requests; the entity-source check is skipped without it.
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

NAMES = (
    "turkish turkey american usa british english german germany french france spanish italian dutch "
    "portuguese brazil brazilian russian chinese china japanese japan korean arabic indian india "
    "thai thailand singapore australia australian canada canadian swedish norwegian danish finnish "
    "polish greek hebrew hindi persian vietnamese indonesian"
).split()
NAME_RE = re.compile(r"(?:^|_)(%s)(?:_|$)" % "|".join(NAMES))
# A capitalised name only counts as a whole camel-case word: the next character
# must start a new word (upper case, digit, underscore) or end the identifier.
# The preceding side needs no check, since a capital letter always starts a word.
CAMEL_RE = re.compile("(?:%s)(?=[A-Z0-9_]|$)" % "|".join(n.capitalize() for n in NAMES))
LANG_NAMES = ("lang", "language", "locale", "lang_code", "language_code")
LANG_CODE_RE = re.compile(r"[a-z]{2}(?:[-_][A-Za-z]{2})?")
LANG_BRANCH_RE = re.compile(
    r"""\b(?:el)?if\b[^\n:]*\b(?:%s)\b\s*(?:==|!=|in)\s*[\(\[\{]?\s*["'][a-z]{2}(?:[-_][A-Za-z]{2})?["']""" % "|".join(LANG_NAMES)
)

ALLOWED_PARTS = {"tests", "test", "national_ids", "node_modules", "__pycache__", "docs"}
ALLOWED_FILES = {"database.py"}
ALLOWED_IDENTIFIERS = {"AUSTRALIA_PA"}  # built-in regulation ID constant (regulation seed data)


def production_py() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "packages/*.py"], cwd=ROOT, capture_output=True, text=True, check=True)
    files = []
    for rel in out.stdout.split():
        p = Path(rel)
        if ALLOWED_PARTS & set(p.parts) or p.name in ALLOWED_FILES or p.name.startswith("test_") or p.name == "conftest.py":
            continue
        files.append(p)
    return files


def bad_identifier(name: str) -> bool:
    if name.startswith("__") or name in ALLOWED_IDENTIFIERS:
        return False
    return bool(NAME_RE.search(name.lower() if "_" in name or name.islower() else "")) or bool(CAMEL_RE.search(name))


def language_case_lines(tree: ast.AST) -> list[int]:
    """Line numbers of ``case "xx":`` arms in a ``match`` on a language/locale variable."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Match):
            continue
        subject = node.subject
        name = subject.id if isinstance(subject, ast.Name) else subject.attr if isinstance(subject, ast.Attribute) else ""
        if name not in LANG_NAMES:
            continue
        for case in node.cases:
            for pat in ast.walk(case.pattern):
                if (
                    isinstance(pat, ast.MatchValue)
                    and isinstance(pat.value, ast.Constant)
                    and isinstance(pat.value.value, str)
                    and LANG_CODE_RE.fullmatch(pat.value.value)
                ):
                    lines.append(case.pattern.lineno)
                    break
    return lines


def check_identifiers_and_branches() -> list[str]:
    errors: list[str] = []
    for rel in production_py():
        text = (ROOT / rel).read_text(encoding="utf-8")
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            errors.append(f"{rel}:{exc.lineno}: cannot be parsed, so it was not checked: {exc.msg}")
            continue
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                names.append(node.name)
            elif isinstance(node, ast.arg):
                names.append(node.arg)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                names.append(node.id)
            for n in names:
                if bad_identifier(n):
                    errors.append(f"{rel}:{node.lineno}: country/language name in identifier '{n}'")
        for i, line in enumerate(text.splitlines(), 1):
            if LANG_BRANCH_RE.search(line):
                errors.append(f"{rel}:{i}: language-specific branch: {line.strip()}")
        for i in language_case_lines(tree):
            errors.append(f"{rel}:{i}: language-specific match/case on a language code")
    return errors


def headings(path: Path) -> list[int]:
    levels, fenced = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced:
            m = re.match(r"(#{1,6})\s+\S", line)
            if m:
                levels.append(len(m.group(1)))
    return levels


def check_readmes() -> list[str]:
    a, b = headings(ROOT / "README.md"), headings(ROOT / "README.tr.md")
    if a != b:
        return [f"README.md ({len(a)} headings) and README.tr.md ({len(b)} headings) differ in heading structure: {a} vs {b}"]
    return []


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def check_entity_sources(base: str) -> list[str]:
    if not base:
        return []
    changed = git("diff", "--name-only", f"{base}...HEAD").split()
    dbs = [f for f in changed if re.fullmatch(r"packages/api/.*database\.py", f)]
    touched = False
    for f in dbs:
        diff = git("diff", "-U0", f"{base}...HEAD", "--", f)
        if any(re.match(r"[+-](?![+-]).*entity_types", l) for l in diff.splitlines()):
            touched = True
    if touched and "packages/core/docs/REGULATION_ENTITY_SOURCES.md" not in changed:
        return ["entity_types changed in database.py but packages/core/docs/REGULATION_ENTITY_SOURCES.md was not updated"]
    return []


def check_config_json() -> list[str]:
    tracked = git("ls-files").split("\n")
    return [f"tracked config.json: {f}" for f in tracked if f.rsplit("/", 1)[-1] == "config.json"]


def main() -> int:
    errors = [
        *check_identifiers_and_branches(),
        *check_readmes(),
        *check_entity_sources(os.environ.get("BASE_REF", "")),
        *check_config_json(),
    ]
    for e in errors:
        print(f"::error::{e}" if os.environ.get("GITHUB_ACTIONS") else e)
    print(f"policy check: {len(errors)} violation(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
