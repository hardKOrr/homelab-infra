#!/usr/bin/env python3
"""Native OPNsense configuration transfer. Private settings arrive only on stdin.

Uses the authenticated Backup & Restore form because the backup API cannot upload
configuration and its download serves historical files rather than current state.
Never emit the response body, credentials or configuration on failure.
"""
import http.cookiejar
import json
import os
import ssl
import sys
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path


class HiddenFields(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.fields = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input' and attrs.get('type') == 'hidden' and attrs.get('name'):
            self.fields[attrs['name']] = attrs.get('value', '')


class SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(req.full_url)[:2] != urllib.parse.urlsplit(newurl)[:2]:
            raise ValueError('cross-origin redirect refused')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def validate(data, settings):
    root = ET.fromstring(data)
    if (root.tag != 'opnsense'
            or root.findtext('system/hostname') != settings['instance']
            or root.findtext('interfaces/lan/ipaddr') != settings['address']
            or not root.findall('system/user')):
        raise ValueError('configuration identity mismatch')
    return root


def transfer(settings):
    context = ssl.create_default_context()
    if not settings.get('validate_certs', True):
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    base = 'https://' + settings['address']
    opener = urllib.request.build_opener(
        SameOriginRedirect(),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        urllib.request.HTTPSHandler(context=context))

    def request(path, data=None, content_type=None):
        req = urllib.request.Request(base + path, data=data)
        if content_type:
            req.add_header('Content-Type', content_type)
        # Do not forward a privileged session to another host after a redirect.
        with opener.open(req, timeout=90) as response:
            if urllib.parse.urlsplit(response.url).netloc != settings['address']:
                raise ValueError('unexpected redirect')
            return response.read()

    def form(path, values):
        return request(path, urllib.parse.urlencode(values).encode(),
                       'application/x-www-form-urlencoded')

    # Validate restore material before even logging in to the target.
    path = Path(settings['path'])
    payload = path.read_bytes() if settings['action'] == 'restore' else None
    if payload is not None:
        validate(payload, settings)
    login = request('/index.php').decode()
    fields = HiddenFields(login).fields
    if not fields:
        raise ValueError('login CSRF token missing')
    form('/index.php', dict(fields, usernamefld='root',
                            passwordfld=settings['password'], login='1'))
    page = request('/diag_backup.php').decode()
    if 'name="conffile"' not in page:
        raise ValueError('native backup form unavailable')
    fields = HiddenFields(page).fields
    if not fields:
        raise ValueError('backup CSRF token missing')
    if settings['action'] == 'backup':
        data = form('/diag_backup.php', dict(fields, download='1', donotbackuprrd='1'))
        validate(data, settings)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as output:
            output.write(data)
        return
    if settings['action'] != 'restore':
        raise ValueError('unsupported action')
    boundary = uuid.uuid4().hex
    parts = []
    for key, value in dict(fields, restore='1', rebootafterrestore='1').items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    parts.extend([
        f'--{boundary}\r\nContent-Disposition: form-data; name="conffile"; filename="config.xml"\r\nContent-Type: application/xml\r\n\r\n'.encode(),
        payload, f'\r\n--{boundary}--\r\n'.encode()])
    result = request('/diag_backup.php', b''.join(parts),
                     'multipart/form-data; boundary=' + boundary).decode()
    if 'The configuration has been restored.' not in result or 'Postponing reboot.' in result:
        raise ValueError('native restore did not confirm success and reboot')


if __name__ == '__main__':
    try:
        transfer(json.load(sys.stdin))
    except Exception:
        sys.exit('OPNsense native configuration transfer failed; response and secrets suppressed')
