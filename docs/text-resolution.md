# Raw text and resolved text

KiCad stores variables and expressions as text. The displayed result depends on the item, sheet occurrence, project, and runtime data.
Resolution returns both forms. It never changes the input document or saves a file.

## Resolve a string

```python
from eedesigncore import TextResolutionContext, resolve_text

context = TextResolutionContext(variables={"SUPPLY": "3.3"})
result = resolve_text("Supply ${SUPPLY} V, half @{${SUPPLY}/2} V", context)
assert result.raw == "Supply ${SUPPLY} V, half @{${SUPPLY}/2} V"
assert result.resolved == "Supply 3.3 V, half 1.65 V"
assert result.complete
```

Each result includes diagnostics. Unknown variables, failed expressions, circular references, and limits remain visible.
A failed expression does not prevent other expressions in the same string from resolving.
Escaped variable and expression markers remain literal.

## Resolve a loaded document

```python
from eedesigncore import resolve_document_text, render_schematic_svg

resolved = resolve_document_text(schematic, context)
for source_path, text in resolved.texts.items():
    print(source_path, text.raw, text.resolved, text.diagnostics)
svg = render_schematic_svg(resolved.document, view_box=view_box)
```

The returned document is a display copy. Do not save it over the source.
A display copy can contain separate embedded symbol definitions for each placed symbol.
Records use source item paths. The input retains expressions, identities, geometry, and saved fields.

Board, footprint, symbol, and schematic SVG operations accept `text_context`.
They resolve text before wrapping and glyph placement. Existing calls without this argument retain their literal behavior.
Use `resolve_document_text` first when a caller needs diagnostics and the text records.
Do not supply `text_context` again when exporting an already resolved display copy.
Native schematic preparation uses this resolver and exposes `resolved_texts` on each page.

## Select a sheet occurrence

```python
from eedesigncore import context_for_sheet, resolve_document_text

context = context_for_sheet(hierarchy, selected_instance_path)
resolved = resolve_document_text(selected_node.schematic, context)
```

Use the complete loaded hierarchy for repeated sheets and cross-sheet references.
The selected occurrence supplies its saved reference, unit, page, sheet fields, and ancestor fields.
The project supplies text variables, field templates, saved net-class assignments, and patterns.
Conflicting saved reference or unit assignments raise an error.
An isolated schematic cannot infer its filename, project name, or omitted child sheets. Supply those inputs explicitly.

## Supported text contexts

| Context | Inputs used |
| --- | --- |
| Free text, text boxes, table cells | Nested variables, expressions, title block, sheet and project scope |
| Placed symbols | Fields, selected occurrence, variants, library identity, pin names, alternate pins, net names and classes |
| Library symbols | Fields, symbol identity, graphics and text boxes |
| Footprints | Fields, variants, library identity, pad nets and pin functions |
| Board drawings and dimensions | Board properties, title block, layer names, dimension text and format strings |
| Labels and sheet pins | Connection type, net names, classes, parent sheet context |
| Global labels | Intersheet references and saved display options |
| Schematic tables | Zero-based rows and columns, cell addresses, cell references |
| Board tables | One-based rows and columns and cell addresses |
| Cross references | Saved UUID, occurrence path, or reference, with an optional variant |

Symbol field lookup ignores case. Custom footprint and sheet fields retain KiCad's case rules.
Schematic connectivity uses the existing file-IO connection engine. Explicit connection and net-class maps can override its results.

## Global-label page references

The loaded hierarchy supplies page references for matching global-label names.
The project settings under `schematic.drawing` control their display:

| Setting | Effect |
| --- | --- |
| `intersheets_ref_show` | Show or hide the page-reference field on the resolved display copy |
| `intersheets_ref_own_page` | Include or exclude the selected sheet occurrence |
| `intersheets_ref_short` | With more than two references, show the first and last separated by `..` |
| `intersheets_ref_prefix` | Text before the reference list |
| `intersheets_ref_suffix` | Text after the reference list |

Short mode does not require consecutive page numbers. With two references, it retains the comma-separated list.
An explicit visibility setting overrides the field's saved visibility for display.
Without that setting, the saved visibility remains unchanged because local KiCad preferences are unavailable.
The source schematic and stored `${INTERSHEET_REFS}` text remain unchanged.
Use the resolved display copy for rendering and bounds measurements.

## Expressions

The evaluator supports KiCad arithmetic, comparisons, strings, numeric prefixes, units, and function calls.
It supports formatting, string operations, conditional selection, math, E-series selection, dates, time, random values, and Git functions.
Function arguments evaluate before the function. A failed unused conditional argument still causes an expression error, as in KiCad.
No expression executes Python or shell commands.

KiCad's resistor-style numeric conversion has surprising behavior in KiCad 10.0.3.
For example, `@{4k7}` displays `47000`. File-IO preserves this observed behavior.
The saved comparison fixture records exact KiCad output rather than electrical-value assumptions.

## Runtime inputs

```python
from eedesigncore import capture_text_context

context = capture_text_context(repository=explicit_repository_path)
```

This helper captures the local clock and a random source. Git access requires an explicit local repository.
It never chooses another clone from the current working directory.
The Git helper uses read-only commands and confines file arguments to that repository.
It captures the selected commit and retains query results. Branch and worktree state are read when first requested.
For repeatable exports, supply a fixed clock, random function, and provider results in `TextResolutionContext`.

A variable provider receives the source item path and variable name.
An expression function provider receives the function name and evaluated arguments.
Return `None` when data is absent. Return the displayed string or numeric result when data exists.
These providers support application-owned simulation results and other external state.

## Explicit limits

- Simulation operating points require the caller's variable provider. File-IO does not run a simulator.
- Symbol and sheet flags affected by rule-area membership require supplied results when local flags cannot determine the answer.
  Directive flags on rule-area borders resolve from saved geometry.
  Net classes affected by rule areas require saved assignments or an explicit net-class map.
- Bus-dependent net-class inheritance is not derived. Supply the effective class through the context when required.
- User language and locale can change KiCad's displayed status phrases and time strings. Built-in status phrases use English.
- Invalid calendar dates report an error instead of applying platform-specific date normalization.
- Resolution has depth, output-length, and operation limits. Git queries also have time, result-size, and history limits.
- A caller must supply the selected variant and its description. Resolution does not infer a viewer's active selection.

Missing required context produces diagnostics. Check `complete` before claiming that every value resolved.
The drawing-sheet format remains outside this exporter.

## Export a saved project with Git values

Reading a schematic alone does not load its project variables or Git metadata.
Supply the project and its own repository directory. Do not use a different clone.

```python
from dataclasses import replace
from eedesigncore import (
    ProjectFile, Schematic, capture_text_context,
    resolve_document_text, render_schematic_svg,
)

project = ProjectFile.from_file(project_path)
schematic = Schematic.from_file(schematic_path)
context = replace(
    capture_text_context(repository=project_directory),
    project=project,
    variables=project.text_variables,
    filename=str(schematic_path),
)
resolved = resolve_document_text(schematic, context)
svg = render_schematic_svg(resolved.document, view_box=view_box)
```

The caller can inspect each raw value, resolved value, and diagnostic before export.
Missing Git context leaves Git expressions unresolved. Font selection cannot resolve those values.

KiCad accepts an omitted final brace at the end of an expression or variable.
The resolver follows this behavior. The raw value still retains the omitted brace.
An expression that still cannot evaluate keeps its diagnostic.
