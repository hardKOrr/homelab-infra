# Gate fixtures

Synthetic, tracked configuration used only by `gate/test-config-fixtures.sh`. Nothing
under this directory is real lab configuration and nothing here may contain a
credential, token, private key, password, or live endpoint — every host, IP, and value
below is a documentation-style placeholder, exactly like `config.example/`.

`config.example/` remains the place a user copies from. This directory exists so the
gate can exercise `ansible/scripts/config-doctor.sh` and the `homelabinfra_config`
merge contract (`ansible/vars/CONTRACT.md`) against known-good and known-bad input,
without ever touching a real `config/` tree.

- `config/valid/` — passes `config-doctor.sh` cleanly; conforms to the CONTRACT.md
  schema.
- `config/invalid/` — deliberately incomplete, to assert the exact failure messages
  `config-doctor.sh` reports for missing/malformed keys.
