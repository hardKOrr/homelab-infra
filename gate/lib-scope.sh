# Shared scope resolution for the lint and test gates. Sourced, never executed.
#
# A full sweep is 33 cold `ansible-playbook --syntax-check` interpreters plus an
# ansible-lint pass over the whole tree, and on /mnt/c that is minutes of 9p syscalls to
# re-prove files nobody touched. This narrows the default run to the working tree's own
# changes, and is deliberately biased toward running MORE than strictly necessary:
#
#   * a clean tree resolves to FULL, not to "nothing changed, pass" — so the run right
#     after a commit, which is the one that gates the commit's own correctness, is the
#     complete sweep. There is no state in which an empty change set reports a green.
#   * anything a playbook consumes (roles/, tasks/, vars/, inventory/, ansible.cfg) fans
#     out into every playbook, so touching one promotes the run back to FULL. Markdown is
#     documentation, even when it is colocated with those files, and does not fan out.
#   * a changed playbook drags in any playbook that names it, because bootstrap.yml
#     chains the app playbooks with import_playbook.
#   * git being unavailable or unhappy (WSL reading a Windows checkout can refuse on
#     dubious ownership) resolves to FULL. The narrow run is an optimisation and must
#     never be the failure mode.
#
# Force a full sweep with `--all` as the first argument, or GATE_SCOPE=all.

# Paths whose change invalidates every playbook.
gate_fanout_re='^ansible/(roles|tasks|vars|group_vars|host_vars|inventory|files)/|^ansible/(ansible\.cfg|requirements\.yml)$|^gate/'

# Sets: gate_scope (full|changed), gate_scope_reason, gate_changed[]
gate_resolve_scope() {
    gate_scope=full
    gate_scope_reason="default"
    gate_changed=()

    if [ "${1:-}" = "--all" ]; then
        gate_scope_reason="--all requested"
        return 0
    fi
    if [ "${GATE_SCOPE:-}" = "all" ]; then
        gate_scope_reason="GATE_SCOPE=all"
        return 0
    fi

    local diffed untracked
    if ! diffed=$(git -c core.quotePath=false diff --name-only HEAD 2>/dev/null); then
        gate_scope_reason="git diff unavailable here; not narrowing"
        return 0
    fi
    if ! untracked=$(git -c core.quotePath=false ls-files --others --exclude-standard 2>/dev/null); then
        gate_scope_reason="git ls-files unavailable here; not narrowing"
        return 0
    fi

    mapfile -t gate_changed < <(printf '%s\n%s\n' "$diffed" "$untracked" | sed '/^$/d' | sort -u)

    if [ "${#gate_changed[@]}" -eq 0 ]; then
        gate_scope_reason="working tree clean; full sweep"
        return 0
    fi

    local path
    for path in "${gate_changed[@]}"; do
        case "$path" in
            *.md) continue ;;
        esac
        if [[ "$path" =~ $gate_fanout_re ]]; then
            gate_scope_reason="$path fans out into every playbook; full sweep"
            return 0
        fi
    done

    gate_scope=changed
    gate_scope_reason="${#gate_changed[@]} changed path(s)"
    return 0
}

# Echoes the changed files that live under ansible/ and end in .yml/.yaml, as paths
# relative to the ansible/ directory. Call only when gate_scope=changed.
gate_changed_ansible_yaml() {
    local path
    for path in "${gate_changed[@]}"; do
        case "$path" in
            ansible/*.yml | ansible/*.yaml) ;;
            *) continue ;;
        esac
        [ -f "$path" ] || continue   # deleted: nothing left to check
        printf '%s\n' "${path#ansible/}"
    done
}

# Echoes the playbooks to syntax-check, relative to ansible/, closed over the playbooks
# that import them. Run with the repo root as cwd. Call only when gate_scope=changed.
gate_changed_playbooks() {
    local seeds=() path base importer
    while IFS= read -r path; do
        case "$path" in playbooks/*) seeds+=("$path") ;; esac
    done < <(gate_changed_ansible_yaml)

    # Written as an `if`, not `[ ... ] && return`: under a caller's `set -e` a failing
    # test as the head of an AND-list terminates the shell.
    if [ "${#seeds[@]}" -eq 0 ]; then
        return 0
    fi

    {
        printf '%s\n' "${seeds[@]}"
        # Any playbook that names a changed playbook re-imports it (bootstrap.yml chains
        # the app playbooks), so it has to be re-checked too. One grep over 33 files.
        for path in "${seeds[@]}"; do
            base=$(basename "$path")
            while IFS= read -r importer; do
                printf '%s\n' "${importer#ansible/}"
            done < <(grep -rl --include='*.yml' -F "$base" ansible/playbooks 2>/dev/null)
        done
    } | sort -u
}
