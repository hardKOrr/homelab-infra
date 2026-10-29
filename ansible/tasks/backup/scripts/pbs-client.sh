#!/bin/bash
# The one guest-side PBS client path. pbs-client-run.yml runs it for Backup App and
# Restore App, and an application's own recovery script on the guest (a cron backup, a
# restore helper) runs the copy pbs-client-script.yml installs, so both use the same
# scratch, pins and recovery-point rules.
#
# usage: pbs-client.sh [--mount=PATH:TARGET]... COMMAND ARGUMENTS
#
#   backup --id ID [--keep-last N] [--keep-daily DAYS | --no-prune] NAME:PATH... [-- ARGS]
#            Uploads each PATH as archive NAME (<name>.pxar|img|conf|log) to a new point
#            host/ID/<UTC time>, checks the point holds every archive, prunes the group
#            (keep-last at least 2) and proves the point that was newest before still
#            exists. Prints the new point last. ARGS go to `backup`. Each --mount puts a
#            host PATH read-only at TARGET, an empty directory inside an archive path.
#   list --id ID
#            Prints the group's points.
#   verify --point POINT [--pre-restore-point POINT] NAME...
#            Checks both points hold every archive, downloads POINT's index and reads its
#            catalog into the client's tmpfs, and prints the index. Changes nothing.
#   extract --point POINT [--pre-restore-point POINT] NAME:DIR... [-- ARGS]
#            Checks as verify does, then extracts each archive into DIR, an existing
#            directory. Prints `extracted` last. ARGS go to every `restore`.
#
# env: PBS_CLIENT_CONFIG      optional root-only file holding the variables below
#      PBS_REPOSITORY PBS_FINGERPRINT PBS_PASSWORD
#      PBS_CLIENT_RUNTIME     docker | native
#                               docker  a short-lived container of PBS_CLIENT_IMAGE that
#                                       installs PBS_CLIENT_VERSION from
#                                       PBS_CLIENT_APT_SOURCE, signed by PBS_CLIENT_KEY_URL;
#                                       each path is mounted at the same path inside it
#                               native  the client pbs-client-install.yml installed
#      PBS_CLIENT_TMPFS_SIZE
#
# The client creates unnamed temporary files in /tmp and keeps its cache under $HOME. A
# Docker overlay, NFS and some LXC root filesystems refuse those files with `Operation
# not supported (os error 95)`. Both runtimes therefore put /tmp on a tmpfs of
# PBS_CLIENT_TMPFS_SIZE and point $HOME, XDG_CACHE_HOME and XDG_RUNTIME_DIR into it.
# Docker mounts that tmpfs for the container's life. Natively the tmpfs is mounted in a
# private mount namespace, so it disappears with the client process even when the run
# fails, and the host's own /tmp is never touched. The same reason keeps every path out
# of /tmp. A --mount is a bind mount in that same private namespace natively, and a
# container mount under Docker; neither reaches the host.
#
# Exit status: 64 for a bad request, before the client runs; 3 when a point lacks an
# archive; 4 when pruning removed the previous point; 5 when the native client is absent.
set -eu

script=$(readlink -f "$0")
request=("$@")
if [ -z "${PBS_CLIENT_STAGE:-}" ] && [ -n "${PBS_CLIENT_CONFIG:-}" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$PBS_CLIENT_CONFIG"
  set +a
  unset PBS_CLIENT_CONFIG
fi

usage() {
  echo "pbs-client.sh: $*" >&2
  exit 64
}

archive_name_ok() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*\.(pxar|img|conf|log)$ ]]
}

path_ok() {
  [[ "$1" == /* && "$1" != / && "$1" != /tmp && "$1" != /tmp/* ]]
}

point_ok() {
  [[ "$1" =~ ^host/[A-Za-z0-9][A-Za-z0-9_.-]*/[^/]+$ ]]
}

mounts=()
while [[ "${1:-}" == --mount=* ]]; do
  mounts+=("${1#--mount=}")
  shift
done
command=${1:-}
[ $# -gt 0 ] && shift

id=
keep_last=2
keep_daily=
prune=1
point=
pre_point=
specs=()
extra=()
while [ $# -gt 0 ]; do
  case "$1" in
    --id) id=${2:-}; shift 2 ;;
    --keep-last) keep_last=${2:-}; shift 2 ;;
    --keep-daily) keep_daily=${2:-}; shift 2 ;;
    --no-prune) prune=0; shift ;;
    --point) point=${2:-}; shift 2 ;;
    --pre-restore-point) pre_point=${2:-}; shift 2 ;;
    --) shift; extra=("$@"); break ;;
    -*) usage "unknown option $1" ;;
    *) specs+=("$1"); shift ;;
  esac
done

# Validate the whole request before any client or container runs.
case "$command" in
  backup | list)
    [[ "$id" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || usage "$command needs a safe --id"
    ;;
  verify | extract)
    point_ok "$point" || usage "$command needs --point host/<id>/<time>"
    [ -z "$pre_point" ] || point_ok "$pre_point" || usage "the pre-restore point must be host/<id>/<time>"
    ;;
  *) usage "unknown command '${command}'; use backup, list, verify or extract" ;;
esac
case "$command" in
  backup)
    [[ "$keep_last" =~ ^[0-9]+$ ]] && [ "$keep_last" -ge 2 ] \
      || usage "keep-last must be at least 2 so the previous point survives a same-day backup"
    [[ "$keep_daily" =~ ^[0-9]*$ ]] || usage "keep-daily must be a number of days"
    ;;
  list) [ ${#specs[@]} -eq 0 ] || usage "list takes no archives" ;;
esac
[ "$command" = list ] || [ ${#specs[@]} -gt 0 ] || usage "$command needs at least one archive"
names=()
paths=()
for spec in "${specs[@]}"; do
  if [ "$command" = verify ]; then
    name=$spec path=
  else
    name=${spec%%:*} path=${spec#*:}
    [ "$name" != "$spec" ] || usage "$command needs NAME:PATH, got $spec"
    path_ok "$path" || usage "$name needs an absolute path outside /tmp, got $path"
  fi
  archive_name_ok "$name" || usage "archive name $name must be <name>.pxar|img|conf|log"
  for seen in "${names[@]}"; do
    [ "$seen" != "$name" ] || usage "archive $name is named twice"
  done
  names+=("$name")
  paths+=("$path")
done
for mount in "${mounts[@]}"; do
  source=${mount%%:*} target=${mount#*:}
  [ "$command" = backup ] || usage "--mount applies to backup only"
  path_ok "$source" && path_ok "$target" || usage "--mount needs absolute paths outside /tmp, got $mount"
  inside=0
  for path in "${paths[@]}"; do
    [[ "$target" == "${path%/}"/* ]] && inside=1
  done
  [ "$inside" = 1 ] || usage "--mount target $target is not inside an archive path"
done
for name in PBS_REPOSITORY PBS_FINGERPRINT PBS_PASSWORD PBS_CLIENT_RUNTIME PBS_CLIENT_TMPFS_SIZE; do
  [ -n "${!name:-}" ] || usage "$name is not set"
done


# First stage: start the client stage in its runtime with the same request.
if [ -z "${PBS_CLIENT_STAGE:-}" ]; then
  export PBS_CLIENT_STAGE=client
  case "$PBS_CLIENT_RUNTIME" in
    native)
      exec unshare --mount --propagation private -- /bin/bash "$script" "${request[@]}"
      ;;
    docker)
      for name in PBS_CLIENT_IMAGE PBS_CLIENT_VERSION PBS_CLIENT_KEY_URL PBS_CLIENT_APT_SOURCE; do
        [ -n "${!name:-}" ] || usage "$name is not set"
      done
      mode=ro
      [ "$command" = extract ] && mode=rw
      docker=(docker run --rm --network host
        --tmpfs "/tmp:rw,nosuid,nodev,mode=1777,size=$PBS_CLIENT_TMPFS_SIZE"
        "--volume=$script:$script:ro")
      for path in "${paths[@]}"; do
        [ -z "$path" ] || docker+=("--volume=$path:$path:$mode")
      done
      for mount in "${mounts[@]}"; do
        docker+=("--volume=${mount%%:*}:${mount#*:}:ro")
      done
      for name in PBS_REPOSITORY PBS_FINGERPRINT PBS_PASSWORD PBS_CLIENT_STAGE PBS_CLIENT_RUNTIME \
        PBS_CLIENT_TMPFS_SIZE PBS_CLIENT_VERSION PBS_CLIENT_KEY_URL PBS_CLIENT_APT_SOURCE; do
        docker+=("--env=$name")
      done
      exec "${docker[@]}" "$PBS_CLIENT_IMAGE" /bin/bash "$script" "${request[@]}"
      ;;
    *) usage "PBS_CLIENT_RUNTIME must be docker or native" ;;
  esac
fi

# Client stage: inside the container or the private mount namespace.
if [ "$PBS_CLIENT_RUNTIME" = native ]; then
  command -v proxmox-backup-client >/dev/null || {
    echo "proxmox-backup-client is not installed here; deploy the application with its current role, which installs the pinned client" >&2
    exit 5
  }
  mount -t tmpfs -o "rw,nosuid,nodev,mode=1777,size=$PBS_CLIENT_TMPFS_SIZE" pbs-client-tmp /tmp
  for mount in "${mounts[@]}"; do
    mount --bind "${mount%%:*}" "${mount#*:}"
    mount -o remount,bind,ro "${mount#*:}"
  done
fi
export HOME=/tmp/.pbs-client XDG_CACHE_HOME=/tmp/.pbs-client/cache XDG_RUNTIME_DIR=/tmp/.pbs-client/run
mkdir -p -m 0700 "$HOME" "$XDG_CACHE_HOME" "$XDG_RUNTIME_DIR"
if [ "$PBS_CLIENT_RUNTIME" = docker ]; then
{
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq --no-install-recommends ca-certificates
  install -d -m 0755 /etc/apt/keyrings
  curl -fsSL "$PBS_CLIENT_KEY_URL" -o /etc/apt/keyrings/proxmox-release.gpg
  echo "deb [signed-by=/etc/apt/keyrings/proxmox-release.gpg] $PBS_CLIENT_APT_SOURCE" \
    > /etc/apt/sources.list.d/proxmox-backup-client.list
  apt-get update -qq
  apt-get install -y -qq --no-install-recommends "proxmox-backup-client=$PBS_CLIENT_VERSION"
} >&2
fi

files=
require_archives() {
  files=$(proxmox-backup-client snapshot files "$1" --output-format json)
  for archive in "${names[@]}"; do
    case "$files" in
      *"\"$archive."*) ;;
      *) echo "recovery point $1 lacks $archive" >&2; return 3 ;;
    esac
  done
}

case "$command" in
  backup)
    # The point is named by the UTC time chosen here and passed as --backup-time, so it is
    # known without parsing client output.
    group="host/$id"
    point_of() { echo "$group/$(date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ)"; }
    previous=$(proxmox-backup-client snapshot list "$group" --output-format json \
      | grep -oE '"backup-time" *: *[0-9]+' | grep -oE '[0-9]+$' | sort -n | tail -n 1)
    now=$(date +%s)
    if [ -n "$previous" ] && [ "$now" -le "$previous" ]; then now=$((previous + 1)); fi
    point=$(point_of "$now")
    uploads=()
    for i in "${!names[@]}"; do
      uploads+=("${names[$i]}:${paths[$i]}")
    done
    proxmox-backup-client backup "${uploads[@]}" ${extra[@]+"${extra[@]}"} \
      --backup-id "$id" --backup-time "$now" >&2
    require_archives "$point"
    if [ "$prune" = 1 ]; then
      if [ -n "$keep_daily" ]; then
        proxmox-backup-client prune "$group" --keep-last "$keep_last" --keep-daily "$keep_daily" >&2
      else
        proxmox-backup-client prune "$group" --keep-last "$keep_last" >&2
      fi
      require_archives "$point"
    fi
    if [ -n "$previous" ]; then
      proxmox-backup-client snapshot files "$(point_of "$previous")" >/dev/null \
        || { echo "pruning removed the previous point $(point_of "$previous")" >&2; exit 4; }
    fi
    echo "$point"
    ;;
  list)
    proxmox-backup-client snapshot list "host/$id" --output-format text
    ;;
  verify | extract)
    require_archives "$point"
    point_files=$files
    if [ -n "$pre_point" ]; then require_archives "$pre_point"; fi
    if [ "$command" = verify ]; then
      proxmox-backup-client restore "$point" index.json "$HOME/index.json" >&2
      case "$point_files" in
        *'"catalog.pcat1.'*) proxmox-backup-client catalog dump "$point" >/dev/null ;;
      esac
      cat "$HOME/index.json"
      exit 0
    fi
    for i in "${!names[@]}"; do
      [ -d "${paths[$i]}" ] && [ ! -L "${paths[$i]}" ] \
        || { echo "staging path ${paths[$i]} for ${names[$i]} is not a directory" >&2; exit 64; }
    done
    for i in "${!names[@]}"; do
      proxmox-backup-client restore "$point" "${names[$i]}" "${paths[$i]}" ${extra[@]+"${extra[@]}"} >&2
    done
    echo extracted
    ;;
esac
