# Strapi CMS

This is a Strapi 5 app using PostgreSQL. `posts`, `series-items`, and `moments` preserve the original Markdown body, source path as `slug`, and frontmatter in the `metadata` JSON field. Draft & Publish controls the publicly readable version; tokens with read permission must only be used on the server. Content API types are private by default. In particular, never expose credentials or raw protected article content through a public API role.

Configure the values in `.env.example` with secrets from a protected store; do not commit them. The persistent media directory is `public/uploads`. Production should use one CMS instance until search indexing is coordinated across replicas. Meilisearch synchronization runs after successful content changes and periodically rebuilds the index to repair missed operations.

```sh
pnpm install --frozen-lockfile
pnpm build
pnpm start
```

The app requires supported Node.js 20–24 and PostgreSQL. Search sync requires `MEILI_URL`, `MEILI_ADMIN_KEY` and `MEILI_TASKS_KEY`. The publication path has been tested with disposable PostgreSQL, Strapi, Astro and Meilisearch containers and on the production cluster.

## Archive migration

Run from this directory against a checked-out, verified `archive/mizuki` snapshot. The default is read-only and reports post, draft, asset and unresolved-reference counts:

```sh
node scripts/import-mizuki.mjs --source /path/to/mizuki
STRAPI_URL=http://cms.internal:1337 STRAPI_PUBLIC_URL=https://hyperbola.cc STRAPI_IMPORT_TOKEN="..." \
  node scripts/import-mizuki.mjs --source /path/to/mizuki --apply
```

Use a temporary full-access token supplied through a protected environment, revoke it afterward, and never paste a real token into shell history. Execute one importer at a time against a staging CMS. The importer checks every slug before upload, aborts on conflicting existing records, and skips matching source-and-asset hashes on retry. The public origin must serve `/uploads/` from Strapi; private CMS URLs are never embedded in posts. Strapi's upload API generates new filenames; the migration does not overwrite same-named public files. Interrupted uploads may leave unreferenced media for an administrator to review. Markdown image references, HTML media `src`/`poster`, and cover images are rewritten when their relative or `public/` source exists. Missing root URLs remain unchanged and are reported; missing relative media blocks non-MDX import. Check the report before switching production.

Draft MDX remains a draft with the exact original source in `metadata.migration.source` and a manual conversion flag; unresolved draft MDX media is reported and left unchanged. Published MDX blocks migration until supported or converted. Root-relative non-media links and unrecognized custom syntax are not automatically transformed and need review. Original frontmatter stays in `metadata`; `_mizukiMigration` is reserved for importer hashes. A passing import is not evidence that the new site renders all legacy extensions, encryption, links or images correctly.

## Save-time Markdown rendering

Set `INTERNAL_RENDER_URL` to the private Astro service URL ending in `/api/internal/render/` and share `INTERNAL_RENDER_TOKEN` through a Kubernetes Secret. The CMS sends the body and frontmatter to this internal endpoint before a save, then stores its versioned HTML and source hash with the document. Metadata object keys are canonicalized before hashing so PostgreSQL JSONB key ordering does not invalidate snapshots. On renderer failure the save still succeeds and clears the old snapshot; Astro renders the content on demand. A later save can persist a fresh snapshot, but there is no automatic write-back job for entries saved during an outage.

Protected content (`encrypted`, or a nonempty `password`) and MDX migration drafts are never sent to this endpoint and their rendered snapshot is cleared. The endpoint must not be exposed publicly. Runtime Markdown includes resolve only published Strapi snippets and do not read application files.

`STRAPI_PUBLIC_URL` denotes the public site origin for generated media URLs. `/uploads/` must be handled by the site's publication-aware proxy; it must not bypass that proxy and connect directly to Strapi's upload directory. The importer rejects local media on draft or protected articles pending a secure private-media design.

To reproduce the full archive import check, start a disposable empty CMS and database on loopback, create a short-lived full-access test token, and run:

```sh
STRAPI_URL=http://127.0.0.1:1337 STRAPI_PUBLIC_URL=https://example.com \
STRAPI_IMPORT_TOKEN="$TEST_TOKEN" ISOLATED_CMS_CONFIRM=disposable \
  node scripts/verify-isolated-import.mjs /path/to/archived/mizuki
```

This test writes the entire archive and media to that CMS. It rejects non-loopback endpoints and existing posts, tests publication/draft counts, content hashes and idempotent retry, and leaves test records in the disposable database for inspection. Destroy that disposable environment afterward; never point it at a production port-forward.

## Initial administrator and API tokens

The public administration entrypoint is `https://strapi.hyperbola.cc/admin`, with an explicit allowlist of admin routes. The content API and uploads must not be routed directly to CMS; uploads use the site's publication-aware proxy. On the admin host, a secure host-only media capability is issued at login and checked against the current Strapi session and upload permissions for every private media request; anonymous requests retain the publication checks. An operator can also port-forward the CMS service to `127.0.0.1:1337` from a trusted host. For initial setup, prepare an existing, caller-owned 0700 directory on protected persistent storage **outside** the repository, then run from the repository checkout:

```sh
node cms/scripts/prepare-bootstrap.cjs /absolute/protected/state
node cms/scripts/bootstrap-local.cjs /absolute/protected/state http://127.0.0.1:1337
```

Preparation reads `git config user.email` locally and generates a random administrator password once in `admin.json` (0600). The bootstrap creates the first administrator only when none exists, logs in with those same credentials, and issues two custom-scoped tokens: `reader.token` can only call the published-content API; `import.token` can read and create posts and upload media, expires after seven days and should be revoked after migration. Neither script prints credentials. Repeat runs verify administrator identity, token identity, exact server-side permissions, and access; they do not silently rotate or recreate tokens.

Keep the protected state available to the authorized operator, so the user can retrieve the administrator credential. Deliver `reader.token` as `STRAPI_API_TOKEN` to the SSR workload using the cluster's reviewed Secret management workflow; never put it in command-line literals, logs, images, or Git. The import token is temporary and should not be installed in the SSR workload. Review the cluster namespace, target pod UID, and live state before issuing credentials; test the Secret update and workload readiness before exposing the site.

Token creation is non-idempotent. A `reader.pending` or `import.pending` marker means the server may have accepted a request even though its result is uncertain: the program stops and does not retry or overwrite. Retain the state and inspect the exact server token identity and operator recovery procedure before doing anything else. A leftover `.bootstrap.lock` after interruption likewise requires confirming the original process is stopped before manual recovery. Loss of the state directory, an unexpected existing administrator or token, or permission drift also stops the workflow; do not resolve these by resetting accounts or minting duplicates automatically.

## Search index readiness

A search result is served only when the live Meilisearch index contains the completed-generation marker, created inside the rebuilt staging index immediately before the atomic swap. A healthy HTTP process or an empty newly created index is not enough. The single CMS writer checks readiness every five seconds and triggers a rebuild after lost readiness, a service outage, or a failed incremental update; full reconciliation also runs periodically. The staging name `${MEILI_INDEX}_rebuild` is reserved exclusively for this application.

Use separate restricted credentials: `MEILI_ADMIN_KEY` is an index writer limited to the live and staging index; `MEILI_TASKS_KEY` grants only `tasks.get` across indexes because Meilisearch's swap task has no single index UID. The independent key-recovery mechanism must restore these keys and the SSR search-only key before recovery can succeed. If a swap result is uncertain, staging is retained and in-process retries stop. After a CMS restart, an existing staging index also blocks rebuilding until reviewed; the cluster-side key-reconciler deployment and a tested recovery procedure remain operational requirements.
