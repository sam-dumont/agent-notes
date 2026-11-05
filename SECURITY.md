# Security policy

Report a vulnerability privately through GitHub's "Report a vulnerability"
button on this repository (Security tab). Please don't open a public issue.

What's in scope: the MCP server (`server/`), the gate (`sync/gate.Caddyfile`),
the sync scripts and both deployments. The threat model and the known limits
are in [docs/security.md](docs/security.md): read it first, a limit listed
there is a design choice, not a finding.
