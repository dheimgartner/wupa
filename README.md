# gev-openapi

Generates an OpenAPI 3.0 (Swagger) spec for a Dynamics 365 / Dataverse Web API such as FINMA's GEV, from its OData `$metadata`, and optionally serves it in Swagger UI with working "Try it out".

```
$metadata ──download──▶ metadata.xml ──generate──▶ openapi.json ──serve──▶ Swagger UI (+ proxy)
                │                            ▲
                └─(--enrich)─▶ attributes.json ┘   required fields, choice values, labels
```

## Install

```bash
pip install -e ".[sspi,ui]"      # Windows: single sign-on + offline Swagger UI assets
pip install -e ".[kerberos,ui]"  # Linux/macOS with a Kerberos ticket
pip install -e ".[ntlm,ui]"      # explicit user/password
```

Requires Python 3.9+. The only hard dependency is `requests`.

## Quick start

```bash
# everything in one go: download, fetch column metadata, generate, open Swagger UI
gev-openapi build --enrich --serve
```

Defaults: base URL `https://gev-int.finma.ch/gev/api/data/v9.1` (or `$GEV_BASE_URL`), all `finma_*` entity sets, Windows single sign-on.

## Commands

| Command | What it does | Network |
|---|---|---|
| `download [-o metadata.xml] [--enrich]` | Saves `$metadata`; with `--enrich` also `attributes.json` for the selected tables | yes |
| `generate metadata.xml [--attributes attributes.json] [-o openapi.json]` | Builds the spec | no |
| `serve openapi.json [--port 8000] [--no-proxy]` | Swagger UI on http://127.0.0.1:8000 | proxy only |
| `build [--enrich] [--serve]` | All of the above | yes |

Examples:

```bash
# only the tables you need, read + create only (small spec for UiPath)
gev-openapi build --enrich --include finma_geschaefts --include finma_finmaobjektinstituts \
    --include finma_geschaeftsprozesses --include finma_teilprozesses --operations list,get,create

# everything except EHP tables
gev-openapi generate metadata.xml --attributes attributes.json --exclude "finma_ehp*"

# production, once you know its URL
gev-openapi build --base-url https://<prod-host>/gev/api/data/v9.1 --enrich
```

Connection options (all commands that call the server): `--auth sspi|kerberos|ntlm|basic|bearer|none`, `--user`, `--ca-bundle` (internal CA), `--insecure`. Passwords and tokens come from `$GEV_PASSWORD` / `$GEV_TOKEN` or a prompt, never from the command line.

## What gets generated

Per entity set (e.g. `finma_geschaefts`, type `finma_geschaeft`):

| Operation | Path | Schema |
|---|---|---|
| `finma_geschaefts_list` | `GET /finma_geschaefts` | `finma_geschaeft_collection` |
| `finma_geschaefts_create` | `POST /finma_geschaefts` | body `finma_geschaeft_create` |
| `finma_geschaefts_get` | `GET /finma_geschaefts({id})` | `finma_geschaeft` |
| `finma_geschaefts_update` | `PATCH /finma_geschaefts({id})` | body `finma_geschaeft_update` |
| `finma_geschaefts_delete` | `DELETE /finma_geschaefts({id})` | |

- **Read schema**: every column with its type. Lookups appear as `_<column>_value` (read-only), with a note on how to set them.
- **Write schemas**: only columns that can be set, and lookups as `<NavigationProperty>@odata.bind` with the target entity set, e.g. `"finma_TeilprozessId@odata.bind": "/finma_teilprozesses(<guid>)"`.
- **With `--enrich`** (recommended): `required` lists for create, `enum`s for choice columns with their labels, `maxLength`, display names, and exact create/update rules from the server. Without it, system columns are excluded by heuristics and nothing is marked required.
- `$select` is an enum of the table's columns (a multi-select in Swagger UI); `$filter`, `$orderby`, `$expand`, `$top`, `$count`, `Prefer`, `If-Match` are documented parameters.
- `GET /WhoAmI` for a quick connection test.

Business rules enforced by server-side plugins are not visible in metadata. A request that matches the spec can still be rejected; the error message says why.

## Swagger UI and the proxy

Swagger UI can't call Dynamics directly: the server sends no CORS headers and expects Windows authentication. `serve` therefore runs a small local proxy. It forwards `/api/*` to the real Web API using your authenticated session, and the spec it serves points at `/api`. "Try it out" then runs with your own permissions, against the real system, so be careful with create, update and delete.

Without the `ui` extra, the Swagger UI assets load from jsDelivr; with it, everything is served locally.

## Using the spec in UiPath

Studio's *Import Service* accepts OpenAPI 3.0 JSON. Keep the spec small (`--include …`, `--operations …`); every operation becomes an activity. Check two things on a first import:

1. Body properties named `…@odata.bind`. These aren't valid C# identifiers, so check that the generated classes still send the right JSON names.
2. Paths with parentheses (`/finma_geschaefts({id})`).

The spec declares `negotiate` (Windows) security by default; set `--security` to match how UiPath authenticates.

## Python API

```python
from gev_openapi import parse_metadata, build_spec
from gev_openapi.download import load_attributes

model = parse_metadata("metadata.xml")
spec = build_spec(model, "https://gev-int.finma.ch/gev/api/data/v9.1",
                  include=["finma_geschaefts"], attributes=load_attributes("attributes.json"))
```

## Development

```bash
pip install -e ".[dev]"
pytest
```

Tests run against a fixture `$metadata` and fake servers; no access to GEV is needed.
