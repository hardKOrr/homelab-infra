#!/bin/sh
# Dump, verify, restore or inspect one application's named database as its own login.
#
# Runs inside the official client image for the server's major (resolve-app-database.yml
# picks it). tasks/database/dump.yml and restore.yml run it with Docker on the application
# host; a Kubernetes recovery Job runs the same file in the same image.
#
# usage: database-client.sh dump|verify|restore FILE
#        database-client.sh inspect
# env:   DB_ENGINE (postgresql|mariadb|mysql) DB_HOST DB_PORT DB_NAME DB_USER DB_PASSWORD
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
set -eu

action=${1:-}
file=${2:-}
case "$action" in
  dump | restore | inspect) required="DB_ENGINE DB_HOST DB_PORT DB_NAME DB_USER DB_PASSWORD" ;;
  verify) required=DB_ENGINE ;;
  *)
    echo "usage: database-client.sh dump|verify|restore FILE | inspect" >&2
    exit 64
    ;;
esac
[ -n "$file" ] || [ "$action" = inspect ] || { echo "database-client.sh: FILE is required" >&2; exit 64; }
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
    client=mysql dump=mysqldump extra=--set-gtid-purged=OFF
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

case "$DB_ENGINE" in
  postgresql) postgresql ;;
  mariadb | mysql) mysql_compatible ;;
  *) echo "database-client.sh: unsupported DB_ENGINE $DB_ENGINE" >&2; exit 64 ;;
esac
[ "$action" = inspect ] && exit 0
echo "database-client.sh: $action $DB_ENGINE database ${DB_NAME:-dump} complete ($(wc -c < "$file") bytes)"
