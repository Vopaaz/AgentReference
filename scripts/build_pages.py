from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


REPOSITORY = "Vopaaz/AgentReference"
BASE_URL = "https://vopaaz.github.io/AgentReference/"
TEXT_MEDIA_TYPES = {
    ".gitignore": "text/plain",
    ".md": "text/markdown",
    ".py": "text/x-python",
    ".txt": "text/plain",
    ".yml": "text/yaml",
    ".yaml": "text/yaml",
}


def run_git(args: list[str], cwd: Path) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def tracked_files(root: Path) -> list[Path]:
    output = run_git(["ls-files"], root)
    return [Path(line) for line in output.splitlines() if line]


def media_type_for(rel_path: Path) -> str:
    if rel_path.name in TEXT_MEDIA_TYPES:
        return TEXT_MEDIA_TYPES[rel_path.name]

    if rel_path.suffix in TEXT_MEDIA_TYPES:
        return TEXT_MEDIA_TYPES[rel_path.suffix]

    media_type, _ = mimetypes.guess_type(rel_path.as_posix())
    return media_type or "application/octet-stream"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def validate_output(root: Path, output: Path, files: list[Path]) -> None:
    resolved_root = root.resolve()
    resolved_output = output.resolve()

    if resolved_output == resolved_root:
        raise ValueError("Output directory cannot be the repository root.")

    for rel_path in files:
        tracked_path = (root / rel_path).resolve()
        if tracked_path == resolved_output or tracked_path.is_relative_to(resolved_output):
            raise ValueError(
                f"Output directory would overwrite tracked file: {rel_path.as_posix()}"
            )


def file_entry(root: Path, rel_path: Path) -> dict[str, object]:
    source = root / rel_path
    rel = rel_path.as_posix()
    return {
        "path": rel,
        "url": BASE_URL + quote(rel, safe="/"),
        "size": source.stat().st_size,
        "sha256": sha256(source),
        "media_type": media_type_for(rel_path),
    }


def build_site(root: Path, output: Path) -> dict[str, object]:
    files = tracked_files(root)
    validate_output(root, output, files)

    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    entries = []
    for rel_path in files:
        copy_file(root / rel_path, output / rel_path)
        entries.append(file_entry(root, rel_path))

    (output / ".nojekyll").write_text("", encoding="utf-8")

    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    source_ref = os.environ.get("GITHUB_SHA") or run_git(["rev-parse", "HEAD"], root)
    manifest = {
        "repository": REPOSITORY,
        "base_url": BASE_URL,
        "generated_at": generated_at,
        "source_ref": source_ref,
        "files": entries,
    }

    manifest_text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    (output / "index.json").write_text(manifest_text, encoding="utf-8")
    (output / "manifest.json").write_text(manifest_text, encoding="utf-8")

    lines = [
        REPOSITORY,
        BASE_URL,
        "",
        "Manifest:",
        f"{BASE_URL}index.json",
        "",
        "Files:",
    ]
    lines.extend(entry["url"] for entry in entries)
    (output / "index.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build raw GitHub Pages hosting files for AgentReference."
    )
    parser.add_argument(
        "--output",
        default="site",
        type=Path,
        help="Output directory for the static Pages artifact.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output
    if not output.is_absolute():
        output = root / output

    manifest = build_site(root, output)
    print(f"Built {len(manifest['files'])} files into {output}")


if __name__ == "__main__":
    main()
