# Schematic reference files

The unchanged-output checks compare saved files with the installed KiCad command-line tool.
On 2026-09-23, these files were refreshed from verified KiCad 10.0.4 save output:

- `t3b_rule_area.kicad_sch`
- `t3b_table.kicad_sch`

Only the label property name changed from `Net Class` to `Netclass`.
The values, positions, UUIDs, and item order did not change. The files use LF line endings.
The checks still require full text equality after KiCad saves the files.

KiCad 10.0.4 changed this field handling from the pinned source reference, KiCad 10.0.3.
These files certify the installed comparison release. They do not establish identical output across both patch releases.
The Python reader and writer retain supplied label field names.

Source details are in `context/audit/2026-09-23_17-54-41_schematic-netclass-field-oracle-normalization.md`.
