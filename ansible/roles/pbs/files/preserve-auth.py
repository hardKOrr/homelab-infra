#!/usr/bin/env python3
"""Carry the target's managed PBS authentication into a staged restored configuration.

usage: preserve-auth.py LIVE_ETC STAGED_ETC TOKEN_ID

A restored recovery point holds an older token hash and TLS keypair than the ones Deploy
published to Vaultwarden and PVE. Copy the live certificate and key, the token's secret hash
and the token's and root@pam's user.cfg stanzas over the staged ones, so those clients keep
working after the swap.
"""
import json
import os
import shutil
import sys

live, staged, token_id = sys.argv[1:4]


def stanzas(path):
    if not os.path.isfile(path):
        return []
    text = open(path).read().strip("\n")
    return [b for b in text.split("\n\n") if b.strip()] if text else []


def key(block):
    return block.split("\n", 1)[0].strip()


for name in ("proxy.pem", "proxy.key"):
    if os.path.isfile(os.path.join(live, name)):
        shutil.copy2(os.path.join(live, name), os.path.join(staged, name))

live_shadow = os.path.join(live, "token.shadow")
if os.path.isfile(live_shadow):
    secrets = json.load(open(live_shadow))
    staged_shadow = os.path.join(staged, "token.shadow")
    merged = json.load(open(staged_shadow)) if os.path.isfile(staged_shadow) else {}
    if token_id in secrets:
        merged[token_id] = secrets[token_id]
    with open(staged_shadow, "w") as handle:
        json.dump(merged, handle)
    os.chmod(staged_shadow, 0o600)

wanted = {"user: root@pam", "token: " + token_id}
kept = [b for b in stanzas(os.path.join(staged, "user.cfg")) if key(b) not in wanted]
kept += [b for b in stanzas(os.path.join(live, "user.cfg")) if key(b) in wanted]
with open(os.path.join(staged, "user.cfg"), "w") as handle:
    handle.write("\n\n".join(kept) + "\n")
