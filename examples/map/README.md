# Site Field and Map — SDK example (Revision 21)

Standalone, no-build HTML/JavaScript tile explorer. It uses WebCrypto and does not use `ldcp` or the engine.

**Prototype limitation:** This initial SDK sample renders a verified 25 × 25 projected parcel grid for a selected tile; the "global/north/south" selector is a tile filter, **not yet** a georeferenced global/polar lunar basemap. Historical survey selection by trusted external anchors is supported by changing the pinned pair.

## Run with a locally generated synthetic field

From a checkout containing both sibling repositories:

```sh
cd ld-field
python generate_demo.py
cd ../ld-sdk
python -m http.server 8000
```

Python's server only exposes the SDK checkout. To serve the field files on the same origin, temporarily copy or symlink the generated `ld-field/demo-field` directory to `ld-sdk/examples/map/field` (not committed), subject to local server symlink access. Alternatively, serve the field from a CORS-enabled static host and pass `?base=https://...`.

Read the externally generated `ld-field/trust-anchor-demo.json` and open:

```text
http://localhost:8000/examples/map/?root=<field_root>&index=<index_hash>&version=synthetic-v1
```

Use the exact values from the separate trust anchor; NEVER derive them from the same host as the field. The map does not authenticate world root-history signatures or `labels.bin`.

## Verification behavior

- Index is hashed with `H("ld.field.index.v0", index.bin)`.
- Index keys are required to be strictly increasing and 6-byte aligned.
- The selected tile's header, format, length, reserved bytes, bitmaps and out-of-zone no-data are validated.
- Tile membership is checked against the pinned `field_root` using exact `tree.bin` byte-range reads and rev21 odd-node promotion.
- No proof is accepted on the basis of values in `field.json`.

This is a **demonstration**, not a certified lunar survey. Synthetic values are labeled.
