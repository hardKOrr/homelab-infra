#!/usr/bin/env bash
# Move an application's live state aside, put restored state in its place, and put the
# original back when the restore fails. ../restore-swap.yml runs it on the host that holds
# the data. Every command reads the filesystem rather than a record of earlier steps, so
# rollback after an interrupted step still puts the original back.
#
# Usage: restore-swap.sh COMMAND PATH [--mount] [--owner UID:GID] [--mode MODE] [--source DIR]
#
#   check     PATH is a real directory (with --mount: an exact mount point) and holds no
#             rollback copy from an earlier run
#   aside     move PATH's state into its rollback copy
#   install   put --source in place of PATH's state, give every file --owner and PATH
#             --mode; with --mount the copy runs as --owner, and the mount keeps its own
#             mode when --mode is not given
#   rollback  when a rollback copy exists, remove the replacement and put the copy back
#   cleanup   remove the rollback copy after a successful restore
#
# Directory mode: the rollback copy is PATH's sibling .NAME.restore-previous, on PATH's own
# filesystem, so moving aside is a rename. --mount: PATH cannot be renamed, so its entries
# move into PATH/.restore-previous, and every step inside it runs as --owner, because a
# root-squashed NFS export gives root no access there.
#
# The archive's ownership is never used: install assigns the numeric owner to every file.
set -euo pipefail

usage() {
  echo "restore-swap.sh: $*" >&2
  exit 64
}

[ $# -ge 2 ] || usage "needs COMMAND PATH"
command=$1 path=$2
shift 2
mount=false owner= mode= source=
while [ $# -gt 0 ]; do
  case "$1" in
    --mount) mount=true ;;
    --owner) owner=${2-}; shift ;;
    --mode) mode=${2-}; shift ;;
    --source) source=${2-}; shift ;;
    *) usage "unknown option $1" ;;
  esac
  shift
done

safe_path() {
  [[ "$1" =~ ^/[^/] ]] && [[ ! "$1" =~ (^|/)\.\.?(/|$) ]] && [[ "$1" != */ ]]
}
safe_path "$path" || usage "PATH must be absolute, normalized and not /, got $path"
[[ -z "$owner" || "$owner" =~ ^[0-9]+:[0-9]+$ ]] || usage "--owner must be numeric UID:GID"
[[ -z "$mode" || "$mode" =~ ^0?[0-7]{3,4}$ ]] || usage "--mode must be octal"
if [ "$mount" = true ]; then
  [ -n "$owner" ] || usage "--mount needs --owner"
  previous=$path/.restore-previous
else
  previous=$(dirname -- "$path")/.$(basename -- "$path").restore-previous
fi

# Run a command inside the mount as its owner; cd as root first, because the owner may not
# be able to traverse the directories above the mount.
as_owner() {
  (cd -- "$path" && setpriv --reuid "${owner%%:*}" --regid "${owner##*:}" --clear-groups -- "$@")
}

previous_exists() {
  if [ "$mount" = true ]; then as_owner test -e .restore-previous; else [ -e "$previous" ]; fi
}

# Every top-level entry of the mount except the rollback copy, NUL-separated.
mount_entries() {
  as_owner find . -mindepth 1 -maxdepth 1 ! -name .restore-previous -print0
}

case "$command" in
  check)
    [ -d "$path" ] && [ ! -L "$path" ] || { echo "$path is missing, a link or not a directory" >&2; exit 1; }
    if [ "$mount" = true ]; then
      [ "$(findmnt --noheadings --output TARGET --mountpoint "$path" 2>/dev/null)" = "$path" ] \
        || { echo "$path is not a mount point; refusing to write its contents to the disk below" >&2; exit 1; }
      as_owner test -w . || { echo "$path is not writable by $owner" >&2; exit 1; }
    fi
    if previous_exists; then
      echo "An earlier restore left its rollback copy at $previous. Put it back or remove it before restoring again." >&2
      exit 1
    fi
    ;;
  aside)
    if [ "$mount" = true ]; then
      as_owner mkdir -m 0700 .restore-previous
      mount_entries | as_owner xargs -0 --no-run-if-empty mv -t .restore-previous --
    else
      [ ! -e "$previous" ] || usage "$previous already exists"
      mv -T -- "$path" "$previous"
    fi
    echo moved
    ;;
  install)
    [ -n "$source" ] && safe_path "$source" && [ -d "$source" ] && [ ! -L "$source" ] \
      || usage "install needs --source, an existing directory"
    [ -n "$owner" ] || usage "install needs --owner"
    if [ "$mount" = true ]; then
      # The owner copies, so give the staged tree to the owner first. Copying "$source/."
      # also copies the staged directory's mode onto the mount, so without --mode the
      # mount gets its own mode back.
      chown -R --no-dereference "$owner" "$source"
      [ -n "$mode" ] || mode=$(as_owner stat -c %a .)
      as_owner cp -a --no-preserve=ownership "$source/." .
      as_owner chmod "$mode" .
    else
      [ -e "$previous" ] && [ ! -e "$path" ] || usage "install needs $path moved aside first"
      mv -T -- "$source" "$path"
      chown -R --no-dereference "$owner" "$path"
      [ -z "$mode" ] || chmod "$mode" "$path"
    fi
    echo installed
    ;;
  rollback)
    if [ "$mount" = true ]; then
      if ! previous_exists; then echo untouched; exit 0; fi
      mount_entries | as_owner xargs -0 --no-run-if-empty rm -rf --
      as_owner find .restore-previous -mindepth 1 -maxdepth 1 -print0 \
        | as_owner xargs -0 --no-run-if-empty mv -t . --
      as_owner rmdir .restore-previous
    else
      if ! previous_exists; then echo untouched; exit 0; fi
      rm -rf -- "$path"
      mv -T -- "$previous" "$path"
    fi
    echo rolled-back
    ;;
  cleanup)
    if [ "$mount" = true ]; then
      as_owner rm -rf -- .restore-previous
    else
      rm -rf -- "$previous"
    fi
    echo cleaned
    ;;
  *) usage "unknown command $command; use check, aside, install, rollback or cleanup" ;;
esac
