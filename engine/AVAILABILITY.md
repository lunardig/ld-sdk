# When each engine version was made available

ioMetics LC keeps this record so that anyone can establish when each version of the
engine was first made available under `FSL-1.1-ALv2` with the Additional Permission
(`NOTICE.md`), and so when it becomes available under the Apache License, Version 2.0.
It is evidence of those dates and does not change them. Times are UTC.

The license was adopted on Nov 1, 2026 (commit `01bb284`). The engine repository is
private, so the routes so far are ld-sdk releases and builds delivered by lunardig.com.
ld-sdk, which ships the engine binaries, became public on Nov 2, 2026.

| Engine commit | First made available | Route | Apache-2.0 from | Content hash |
| --- | --- | --- | --- | --- |
| `01bb284` | 2026-11-01 23:44 | lunardig.com build (web `346f484`) | 2028-11-01 | not recorded |
| `ff00491` | 2026-11-02 01:11 | lunardig.com build (web `4f25cb2`) | 2028-11-02 | not recorded |
| `56215391` | 2026-11-02 01:13 | ld-sdk 0.1.0-alpha.1 | 2028-11-02 | see the release's `SHA256SUMS` / notes |
| `7aa3054` | 2026-11-02 06:12 | lunardig.com build (web `03ac133`) | 2028-11-02 | not recorded |
| `535227e` | 2026-11-02 06:29 | ld-sdk 0.1.0-alpha.2 (also 0.1.0-alpha.3) | 2028-11-02 | see the release notes |
| `5f53654` | 2026-11-02 15:28 | lunardig.com build (web `d80eaee`) | 2028-11-02 | not recorded |
| `43b740a` | 2026-11-02 15:50 | lunardig.com build (web `ea33754`) | 2028-11-02 | not recorded |
| `e7eca80` | 2026-11-02 17:44 | ld-sdk 0.1.0-alpha.4 | 2028-11-02 | in the release's `engine/ENGINE.json`: linux `a0d302c3…`, macos `49368213…`, windows `c93b5f6c…`, wasm `047f153b…` |

**How the rows were established.** ld-sdk rows come from the GitHub releases (publication
time and the engine commit in their notes). lunardig.com rows are the successful deploys
of ld-web whose Terms of Use and notices already named this license (from web `346f484`
on). Each deploy builds ld-engine `main` as it stood when the deploy started; the engine
commit shown is that one, reconstructed from commit times. Deploys from now on record the
exact engine commit and the SHA-256 of the WebAssembly file in `/version.json`, and SDK
releases record the SHA-256 of each engine binary in `engine/ENGINE.json`; later rows
carry those hashes.

Builds delivered by lunardig.com before web `346f484` were not offered under this license.

A release's own copy of this file lists the versions before it; the copy in the repository
is complete.
