# Appearance and bundled themes

Instance administrators manage **Darstellung** with two tabs: **Anpassung** for
the application name, description and logo, and **Themes** for the instance
default and enabled themes. The current default cannot be disabled. Choose a
different enabled default first; both changes can be saved together.

The initial release contains only `matoloop`. The instance default applies to
all users, including the login page. Personal theme selection, theme uploads,
an editor and external repository installation are outside this milestone.
The assistant's identity and persona settings remain independent of branding.

## Portable source folders

Themes live in `apps/frontend/public/themes/<id>/`:

```text
matoloop/
  theme.json
  theme.css
```

`theme.json` contains an `id` matching the directory and a display `name`:

```json
{"id": "matoloop", "name": "MatoLoop"}
```

IDs use lowercase ASCII letters, digits and hyphens, begin with a letter, and contain
at most 64 characters. Display names contain 1 to 80 characters. Branding accepts an
application name of 1 to 80 characters and description of at most 500 characters.
The backend discovers these folders from the frontend public directory in
development and the built static directory in production. Quasar copies them
unchanged into the frontend build. No second registry or component imports are
needed. A separately maintained repository can later supply the same folders
at build time; runtime downloads and plugin code are not part of the contract.

Each theme supplies a complete stylesheet. Start by copying the bundled theme,
including its `:root` and `body.body--dark` variables. Light/dark mode remains a
separate Quasar state. Relative font and image URLs resolve within the theme
directory. The default theme defines the existing `--app-*` and `--q-*` colors;
ordinary CSS can also style application classes, typography, layout, spacing,
surfaces and optional metadata. There are no theme-name checks in components
and no presentation-mode JSON. Application markup and interactions are shared.

Themes are trusted, reviewed source code. Preserve required decisions, errors,
keyboard focus, accessible names and touch targets. Prefer application classes
and CSS variables over Quasar implementation selectors. Shared layout CSS stays
in the application; future changes to its classes may require theme updates.

The HTML includes the MatoLoop stylesheet as a startup fallback. The central
appearance boot layer loads the effective theme before mounting the UI. A new
stylesheet replaces the previous one only after a successful load; a failed load
keeps the previous appearance and reports a controlled error. Switching themes
does not reload or remount the application, so drafts and open files survive.
Existing browsers fetch configuration on the next page load; an administrator's
successful save applies immediately in that browser.

## Persistence and public assets

The existing global settings document store holds `appearance`; no relational
schema change is necessary. Only admin-authorized endpoints update branding,
theme policy or the logo. The public `/api/appearance` response contains the
display configuration and theme catalog, never the stored image bytes.

Logos accept PNG, JPEG or WebP up to 1 MiB. Existing image validation is reused;
images are re-encoded to a transparent, square 512px PNG with metadata removed.
The bounded PNG is stored as base64 in the settings document, keeping branding
atomic and included in normal database backups without a separate asset store.
`/api/appearance/logo` serves it publicly for login, favicon and app icons.
The generated `/manifest.webmanifest` URL redirects to the uncached public
`/api/appearance/manifest.webmanifest`, which uses the same name,
description and logo; installed app updates follow browser/platform behavior.

An unavailable stored theme falls back to an available enabled theme, preferring
MatoLoop. MatoLoop remains the emergency fallback if no configured theme is
usable. Desired, effective and actually loaded themes are shown separately when
they differ. Invalid or escaping theme manifests are not listed.
