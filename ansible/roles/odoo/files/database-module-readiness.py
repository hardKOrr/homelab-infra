import configparser
import sys
import psycopg2
config = configparser.ConfigParser(interpolation=None)
config.read('/etc/odoo/odoo.conf')
options = config['options']
with psycopg2.connect(host=options['db_host'], port=options['db_port'],
                      user=options['db_user'], password=options['db_password'],
                      dbname=sys.argv[1]) as connection:
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('public.ir_module_module')")
        ready = False
        if cursor.fetchone()[0] is not None:
            modules = sys.argv[2].split(',')
            cursor.execute("SELECT name FROM ir_module_module WHERE name = ANY(%s) AND state = 'installed'", (modules,))
            ready = set(modules) <= {row[0] for row in cursor.fetchall()}
        print('ready' if ready else 'initialize')
