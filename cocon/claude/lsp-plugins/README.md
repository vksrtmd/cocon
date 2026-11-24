# Vendored LSP plugins

`typescript-lsp`, `gopls-lsp`, `rust-analyzer-lsp` and `pyright-lsp`, copied verbatim (`.claude-plugin/plugin.json`
and `LICENSE`, Apache-2.0) from `plugins/<name>/` of
[anthropics/claude-plugins-official](https://github.com/anthropics/claude-plugins-official) at commit
`5cec6716767283f4675a05f5f8855b5b49c81783`. Each is a manifest naming a language server, no code.
`cocon/entrypoint.sh` passes `--plugin-dir` for one whose server `command` is on PATH.
