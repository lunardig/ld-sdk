"""Lists the third-party crates compiled into an engine binary, with their license texts,
as Markdown, for engine/THIRD-PARTY-NOTICES.md in a release.

    python tools/third_party.py --workspace path/to/ld-engine --crate ld-engine-cli --crate ld-engine-wasm > THIRD-PARTY-NOTICES.md

It follows each crate's normal dependencies (not build or dev dependencies, not
proc-macros, which never reach the binary) for every target the release ships, and
reproduces the license file each crate ships. Where a crate offers MIT OR Apache-2.0,
the MIT text is reproduced, which is the license elected for it.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

TARGETS = ["x86_64-unknown-linux-gnu", "aarch64-apple-darwin", "x86_64-pc-windows-msvc", "wasm32-unknown-unknown"]
OWN = {"ld-engine", "ld-engine-cli", "ld-engine-wasm", "ld-sdk", "ld-mock", "ld-mock-wasm", "ld-interfaces-types"}


def license_text(manifest_dir: Path, expr: str) -> str:
    files = {p.name.upper(): p for p in manifest_dir.iterdir() if p.name.upper().startswith(("LICENSE", "LICENCE", "COPYING", "UNLICENSE"))}
    prefer = ["LICENSE-MIT", "LICENSE-MIT.TXT", "LICENSE-MIT.MD"] if "MIT" in expr else []
    prefer += ["LICENSE", "LICENSE.TXT", "LICENSE.MD", "LICENSE-APACHE", "LICENSE-APACHE.TXT", "LICENSE-ZLIB", "COPYING"]
    for name in prefer:
        if name in files:
            return files[name].read_text(errors="replace")
    return next(iter(files.values())).read_text(errors="replace") if files else ""


def crates(workspace: Path, roots: list[str]) -> list[dict]:
    seen: dict[str, dict] = {}
    for target in TARGETS:
        meta = json.loads(subprocess.run(["cargo", "metadata", "--format-version", "1", "--filter-platform", target],
                                         cwd=workspace, check=True, capture_output=True, text=True).stdout)
        pkgs = {p["id"]: p for p in meta["packages"]}
        nodes = {n["id"]: n for n in meta["resolve"]["nodes"]}
        todo = [pid for pid, p in pkgs.items() if p["name"] in roots]
        done = set()
        while todo:
            pid = todo.pop()
            if pid in done:
                continue
            done.add(pid)
            for dep in nodes[pid]["deps"]:
                if not any(k["kind"] is None for k in dep["dep_kinds"]):
                    continue
                p = pkgs[dep["pkg"]]
                if any("proc-macro" in t["kind"] for t in p["targets"]):
                    continue
                todo.append(dep["pkg"])
        for pid in done:
            p = pkgs[pid]
            if p["name"] in OWN:
                continue
            key = f"{p['name']} {p['version']}"
            if key not in seen:
                expr = p.get("license") or "see the crate's license file"
                seen[key] = {"name": p["name"], "version": p["version"], "license": expr, "repo": p.get("repository") or "",
                             "text": license_text(Path(p["manifest_path"]).parent, expr)}
    return sorted(seen.values(), key=lambda c: (c["name"], c["version"]))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--workspace", type=Path, required=True)
    ap.add_argument("--crate", action="append", required=True)
    a = ap.parse_args()
    cs = crates(a.workspace.resolve(), a.crate)
    out = ["# Third-party notices", "",
           "The Lunar Dig engine binaries in this release (native and WebAssembly) include the components below, by "
           "their authors, each used under the license shown. Where a component offers MIT OR Apache-2.0, MIT is elected.", ""]
    for c in cs:
        lic = c["license"].replace("MIT OR Apache-2.0", "MIT (elected from MIT OR Apache-2.0)").replace("Apache-2.0 OR MIT", "MIT (elected from Apache-2.0 OR MIT)").replace("Unlicense OR MIT", "MIT (elected from Unlicense OR MIT)")
        out += [f"## {c['name']} {c['version']}", "", f"License: {lic}" + (f". Source: {c['repo']}" if c["repo"] else ""), "", "```",
                c["text"].strip() or "License text not shipped with the crate; see its source.", "```", ""]
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
