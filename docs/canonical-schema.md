# UCI Canonical Schema

The canonical schema is the heart of UCI. Every parser, analyzer, and adapter normalizes what it finds
into these **entity types** and **relationship types**. The schema is deliberately broader than "code
symbols" so it can absorb tests, data, runtime/config, ownership/evolution, business/domain, and legacy
modernization facts over time.

Implemented in `uci.core.entities`, `uci.core.relationships`, `uci.core.schema`, and validated by
`uci.core.schema.validate_relationship()`.

## 1. Identity & provenance

Every node has a **stable, deterministic ID** so re-indexing is idempotent and cross-references resolve.

```
node id  = "{kind}:{repo}:{path}:{qualified_name}[@{disambiguator}]"
edge id  = "{type}:{src_id}->{dst_id}[#{ordinal}]"
```

IDs are produced by `uci.core.ids`. Collisions (overloads, shadowing) append `@{start_line}`.

Every node/edge carries **provenance** (`uci.core.provenance.Provenance`):

```python
Provenance(repo_id, path, start_line, end_line, extractor, confidence)
```

This guarantees the principle *"all extracted facts trace back to files and line ranges."*

## 2. Entity types (`EntityType`)

Grouped by domain. **Tier legend:** `*` = ✅ Populated by MVP extractors · (unmarked) = 🚧 Planned
(Phase 4 extractors) or ⏳ Aspirational (Phase 5 legacy). The schema is intentionally ahead of the
extractors; MCP tools advertise `available: false` for fact types not yet present in an index.

### Structural (code)
| Type | Notes |
| --- | --- |
| `REPOSITORY` * | Root container |
| `DIRECTORY` * | Folder |
| `FILE` * | Source file |
| `MODULE` * | Import-level unit (Python module, JS/TS file-as-module) |
| `PACKAGE` | Logical package / namespace |
| `SYMBOL` * | Generic named symbol (superclass of the below) |
| `FUNCTION` * | Free function |
| `METHOD` * | Function bound to a class |
| `CLASS` * | Class / struct |
| `INTERFACE` * | Interface / protocol / trait |
| `ENUM` | Enumeration |
| `VARIABLE` * | Module/const/field-level binding |
| `IMPORT` * | Import statement (as a first-class node for lineage) |
| `TYPE` | Type alias / typedef |

### Testing
| Type | Notes |
| --- | --- |
| `TEST` * | Test function/case (heuristic detection in MVP) |
| `TEST_SUITE` | Group of tests |

### Data
| Type | Notes |
| --- | --- |
| `DATABASE_TABLE` * | Table (materialized from `EXEC SQL` / DCLGEN references) |
| `DATASET` * | z/OS dataset / VSAM file (JCL `DD DSN=`, COBOL `ASSIGN TO`, CICS `FILE(...)`, CSD `DEFINE FILE`) |
| `DATABASE_COLUMN` | Column |
| `QUERY` | SQL/ORM query site |
| `DTO` | API/data-transfer model |

### Runtime / config
| Type | Notes |
| --- | --- |
| `CONFIG_KEY` * | Configuration key (from `.env`, yaml, json, toml) |
| `FEATURE_FLAG` | Flag controlling a code path |
| `API_ENDPOINT` | HTTP route |
| `JOB` | Scheduled job / cron / batch program |
| `QUEUE` | Message queue |
| `TOPIC` | Pub/sub topic |
| `LOG_EVENT` | Emitted log/metric event |
| `COMPONENT` | Deployable/logical component |

### Ownership / evolution
| Type | Notes |
| --- | --- |
| `COMMIT` * | VCS commit (from git) |
| `AUTHOR` * | Commit author |
| `TICKET` | Issue/ticket reference |
| `TEAM` | Owning team |

### Business / domain
| Type | Notes |
| --- | --- |
| `BUSINESS_CAPABILITY` | Capability / feature area |
| `USER_FLOW` | End-to-end user journey |
| `REPORT` | Report/analytics artifact |
| `SERVICE` | Service boundary |

### Legacy modernization
| Type | Notes |
| --- | --- |
| `LEGACY_PROGRAM` * | COBOL/HLASM program (PROGRAM-ID / CSECT / member stem) |
| `COPYBOOK` * | COBOL copybook (incl. DCLGEN detection) |
| `JCL_JOB` * | JCL job or PROC member |
| `PARAGRAPH` * | COBOL paragraph (PERFORM targets) |
| `TRANSACTION_CODE` * | CICS transaction (CSD `DEFINE TRANSACTION`) |
| `SCREEN` * | BMS mapset/map (`DFHMSD`/`DFHMDI`, CSD `DEFINE MAPSET`) |

**Documentation** (tier `*`):

| Type | Notes |
| --- | --- |
| `DOC_SECTION` * | A documentation heading/section (Markdown/RST/AsciiDoc/text/HTML/PDF/DOCX). Rides under its file's MODULE like a `PARAGRAPH`; **not** a symbol kind. See [`documentation-ingestion.md`](documentation-ingestion.md). |

## 3. Relationship types (`RelationType`)

`*` = produced by MVP parsers/analyzers.

| Type | Typical (src → dst) | Meaning |
| --- | --- | --- |
| `CONTAINS` * | Repository/Directory/File → child | Structural containment |
| `DEFINES` * | File/Module/Class → Symbol | Definition site |
| `REFERENCES` * | Symbol/File → Symbol | Non-call reference / use |
| `CALLS` * | Function/Method → Function/Method | Call edge |
| `IMPORTS` * | Module/File → Module/Package | Import edge |
| `EXTENDS` * | Class → Class | Inheritance |
| `IMPLEMENTS` * | Class → Interface | Interface implementation |
| `READS` * | Function/Program/Job → Table/Dataset | Data read (`EXEC SQL`, `OPEN INPUT`, CICS `READ FILE`, JCL `DD DISP=SHR`) |
| `WRITES` * | Function/Program/Job → Table/Dataset | Data write (`INSERT/UPDATE/DELETE`, `OPEN OUTPUT`, CICS `WRITE`, JCL `DISP=NEW`) |
| `CONFIGURES` * | ConfigKey → Component/Function | Configuration binding |
| `CONTROLS` | FeatureFlag → CodePath/Function | Flag gates code |
| `TESTS` * | Test → Function/Class/Module | Test targets symbol |
| `COVERS` | Test → BusinessCapability | Behavioral coverage |
| `OWNS` | Author/Team → Module/Service | Ownership |
| `CHANGED` * | Commit → File/Symbol | Commit touched entity |
| `RELATES_TO` | Ticket → Commit/Module/Capability | Loose relation |
| `IMPLEMENTS_CAPABILITY` | Module/Service/Function → BusinessCapability | Capability realization |
| `DESCRIBES` * | DocSection → any artifact | Documentation describes the artifact. **Never** dependency-like — excluded from impact/completeness (`documented-artifact-missing` misses feed the gap registry). |
| `RUNS` * | JCLJob/PROC → Program/PROC | Execution (`EXEC PGM=` / `EXEC PROC=`) |
| `SCHEDULES` | Scheduler → Job | Scheduling |
| `MAPS_TO` * | DCLGEN Copybook → Table | Mapping/lineage (`EXEC SQL DECLARE <t> TABLE`) |
| `DEPENDS_ON` * | Module/Component → Module/ExternalPackage | Dependency |
| `EMITS` | Function → LogEvent | Log/metric emission |
| `HANDLES` | Endpoint → Function/Controller | Request handling |
| `CANDIDATE_FOR_MIGRATION` | LegacyModule → TargetService | Modernization candidate |
| `USES` * | Program → Screen (CICS `SEND/RECEIVE MAP`); UserFlow → API/Function/Table | Usage |
| `INVOKES` * | TransactionCode → Program | CICS routing (CSD) |

The schema records, for each relationship type, its **allowed source/target entity kinds** and whether
it is **directed**. `validate_relationship()` warns (not fails) on unexpected src/dst kinds so new
extractors degrade gracefully — mirroring Understand-Anything's alias/auto-fix robustness.

## 4. Alias normalization

Extractors and LLMs emit inconsistent names. `uci.core.schema.normalize_relation()` and
`normalize_entity()` map common aliases to canonical types:

```
func, def, procedure          → FUNCTION
struct, record                → CLASS
protocol, trait               → INTERFACE
inherits, subclasses, extends → EXTENDS
uses, invokes(call)           → CALLS      (context-aware)
import, require, include      → IMPORTS
reads_from                    → READS
writes_to                     → WRITES
tested_by / tests             → TESTS
```

## 5. Canonical node & edge records

```python
@dataclass(frozen=True)
class Entity:
    id: str
    kind: EntityType
    name: str
    qualified_name: str
    provenance: Provenance
    attributes: dict[str, Any]   # language, signature, docstring, modifiers, is_exported, ...

@dataclass(frozen=True)
class Relationship:
    id: str
    type: RelationType
    src_id: str
    dst_id: str
    provenance: Provenance
    attributes: dict[str, Any]   # e.g. call kind, import alias, confidence, resolution
```

## 6. Example: a Python file becomes graph

Given `pricing/calculator.py`:

```python
from .rules import DiscountRule           # (1)

class PricingCalculator(BaseCalculator):   # (2)
    def calculate(self, cart):             # (3)
        return DiscountRule().apply(cart)  # (4)
```

Produces:

```
FILE   pricing/calculator.py
MODULE pricing.calculator
CLASS  pricing.calculator.PricingCalculator          (2)
METHOD pricing.calculator.PricingCalculator.calculate (3)

FILE   --CONTAINS-->   MODULE
MODULE --DEFINES-->    CLASS PricingCalculator
CLASS  --DEFINES-->    METHOD calculate
MODULE --IMPORTS-->    MODULE pricing.rules            (1)
CLASS  --EXTENDS-->    CLASS BaseCalculator            (2)
METHOD --CALLS-->      METHOD DiscountRule.apply       (4)
METHOD --REFERENCES--> CLASS DiscountRule              (4)
```

Every edge above carries the file path and line range of the syntax that produced it.

## 7. Extensibility
Adding a new fact type = add an `EntityType`/`RelationType` member + (optionally) allowed-kinds metadata
+ an extractor that emits it. No storage migration is needed for the SQLite backend because entities and
relationships are stored generically with a typed `kind`/`type` column and a JSON `attributes` blob.

## 8. Placeholder (stub) entities & the gap registry

When an edge's target cannot be resolved to an indexed entity, the normalizer creates a **stub entity**
instead of dropping the edge: a reserved `__missing__` path segment in the id, `attributes.missing=true`
(or `external=true` for stdlib/vendor), and `confidence=0.0`. Real edges point at the stub with
`resolution="missing"`, so traversal and impact still see them. Stubs are idempotent (same missing name
→ same id) and **self-heal** when the artifact is later indexed (the full-rebuild resolves the real
entity, and the stub/gap disappears). Each missing artifact also produces a **gap record** (see
`docs/next-iteration-gap-registry.md`) surfaced via `uci gaps` / `list_index_gaps`.
