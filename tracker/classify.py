"""Heuristic 'type of work' classifier. Transparent on purpose: keyword + file + tool signals,
and the user can always override the result in the dashboard."""
from __future__ import annotations

import re

WORK_TYPES = [
    "feature", "bugfix", "refactor", "testing", "docs", "ui-design",
    "devops-config", "exploration", "review", "planning", "other",
]

_KEYWORDS: dict[str, re.Pattern] = {
    "bugfix": re.compile(
        r"\b(fix|bug|broken|crash|crashes|error|errors|exception|fails?|failing|regression|"
        r"doesn'?t work|not working|wrong|issue|stack ?trace|hotfix|flaky)\b", re.I),
    "feature": re.compile(
        r"\b(add|implement|create|build|introduce|support|new feature|feature|endpoint|"
        r"integrate|enable|allow)\b", re.I),
    "refactor": re.compile(
        r"\b(refactor|rename|clean ?up|simplif\w+|extract|restructure|reorgani[sz]e|migrate|"
        r"deduplicate|tidy|move .* to)\b", re.I),
    "testing": re.compile(
        r"\b(tests?|specs?|coverage|pytest|jest|vitest|unit test|e2e|mock|assert)\b", re.I),
    "docs": re.compile(r"\b(readme|docs?|document\w*|changelog|comment|jsdoc|docstring)\b", re.I),
    "ui-design": re.compile(
        r"\b(ui|ux|css|style|styling|layout|design|component|tailwind|button|modal|sidebar|"
        r"theme|responsive|animation|icon)\b", re.I),
    "devops-config": re.compile(
        r"\b(docker|ci|cd|pipeline|deploy\w*|config\w*|yaml|terraform|k8s|kubernetes|helm|"
        r"github actions|env|dependenc\w+|upgrade|npm install|build script|webpack|vite)\b", re.I),
    "exploration": re.compile(
        r"\b(explain|how does|why does|why is|what is|what does|investigate|understand|"
        r"look at|find out|where is|walk me through|show me|research|compare)\b", re.I),
    "review": re.compile(
        r"\b(review|pr|pull request|code review|audit|check this|feedback)\b", re.I),
    "planning": re.compile(
        r"\b(plan|spec|design doc|architecture|roadmap|proposal|approach|brainstorm|"
        r"strategy|break (it )?down)\b", re.I),
}

_FILE_HINTS = [
    # (type, pattern, weight). Test files are edited alongside almost any feature (TDD), and
    # component files carry logic too, so those weigh less than prose/style/config files.
    ("testing", re.compile(r"(^|[\\/])(tests?|__tests__|spec)[\\/]|\.(test|spec)\.\w+$|_test\.\w+$", re.I), 0.6),
    ("docs", re.compile(r"\.(md|mdx|rst|txt)$|[\\/]docs?[\\/]", re.I), 1.2),
    ("ui-design", re.compile(r"\.(css|scss|sass|less|svg|html)$", re.I), 1.2),
    ("ui-design", re.compile(r"\.(tsx|jsx|vue|svelte)$", re.I), 0.6),
    ("devops-config", re.compile(
        r"(dockerfile|docker-compose|\.ya?ml|\.toml|\.github[\\/]|\.env|package\.json|"
        r"requirements\.txt|\.csproj|\.tf)$", re.I), 1.2),
]

_TEST_CMD = re.compile(
    r"\b(pytest|jest|vitest|mocha|npm (run )?test|yarn test|pnpm test|cargo test|dotnet test|"
    r"go test|mvn test|gradle test|phpunit|rspec|playwright test|cypress run)\b", re.I)


def is_test_command(cmd: str) -> bool:
    return bool(_TEST_CMD.search(cmd or ""))


def classify(prompts: list[str], files: list[str], edits: int, tool_counts: dict[str, int]) -> tuple[str, dict]:
    """Return (primary_type, normalised score distribution)."""
    scores: dict[str, float] = {t: 0.0 for t in WORK_TYPES if t != "other"}
    # Early prompts define the task; weigh them more than the tail of a long session.
    for i, text in enumerate(prompts[:12]):
        weight = 3.0 if i == 0 else 1.0
        for t, rx in _KEYWORDS.items():
            hits = len(rx.findall(text[:600]))
            if hits:
                scores[t] += weight * min(hits, 3)
    for f in files:
        for t, rx, w in _FILE_HINTS:
            if rx.search(f):
                scores[t] += w
                break
        else:
            scores["feature"] += 0.8
    # No edits at all means reading/asking, whatever the words said.
    if edits == 0:
        if scores["review"] >= 3 and scores["review"] >= scores["exploration"]:
            scores["review"] += 6
        elif scores["planning"] >= 3:
            scores["planning"] += 6
        else:
            scores["exploration"] += 8
    total = sum(scores.values())
    if total <= 0:
        return "other", {"other": 1.0}
    primary = max(scores, key=scores.get)
    dist = {t: round(s / total, 3) for t, s in sorted(scores.items(), key=lambda kv: -kv[1]) if s > 0}
    return primary, dist
