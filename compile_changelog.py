"""Compile the supported CHANGELOG.md subset into UI-friendly JSON blocks."""

from __future__ import annotations

import argparse
import json
import pathlib


def compile_markdown(markdown):
    blocks = []
    paragraph = []

    def flush_paragraph():
        if paragraph:
            blocks.append({"type": "paragraph", "text": " ".join(paragraph)})
            paragraph.clear()

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        indentation = len(raw_line) - len(raw_line.lstrip(" "))
        if not line:
            flush_paragraph()
            continue
        if line.lower().startswith("###t "):
            flush_paragraph()
            blocks.append({"type": "indented_heading3", "text": line[5:].strip()})
        elif line.startswith("### "):
            flush_paragraph()
            blocks.append({
                "type": "indented_heading3" if indentation >= 2 or raw_line.startswith("\t")
                else "heading3",
                "text": line[4:].strip(),
            })
        elif line.startswith("# "):
            flush_paragraph()
            blocks.append({"type": "heading1", "text": line[2:].strip()})
        elif line.startswith("## "):
            flush_paragraph()
            blocks.append({"type": "heading2", "text": line[3:].strip()})
        elif line.startswith("> "):
            flush_paragraph()
            blocks.append({"type": "note", "text": line[2:].strip()})
        elif line.startswith("- "):
            flush_paragraph()
            blocks.append({
                "type": "nested_bullet" if indentation else "bullet",
                "text": line[2:].strip(),
            })
        elif line.startswith("{{") and line.endswith("}}"):
            flush_paragraph()
            blocks.append({"type": "placeholder", "name": line[2:-2].strip()})
        elif indentation >= 2 or raw_line.startswith("\t"):
            flush_paragraph()
            blocks.append({"type": "indented_text", "text": line})
        else:
            paragraph.append(line)
    flush_paragraph()
    return {"schema_version": 1, "blocks": blocks}


def github_markdown(markdown):
    """Translate app-only changelog syntax into ordinary GitHub Markdown."""
    lines = []
    for raw_line in markdown.splitlines():
        stripped = raw_line.strip()
        indentation = len(raw_line) - len(raw_line.lstrip(" "))
        if stripped.lower().startswith("###t "):
            lines.append(f"- {stripped[5:].strip()}")
        elif stripped.startswith("### ") and (indentation >= 2 or raw_line.startswith("\t")):
            lines.append(f"- {stripped[4:].strip()}")
        else:
            lines.append(raw_line)
    return "\n".join(lines).rstrip() + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", nargs="?", default="CHANGELOG.md")
    parser.add_argument("output", nargs="?", default="changelog.json")
    parser.add_argument("--github-output")
    args = parser.parse_args()
    source = pathlib.Path(args.source)
    output = pathlib.Path(args.output)
    compiled = compile_markdown(source.read_text(encoding="utf-8"))
    compiled["source"] = source.name
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(compiled, indent=2) + "\n", encoding="utf-8")
    print(f"Compiled {source} to {output}")
    if args.github_output:
        github_output = pathlib.Path(args.github_output)
        github_output.parent.mkdir(parents=True, exist_ok=True)
        github_output.write_text(
            github_markdown(source.read_text(encoding="utf-8")), encoding="utf-8")
        print(f"Rendered GitHub release notes to {github_output}")


if __name__ == "__main__":
    main()
