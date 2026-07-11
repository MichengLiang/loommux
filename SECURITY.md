# Security Policy

## Supported versions

Security fixes are developed for the latest released version of loommux.

## Reporting a vulnerability

Do not report vulnerabilities in a public GitHub issue. Use the repository's
[private vulnerability reporting form](https://github.com/MichengLiang/loommux/security/advisories/new)
instead. Include the affected version, reproduction steps, impact, and any
suggested mitigation.

The maintainer will acknowledge the report, investigate it, and coordinate a
fix and disclosure timeline through GitHub's private advisory process.

## Deployment boundary

loommux intentionally executes Python in its configured workspace. Treat the
MCP client, the process account, installed packages, workspace files, and any
network exposure as part of the security boundary. Do not expose the HTTP
server directly to untrusted networks.
