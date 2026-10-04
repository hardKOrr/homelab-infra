# Gate fixtures

Synthetic, tracked configuration used by the offline gates. Nothing
under this directory is real lab configuration and nothing here may contain a
credential, token, private key, password, or live endpoint — every host, IP, and value
below is a documentation-style placeholder, exactly like `config.example/`.

`config.example/` remains the place a user copies from. This directory exists so the
gate can exercise `ansible/scripts/config-doctor.sh` and the `homelabinfra_config`
merge contract (`ansible/vars/CONTRACT.md`) against known-good and known-bad input,
without ever touching a real `config/` tree.

`localhost.ini` is the parsed local inventory used by lint and syntax gates. A
list-valued `ANSIBLE_INVENTORY=localhost,` setting loses the comma and names a missing
file; the fixture keeps checks compatible with fatal inventory parsing errors.

- `config/valid/` — passes `config-doctor.sh` cleanly; conforms to the CONTRACT.md
  schema.
- `config/invalid/` — deliberately incomplete, to assert the exact failure messages
  `config-doctor.sh` reports for missing/malformed keys.
