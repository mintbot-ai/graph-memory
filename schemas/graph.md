# Graph model

Graphiti owns the physical embedded graph schema. LadybugDB, the maintained
continuation of Kuzu, provides the storage engine. The stable logical model exposed by
this extension is:

- Entity nodes: people, projects, topics, decisions, organizations, artifacts,
  systems, and any other concepts extracted from episodes.
- Episode nodes: source conversation turns, explicit memory writes,
  pre-compression transcripts, and delegation results.
- Entity edges: natural-language facts connecting two entities.
- Temporal fields: `valid_at`, `invalid_at`, and ingestion timestamps preserve
  when a fact was true and when a later episode superseded it.
- Semantic vectors: 384-dimensional local FastEmbed vectors support recall by
  meaning even when query wording differs from the stored fact.

The Ladybug database is profile-scoped at `$HERMES_HOME/graph-memory/graph.lbug`.
An existing `graph.kuzu` database is migrated by export/import and retained as
a backup.
No custom Graphiti `group_id` is used because each Hermes profile already has a
separate database and Graphiti 0.29.3's embedded operation layer does not support named
database groups.
