# Security Policy

Do not commit customer runtime data, credentials, tokens, OAuth state, Hermes sessions, memories, SQLite databases, or generated installation artifacts.

Customer machines keep runtime state outside this repository:

- `~/.hermes`
- `~/agent-mission-control`
- `~/.config/gooros/hermes-mission-control`
- `~/.local/share/gooros/hermes-mission-control`

The updater treats Git as release source only. It snapshots customer state before mutation and stages releases before switching the active version.
