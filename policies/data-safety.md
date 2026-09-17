# Data safety

- Keep credentials, local settings, session transcripts, runtime memory, and generated reports out of Git.
- Read the current file before editing. Preserve unrelated changes and user-owned configuration.
- Use collision-aware deployment and preserve backups before replacing managed files.
- Do not remove directories or rewrite remote history without authorization covering that action.
- Before publishing, audit the exact Git tree with `tools/public_safety.py` and a secret scanner. Keep private denylists outside the repository.
- Build public copies from public history and explicitly selected files. Never merge private history into them.
- Use synthetic public test fixtures. Do not copy real prompts, customer records, private endpoints, or local profiles into tests.
