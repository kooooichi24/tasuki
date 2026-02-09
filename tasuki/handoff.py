"""Handoff format. A single document submitted by a worker to the planner."""

from pathlib import Path


def parse_handoff(content: str) -> dict:
    """Extract structure from the handoff body (simplified). Returns presence of required sections."""
    sections = {}
    current = None
    buf = []
    for line in content.splitlines():
        if line.startswith("#"):
            if current:
                sections[current] = "\n".join(buf).strip()
            current = line.lstrip("#").strip().lower()
            buf = []
        else:
            buf.append(line)
    if current:
        sections[current] = "\n".join(buf).strip()
    return sections


def validate_handoff(content: str) -> tuple[bool, list[str]]:
    """Validate whether the content is a proper handoff. Checks for recommended sections."""
    sections = parse_handoff(content)
    recommended = ["summary", "what was done", "notes", "concerns", "discoveries", "feedback"]
    missing = [s for s in recommended if not any(s in k for k in sections.keys())]
    # Not strict. OK if there's something resembling a summary
    has_summary = any(
        k for k in sections if "summary" in k or "done" in k or "change" in k
    )
    ok = len(content.strip()) >= 100 and (has_summary or len(sections) >= 1)
    return ok, missing


def read_handoff(path: Path) -> str:
    return path.read_text(encoding="utf-8")
