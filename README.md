# Keep Macs Organized

A small, conservative macOS file organizer for `Desktop`, `Documents`, and
`Downloads`. It recursively scans those folders, proposes a dated filing
location, and only moves files when you pass `--apply`.

The organizer never deletes files. Duplicate content and uncertain files are
placed in review folders.

## Vision classification

The configured private vision model at `192.168.5.40:8899` is used for PDFs
that do not match a keyword rule. The bot renders at most the first two pages
to PNG locally and sends those images, plus the filename, to the LAN endpoint.
Raw PDF files are not uploaded. Model output is restricted to categories from
`config.toml`, requires at least 70% confidence, and cannot choose paths.

Results are cached by PDF content, model, and category set under
`~/Library/Caches/KeepMacsOrganized/vision`. The default cap is 50 new model
calls per run; cached files do not count against it. Set `mode = "all"` to have
the model review even keyword matches, or `mode = "off"` for rules-only mode.
The first dry run may therefore contact the model and populate this cache even
though it does not move files. Use `--no-vision` for a completely local preview.

The renderer uses `pdftoppm` when available and falls back to macOS Quick
Look. If the renderer or LLM is unavailable, files keep their normal
rules-based destination.

## Quick start

1. Preview what the bot would do:

   ```sh
   python3 organizer.py --config config.toml
   ```

2. Review and edit `config.toml`. In particular, choose an `organized_root`.
   If both Macs use iCloud Drive, an iCloud folder is a convenient shared
   destination, for example:

   ```toml
   organized_root = "~/Library/Mobile Documents/com~apple~CloudDocs/Organized"
   ```

3. Apply the proposed moves:

   ```sh
   python3 organizer.py --config config.toml --apply
   ```

4. After you are happy with the results, install the periodic LaunchAgent:

   ```sh
   ./install-launch-agent.sh
   ```

The LaunchAgent runs every six hours and at login. It always uses `--apply`,
so do several manual previews first. macOS may ask you to grant your terminal
or Python access to Desktop, Documents, Downloads, or iCloud Drive.

## Safety model

- Dry-run is the default.
- Files newer than 24 hours are ignored, avoiding partial downloads and files
  still being edited.
- Hidden files, packages, aliases/symlinks, and the organized destination are
  skipped.
- `exclude_paths` protects source-code repositories and other working trees;
  the organizer automatically protects its own Git checkout on every Mac.
- Existing identical files are moved into `_Review/Duplicates`; they are not
  deleted.
- Name collisions receive a numeric suffix.
- Every applied move is appended to
  `~/Library/Logs/KeepMacsOrganized/actions.jsonl`.
- You can reverse an individual move using the `source` and `destination`
  fields in that log.
- Vision failures never stop the organizer; they fall back to file-type rules.

## Classification

Keyword rules are checked against the file name and its parent folder names.
PDFs may also use text already indexed by macOS Spotlight. Rules are evaluated
in the order written in `config.toml`; the first match wins. Unmatched files
are grouped by broad type under `By Type`.

The resulting structure looks like:

```text
Organized/
  Financial/2026/example-statement.pdf
  Receipts/2025/coffee-receipt.pdf
  By Type/PDF/2024/research-paper.pdf
  _Review/Duplicates/2026/...
```

## Useful commands

```sh
# Include new files for a one-time preview
python3 organizer.py --config config.toml --min-age-hours 0

# Preview without contacting the vision server
python3 organizer.py --config config.toml --no-vision

# Machine-readable preview
python3 organizer.py --config config.toml --json

# Uninstall the scheduler (does not touch organized files)
./uninstall-launch-agent.sh
```
