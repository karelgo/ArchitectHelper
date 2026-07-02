# BiZZdesign Horizzon integration

ArchFlow talks to the **Bizzdesign Open API v3.0** — the only officially
documented programmatic interface to Horizzon (spec:
`https://downloads.bizzdesign.com/Support/api/3.0/Bizzdesign_Open_API_documentation_v3.0.yaml`,
docs: help.bizzdesign.com, topic "Bizzdesign Open API").

## What the API can and cannot do (v3.0, verified 2026)

| Capability | Supported? |
|---|---|
| Read repositories, objects, relations (paginated, filterable) | ✅ |
| Create/update **architecture automation** collections, entities, links | ✅ (incl. bulk) |
| Create/update **data blocks** (attribute sets on existing objects) | ✅ |
| Modify native Enterprise Studio elements / relations / properties | ❌ read-only |
| Create or update **views / diagrams** | ❌ no endpoints at all |
| Trigger site regeneration | ❌ (not needed: API data appears without a publish) |

Consequences for ArchFlow's publication stage:

1. **Element/relation data** is pushed via the API as an *architecture
   automation collection* named `ArchFlow — <request title>`. It appears in
   the model package's read-only **Collections** folder in Enterprise Studio
   and on Horizzon sites, without a publish step. Entities and links carry
   stable `externalId`s (`archflow-<request-id>-…`) so re-publishing is
   idempotent per request.
2. **The stakeholder-map view always travels by file**: at publication
   ArchFlow writes an ArchiMate Open Exchange file
   (`artifacts/<request-id>/publication_export.archimate.xml` — kept separate
   from the immutable stakeholder-analysis artifact)
   that an architect imports in Enterprise Studio via
   *File → Import → ArchiMate Model Exchange File* (versions 2.1–3.2 are
   accepted). For fully automated view manipulation the only route is the
   Enterprise Studio scripting language (`autoLayoutDiagram`, `addNewObject`,
   `addRelationTo`, …) executed inside the desktop tool.
3. **No Horizzon configured?** Publication still succeeds: ArchFlow falls
   back to the file export alone and says so in the publish result.

## Setup

1. A Horizzon **Administrator** creates an API client: *Settings → Clients →
   New client*. Grant **Read** and **Write**, and give it access to the
   target model package. The client secret is shown **once** — store it in a
   secret manager.
2. Configure ArchFlow (see `.env.example`):

   ```bash
   ARCHFLOW_HORIZZON_BASE_URL=https://<your-org>.horizzon.cloud
   ARCHFLOW_HORIZZON_CLIENT_ID=...
   ARCHFLOW_HORIZZON_CLIENT_SECRET=...
   ```

3. Test the connection: `archflow publish <request-id>`. The publish result
   tells you which mode was used and where the exchange file is.

## Gotchas

- **403 on the token endpoint** means your Horizzon license tier does not
  include the Open API — talk to your Bizzdesign account manager. 401 means
  wrong credentials.
- **Rate limiting**: any call may return 429. The client retries with
  exponential backoff and honours `Retry-After`.
- **Type terms are tenant-specific.** ArchFlow defaults to
  `ArchiMate:Stakeholder`, `ArchiMate:Driver`, `ArchiMate:Assessment`,
  `ArchiMate:Goal` for entities and best-effort
  `ArchiMate:AssociationRelation` / `ArchiMate:InfluenceRelation` for links.
  Verify the exact terms via the Metamodel browser in Enterprise Studio
  (help topic *"Finding element type names"*) and override with
  `HorizzonPublisher(element_terms=..., relation_terms=...)` if yours differ.
- **Limits**: max 5000 objects per container, 500 links per entity; bulk
  calls are chunked at 500 by the client.
- The API speaks **proprietary Bizzdesign JSON** (type terms, `{"en": ...}`
  name maps, `{_items, _offset, _limit}` envelopes) — not ArchiMate Open
  Exchange. The exchange format is only used for the file-based view import.
- A **GraphQL API** is advertised on Bizzdesign's marketing site but has no
  public documentation; don't build against it without confirmation from
  Bizzdesign support.

## Roundtrip pattern (recommended)

For a governed change: read current state via the API (`iter_objects`,
`iter_relations`, filtered by `updatedAfter` for increments) → generate the
target model and stakeholder map with ArchFlow → publish (API collection +
exchange file) → import the file in Enterprise Studio, place the generated
elements on views, and commit — the commit flows back to Horizzon sites.
