#!/usr/bin/env bash
# Focused behavioral regression coverage for issue 11's evidence-ranked parser.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
python="${GATE_PYTHON:-$HOME/.venvs/homelab-ansible/bin/python}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

mkdir -p "$work/primary" "$work/other-week" "$work/empty"
"$python" - "$work" <<'PY'
import pathlib, sys

work = pathlib.Path(sys.argv[1])
header = (
    " X-Plex-Model=43UR8000AUA; X-Plex-Product=Plex for LG; "
    "X-Plex-Version=5.94.3; X-Plex-Platform=webOS; Referer="
)
oversize = "https://plex.example/start.m3u8?" + "x" * 4160
well_formed = "https://plex.example/start.m3u8?ok=1"
lines = []
# Correct finding deliberately has only 33 events; loud cosmetic noise must not rank.
lines.extend("Error parsing HTTP request: ET /video/x/transcode/universal/session/a/base/index.m3u8" + header + oversize + "\n" for _ in range(33))
lines.append("Error parsing HTTP request: ET /video/x/transcode/universal/session/b/base/index.m3u8" + header + well_formed + "\n")
lines.extend("UltraBlurProcessor palette failure\n" for _ in range(1208))
lines.extend("Error parsing HTTP request: ET /@fs/root/.aws/credentials; User-Agent=ClaudeBot\n" for _ in range(332))
lines.extend("Failed to transcode file (234) /media/Show-[MAX WEBDL-1080p]-AndreMor.mkv\n" for _ in range(522))
lines.append("[FFMPEG] Unknown/unsupported AVCodecID S_TEXT/WEBVTT.\n")
(work / "primary" / "Plex Media Server.log").write_text("".join(lines), encoding="utf-8")
(work / "other-week" / "Plex Media Server.log.1").write_text(
    "Error parsing HTTP request: GET /video/x/transcode/universal/session/z/base/index.m3u8" + header + oversize + "\n",
    encoding="utf-8",
)
(work / "empty" / "Plex Media Server.log").write_text("ordinary startup line\n", encoding="utf-8")
PY

signatures="$($python - "$repo/ansible/vars/plex-log-signatures.yml" <<'PY'
import json, sys, yaml
print(json.dumps(yaml.safe_load(open(sys.argv[1], encoding="utf-8"))["plex_log_signatures"]))
PY
)"
run() { "$python" "$repo/ansible/scripts/plex-log-troubleshooter.py" --bundle "$1" --signatures-json "$signatures"; }

run "$work/primary" > "$work/primary.json"
"$python" - "$work/primary.json" <<'PY'
import json, sys
findings = json.load(open(sys.argv[1], encoding="utf-8"))["findings"]
assert [item["id"] for item in findings] == [
    "oversized-hls-request", "credits-webvtt-release-group", "rejected-fs-probe"
]
top, credits, probe = findings
assert top["count"] == 33 and top["evidence"]["client"]["model"] == "43UR8000AUA"
assert top["evidence"]["client"]["product"] == "Plex for LG"
assert top["evidence"]["referer_bytes"] == [4192]  # URL prefix plus 4160-byte query payload.
assert credits["count"] == 522 and credits["evidence"]["release_groups"] == {"AndreMor": 522}
assert probe["severity"] == "info" and probe["count"] == 332
PY

run "$work/other-week" > "$work/other-week.json"
"$python" - "$work/other-week.json" <<'PY'
import json, sys
finding = json.load(open(sys.argv[1], encoding="utf-8"))["findings"][0]
assert finding["id"] == "oversized-hls-request"
assert finding["evidence"]["client"]["model"] == "43UR8000AUA"
PY

run "$work/empty" > "$work/empty.json"
"$python" - "$work/empty.json" <<'PY'
import json, sys
result = json.load(open(sys.argv[1], encoding="utf-8"))
assert result["findings"] == [] and result["summary"] == "No ranked Plex findings."
PY

echo "plex-client-troubleshooter: all cases passed."
