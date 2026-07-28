# Graph Memory

A temporal knowledge-graph long-term memory for Hermes Agent, packaged as an
[AXP](https://github.com/mintbot-ai/agent-extension) extension.

Graph Memory remembers more than isolated sentences. It extracts entities such
as people, projects, decisions, topics, and artifacts; stores the relationships
between them; tracks when facts become valid or are superseded; and recalls
relevant subgraphs by meaning rather than exact wording.

## Architecture

```text
Hermes turn / explicit memory write
                 │
                 ▼
     user's active Hermes model
  (proxy, Codex, Claude, BYOK, ...)
      entity + relationship extraction
                 │
                 ▼
       Graphiti temporal graph
          ┌──────┴──────┐
          ▼             ▼
  embedded Kuzu    local FastEmbed
   graph store      semantic vectors
```

- Extraction follows the model/provider currently selected in Hermes. The
  extension creates a tool-free nested agent with `skip_memory=True`, avoiding
  recursion and avoiding a second credential configuration.
- Embeddings and reranking run locally with `BAAI/bge-small-en-v1.5` through
  FastEmbed/ONNX. There is no embeddings API cost or data egress.
- Kuzu runs in-process and stores one profile-scoped database at
  `$HERMES_HOME/graph-memory/graph.kuzu`. There is no Docker container, port,
  database daemon, or password to operate.

## What it provides

- Automatic extraction after each primary conversation turn (configurable).
- Automatic relevant-memory injection before model calls.
- `memory_query`: explicit semantic/relationship/history search.
- `memory_write`: confirmed durable graph write.
- Mirroring of Hermes built-in memory add/replace/remove operations with
  temporal supersession/removal events.
- Pre-compression and delegation-result ingestion.
- Clean shutdown that serializes and drains pending writes.

## Install

### AXP-aware host

Install `agent-extension.json` using the host's AXP installer. The host verifies
permissions, release metadata, artifact hash/signature when present, and runs
the Hermes target's lifecycle hooks.

The development manifest is intentionally unsigned and contains release hash
placeholders until the first GitHub release artifact is built. A conforming AXP
host must show the unsigned-extension warning described by AXP v0.2.

### Standalone fallback (no AXP support)

```bash
git clone https://github.com/mintbot-ai/graph-memory.git
cd graph-memory
sudo ./install.sh
```

The installer is idempotent. It copies the provider into
`$HERMES_HOME/plugins/graph-memory`, uses the sanctioned
`hermes memory setup graph-memory` path to install pinned dependencies, and
activates `memory.provider: graph-memory`. Start a new Hermes session afterward.

A non-root install also works; lifecycle scripts are stored under
`$HERMES_HOME/extensions/graph-memory` instead of `/opt/graph-memory`.

## Configuration

Run:

```bash
hermes memory setup graph-memory
```

The defaults are:

```yaml
memory:
  provider: graph-memory
  graph-memory:
    embedding_model: BAAI/bge-small-en-v1.5
    ingest_turns: all          # or explicit-only
    recall_limit: 8
```

No second LLM key is requested. Extraction resolves the active Hermes runtime
on every call, so a later model/provider switch is picked up automatically.

## Operations

```bash
./healthcheck.sh                         # provider installed + active + deps import
./upgrade.sh                             # idempotent reinstall; sees AXP_FROM_VERSION
./uninstall.sh                           # keeps the graph database
AXP_PURGE=1 ./uninstall.sh               # also deletes long-term memory
```

`hermes backup` already includes the graph because it lives under HERMES_HOME.

## Important Kuzu constraints

Kuzu was selected for its excellent embedded, zero-service shape. Graphiti
0.29.3 marks its Kuzu driver deprecated because the upstream Kuzu project is no
longer maintained. This extension therefore pins the exact tested pair
`graphiti-core==0.29.3` and `kuzu==0.11.3`, labels the initial AXP release
`beta`, and keeps the storage boundary isolated so a future FalkorDB/Neo4j
migration can replace the driver without changing the Hermes provider contract.

Kuzu also allows one process to own a database at a time. Do not run a CLI and
gateway concurrently against the same Hermes profile while this provider is
active. Different Hermes profiles have separate databases and are isolated.

## Development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
```

The integration test exercises the real Kuzu/FastEmbed stack. A live extraction
test additionally uses whichever Hermes model/provider is currently selected.

## License

MIT — see [LICENSE](LICENSE).
