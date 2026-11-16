# Run inside `odoo shell` (stdin): give the admin user the password in odoo.conf's
# admin_passwd, which Deploy records as the instance's admin_password. A database
# initialized by --init keeps Odoo's default login otherwise. Prints "changed" or "kept".
import odoo

password = odoo.tools.config.get('admin_passwd') or ''
if not password or password.startswith('$pbkdf2'):
    raise SystemExit('admin_passwd in odoo.conf is not a plain password')
admin = env.ref('base.user_admin')
env.cr.execute('SELECT COALESCE(password, %s) FROM res_users WHERE id = %s', ['', admin.id])
stored = env.cr.fetchone()[0]
if stored and env['res.users']._crypt_context().verify(password, stored):
    print('kept')
else:
    admin.password = password
    env.cr.commit()
    print('changed')
