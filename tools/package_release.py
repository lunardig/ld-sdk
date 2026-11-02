"""Assembles a self-contained SDK release: this repository at HEAD, the pinned
ld-interfaces at vendor/ld-interfaces (so every relative path still works), and the
engine binaries under engine/. Nothing untracked is included.

    python tools/package_release.py --version 0.1.0-alpha.1 \\
        --engines dist/engines --wasm dist/wasm --engine-info dist/ENGINE.json \\
        --engine-license dist/LICENSE.md --engine-notice dist/NOTICE.md \\
        --engine-third-party dist/THIRD-PARTY-NOTICES.md --out dist

`--engines` holds one folder per platform (linux-x86_64, macos-arm64, windows-x86_64),
each with ld-engine-cli or ld-engine-cli.exe. Writes ld-sdk-<version>.tar.gz and .zip,
and prints their SHA-256.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DROP = {".gitmodules", ".github", ".gitignore"}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def extract_head(repo: Path, dest: Path) -> None:
    data = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", "HEAD"], check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as t:
        t.extractall(dest, filter="data")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--version", required=True)
    ap.add_argument("--engines", type=Path, required=True)
    ap.add_argument("--wasm", type=Path)
    ap.add_argument("--engine-info", type=Path, required=True, help="JSON: engine commit and version")
    ap.add_argument("--engine-license", type=Path, required=True, help="ld-engine's LICENSE.md")
    ap.add_argument("--engine-notice", type=Path, required=True, help="ld-engine's NOTICE.md (license summary and Additional Permission)")
    ap.add_argument("--engine-third-party", type=Path, required=True, help="tools/third_party.py output for the engine binaries")
    ap.add_argument("--out", type=Path, default=ROOT / "dist")
    a = ap.parse_args()

    name = f"ld-sdk-{a.version}"
    work = Path(tempfile.mkdtemp())
    top = work / name
    top.mkdir()
    extract_head(ROOT, top)
    for d in DROP:
        p = top / d
        shutil.rmtree(p) if p.is_dir() else p.unlink(missing_ok=True)
    iface = ROOT / "vendor/ld-interfaces"
    (top / "vendor/ld-interfaces").mkdir(parents=True, exist_ok=True)
    extract_head(iface, top / "vendor/ld-interfaces")
    for d in DROP:
        p = top / "vendor/ld-interfaces" / d
        shutil.rmtree(p) if p.is_dir() else p.unlink(missing_ok=True)

    platforms = []
    for pdir in sorted(x for x in a.engines.iterdir() if x.is_dir()):
        for exe in ("ld-engine-cli", "ld-engine-cli.exe"):
            if (pdir / exe).exists():
                dst = top / "engine" / pdir.name
                dst.mkdir(parents=True, exist_ok=True)
                shutil.copy2(pdir / exe, dst / exe)
                (dst / exe).chmod(0o755)
                platforms.append(pdir.name)
    if not platforms:
        raise SystemExit("no engine binaries found under --engines")
    if a.wasm:
        shutil.copytree(a.wasm, top / "engine/wasm", ignore=shutil.ignore_patterns("*.d.ts", "package.json", ".gitignore"))
    # The engine's FSL clock starts on the date a version is made available: this release.
    released = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    info = json.loads(a.engine_info.read_text())
    # SHA-256 of each engine binary: the engine's first-availability record (ld-engine
    # AVAILABILITY.md) identifies versions by commit and content.
    hashes = {str(f.relative_to(top / "engine")): hashlib.sha256(f.read_bytes()).hexdigest()
              for f in sorted((top / "engine").rglob("*")) if f.is_file() and (f.name.startswith("ld-engine-cli") or f.suffix == ".wasm")}
    info |= {"platforms": platforms, "wasm": bool(a.wasm), "license": "FSL-1.1-ALv2 with the Additional Permission in NOTICE.md",
             "made_available": released, "sha256": hashes}
    (top / "engine/ENGINE.json").write_text(json.dumps(info, indent=2) + "\n")
    shutil.copy2(a.engine_license, top / "engine/LICENSE.md")
    shutil.copy2(a.engine_notice, top / "engine/NOTICE.md")
    shutil.copy2(a.engine_third_party, top / "engine/THIRD-PARTY-NOTICES.md")

    manifest = {
        "release": a.version,
        "built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ld-sdk": git(ROOT, "rev-parse", "HEAD"),
        "ld-interfaces": git(iface, "rev-parse", "HEAD"),
        "ld-engine": info.get("commit"),
        "engine_version": info.get("engine_version"),
        "engine_made_available": released,
        "licenses": {"sdk, interfaces, catalog, client, examples": "Apache-2.0", "engine": "FSL-1.1-ALv2 with an Additional Permission (engine/NOTICE.md)"},
        "catalog_hash": json.loads((ROOT / "fixtures/alpha/create.json").read_text())["genesis"]["catalog_hash"],
    }
    (top / "RELEASE.json").write_text(json.dumps(manifest, indent=2) + "\n")

    a.out.mkdir(parents=True, exist_ok=True)
    tgz, zp = a.out / f"{name}.tar.gz", a.out / f"{name}.zip"
    with tarfile.open(tgz, "w:gz") as t:
        t.add(top, arcname=name)
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(top.rglob("*")):
            if f.is_file():
                zi = zipfile.ZipInfo.from_file(f, f.relative_to(work).as_posix())
                with f.open("rb") as fh:
                    z.writestr(zi, fh.read(), zipfile.ZIP_DEFLATED)
    for f in (tgz, zp):
        print(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}")
    shutil.rmtree(work)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
