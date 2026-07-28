# Graph Memory installed

The provider and dependencies are installed. Start a new Hermes session so the
new memory-provider tool surface and system-prompt block are assembled once at
session startup.

Useful commands:

- `hermes memory status` — confirm `graph-memory` is active and available.
- `hermes memory setup graph-memory` — configure embedding model, ingestion
  mode, and recall limit.
- `/root/.hermes/plugins/graph-memory/healthcheck.sh` is not the lifecycle copy;
  the AXP/standalone installer stores lifecycle scripts under
  `/opt/graph-memory` (or `$HERMES_HOME/extensions/graph-memory` for non-root).

The first provider initialization downloads the local embedding model once.
Graphiti entity/relationship extraction uses the active Hermes model and may
therefore consume that provider's quota or mintbot credit.
