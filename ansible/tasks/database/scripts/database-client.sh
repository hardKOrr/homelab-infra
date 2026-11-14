#!/bin/sh
# Dump, verify, restore or inspect one application's named database as its own login.
#
# Runs inside the official client image for the server's major (resolve-app-database.yml
# picks it). tasks/database/dump.yml and restore.yml run it with Docker on the application
# host; a Kubernetes recovery Job runs the same file in the same image.
#
# usage: database-client.sh dump|verify|restore FILE
#        database-client.sh inspect
# env:   DB_ENGINE (postgresql|mariadb|mysql|redis) DB_HOST DB_PORT DB_NAME DB_USER DB_PASSWORD
#        (verify needs only DB_ENGINE)
#
# dump     PostgreSQL: custom format without owners or grants.
#          MariaDB/MySQL: one consistent SQL transaction, no CREATE DATABASE.
# verify   Checks FILE is a loadable dump without connecting, before anything is stopped.
# restore  Replaces the database's contents with FILE.
#          PostgreSQL: in ONE transaction, drops everything the login owns and loads FILE,
#          skipping extensions (provision.yml creates those as the superuser). A failed
#          load leaves the previous contents.
#          MariaDB/MySQL: recreates the database with its previous character set and
#          collation, then loads FILE without any `USE` or `CREATE DATABASE` it carries.
# inspect  Read only: one line counting the tables and rows a restore would replace.
#
# Whole MariaDB, MySQL or PostgreSQL server (a Kubernetes backend's own recovery point;
# DB_USER is root or postgres, which connects from inside the cluster only, and DB_NAME is
# not used):
#   server-dump FILE | server-verify FILE | server-load FILE | server-inspect
#   server-dump    every schema and the grant tables at one point (global read lock).
#   server-load    drops every application schema, loads FILE, then sets the root
#                  password back to DB_PASSWORD: the point carries the SOURCE server's
#                  root hash, and the target's own credential must keep working.
#                  DB_ROOT_HOSTS lists the root account hosts (default: localhost).
#   PostgreSQL: one pg_dumpall script; server-load drops every database and role but the
#                  superuser, loads FILE and keeps this server's own postgres password.
#
# Whole Redis server (DB_ENGINE=redis, DB_USER the ACL login, FILE named *.rdb): the point is
# the RDB beside its ACL users, `<name>.acl`, taken and loaded over the network.
#   server-dump    redis-cli --rdb (a consistent snapshot through replication) and ACL LIST.
#   server-load    makes the running server a replica of a throwaway Redis that holds FILE,
#                  waits for the full sync, promotes it again and saves. The point's `default`
#                  user is skipped: this server keeps its own login. DB_HOST is the server.
set -eu

action=${1:-}
file=${2:-}
case "$action" in
  dump | restore | inspect) required="DB_ENGINE DB_HOST DB_PORT DB_NAME DB_USER DB_PASSWORD" ;;
  verify) required=DB_ENGINE ;;
  server-dump | server-load | server-inspect) required="DB_ENGINE DB_HOST DB_PORT DB_USER DB_PASSWORD" ;;
  server-verify) required=DB_ENGINE ;;
  *)
    echo "usage: database-client.sh dump|verify|restore FILE | inspect | server-dump|server-verify|server-load FILE | server-inspect" >&2
    exit 64
    ;;
esac
[ -n "$file" ] || [ "$action" = inspect ] || [ "$action" = server-inspect ] || { echo "database-client.sh: FILE is required" >&2; exit 64; }
for name in $required; do
  eval "value=\${$name:-}"
  [ -n "$value" ] || { echo "database-client.sh: $name is required" >&2; exit 64; }
done
case "${DB_NAME:-verify}" in
  *[!A-Za-z0-9_]* | [0-9]*) echo "database-client.sh: unsafe DB_NAME" >&2; exit 64 ;;
esac

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

postgresql() {
  if [ "$action" = verify ]; then
    pg_restore --list "$file" > /dev/null
    return
  fi
  export PGHOST="$DB_HOST" PGPORT="$DB_PORT" PGUSER="$DB_USER" PGDATABASE="$DB_NAME"
  export PGPASSWORD="$DB_PASSWORD"
  if [ "$action" = inspect ]; then
    psql --no-psqlrc --tuples-only --no-align --command="SELECT count(*) || ' tables, '
      || coalesce(sum(n_live_tup), 0) || ' rows (estimated)' FROM pg_stat_user_tables"
    return
  fi
  if [ "$action" = dump ]; then
    pg_dump --format=custom --no-owner --no-acl --file="$work/dump"
    test -s "$work/dump"
    mv "$work/dump" "$file"
    return
  fi
  test -s "$file"
  # DROP OWNED below also drops extensions the application created as its own login (Immich
  # creates unaccent, pg_trgm and uuid-ossp), so the dump's CREATE EXTENSION IF NOT EXISTS
  # entries stay: they skip the extensions provision.yml created and recreate the login's
  # own. Only an extension's owner may comment on it, so those entries go.
  pg_restore --list "$file" \
    | grep -v -E '^[0-9]+; [0-9]+ [0-9]+ COMMENT - EXTENSION ' > "$work/list"
  # Render first, then load: a pipe would let psql commit a truncated script.
  {
    echo 'DROP OWNED BY CURRENT_USER;'
    pg_restore --no-owner --no-acl --use-list="$work/list" --file=- "$file"
  } > "$work/restore.sql"
  psql --no-psqlrc --quiet --set=ON_ERROR_STOP=1 --single-transaction --file="$work/restore.sql"
}

mysql_compatible() {
  if [ "$action" = verify ]; then
    grep -q '^CREATE TABLE ' "$file"
    return
  fi
  if [ "$DB_ENGINE" = mariadb ]; then
    client=mariadb dump=mariadb-dump extra="--routines --events"
  else
    client=mysql dump=mysqldump extra=
    # Oracle's mysqldump takes this; the MariaDB-provided one a native guest installs
    # (Debian ships no Oracle client) does not.
    if "$dump" --help 2>/dev/null | grep -q -- --set-gtid-purged; then extra=--set-gtid-purged=OFF; fi
  fi
  umask 077
  printf '[client]\nuser="%s"\npassword="%s"\nhost="%s"\nport=%s\n' \
    "$DB_USER" "$DB_PASSWORD" "$DB_HOST" "$DB_PORT" > "$work/client.cnf"
  if [ "$action" = inspect ]; then
    "$client" --defaults-extra-file="$work/client.cnf" --batch --skip-column-names \
      --execute="SELECT CONCAT(COUNT(*), ' tables, ', COALESCE(SUM(TABLE_ROWS), 0),
        ' rows (estimated)') FROM information_schema.TABLES WHERE TABLE_SCHEMA = '$DB_NAME'"
    return
  fi
  if [ "$action" = dump ]; then
    # shellcheck disable=SC2086 # $extra holds engine-specific flags
    "$dump" --defaults-extra-file="$work/client.cnf" --single-transaction --quick \
      --hex-blob --triggers --no-tablespaces $extra "$DB_NAME" > "$work/dump"
    grep -q '^CREATE TABLE ' "$work/dump"
    mv "$work/dump" "$file"
    return
  fi
  test -s "$file"
  charset=$("$client" --defaults-extra-file="$work/client.cnf" --batch --skip-column-names \
    --execute="SELECT CONCAT(DEFAULT_CHARACTER_SET_NAME, ' COLLATE ', DEFAULT_COLLATION_NAME)
      FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = '$DB_NAME'")
  [ -n "$charset" ] || { echo "database $DB_NAME does not exist; provision it first" >&2; exit 65; }
  "$client" --defaults-extra-file="$work/client.cnf" --execute="
    DROP DATABASE \`$DB_NAME\`;
    CREATE DATABASE \`$DB_NAME\` CHARACTER SET $charset;"
  # A dump taken with --databases names its source; drop those lines so a restore into a
  # new destination can never switch to, or create, the source database.
  sed -e '/^USE `[^`]*`;$/d' -e '/^CREATE DATABASE /d' "$file" > "$work/load.sql"
  "$client" --defaults-extra-file="$work/client.cnf" --database="$DB_NAME" < "$work/load.sql"
}

# One MariaDB or MySQL server as a unit: all schemas, routines, events, triggers and grants.
sql_server() {
  [ "$DB_ENGINE" != postgresql ] || { echo "database-client.sh: $action needs DB_ENGINE=mariadb or mysql" >&2; exit 64; }
  if [ "$action" = server-verify ]; then
    grep -q '^CREATE TABLE ' "$file"
    return
  fi
  if [ "$DB_ENGINE" = mariadb ]; then
    client=mariadb dump=mariadb-dump extra=
  else
    client=mysql dump=mysqldump extra=--set-gtid-purged=OFF
  fi
  umask 077
  printf '[client]\nuser="%s"\npassword="%s"\nhost="%s"\nport=%s\n' \
    "$DB_USER" "$DB_PASSWORD" "$DB_HOST" "$DB_PORT" > "$work/client.cnf"
  system="'information_schema', 'performance_schema', 'mysql', 'sys'"
  if [ "$action" = server-inspect ]; then
    # Reloading grant tables can briefly withdraw the server's Service endpoints while
    # its readiness probe catches up. Reconnect for this read only; never replay a load.
    attempt=0
    until "$client" --defaults-extra-file="$work/client.cnf" --connect-timeout=2 --batch --skip-column-names \
      --execute="SELECT CONCAT(COUNT(DISTINCT TABLE_SCHEMA), ' schemas, ', COUNT(*),
        ' tables') FROM information_schema.TABLES WHERE TABLE_SCHEMA NOT IN ($system)" \
      2> "$work/inspect.err"; do
      attempt=$((attempt + 1))
      if [ "$attempt" -ge 30 ]; then
        cat "$work/inspect.err" >&2
        return 1
      fi
      sleep 2
    done
    return
  fi
  if [ "$action" = server-dump ]; then
    # shellcheck disable=SC2086 # $extra holds engine-specific flags
    "$dump" --defaults-extra-file="$work/client.cnf" --all-databases --lock-all-tables \
      --routines --events --triggers --hex-blob --flush-privileges --skip-dump-date $extra \
      > "$work/dump"
    grep -q '^CREATE TABLE ' "$work/dump"
    mv "$work/dump" "$file"
    return
  fi
  test -s "$file"
  # Drop first, so tables the point lacks do not survive. Names come from the server.
  "$client" --defaults-extra-file="$work/client.cnf" --batch --skip-column-names \
    --execute="SELECT CONCAT('DROP DATABASE IF EXISTS \`', REPLACE(SCHEMA_NAME, '\`', '\`\`'), '\`;')
      FROM information_schema.SCHEMATA WHERE SCHEMA_NAME NOT IN ($system)" > "$work/drop.sql"
  "$client" --defaults-extra-file="$work/client.cnf" < "$work/drop.sql"
  # The same session loads the point and then re-keys root, so the credential this run
  # used keeps working after the point's own grant tables take effect.
  case "$DB_PASSWORD" in
    *[!A-Za-z0-9_.-]*) echo "database-client.sh: the root password must use letters, digits, '.', '_' and '-'" >&2; exit 64 ;;
  esac
  {
    cat "$file"
    echo 'FLUSH PRIVILEGES;'
    for host in localhost ${DB_ROOT_HOSTS:-}; do
      echo "ALTER USER IF EXISTS 'root'@'$host' IDENTIFIED BY '$DB_PASSWORD';"
    done
    echo 'FLUSH PRIVILEGES;'
  } > "$work/load.sql"
  "$client" --defaults-extra-file="$work/client.cnf" < "$work/load.sql"
}

# One Redis server as a unit: the data set as an RDB, its ACL users beside it.
redis_server() {
  acl="${file%.rdb}.acl"
  if [ "$action" = server-verify ]; then
    redis-check-rdb "$file" > /dev/null
    [ -s "$acl" ]
    return
  fi
  export REDISCLI_AUTH="$DB_PASSWORD"
  # -e: exit non-zero on an error reply, which redis-cli otherwise prints and ignores.
  cli() { redis-cli -e --no-auth-warning --user "$DB_USER" -h "$DB_HOST" -p "$DB_PORT" "$@"; }
  umask 077
  if [ "$action" = server-inspect ]; then
    attempt=0
    until out=$(cli DBSIZE 2> "$work/inspect.err") && cli ACL USERS > "$work/users" 2>> "$work/inspect.err"; do
      attempt=$((attempt + 1))
      if [ "$attempt" -ge 30 ]; then
        cat "$work/inspect.err" >&2
        return 1
      fi
      sleep 2
    done
    case "$out" in '' | *[!0-9]*) echo "database-client.sh: unexpected DBSIZE reply: $out" >&2; return 1 ;; esac
    echo "$out keys, $(wc -l < "$work/users") ACL users"
    return
  fi
  if [ "$action" = server-dump ]; then
    cli --rdb "$work/dump.rdb" > /dev/null
    redis-check-rdb "$work/dump.rdb" > /dev/null
    cli ACL LIST > "$work/dump.acl"
    test -s "$work/dump.acl"
    mv "$work/dump.acl" "$acl"
    mv "$work/dump.rdb" "$file"
    return
  fi
  test -s "$file"
  # A throwaway master holds the point; the target replicates it, which replaces its whole
  # data set. The Job pod's address is reachable from the target over the pod network.
  cp "$file" "$work/point.rdb"
  redis-server --port 6390 --bind 0.0.0.0 --protected-mode no --dir "$work" \
    --dbfilename point.rdb --save "" --appendonly no > "$work/master.log" 2>&1 &
  master=$!
  # Whatever ends the load, a target still replicating from the throwaway master is
  # promoted again first: it would otherwise stay read-only, chasing a dead master.
  replicating=0
  redis_cleanup() {
    if [ "$replicating" = 1 ]; then cli REPLICAOF NO ONE > /dev/null 2>&1 || true; fi
    kill "$master" 2> /dev/null || true
    rm -rf "$work"
  }
  trap redis_cleanup EXIT
  trap 'exit 143' INT TERM
  attempt=0
  until [ "$(redis-cli -p 6390 ping 2> /dev/null)" = PONG ]; do
    attempt=$((attempt + 1))
    [ "$attempt" -lt 60 ] || { cat "$work/master.log" >&2; echo "database-client.sh: the throwaway Redis did not start" >&2; exit 1; }
    sleep 1
  done
  replicating=1
  cli REPLICAOF "$(hostname -i | cut -d' ' -f1)" 6390 > /dev/null
  attempt=0
  until cli INFO replication > "$work/info" && grep -q '^master_link_status:up' "$work/info"; do
    attempt=$((attempt + 1))
    [ "$attempt" -lt "${SYNC_WAIT:-120}" ] || { echo "database-client.sh: the full sync did not finish" >&2; exit 1; }
    sleep 1
  done
  cli REPLICAOF NO ONE > /dev/null
  replicating=0
  # Users: the point's, but never its `default` (this server's login stays as deployed).
  # Each list lands in a file first, so a failed command stops the script (a pipe would not).
  cli ACL LIST > "$work/current.acl"
  grep '^user ' "$work/current.acl" | grep -v '^user default ' > "$work/current.users" || true
  while read -r _ name _; do
    cli ACL DELUSER "$name" > /dev/null
  done < "$work/current.users"
  grep '^user ' "$acl" | grep -v '^user default ' > "$work/point.users" || true
  set -f
  while read -r _ name rules; do
    # shellcheck disable=SC2086 # the rules are separate ACL SETUSER arguments
    cli ACL SETUSER "$name" $rules > /dev/null
  done < "$work/point.users"
  set +f
  cli ACL SAVE > /dev/null
  cli SAVE > /dev/null
}

# One PostgreSQL server as a unit: every database, role and grant, as one pg_dumpall script.
# DB_USER is the bootstrap superuser, which connects from the pod network only.
postgresql_server() {
  if [ "$action" = server-verify ]; then
    # pg_dumpall ends a complete script with this line; a cut-off dump lacks it.
    tail -n 5 "$file" | grep -q 'PostgreSQL database cluster dump complete'
    return
  fi
  export PGHOST="$DB_HOST" PGPORT="$DB_PORT" PGUSER="$DB_USER" PGDATABASE=postgres PGPASSWORD="$DB_PASSWORD"
  umask 077
  if [ "$action" = server-inspect ]; then
    # A load drops the connections of the server's readiness probe and its consumers.
    # Reconnect for this read only; never replay a load.
    attempt=0
    until PGCONNECT_TIMEOUT=2 psql --no-psqlrc --tuples-only --no-align \
      --command="SELECT (SELECT count(*) FROM pg_database WHERE NOT datistemplate AND datname <> 'postgres')
        || ' application databases, ' || (SELECT count(*) FROM pg_roles WHERE rolname !~ '^pg_' AND rolname <> 'postgres')
        || ' roles'" 2> "$work/inspect.err"; do
      attempt=$((attempt + 1))
      if [ "$attempt" -ge 30 ]; then
        cat "$work/inspect.err" >&2
        return 1
      fi
      sleep 2
    done
    return
  fi
  if [ "$action" = server-dump ]; then
    pg_dumpall --no-sync > "$work/dump"
    tail -n 5 "$work/dump" | grep -q 'PostgreSQL database cluster dump complete'
    mv "$work/dump" "$file"
    return
  fi
  test -s "$file"
  # Start from an empty cluster, so what the point lacks does not survive: every database
  # but the templates is dropped (with its sessions) and `postgres` is created again, then
  # every role but the bootstrap superuser. Names come from the server.
  {
    echo '\set ON_ERROR_STOP on'
    echo '\connect template1'
    psql --no-psqlrc --tuples-only --no-align --command="SELECT format('DROP DATABASE IF EXISTS %I WITH (FORCE);', datname)
      FROM pg_database WHERE NOT datistemplate"
    echo 'CREATE DATABASE postgres;'
    psql --no-psqlrc --tuples-only --no-align --command="SELECT format('DROP ROLE IF EXISTS %I;', rolname)
      FROM pg_roles WHERE rolname !~ '^pg_' AND rolname <> 'postgres'"
    # The point carries the SOURCE server's postgres role and password hash: keep this
    # server's own, which the next connection of this script authenticates with.
    sed -e '/^CREATE ROLE postgres;$/d' -e "/^ALTER ROLE postgres WITH /s/ PASSWORD '[^']*'//" "$file"
  } > "$work/load.sql"
  psql --no-psqlrc --quiet --dbname=template1 --file="$work/load.sql"
}

case "$DB_ENGINE" in
  postgresql | mariadb | mysql | redis) ;;
  *) echo "database-client.sh: unsupported DB_ENGINE $DB_ENGINE" >&2; exit 64 ;;
esac
case "$action" in
  server-*) case "$DB_ENGINE" in postgresql) postgresql_server ;; redis) redis_server ;; *) sql_server ;; esac
            [ "$action" = server-inspect ] && exit 0
            echo "database-client.sh: $action $DB_ENGINE server complete ($(wc -c < "$file") bytes)"
            exit 0 ;;
esac

case "$DB_ENGINE" in
  redis) echo "database-client.sh: $action needs a server action for redis" >&2; exit 64 ;;
  postgresql) postgresql ;;
  mariadb | mysql) mysql_compatible ;;
  *) echo "database-client.sh: unsupported DB_ENGINE $DB_ENGINE" >&2; exit 64 ;;
esac
[ "$action" = inspect ] && exit 0
echo "database-client.sh: $action $DB_ENGINE database ${DB_NAME:-dump} complete ($(wc -c < "$file") bytes)"
