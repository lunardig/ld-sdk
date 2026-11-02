"""Checks an SDK release on its own: unpacks it into a fresh folder, away from any clone,
and runs everything a player would, using only what is inside. Exits non-zero on the
first failure.

    python tools/verify_release.py dist/ld-sdk-0.1.0-alpha.1.tar.gz [--platform linux-x86_64] [--wasm]

Checks: the client's tests; the guide's walkthrough; the loop controller through a lunar
night; the conformance vectors and fixtures; with --wasm, the walkthrough and vectors on
the browser build too, which must agree with the native engine.
"""
from __future__ import annotations

import argparse
import os
import platform as plat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path


def default_platform() -> str:
    s, m = plat.system().lower(), plat.machine().lower()
    arch = "arm64" if m in ("arm64", "aarch64") else "x86_64"
    return {"darwin": f"macos-{arch}", "windows": f"windows-{arch}"}.get(s, f"linux-{arch}")


def run(label: str, cmd: list[str], cwd: Path, env: dict | None = None) -> str:
    print(f"--- {label}", flush=True)
    r = subprocess.run(cmd, cwd=cwd, env={**os.environ, **(env or {})}, capture_output=True, text=True)
    tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-4:])
    print(tail)
    if r.returncode != 0:
        raise SystemExit(f"FAILED: {label} (exit {r.returncode})")
    return r.stdout


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("archive", type=Path)
    ap.add_argument("--platform", default=default_platform())
    ap.add_argument("--wasm", action="store_true", help="also check the WebAssembly engine (needs Node)")
    a = ap.parse_args()
    work = Path(tempfile.mkdtemp(prefix="ld-sdk-verify-"))
    if a.archive.suffix == ".zip":
        zipfile.ZipFile(a.archive).extractall(work)
    else:
        with tarfile.open(a.archive) as t:
            t.extractall(work, filter="data")
    top = next(work.iterdir())
    exe = top / "engine" / a.platform / ("ld-engine-cli.exe" if a.platform.startswith("windows") else "ld-engine-cli")
    if not exe.exists():
        raise SystemExit(f"no engine for {a.platform} in the release: {exe}")
    exe.chmod(0o755)
    for f in ("LICENSE.md", "NOTICE.md", "THIRD-PARTY-NOTICES.md", "ENGINE.json"):
        if not (top / "engine" / f).exists():
            raise SystemExit(f"FAILED: the release has no engine/{f}")
    py = sys.executable
    engine = str(exe)
    print(f"verifying {top.name} with {a.platform} in {work}")
    run("client tests", [py, "-m", "unittest", "discover", "-s", "python/tests"], top, {"LD_ENGINE": engine})
    run("guide walkthrough", [py, "examples/first_controller.py", "--engine", engine], top)
    run("scenario for the web game, solved", [py, "examples/make_scenario.py", "--engine", engine, "--out", str(work / "ice-jam.json"), "--solve"], top)
    run("loop controller, fuel-cell night", [py, "examples/loop_controller.py", "--engine", engine, "--night", "fuel-cell"], top)
    run("conformance vectors", [py, "tools/run_vectors.py", engine, "vendor/ld-interfaces/vectors"], top)
    run("fixtures", [py, "tools/check_fixtures.py", engine], top)
    if a.wasm:
        wasm = "node engine/wasm-cli.mjs"
        native = run("walkthrough, native (for comparison)", [py, "examples/first_controller.py", "--engine", engine], top)
        browser = run("walkthrough, browser build", [py, "examples/first_controller.py", "--engine", wasm], top)
        if native != browser:
            raise SystemExit("FAILED: the browser build and the native engine disagree")
        run("conformance vectors, browser build", [py, "tools/run_vectors.py", wasm, "vendor/ld-interfaces/vectors"], top)
    print(f"ok: {top.name} works on its own")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
