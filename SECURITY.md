# Security Policy

## Supported Versions

RatchetLab is pre-1.0. Security fixes are handled on the latest public version.

## Reporting a Vulnerability

Please open a private security advisory on GitHub if available. If that is not available, open an issue with a minimal description and avoid posting exploit details until there is a maintainer response.

Useful reports include:

- a clear reproduction path;
- the affected config, command, or file boundary;
- whether protected or disallowed files can be modified or exfiltrated;
- the operating system and Python version.

## Security Model

RatchetLab runs configured shell commands. Treat `agent_cmd` and `eval_cmd` as trusted local automation.

The project is designed to reduce accidental goalpost-moving by checking allowed paths, protected paths, evaluator output, gates, and workspace restoration. It is not a sandbox for untrusted code. Do not run untrusted agent commands, evals, or configs on a machine with sensitive files.
