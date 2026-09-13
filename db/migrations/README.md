# Migrations

Plain SQL, applied in filename order by `make migrate` and by the Postgres
container's init hook on a fresh volume.

| File               | Adds                                                                         | Phase                                                          |
| ------------------ | ---------------------------------------------------------------------------- | -------------------------------------------------------------- |
| `0001_init.sql`    | traces, corpus, runs, generations, generation_cache, judgments, human_labels | P0                                                             |
| `0002_vectors.sql` | `corpus_items.embedding`, `routes`, `semantic_cache_entries`, HNSW indexes   | P2 — blocked on choosing the embedding model and dimension `D` |

`D` is a migration-breaking constant (DECISIONS.md D-015): changing it means
rewriting three columns and rebuilding two HNSW indexes. It is therefore chosen
once, in P2, and not guessed in P0.

There is no migration runner library yet. If migrations start needing rollback
or out-of-order application, that becomes its own decision entry rather than a
tool picked up silently.
