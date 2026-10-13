# 0.26.0 snapshots of the examples (transitional; re/07, P-16)

Byte-identical copies of every `examples/*/input.json` as committed before the
re/07 example migration (`2eed3f3`), kept so the tests of the legacy 0.26.0
path (`GSASII_Rietveld` / `GSASII_SPF`: `powderline.schema`, the legacy TOPAS
writer and roundtrip, the legacy easydiffraction path, the 0.26.0 converter's
example sweep) keep their inputs after `examples/` moved to the native
`gsasii.*` / `topas.*` / `easydiffraction.*` recipes. Same blobs as the
history (no repository growth). Deleted together with the legacy path and
its tests in the re/07 deletion step; nothing new may depend on this directory.
