"""Package the same tested wheel with each Agent's own templates and guide."""

import hashlib
import tomllib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"


def build():
    with (ROOT / "pyproject.toml").open("rb") as project:
        version = tomllib.load(project)["project"]["version"]
    wheel = DIST / f"vdra-{version}-py3-none-any.whl"
    source = DIST / f"vdra-{version}.tar.gz"
    for artifact in (wheel, source):
        if not artifact.is_file():
            raise SystemExit(f"Build {artifact.name} first with python -m build")
    shared = [
        wheel,
        source,
        ROOT / "LICENSE",
        ROOT / "NOTICE",
        ROOT / "docs/INSTALL.md",
        ROOT / "docs/PROJECTS.md",
        ROOT / "examples/ov.conf.example",
        ROOT / "examples/work-memory.json",
    ]
    bundles = []
    for agent, files in {
        "codex": ("CONNECT.md", "config.toml.example", "ovcli.conf.example"),
        "dsh": ("CONNECT.md", "ovcli.conf.example"),
        "mcp": ("CONNECT.md", "mcp.json.example"),
    }.items():
        target = DIST / f"vdra-{version}-{agent}.zip"
        with ZipFile(target, "w", compression=ZIP_DEFLATED) as archive:
            for path in shared:
                member = path.name if path.parent == DIST else path.relative_to(ROOT)
                archive.write(path, str(member))
            archive.writestr(
                "README.md",
                f"# vdra for {agent}\n\n"
                "[Install vdra](docs/INSTALL.md) and "
                f"[connect your Agent](integrations/{agent}/CONNECT.md).\n",
            )
            for name in files:
                path = ROOT / "integrations" / agent / name
                archive.write(path, path.relative_to(ROOT))
        bundles.append(target)

    hashes = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}"
        for path in (wheel, source, *bundles)
    ]
    (DIST / "SHA256SUMS").write_text("\n".join(hashes) + "\n", encoding="utf-8")


if __name__ == "__main__":
    build()
