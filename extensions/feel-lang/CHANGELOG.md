# Changelog

All notable changes to the Feel Language extension are documented here.

## [0.1.0] - 2026-05-25

### Added
- Syntax highlighting for `.feel` files
  - Keywords, control flow, HTTP methods
  - All 18 stdlib namespaces with method-level coloring
  - String interpolation `{expr}` inside double-quoted strings
  - Record types (PascalCase), function calls, operators
  - Pipeline operator `|` and arrow `->` coloring
  - Comments `-- ...`
- 40+ code snippets for common patterns (routes, CRUD, auth, AI, DB)
- Language configuration (auto-close brackets, comment toggling, folding)
- File icon for `.feel` files (dark + light theme)
- `Feel: Run Current File` command (plays in integrated terminal)
- `Feel: Format Document` command (uses feelfmt via interpreter)
- Format-on-save support (configurable)
- Auto-detect Feel interpreter (`main.py`) by walking up the directory tree
