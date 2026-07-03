# Control-Flow Graphs — the logic *inside* a routine (Tier-2 block scheme)

**Date:** 2026-07-03 · **Module:** `src/uci/analysis/cfg.py` · **Eval:** `evals/cfg_eval.py`

Where the graph shows *how programs connect* (calls, data, screens — the flow-level block scheme),
a **control-flow graph (CFG)** shows *how one routine decides* — its branches, loops, and returns as
a block scheme you can read as logic. This is the "full understanding of the logic inside" layer.

## What it is (and isn't)

- **Deterministic, on-demand analysis** — computed from source when asked (like `walkthrough` /
  `architecture`), **not persisted** as graph entities. So it never bloats the canonical graph, and
  every node cites a source line. (Promoting hot CFGs into the graph is a future option.)
- **Parsed fact, not narration** — no LLM in the structure. An optional LLM pass can *label* blocks
  in business terms later, but it can never invent control flow (same honesty contract as the rest).
- **Per-language builders, one model.** Ships **Python** (stdlib `ast`, fully faithful) and **COBOL**
  (procedure-division: `IF`/`ELSE`/`END-IF`, `EVALUATE`/`WHEN`, `PERFORM … UNTIL` loops, inline
  `PERFORM … END-PERFORM`, `GO TO`, `GOBACK`/`STOP RUN`, paragraph fall-through, and `PERFORM` shown
  as a call into its paragraph). HLASM rides on the Che4z LSP expanded view; JS/TS waits on a real
  parser. See `lsp-refactoring-recommendations.md` for the per-language feasibility.

  COBOL note: the builder targets well-structured code with explicit scope terminators
  (`END-IF`/`END-EVALUATE`/`END-PERFORM`); deeply nested period-only scoping is approximated.

## Model

| Node kind | Meaning | Mermaid shape |
| --- | --- | --- |
| `entry` / `exit` | start / end | stadium `([…])` |
| `decision` | `if` / `match` branch point | rhombus `{…}` |
| `loop` | `while` / `for` header | hexagon `{{…}}` |
| `call` | bare call statement | subroutine `[[…]]` |
| `return` / `raise` | terminates the routine | parallelogram `[/…/]` |
| `break` / `continue` | loop control | parallelogram |
| `statement` | anything else | rectangle `[…]` |

Edge labels carry the branch semantics: `true` / `false` (decisions), `loop` / `exit` (loop
header), `case …` (match), `when …` (COBOL `EVALUATE`), `perform` (COBOL `PERFORM` → its paragraph),
`except …` (try). A `while`/`for`/`PERFORM UNTIL` header gets a **back-edge** from the end of its body
and an `exit` edge to what follows; `return`/`raise`/`GOBACK` connect straight to `exit`; `continue`
targets the loop header; `break` targets the after-loop node; `GO TO` transfers to the target
paragraph; consecutive COBOL paragraphs fall through, per COBOL semantics.

## Use it

```bash
uci cfg <function>            # Mermaid flowchart of the routine's logic
uci cfg <function> --json     # nodes, edges, per-kind stats, and the Mermaid string
```

MCP: the `control_flow` tool returns the same structured JSON for agents. Programmatic:
`Engine.control_flow(symbol)`.

Example (`uci cfg post`):

```mermaid
flowchart TD
  n0(["start"])
  n2["total = 0"]
  n3{{"for t in txns"}}
  n4{"if t < 0"}
  n5{"if balance + t < 0"}
  n6[/"return 'overdraft'"/]
  n0 --> n2 --> n3
  n3 -->|loop| n4
  n4 -->|true| n5
  n5 -->|true| n6
  n4 -->|false| n8["balance += t"]
```

## Correctness — the eval

`evals/cfg_eval.py` runs the builders over fixtures covering every construct family — Python
(`if/elif/else`, `while` + `break`/`continue`, `match/case`, `try/finally`) and COBOL
(`IF/ELSE/END-IF`, `EVALUATE/WHEN`, out-of-line `PERFORM UNTIL`, inline `PERFORM … END-PERFORM`,
`GO TO`, `GOBACK`, fall-through) — and checks the invariants a correct CFG must satisfy: single
entry/exit, **full reachability both ways** (every node is reachable from entry and can reach exit),
well-formed decision forks (`true`+`false`), and loop wiring (back-edge + exit) — plus per-fixture
golden counts. It scores **100/100** and is CI-gated by `tests/test_cfg_eval.py`. Adding a language
means adding a builder + fixtures to the same harness.

## Next

1. **Dashboard view** — render the Mermaid on the symbol-detail page.
2. **Optional LLM narration** — business-language labels per block, layered on the deterministic CFG.
3. **HLASM** — via the Che4z LSP expanded view (basic-block CFG); **JS/TS** once a real parser lands.
