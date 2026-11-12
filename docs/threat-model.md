# Threat model

What cocon promises, what enforces each promise and which test checks it, and what is known not to hold.
Depth lives in the ADRs linked from each section. A change that touches a trust boundary is checked against this
doc; a change that closes or opens a gap updates it in the same PR.

## The attacker

A prompt-injected session: anything that runs in the session's container as its user. Claude misled by a file, an
issue or a web page, a dependency's install script, a tool it calls. Assume it controls everything the session can
write and every request the broker lets out, for as long as the session runs, and that what it leaves behind stays.

It wants to run code on the Mac, copy a real token, send the repo's code or data out, reach another repo's session,
volumes or token, or land code on the default branch without a human looking.

Out of scope:

- The host user, and what they run by hand. The kit can only report what a session left for them (container to
  host, host git).
- The host's Docker runtime and the Linux VM it runs every container in (OrbStack, Colima, Docker Desktop;
  [ADR 0004](adr/0004-docker-compose-runtime.md)). A kernel escape from a container is listed as a gap below, not
  defended against.
- GitHub, Anthropic and the allowlisted hosts themselves, and what they accept from a valid credential.
- Other local users on the Mac, except that they can't read or edit the audit log.

Severity in the gap lists: **high** means code on the Mac, a real token out, or another repo reached; **medium**
means data out, or unreviewed code landing, unless the user does something the kit can't check; **low** needs
another failure first, or only weakens detection.
Each gap names its open issue, or where it is accepted; "No ticket" means no issue tracks it.

"The launcher" is `cocon/py/kit/launcher/`, host-guard is `cocon/py/kit/guard/` and `workflow-push` is `cocon/py/kit/workflowpush/`:
the commands of those names in `cocon/` are shims that run them with `python3 -I -B`
([ADR 0013](adr/0013-kit-and-tests-in-python.md)). The launcher and `workflow-push` call host-guard in-process.

## Container to host

| Promise | Enforced by | Test |
| --- | --- | --- |
| The session gets no host credentials, SSH agent or Docker socket: only the checkout and the user's Claude setup read-only | the launcher's mounts for `dev` (`cocon/py/kit/launcher/mounts.py`), `cocon/compose.yml` | `tests/test_launcher_session.py`: `test_setup_mounted_read_only_claude_md_as_user_level`, `test_no_docker_socket_ssh_dir_or_agent_whatever_the_host_sets` |
| No root in the session: non-root user, no capabilities, `no-new-privileges`, no setuid or setgid files, capped memory and processes | `cocon/compose.yml` (`dev`), the Dockerfile's last stage | `tests/test_dockerfile.py`: `test_final_stage_strips_and_asserts_last`; `tests/test_compose.py`: `test_dev_has_no_root_path`, `test_docker_sidecar_has_the_sessions_limits` (limits) |
| No hook can be planted: `.git/hooks`, every submodule's hooks dir and an in-tree `core.hooksPath` are read-only, made empty if missing; a hooks dir that is, or goes through, a symlink refuses the launch, so a session can't get a host dir mounted | launcher mounts ([ADR 0008](adr/0008-deferred-host-escape.md), [ADR 0011](adr/0011-detect-host-entry-points-no-copy-mode.md), [ADR 0015](adr/0015-host-git-never-trusts-session-writable-state.md)) | `tests/test_launcher_session.py`: `test_git_repo_hooks_read_only_exit_code_passed_through_quiet`; `tests/test_launcher_hooks.py`: `test_submodule_hooks_dir_read_only_too`, `test_in_tree_hooks_path_created_and_mounted_read_only`, `test_symlinked_submodule_hooks_dir_refuses_the_launch`, `test_symlinked_component_in_in_tree_hooks_path_refuses_the_launch` |
| The kit the host runs can't be changed: cocon's main checkout is read-only except `.git` and `.claude/worktrees/`, and so is a Claude setup dir inside the checkout | launcher mounts | `tests/test_launcher_session.py`: `test_main_checkout_read_only_but_git_and_worktrees_hooks_too`, `test_inside_the_project_checkout_read_only_but_git_and_worktrees` |
| After each session the launcher reports every risky git config entry beyond the trusted baseline and every host entry point added, removed or changed in a writable checkout (a cocon worktree's kit included); a read-only one changes only on the host | host-guard (`cocon/py/kit/guard/`) | `tests/test_launcher_hooks.py`: `test_planted_git_config_entry_is_reported`, `test_changed_entry_points_reported_exit_code_kept`; `tests/test_launcher_session.py`: `test_read_only_checkout_changed_on_the_host_isnt_reported`; `tests/test_host_guard.py`: `test_files_kit_change_in_a_cocon_checkout_is_reported` |
| A check that can't read its input refuses or warns loudly, never passes | host-guard (exit 2) | `tests/test_launcher_hooks.py`: `test_unreadable_git_before_session_refuses_to_start`, `test_unreadable_host_entry_point_before_session_refuses_to_start`, `test_include_of_a_fifo_before_session_refuses_to_start`, `test_include_of_a_fifo_planted_in_session_is_reported`; `tests/test_host_guard.py`: `test_an_include_of_a_fifo_refuses_without_hanging` |
| Nothing in the session is reachable from the Mac but the `--port` ports, on loopback | port forwarder, `internal` network | `tests/test_launcher_port.py`: `test_forwarder_publishes_on_loopback_session_answers_to_dev_removed_after`, `test_down_and_destroy_remove_this_checkouts_forwarders_only`; `tests/test_compose_integration.py`: `test_internal_only_container_drops_published_port` |

Known gaps:

- **medium.** Files the host runs that aren't host entry points stay writable and unreported: Makefiles, `.envrc`,
  `.vscode/tasks.json`, nested repos, hooks dirs of submodules added mid-session. Accepted in ADR 0011: the README
  tells the user to review diffs first. No ticket.
- **medium.** A repo hook that runs project code (`npm test` in `pre-push`) runs what the session changed at the next
  host `git`. Accepted in ADR 0011. No ticket.
- **high.** Every session shares one Linux VM and kernel: a container escape reaches every repo's brokers, volumes and
  tokens. Accepted in ADR 0004. No ticket.
- **low.** The after-session report doesn't run when the terminal closes mid-session; `--detach` keeps it
  ([ADR 0001](adr/0001-detach-via-host-tmux.md)). No ticket.
- **low.** The launcher checks the hooks dirs, then Docker mounts them: a session already running on the same checkout
  could swap one for a symlink in between. Needs two sessions on one checkout and a tight race. No ticket.

## Host git

[ADR 0015](adr/0015-host-git-never-trusts-session-writable-state.md): host git never trusts session-writable state.

| Promise | Enforced by | Test |
| --- | --- | --- |
| Every git call the launcher and `workflow-push` make goes through `host-guard git`, which finds the git dir from the path and refuses mismatched worktree pointers, a planted `commondir` and worktrees outside `.claude/worktrees/`; the config values the launcher reads come through the guard's `config_get`, which finds the git dir the same way | host-guard (`cocon/py/kit/guard/`) | `tests/test_host_guard.py`: `test_refuses_a_worktree_pointing_at_another_repo`, `test_refuses_a_planted_commondir_in_the_main_git_dir`, `test_core_worktree_cant_make_a_session_made_repo_the_main_checkout`; `tests/test_launcher_worktree.py`: `test_worktree_file_pointing_at_a_fake_repo_refuses_the_launch` |
| Host git refuses while a risky config entry (`remote.*.url`, `pushurl`, `push`, `mirror` and `proxy`, and every `http.*` key, included) is not in the trusted baseline | host-guard (`cocon/py/kit/guard/`) | `tests/test_host_guard.py`: `test_planted_risky_keys_are_flagged`, `test_keys_that_redirect_host_git_credentials_are_flagged`, `test_refuses_an_untrusted_risky_entry_and_names_it`; `tests/test_launcher_worktree.py`: `test_untrusted_risky_config_refuses_worktree_creation_before_any_git` |
| Code-running settings host-guard might miss (fsmonitor, hooks path, ssh command, pager, editor, askpass) are overridden on the command line; the user's global config still applies | host-guard (`cocon/py/kit/guard/`) | `tests/test_host_guard.py`: `test_each_override_neutralises_a_repo_local_value`, `test_a_trusted_fsmonitor_still_does_not_run`, `test_a_planted_hook_does_not_run`, `test_the_users_global_config_still_applies`; `tests/test_launcher_worktree.py`: `test_destroy_runs_no_planted_fsmonitor` |
| The trusted git config baseline lives in host config and changes only by `--trust-git`; a planted entry is reported after every session until the user acts | launcher, host-guard | `tests/test_launcher_baseline.py`: `test_entry_planted_in_session_1_is_reported_after_session_2_too`, `test_trust_declined_changes_nothing`, `test_repin_keeps_the_baseline` |
| Token, allowlist and volumes follow the pinned repo ID, never `origin`: a changed `origin`, or a host other than exactly `github.com`, refuses until `--repin` | launcher | `tests/test_launcher_pin.py`: `test_changed_origin_refuses_before_broker_or_session`, `test_non_github_host_gives_no_repo_id`; `tests/test_launcher_token.py`: `test_every_subcommand_with_origin_rewritten_uses_the_pin_not_origin`; `tests/test_launcher_pin.py`: `test_build_attach_proxy_log_need_no_token_so_pass_the_pin_check` |

Known gaps:

- **low.** `host-guard git` checks, then runs git: a live session could write config in the milliseconds between.
  Accepted in ADR 0015. No ticket.
- **medium.** Git the user runs by hand on the host isn't guarded; only the after-session report stands between a
  planted entry and it. Accepted in ADR 0008. No ticket.

## `workflow-push`

| Promise | Enforced by | Test |
| --- | --- | --- |
| The command the user pastes holds nothing the session wrote but a branch name, and a name that is an option, a force refspec or the default branch is refused | `workflow-push` (`cocon/py/kit/workflowpush/`) | `tests/test_workflow_push.py`: `test_old_forms_fail_with_usage`, `test_names_that_become_options_or_force_pushes_are_refused`, `test_default_branch_is_refused`, `test_default_branch_comes_from_gh_for_the_pinned_repo` |
| It publishes no host file the session pointed at: the git common dir can't be a symlink, and the patch is read once, each component of its path opened without following a symlink, so a symlink swapped in at any moment fails the open itself | `workflow-push` (`cocon/py/kit/workflowpush/`) | `tests/test_workflow_push.py`: `test_symlinked_patch_is_refused`, `test_symlinked_common_dir_is_refused`, `test_slash_in_name_cannot_walk_through_a_symlinked_prefix`; `tests/test_workflow_push_files.py`: `test_title_that_is_a_symlink_only_while_opened_is_refused`, `test_dir_swapped_for_a_symlink_after_the_look_is_refused` |
| The patch carries only `.github/workflows/` regular files and is checked and applied from one private copy; the whole commit pushed is shown as text (a `-diff` attribute or `core.bigFileThreshold` the session planted can't turn it into "Binary files differ"), control characters escaped, for a y/N confirmation. It is built on the branch's tip on the pinned repo, which the session's token pushed and so holds no workflow change, so that diff is every workflow change the push makes | `workflow-push` (`cocon/py/kit/workflowpush/`) | `tests/test_workflow_push.py`: `test_shows_the_whole_commit_and_a_wrong_answer_pushes_nothing`, `test_control_characters_in_the_workflow_change_are_shown_escaped`, `test_workflow_marked_binary_by_attributes_or_config_is_still_shown`, `test_patch_outside_workflows_is_refused`, `test_patch_adding_symlink_or_executable_is_refused`, `test_patch_renaming_out_of_workflows_is_refused`, `test_patch_swapped_after_the_confirmation_changes_nothing`, `test_nul_in_the_patch_subject_refuses_before_anything_acts`; manually, on 2026-11-07, from a session: a workflow commit pushed onto an existing branch, the same commit as a new ref, and a new ref at an older commit of main whose workflows differ were each refused with `refusing to allow a Personal Access Token to create or update workflow … without workflow scope` |
| It pushes the commit it showed: the tip is read once, as the exact ref from the pinned URL, and the commit is that tip plus the confirmed patch, built in a private index, so nothing staged in the worktree's index, committed locally or pushed under a decoy ref reaches the remote | `workflow-push` (`cocon/py/kit/workflowpush/`) | `tests/test_workflow_push.py`: `test_a_file_staged_in_the_worktree_is_not_pushed`, `test_a_commit_made_while_the_prompt_waits_is_not_pushed`, `test_local_commits_the_session_did_not_push_are_neither_shown_nor_pushed`, `test_a_decoy_ref_ending_in_the_branch_name_is_not_its_tip` |
| Its commit runs no hook (it is built with plumbing), and its git runs through `host-guard git` | `workflow-push` (`cocon/py/kit/workflowpush/`) | `tests/test_workflow_push.py`: `test_patch_commit_runs_no_hook`, `test_patch_commit_runs_no_post_commit_hook`, `test_planted_fsmonitor_never_runs`, `test_planted_clean_filter_never_runs`, `test_rewritten_worktree_git_file_is_refused`, `test_no_baseline_is_refused` |
| It pushes a non-forcing refspec to the pinned repo's URL, whatever `origin` says | `workflow-push` (`cocon/py/kit/workflowpush/`) | `tests/test_workflow_push.py`: `test_planted_force_push_config_leaves_main_alone`, `test_planted_origin_url_is_refused_and_its_hook_never_runs`, `test_trusted_origin_url_still_cannot_redirect_the_push`, `test_unpinned_checkout_is_refused` |

Known gaps:

- **medium.** The confirmation protects only as far as the user reads the workflow diff: once pushed, those
  workflows run on CI with the repo's secrets. Accepted. No ticket.

## Egress

[ADR 0005](adr/0005-egress-domain-allowlist.md) for the allowlist, [ADR 0014](adr/0014-credentials-brokered-outside-the-session.md)
for the broker's checks, [ADR 0012](adr/0012-audit-log-is-what-the-session-cannot-write.md) for the audit log.

| Promise | Enforced by | Test |
| --- | --- | --- |
| The one way out is the session's broker, then squid: `dev` has no route out, and only the broker reaches squid | `cocon/compose.yml` networks, `cocon/squid/squid.conf` | `tests/test_compose.py`: `test_only_the_broker_shares_a_network_with_egress`; `tests/test_broker_integration.py`: `test_dev_cant_open_a_connection_to_egress_by_name_or_any_of_its_addresses` |
| Each shared allowlist host is there for a tool sessions use, named in the comment above its group in `cocon/squid/allowlist.txt`; a host only one repo needs goes in that repo's own file | `cocon/squid/allowlist.txt` | None: checked in review |
| Only the shared and the repo's own allowlist are reachable; every other host is denied and logged | `cocon/squid/allowlist.txt`, the repo's allow file | `tests/test_compose_integration.py`: `test_audit_log_format_and_reopen_after_move`; `tests/test_launcher_repo.py`: `test_github_repo_allowlist_file_created_and_passed_to_compose` |
| `dev` can't resolve outside names, only the broker's: naming a host to the broker is the one way to reach it | `cocon/compose.yml` (`internal` network is `internal: true`) | `tests/test_broker_integration.py`: `test_dev_cant_resolve_outside_names_only_the_broker` |
| Names are matched as given, never resolved, and raw-IP destinations are denied | `cocon/squid/squid.conf` | `tests/test_broker_integration.py`: `test_raw_ip_connect_refused_by_squid_and_logged_allowlisted_names_still_work` |
| CONNECT reaches only port 443, even on an allowlisted name | `cocon/squid/squid.conf` | `tests/test_broker_integration.py`: `test_connect_to_an_allowlisted_host_on_a_port_other_than_443_refused_by_squid_and_logged` |
| A decrypted request whose Host header isn't its connection's host is refused; over HTTP/2 that is both `:authority` and any Host header sent beside it | `cocon/broker/addon.py` | `tests/test_broker_addon.py`: `test_a_host_header_naming_another_host_is_refused_on_every_decrypted_host`, `test_http2_host_header_beside_a_matching_authority_is_checked_too` (the HTTP/2 case against the addon's stub only: the integration harness has no HTTP/2 client); `tests/test_broker_integration.py`: `test_plain_http_or_a_forged_host_header_gets_the_brokers_403` |
| `storage.googleapis.com` passes only GET and HEAD inside the Go module proxy's bucket; a dot segment or an encoded dot, slash or backslash in the path is refused | `cocon/broker/addon.py` | `tests/test_broker_addon.py`: `test_google_storage_passes_only_reads_inside_the_go_proxys_bucket`; `tests/test_broker_integration.py`: `test_google_storage_reads_of_the_go_proxys_bucket_pass_the_rest_never_reach_upstream` |
| A tunnel carries only TLS whose SNI is the CONNECT host: a mismatch, no SNI, ECH or non-TLS is dropped | `cocon/broker/addon.py` | `tests/test_broker_addon.py`: `test_tunnel_whose_sni_names_another_host_is_dropped_with_one_audit_line`, `test_no_sni_ech_or_not_tls_is_dropped`, `test_host_case_and_trailing_dots_dont_dodge_the_checks`; `tests/test_broker_integration.py`: `test_tunnel_whose_sni_names_another_host_or_none_is_dropped_with_an_audit_line` |
| Writes to the public npm registry, Docker Hub, PyPI's and crates.io's download hosts are refused, from the session and from nested containers | `cocon/broker/addon.py` | `tests/test_broker_addon.py`: `test_writes_to_the_public_registries_refused_reads_pass_with_or_without_credentials`; `tests/test_broker_integration.py`: `test_npm_style_put_gets_403_with_an_audit_line_reads_pass_and_never_reach_upstream`; `tests/test_compose_integration.py`: `test_docker_sidecar_pulls_from_docker_hub_through_the_broker_and_its_push_is_refused` |
| The audit log is written only outside `dev`, closed to other local users, never deleted by the kit, and the broker serves nothing it can't log | squid and broker logs, launcher | `tests/test_broker_addon.py`: `test_unwritable_audit_log_stops_the_broker_rather_than_serving_unaudited`; `tests/test_broker_integration.py`: `test_audit_one_line_per_decrypted_request_no_query_headers_or_tokens`; `tests/test_launcher_audit.py`: `test_state_dir_closed_to_other_users_even_if_it_was_open`, `test_destroy_keeps_the_audit_log_and_says_where` |

Known gaps:

- **medium.** Data can leave through what allowlisted hosts accept: GitHub with the repo's own token (issues,
  comments, branches; public if the repo is), and other hosts' upload features. Accepted in ADR 0005. No ticket.
- **medium.** A read's path and query carry data out to npm, Docker Hub and every other allowlisted host; the broker
  refuses none. Accepted (README, What egress stops). No ticket.
- **low.** A tunnel the broker doesn't decrypt (`proxy.golang.org`, `sum.golang.org`, a repo's own hosts) is checked
  by SNI only: whether its Host header can reach another tenant is the edge's to refuse. Accepted in ADR 0005.
  No ticket.
- **low.** Playwright browsers are fetched by the launcher outside the proxy, with npm install scripts off, at the
  version the session's `node_modules` names, from the npm registry; nothing else the session wrote is in reach.
  Accepted in ADR 0005. No ticket.
- **low.** The per-repo audit log dirs are mode 777, because the runtimes enforce bind-mount modes differently;
  the 700 state dir above them is what keeps other local users out. Accepted (host checklist). No ticket.

## Credentials

[ADR 0007](adr/0007-per-repo-github-token.md) for the GitHub token, ADR 0014 for the broker.

| Promise | Enforced by | Test |
| --- | --- | --- |
| The session holds only placeholders; the real Claude and GitHub tokens are only in its broker, and of a session's docker calls only the broker's run has them | launcher, `cocon/compose.yml` | `tests/test_launcher_broker.py`: `test_broker_gets_real_tokens_dev_only_placeholders_and_the_broker_as_proxy`, `test_only_the_brokers_docker_call_carries_the_real_tokens`, `test_detach_relaunch_starts_the_broker_dev_gets_only_placeholders` |
| A token is swapped in only over TLS, to the host it belongs to (the connection's host, never the `Host` header); a placeholder anywhere else is refused | `cocon/broker/addon.py` | `tests/test_broker_addon.py`: `test_bearer_x_api_key_token_and_basic_swapped_at_their_own_hosts`, `test_plain_http_and_unknown_hosts_refused_never_sent_on`, `test_a_host_header_naming_another_host_is_refused_on_every_decrypted_host`; `tests/test_broker_integration.py`: `test_plain_http_or_a_forged_host_header_gets_the_brokers_403` |
| On the token hosts, any credential that isn't the session's placeholder is refused, so the session can't act as someone else | `cocon/broker/addon.py` | `tests/test_broker_addon.py`: `test_foreign_credentials_refused_on_token_hosts_no_credential_passes`; `tests/test_broker_integration.py`: `test_foreign_authorization_on_a_github_host_gets_403_the_placeholder_still_works` |
| No full-scope Claude credential in the session: a `/login` credential is deleted at each start | `cocon/entrypoint.sh` | `tests/test_broker_integration.py`: `test_dev_removes_a_login_credential_at_start_with_one_line` |
| Only a fine-grained GitHub token is brokered, and one that reaches other private repos is warned about | launcher, through the host's `gh` | `tests/test_launcher_token.py`: `test_classic_token_refused_naming_the_readme_no_broker_no_gh`, `test_token_reaching_another_private_repo_warns_and_launches` |
| The broker's CA is per session, its key in a volume only the broker mounts, both removed when the session ends; the broker has no capabilities | launcher, `cocon/compose.yml` | `tests/test_launcher_broker.py`: `test_removed_with_both_ca_volumes_after_the_session_also_when_it_fails`; `tests/test_broker_integration.py`: `test_no_capabilities_no_new_privileges` |
| A credential file is parsed as KEY=VALUE lines, never run, and one with a loose mode is refused before it is read | launcher (`credentials.py`) | `tests/test_launcher_token.py`: `test_doctor_doesnt_run_a_token_file_with_a_loose_mode`; `tests/test_launcher_credentials.py`: `test_a_line_that_would_run_a_command_refuses_and_never_runs`, `test_loose_mode_refused_before_the_file_is_read` |

Known gaps:

- **high.** While it runs, the session acts as the user within the token's scope: it pushes and deletes unprotected
  branches and tags, edits releases and others' issues, and merges its own PR unless branch protection stops it. The
  kit can't read branch protection; the README lists the settings. Accepted in ADR 0007. No ticket.
- **medium.** The reach check can't see a token's write access to public repos the user collaborates on, nor
  permissions such as Workflows or Administration; only the README says not to grant them. Accepted in ADR 0007. No
  ticket.
- **medium.** The broker sees the plaintext of the hosts it decrypts and holds the real tokens, so a compromised
  mitmproxy image is a token leak. Mitigated by its digest pin and its own Renovate PR (CI and supply chain). Accepted
  in ADR 0014. No ticket.

## The Docker sidecar

[ADR 0016](adr/0016-docker-sidecar-is-rootless.md).

| Promise | Enforced by | Test |
| --- | --- | --- |
| The daemon is rootless in an unprivileged container: nothing it starts gets root on the VM or reads its disks | `cocon/compose.yml` (`docker`) | `tests/test_compose.py`: `test_docker_sidecar_is_unprivileged_on_the_internal_network_only`; `tests/test_compose_integration.py`: `test_neither_the_sidecar_nor_a_nested_privileged_container_can_read_a_vm_disk` |
| A nested container sees the checkout as the session does: read-only where the session's mount is, every hooks dir read-only | launcher's per-session compose override | `tests/test_launcher_with.py`: `test_docker_sidecar_gets_the_sessions_checkout_mounts_and_modes`; `tests/test_launcher_session.py`: `test_main_checkout_read_only_for_the_docker_sidecar_too`; `tests/test_compose.py`: `test_docker_sidecar_mounts_nothing_of_the_host_writable`; `tests/test_compose_integration.py`: `test_nested_container_cant_write_the_hooks_dir_of_a_writable_checkout` |
| The sidecar has the session's memory and process caps | `cocon/compose.yml` | `tests/test_compose.py`: `test_docker_sidecar_has_the_sessions_limits`; `tests/test_compose_integration.py`: `test_nested_container_cant_write_what_the_session_cant_and_the_sidecar_has_the_sessions_limits` |
| It never falls back to a privileged or rootful daemon | launcher | `tests/test_launcher_with.py`: `test_docker_sidecar_that_isnt_healthy_refuses_naming_the_runtime` |
| Its traffic goes through a broker of its own, which holds no token, under the same checks | `cocon/compose.yml` (`docker-broker`) | `tests/test_compose_integration.py`: `test_docker_sidecar_pulls_through_its_broker`, `test_docker_sidecar_uses_its_own_broker_whatever_sessions_run_and_trusts_it_after_a_restart` |

Known gaps:

- **medium.** The sidecar runs with seccomp, AppArmor and the `/proc` and `/sys` masks off, which rootless needs, so
  an escape from its user namespace meets fewer walls than one from `dev`. Accepted in ADR 0016. No ticket.
- **low.** Its API is plain unauthenticated TCP on the checkout's internal network, so anything there can drive it.
  Accepted in ADR 0016. No ticket.
- **low.** A privileged sidecar a version before ADR 0016 started keeps running, reachable from any session of its
  checkout, until `cocon --down`; nothing finds it. Accepted: every known install has launched since. No ticket.
- **low.** The tests that start the sidecar skip in the cocoon (nested rootless dind) and run only in CI, and each host
  runtime is checked by hand from the PR's host checklist. No ticket.

## Isolation between repos

[ADR 0009](adr/0009-nothing-writable-shared-between-repos.md), [ADR 0010](adr/0010-slug-is-the-repo-id.md),
[ADR 0002](adr/0002-compose-project-per-worktree.md).

| Promise | Enforced by | Test |
| --- | --- | --- |
| Each repo has its own Claude config and cache volumes, keyed by its pinned repo ID | launcher, `cocon/compose.yml` | `tests/test_launcher_repo.py`: `test_repo_volumes_keyed_by_owner_repo_shared_by_worktrees`; `tests/test_compose_integration.py`: `test_volumes_named_after_repo_volumes`, `test_repo_volumes_unset_is_refused` |
| Each checkout and worktree has a slug no other repo's checkout shares, naming its compose project, services, data and `node_modules`; `--destroy` removes only its own | launcher | `tests/test_launcher_repo.py`: `test_github_origin_slug_is_the_repo_id_whatever_the_folder`, `test_names_that_sanitize_alike_by_the_same_owner_differ`, `test_main_folder_with_separator_and_a_worktree_differ`, `test_destroy_names_only_its_own_project` |
| Each session has its own broker, holding only its repo's token, and its repo's own allowlist | launcher | `tests/test_launcher_broker.py`: `test_two_sessions_each_their_own_broker_ending_one_leaves_the_others`; `tests/test_launcher_repo.py`: `test_github_repo_allowlist_file_created_and_passed_to_compose` |
| A session can't reach another checkout's broker, databases or Docker sidecar: each compose project has its own internal network | `cocon/compose.yml` (a network per project, [ADR 0002](adr/0002-compose-project-per-worktree.md)) | `tests/test_compose_integration.py`: `test_session_network_cant_reach_another_projects_broker` (two projects; a container on the first's network can't connect to the second's broker by name or address) |

Known gaps:

- **low.** Two checkouts without a GitHub `origin` in same-named folders share a slug and per-repo volumes; `--doctor`
  says so. Accepted in ADR 0010. No ticket.
- **low.** A second clone of one repo shares its slug: databases, `node_modules` and the detached session, and
  `--destroy` in one removes the other's. Within one repo and its token, so not between repos; unsupported, and the
  launch and `--doctor` warn (`tests/test_launcher_repo.py`: `test_shares_the_slug_and_warns_while_the_other_exists`). Accepted in ADR 0010. No ticket.
- The shared VM is listed under container to host.

## CI and supply chain

| Promise | Enforced by | Test |
| --- | --- | --- |
| Everything the image build downloads is checked against a pin, or comes from a digest-pinned image | `cocon/Dockerfile` | `tests/test_dockerfile.py`: `test_image_build_checks_every_download`; the CI image build (`cocon.yml`) |
| The image's npm tools install from a committed lockfile, with install scripts off unless listed | `cocon/npm-tools/` | `tests/test_dockerfile.py`: `test_kit_lockfile_manifest_and_rebuild_agree` |
| The Playwright browser fetch, the one container with open internet, runs no npm install scripts | launcher | `tests/test_launcher_with.py`: `test_playwright_fetch_runs_no_npm_install_scripts` |
| That fetch reads nothing a session can write but the Playwright version, which `jq` parses out of `package.json` (node's `require()` of that path would run a module planted there): it mounts only its own volume, which no session mounts, and `node_modules` read-only; the browsers reach the session's cache through a copy with no network | launcher | `tests/test_launcher_with.py`: `test_playwright_fetch_mounts_nothing_the_session_writes`, `test_playwright_downloads_then_copies_into_cache_volume_before_session` |
| PR code on CI runs with a read-only `GITHUB_TOKEN` that isn't left in the git config | `permissions` and `persist-credentials: false` in both workflows | `tests/test_ci_hardening.py`: `test_workflows_grant_only_contents_read`, `test_every_checkout_drops_the_token` |
| A session can't change the workflows: its token lacks the Workflows permission, and `workflow-push` confirms what reaches them | token setup (README), `workflow-push` | see `workflow-push` |
| Renovate proposes a version only once it is 7 days old, and the broker's and egress proxy's images get PRs of their own | `renovate.json` | `tests/test_ci_hardening.py`: `test_renovate_waits_7_days_and_isolates_the_broker_and_proxy_images` |
| CI runner labels and shellcheck are pinned | `shell.yml`, `cocon.yml` | `tests/test_ci_hardening.py`: `test_runners_and_shellcheck_are_pinned` |

Known gaps:

- **medium.** A PR's code runs on GitHub's runners with open internet; the read-only token limits what it can
  write, not what it can send. Accepted in ADR 0007. No ticket.
- **medium.** apt packages from the base image's distribution are unpinned beyond the base image's digest. Outside what
  the image build verifies (every download it makes, pinned and checked). No ticket.
- **low.** The image isn't signed or scanned for vulnerabilities. Outside what the image build verifies. No ticket.
