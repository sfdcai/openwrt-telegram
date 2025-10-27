# Repository Guidelines

- Never commit real Telegram bot tokens, chat identifiers, or UI API tokens. Store them locally in `config/config.json` or provide them via environment variables during deployment.
- Keep diagnostics helpers and logging facilities robust: when extending Python modules, add structured logging around external calls (Telegram, nftables, filesystem).
- Update the README whenever configuration keys, installer behaviour, or operational workflows change.
- Web assets inside `www/` should remain dependency-free and compatible with uhttpd's static file serving. Use vanilla JS/CSS only.
- Ensure shell scripts stay POSIX-compliant and executable on BusyBox sh (no bash-specific extensions).
