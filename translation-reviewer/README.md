# Translation Reviewer (EN → HR)

A standalone, zero-dependency Liquid Glass (light mode) web app for reviewing `en.json` and `hr.json` localization strings one by one.

## Running Locally

Serve the folder with Python's built-in HTTP server (recommended for consistent browser storage origin):

```bash
cd translation-reviewer
python3 -m http.server 8181
```

Then open <http://localhost:8181> in your browser (or open `index.html` directly).

## Features

- **Exact Master Key Order**: Parses `en.json` with an ordered JSON parser and preserves the exact key hierarchy, arrays, and key order in the left sidebar and in the exported `hr.json`.
- **Two-Box Side-by-Side Editor**: Read-only English source on the left, editable Croatian translation on the right, with live `{placeholder}` validation.
- **Fast Keyboard Workflow**:
  - `Enter` — Approve the current string (and save any edits) and move to the next string in line
  - `E` — Focus the Croatian editor to edit
  - `Shift + Enter` — Insert a new line inside the Croatian editor
  - `↑` / `↓` — Move to the previous or next string without changing approval status
  - `Esc` — Blur the editor / close modals
- **IndexedDB Autosave**: Every edit, approval, and cursor position autosaves locally in IndexedDB.
- **Progress Import / Export**: Export a `.json` progress snapshot at any time and import it back later or on another browser.
- **Final `hr.json` Export**: Export the completed `hr.json` formatted cleanly in master `en.json` key order.
