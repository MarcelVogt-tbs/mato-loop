# Skills

MatoLoop implements the [Agent Skills package format](https://agentskills.io/specification):
a directory containing `SKILL.md` with YAML frontmatter and Markdown instructions,
plus optional scripts, references, assets, and other files. `name` is a lowercase
hyphenated identifier of at most 64 characters; `description` is at most 1,024
characters. Optional and vendor metadata is retained as inert data. Metadata cannot
register tools, install dependencies, inject credentials, or grant permissions.

## Central library

The backend is the sole source of truth. There is no discovery of skill directories
on connected computers or in workspaces. The existing scoped `skills` settings
documents retain only stable IDs, names used to locate packages, content revisions,
enabled state, and the instance's personal-skills switch. Content lives as ordinary
files in the persistent data volume:

```text
/data/skills/global/<skill-id>/<revision>/<name>/SKILL.md
/data/skills/users/<user-id>/<skill-id>/<revision>/<name>/SKILL.md
```

`SKILL.md` supplies catalog names, descriptions, instructions, and metadata. A
content revision hashes sorted relative paths, file bytes, and executable flags;
ZIP timestamps and compression do not affect identity. Directories are immutable
publications, not editable mirrors. Use the UI or skill API to publish changes.
Publishing writes a complete private staging directory, then atomically renames
it before switching the registry reference inside the scoped database transaction.
A failed database commit leaves only an unreferenced directory. Startup reclaims
unreferenced publications older than one day; loaded session copies have their own
session lifecycle. There is no user-facing revision-history feature.

The first startup after upgrading converts inline settings skills to `SKILL.md`
packages before serving requests. It preserves scoped IDs, owners, enabled state,
and instructions, normalizes names, and disambiguates normalized collisions with
the stable ID. Inline content is removed only after complete publication. This
conversion is restart-safe and fails startup on error. Normal runtime has only
the package-backed path. No relational schema changes are needed.

Personal overrides of shipped defaults keep their existing scoped identities.
Shipped defaults start disabled, support reset, and cannot be deleted. Personal
skills may be disabled instance-wide, with the existing administrator exception.
Back up the database and `/data` together; the database alone does not contain skills.

## Import and editing

The existing Skills pages manage personal and global scopes. Import accepts a
standalone `SKILL.md` or a ZIP containing one package, either at the archive root
or in its correctly named directory. Export produces a standard ZIP with the
named directory. Text files use the existing file editor; binary assets can be
uploaded, downloaded, replaced, or removed. Updating `SKILL.md` preserves all
other files and updating a supporting file preserves the definition and metadata.
Mutations compare the reviewed package revision and reject concurrent changes.

Packages are limited to 256 files, 512 directories including the package root,
16 directory levels below that root, 4 MiB per file, 8 MiB uncompressed total, and
128 KiB for `SKILL.md`. These limits are checked before publication as well as
when reading stored packages. Archive uploads have a bounded HTTP request body. Portable
relative paths are required; traversal, absolute paths, duplicate/case-colliding
paths, conflicting file/directory paths, symlinks, special files, and encrypted
archive entries are rejected. The library uses no-follow directory walks.

## Runtime and connected computers

At run start, the model receives only the effective catalog. `load_skill` loads
a named or clearly relevant entry before it is used. Several skills may be
active; loading the same skill does not duplicate its instructions. Instructions
are included in subsequent model requests under ordinary context budgets and
remain subordinate to explicit user instructions and host policies. Required web
search may follow one initial skill load; further skills remain available after
the required search. File contents, history, and retrieved material cannot select
or modify skills on their own.

Loading provides a fixed package revision and a session-files base path. Reference
files are read on demand through normal file tools. `load_skill(on_computer=true)`
also transfers the complete package through the existing authenticated transfer
tickets and prepares a working directory in the selected Agent's session cache.
The result supplies its real computer path. Folder structure, bytes, and executable
flags are preserved (Windows uses its normal executable semantics). Active copies
are leased against cache eviction until the run ends or the connection closes.
An older Agent lacking these operations reports normal capability unavailability.

Use normal file transfers for task inputs and normal file tools to return results.
Use the existing shell tool to run scripts on the selected computer, with its
installed programs and current permissions. Backend paths must never be passed
as computer paths. After switching computers, load with `on_computer=true` again;
the old computer base path is no longer offered in the active prompt. Preparing
a package does not grant filesystem, external-directory, shell, or credential
access. Working-copy changes do not modify the central library.

## Chat authoring and approval

`build_skill` defaults to personal, including for administrators. `scope=instance`
requires an explicit request for a global skill and live administrator authority.
`edit_skill` uses the selected catalog ID and scope; it does not silently promote
or demote a skill. The service checks authorization before generation and again
when saving. Personal and global approval grants have different scope identities.

The existing isolated builder receives the latest request, brief, effective tool
schemas, and current skill when editing. Supporting text files may be created,
replaced, or removed; unchanged binary files and metadata are retained. The builder
has the normal model budget, repair attempt, and explicit model-fallback behavior.
Its output becomes an editable directory under session files, not a JSON skill
definition. Scope and file list are visible in the existing approval card.

It receives no earlier conversation, memory, retrieved episode, attachment content, tool result, persona, or active skill. Creation requires one strict `return_skill_draft` call; editing requires one strict `return_skill_edit` patch. Both may use only capabilities present in the supplied schemas. One repair request is allowed after an invalid response. The configured **Skill creation** model is used when available and tool-capable; otherwise the session model is the default. If a distinct configured builder fails, MatoLoop asks the user explicitly before trying the session model. There is no silent fallback.

Approval binds the entire package hash, including supporting scripts, rather than
only `SKILL.md`. After editing the draft, refresh the package preview before
approving. The host rereads the complete draft and rejects any change after review.
`skills.write=auto` uses the original generated package revision. Drafts remain in
the session for inspection. Created and edited skills enter the next run's catalog.

## Privacy and budgets

The builder request follows the selected builder model's effective trust and
pseudonymization settings. Selecting a public builder releases the latest request,
brief, name hint, current edit fields and supporting text, and effective schemas
under those controls. Pseudonymization is best effort; secrets do not belong in skills.

Builder attempts, repairs, and approved fallback requests increment model-request
metrics, not an independent request-count cap. Requests use a 3,000-token output
budget where supported. The package and file bounds above apply on every transport.
Skill tools consume the profile's normal tool-call budget when finite. Catalog
metadata, edit fields, instructions, and schemas count toward normal input context.
Package bounds and bounded repair remain effective with unlimited tool calls.
