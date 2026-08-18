# Patchright Runtime Marker Contract

## Required markers

| Marker | Meaning |
|---|---|
| `[PATCHRIGHT_FARM_START]` | A Patchright browser farm was requested. |
| `[PATCHRIGHT_CONTEXT_STARTED]` | The persistent Chrome context was created with the configured proxy binding. |
| `[PATCHRIGHT_DIAGNOSTICS_SAVED]` | Existing diagnostic artifacts were finalized. |

## Required fields

Patchright lifecycle events contain `event`, `timestamp`, and only redacted local artifact paths or proxy identity. Existing challenge marker fields remain governed by Feature 004.

## Failure patterns

The following must not appear in normal runtime configuration:

- Imports from `playwright.async_api` in production browser code.
- Removal of the required `playwright_stealth` compatibility hook.
- Custom launch user-agent or browser-header overrides.
- Cookie values or proxy credentials in logs.
- Automated slider dragging or CAPTCHA completion markers.

## Compatibility

The public class name and existing diagnostic filenames remain unchanged for callers and operators.
