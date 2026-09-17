# Troubleshooting notes

This public distribution contains no collected user sessions or project incident history.

- On Windows, use PowerShell paths and quoting consistently. See [platform awareness](../skills/platform-awareness/SKILL.md).
- Use UTF-8 for configuration files; PowerShell versions have different encoding defaults.
- Inspect contents or byte size before treating a zero line count as an empty file.
- Reset module caches in tests that change environment variables or temporary configuration.
- Read persisted counters before computing and writing their next value.
- Write multiline issue and pull-request bodies to a file and pass that file to the CLI.
- After changing deployment sources, run tests and validate the deployment manifest.
- Authentication errors are not automatically temporary transport failures.
