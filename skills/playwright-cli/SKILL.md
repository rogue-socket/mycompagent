---
name: playwright-cli
description: Local browser automation for everyday browsing tasks (search, navigation, form filling, shopping, and content extraction) using Playwright CLI snapshots and element refs.
allowed-tools: Bash(playwright-cli:*)
---

# Browser Automation with playwright-cli (General Web Use)

This skill is intended for **normal browser use**: searching the web, navigating sites, filling forms, handling logins, and collecting information. It is **not** focused on testing workflows.

## Core flow

1. Open a browser session.
2. Use `snapshot` to get element references.
3. Interact using element refs (e1, e2, e3...).
4. Repeat until done.

## Quick start

```bash
# open new browser
playwright-cli open
# navigate to a page
playwright-cli goto https://example.com
# take a snapshot to get element refs
playwright-cli snapshot
# interact with the page using refs
playwright-cli click e15
playwright-cli fill e15 "search query"
playwright-cli press Enter
# close when done
playwright-cli close
```

## General browsing commands

```bash
playwright-cli open
playwright-cli open https://example.com/
playwright-cli goto https://example.com
playwright-cli click e3
playwright-cli dblclick e7
playwright-cli fill e5 "user@example.com"
playwright-cli type "search query"
playwright-cli press Enter
playwright-cli select e9 "option-value"
playwright-cli upload e12 ./document.pdf
playwright-cli check e12
playwright-cli uncheck e12
playwright-cli hover e4
playwright-cli drag e2 e8
playwright-cli snapshot
playwright-cli screenshot
playwright-cli close
```

## Navigation

```bash
playwright-cli go-back
playwright-cli go-forward
playwright-cli reload
```

## Tabs

```bash
playwright-cli tab-list
playwright-cli tab-new
playwright-cli tab-new https://example.com/page
playwright-cli tab-close
playwright-cli tab-close 2
playwright-cli tab-select 0
```

## Sessions and profiles (logins)

```bash
# Named session
playwright-cli -s=mysession open https://example.com/login

# Persistent profile
playwright-cli open https://example.com --persistent
playwright-cli open https://example.com --profile=/path/to/profile

# Save and restore login state
playwright-cli state-save auth.json
playwright-cli state-load auth.json
```

## Snapshots

The `snapshot` command emits a snapshot reference. Use it to pick element refs.

```bash
> playwright-cli snapshot
### Page
- Page URL: https://example.com/
- Page Title: Example Domain
### Snapshot
[Snapshot](.playwright-cli/page-2026-02-14T19-22-42-679Z.yml)
```

## Debug and observability

```bash
playwright-cli tracing-start
# perform actions
playwright-cli tracing-stop

playwright-cli video-start
# perform actions
playwright-cli video-stop session.webm
```

## Notes

- Prefer element refs from snapshots over selectors.
- Use `--persistent` or `state-save/state-load` for login reuse.
- Use `--headed` when you want to watch the browser.
- `type` acts on the currently focused element; prefer `fill eX "text"` when possible.
- `upload` should target a file input element ref (e.g., `upload e12 ./file.pdf`).

## Optional advanced references

- Browser session management: references/session-management.md
- Storage state (cookies/localStorage): references/storage-state.md
- Tracing: references/tracing.md
- Video recording: references/video-recording.md
- Running custom code: references/running-code.md
- Request mocking (rarely needed for browsing): references/request-mocking.md
- Test generation (not typical for browsing): references/test-generation.md
