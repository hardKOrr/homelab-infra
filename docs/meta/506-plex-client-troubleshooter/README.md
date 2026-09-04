# 506 — Plex client troubleshooter

**Status:** open
**Subject:** Media
**Related:** 504 (wire-media-stack, the first stack playbook this clones its shape from),
505 (app-servarr, which owns the Sonarr side of finding 3's release-group correlation)

## Goal

Turn "playback is broken again" into a named client and a named cause, from the log bundle
alone, without a browser and without watching a stream fail.

A Plex log bundle answers this question already — but only after `grep`, and only if you
know that the interesting evidence is *not* where the volume is. The bundle that motivated
this slice (`2026-08-20_23-24-03`, PMS v1.43.3.10896, i7-8700, Ubuntu 22.04) carries **5189
ERROR and 2649 WARN lines, and essentially none of the loud ones are the playback fault.**
Ranked by count the top three errors are `UltraBlurProcessor` palette failures (2101, a
cosmetic artwork-blur job) and `CreditsDetectionManager` mismatches (296). The actual
playback fault appears **33 times** in the current log and is invisible to a severity sort.

So the deliverable is a triage tool with an opinion about rank, not a log summarizer. Three
findings it must produce from this bundle, because all three are in it and none survives a
naive `grep -c ERROR`:

**1 — One client, not the server.** All 82 `Error parsing HTTP request: ET
/video/:/transcode/universal/session/<sid>/base/index.m3u8` lines across every retained log
come from a single device: `X-Plex-Model=43UR8000AUA`, `Plex for LG` 5.94.3, webOS 10.3.1.
No other client in the house produces one. The failing requests carry a `Referer` header of
**4160 and 4281 bytes** — the whole `start.m3u8` URL with the client profile inline — for a
total request of ~4.6 KB, over the parser's 4 KB line budget. The client then retries in
tight bursts of eleven, ~10 ms apart (23:20:12.696→.792, 23:21:47.946→48.038, 23:21:49),
and playback never starts. It recurs across the whole retained month: 352 in Aug 5–13, 30
in Aug 13–19, 33 in Aug 19–20. **The `ET` is a logging artifact, not wire corruption** —
Plex prints the buffer having already consumed the `G`, and the same truncation shows on
requests that are provably well-formed. A troubleshooter that reports "malformed request
from client" here has read the log correctly and diagnosed it wrong.

**2 — Noise that must not be ranked as a finding.** 332 requests probing
`/@fs/../../.env`, `/@fs/root/.aws/credentials`, `/@fs/root/.claude/settings.json`,
`/etc/passwd` and ~40 more arrive on the public vhosts (`plex.` 226, `css.` 94 — **both
intentional operator-chosen names for this service**). **All 332 were rejected by the HTTP
parser and none reached a handler**; `/@fs/` is a Vite dev-server route PMS does not have.
Every probe forges a crawler `User-Agent` — ChatGPT-User, ClaudeBot, GPTBot, Amazonbot —
rotating identity across one campaign, so those are not those crawlers and UA filtering is
the wrong control anyway. This is background radiation against an exposed host, already
fully repelled. It is in this slice as a **negative** case: 332 occurrences of a
scary-looking string that the tool must report as informational at most. Getting this one
wrong in the alarming direction is the failure mode that makes a triage tool ignorable.

**3 — A nightly job that fails identically forever.** `Failed to transcode file (234)` 522
times, always error 234, **261 at 02:xx on Aug 19 and 261 at 02:xx on Aug 20** — the same
seven shows (Steven Universe, Scooby-Doo ×2, Aqua Teen Hunger Force, Adventure Time,
Octonauts, Justice League) re-attempted every night and failing the same way, because the
files carry **WebVTT subtitles muxed into MKV** (`Unknown/unsupported AVCodecID
S_TEXT/WEBVTT` → `Could not find codec parameters for stream 4 (Subtitle: none)`), which
the bundled ffmpeg will not decode into the ASS track credits detection wants. **All 261
are `[MAX WEBDL-1080p]…-AndreMor.mkv`** — a single release group, a perfect correlation the
tool should surface, since "which source do my failures share" is the question that ends
this class of ticket. It burns
the box for an hour nightly and will never converge. Adjacent and probably the same root:
766 `SLOW QUERY` (worst 7430 ms) and 120 `Held transaction for too long`, **356 of the slow
queries inside the 03:00 hour** — the Butler window — which is the mechanism by which a
background job becomes someone's stutter.

Shape follows 504: table-driven, one signature table (`vars/plex-log-signatures.yml`)
mapping pattern → severity → owning subject, so a new Plex release's new error is a table
entry and not a code change. Read-only and safe to run at any time, against a bundle path
or a live guest. Ranked findings out, plus the same Ntfy summary contract 504 uses.

**Scope boundary, on record:** this reads logs and reports. It does not restart PMS, edit
`Preferences.xml`, remux media, or change a Sonarr release profile.

## Remaining

- [x] built — Signature table covers the three findings above, and a fourth bundle from a different
      week reproduces finding 1's device attribution with no hand-editing.
- [x] built — Rank is verified to be independent of volume — asserted against this bundle, where
      the correct top finding has 33 occurrences and the loudest non-finding has 1208.
- [x] built — `ET`-prefix normalization proven: a well-formed request and an oversize one both log
      the artifact, and only the oversize one is reported. This is the one place the tool
      can be confidently wrong, so it needs a fixture both ways.
- [x] built — The 332 `/@fs/` probes rank as informational, never above finding 1, and the tool
      does not describe an operator's intentional vhost names as a misconfiguration. Both
      vhosts here are deliberate; only *disposition* (rejected at parse) is evidence.
- [x] built — Runs clean against a bundle with no findings and says so, rather than ranking noise.
- [ ] **Live:** one run against a real bundle pulled the same day, reviewed next to the raw
      logs, confirming it named the LG and did not name the Vizio.
- [x] built — Rundeck job lands under `Operate/`, is read-only, and has no confirmation prompt.

## Links
- `ansible/playbooks/stacks/troubleshoot-plex-client.yml` — the playbook
- `ansible/vars/plex-log-signatures.yml` — pattern → severity → subject table
- `rundeck/jobs/troubleshoot-plex-client.yaml` — `Operate/` group, see 602
- `docs/meta/504-wire-media-stack/README.md` — the stack-playbook shape being cloned
- notes.md — the full evidence walk, and the two readings that looked right and were not
