# LSP plugins

`typescript-lsp` and `pyright-lsp`: cocon's own manifests (`.claude-plugin/plugin.json`, no code), each naming a
language server. Their names mirror the official marketplace's `typescript-lsp` and `pyright-lsp`, so a volume that
installed one is recognised. `cocon/entrypoint.sh` passes `--plugin-dir` for one whose server `command` is on PATH
and which isn't installed at user scope.
