from __future__ import annotations

import argparse
import hashlib
import html
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


def render_text_html(source: Path, target: Path, source_rel: Path) -> None:
    content = source.read_text(encoding="utf-8")
    source_path = source_rel.as_posix()
    data = {
        "source_path": source_path,
        "content": content,
    }
    data_json = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace(
        "</", "<\\/"
    )
    page = "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            f"<title>{html.escape(source_path)}</title>",
            "</head>",
            "<body>",
            f'<pre id="content">{html.escape(content)}</pre>',
            f'<script id="data" type="application/json">{data_json}</script>',
            "</body>",
            "</html>",
            "",
        ]
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(page, encoding="utf-8")


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


def published_path_for(rel_path: Path) -> Path:
    if rel_path.suffix == ".txt":
        return rel_path.with_suffix(".html")
    return rel_path


def file_entry(
    source: Path,
    target: Path,
    source_rel: Path,
    published_rel: Path,
) -> dict[str, object]:
    published = published_rel.as_posix()
    return {
        "path": published,
        "url": BASE_URL + quote(published, safe="/"),
        "size": target.stat().st_size,
        "sha256": sha256(target),
        "media_type": media_type_for(published_rel),
        "source_path": source_rel.as_posix(),
        "source_size": source.stat().st_size,
        "source_sha256": sha256(source),
        "source_media_type": media_type_for(source_rel),
    }


def build_site(root: Path, output: Path) -> dict[str, object]:
    files = tracked_files(root)
    validate_output(root, output, files)

    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    entries = []
    for rel_path in files:
        source = root / rel_path
        published_rel = published_path_for(rel_path)
        target = output / published_rel
        if rel_path.suffix == ".txt":
            render_text_html(source, target, rel_path)
        else:
            copy_file(source, target)
        entries.append(file_entry(source, target, rel_path, published_rel))

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

    links = "\n".join(
        f'<li><a href="{quote(str(entry["path"]), safe="/")}">'
        f'{html.escape(str(entry["path"]))}</a></li>'
        for entry in entries
    )
    index_html = "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            f"<title>{REPOSITORY}</title>",
            "</head>",
            "<body>",
            f"<h1>{REPOSITORY}</h1>",
            '<p><a href="index.json">index.json</a></p>',
            f"<ul>{links}</ul>",
            "</body>",
            "</html>",
            "",
        ]
    )
    (output / "index.html").write_text(index_html, encoding="utf-8")

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
