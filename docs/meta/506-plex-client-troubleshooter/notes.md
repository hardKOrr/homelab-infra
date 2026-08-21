# 506 — notes

Append-only. Session narrative and dead ends.

## 2026-08-20 — evidence walk over bundle `Plex Media Server Logs_2026-08-20_23-24-03`

Bundle spans Jul 22 → Aug 20 2026 across six rotations of `Plex Media Server.log`
(current 1.4 MB, `.2` 10 MB). PMS v1.43.3.10896-cb3ebc72d, Ubuntu 22.04.5, i7-8700,
library on `/mnt/data/media`, server reached locally as `192.168.1.4:32400`.

### Two readings that looked right and were not

**Dead end 1 — "the network is corrupting requests."** The failing line reads
`Error parsing HTTP request: ET /video/...`. A missing `G` on the request verb is a
textbook truncated-socket symptom, and the first pass wrote it up that way. It is wrong.
The same `ET` prefix appears on the `/@fs/` scanner probes, which are separate, remote, and
well-formed enough to be recognizable — and `.2.log` shows both `ET` (287) and `GET` (58)
on the *same* probe campaign. Plex logs the buffer after consuming one byte. Nothing on the
wire is truncated. What is actually oversize is the `Referer`, at 4160/4281 bytes.
**The tool must normalize this prefix or it will confidently report a network fault that
does not exist.** Kept as an acceptance item.

**Dead end 2 — "MDE can't find a transcode profile."** 1220 `MDE: unable to find a working
transcode profile for video stream` is the loudest WARN in the bundle and reads exactly
like a playback fault. It is not: every occurrence sits inside the `CreditsDetectionManager`
job path, immediately before `Failed to transcode file (234)`, on a subtitle stream whose
codec is `none`. Zero of them touch a user session. This is the clearest case for ranking
findings by *provenance* rather than by count or severity.

### What the transcoder statistics actually contained

All six `Plex Transcoder Statistics*.log` files hold **one session between them** —
`9779eb52…`, Victoria Coover, `Plex for Vizio` on a V505-H9 at `192.168.7.20`, keys
191177→191182, 13:05–13:16 on Aug 20. FLAC→AAC audio-only, `audioDecision="transcode"`,
`local="1" relayed="0"`. It is a music album playing normally; the six rotations are one
file per track, not six incidents. Worth recording because the file *count* invites the
opposite conclusion, and because it means **the stats logs contain no evidence about the
LG at all** — the client that is actually failing never gets far enough to produce a
session report. A troubleshooter that starts from the stats logs will find nothing wrong.

`transcodeHwRequested="1" transcodeHwFullPipeline="0"` on every variant. Expected for an
audio-only transcode; not evidence of a broken GPU path, and should not be reported as one
without a video session to compare against. No `nvdec`/`vaapi`/`qsv` string appears
anywhere in the bundle, so hardware transcode capability is **unconfirmed either way** from
logs alone. Left out of the slice deliberately.

### Subnet observation, unresolved

Three private ranges appear: server `192.168.1.4`, Vizio `192.168.7.20`, and a failed
outbound `HTTP error requesting GET http://192.168.4.65:1096/ (7, Couldn't connect)`.
Whether that is intentional segmentation or drift is not answerable from these logs. Not
scoped into 506 — noted so the next person does not re-derive it.

### Counts, for regression fixtures

| Signal | Count | Where |
|---|---:|---|
| `index.m3u8` parse failure (LG only) | 82 all logs / 33 current | the finding |
| `/@fs/` credential probes | 332 | `.1` 19, `.2` 313 |
| `Failed to transcode file (234)` | 522 | 261 × two nights, 02:xx |
| `MDE: unable to find…profile` | 1220 | credits job, not playback |
| `UltraBlurProcessor` failures | 2101 | cosmetic |
| `SLOW QUERY` | 766 | 356 in the 03:00 Butler hour; worst 7430 ms |
| `Held transaction` / `Took too long` | 120 | peak 119 in Aug 19 22:00 |

Retry burst shape for finding 1 is eleven requests ~10 ms apart, three bursts in the final
90 seconds of the log. If the tool ever reports a *count* of failures rather than a count
of *bursts*, the number will be meaningless — eleven retries is one failed play attempt.

### Not investigated

- Whether the LG's oversize `Referer` is fixed in a Plex for LG newer than 5.94.3. That is
  the likely real remedy and it is a vendor question, not a repo question.
- `PMS Plugin Logs/` — not opened.
- DLNA logs stop Jan 2024; assumed disabled, not verified.

## 2026-08-21 — two corrections from operator review

**Dead end 3 — "the `css.` subdomain is a catch-all misconfiguration."** Wrong, and wrong in
the way that matters most. `plex.` and `css.` are **both intentional operator-chosen names**
for this service. The inference ran from naming aesthetics to a Caddy fault with no evidence
for the middle step, and it produced a work item against slice 402 that should never have
existed.

Re-checked on disposition rather than on hostname: **all 332 `/@fs/` probes appear only as
`Error parsing HTTP request` lines or as `Referer` values inside them. Zero were served.**
They never reached a handler — `/@fs/` is a Vite dev-server route PMS does not implement.
An exposed host receives this traffic continuously and this one rejected all of it.

Both corrections point the same way, so it is now an explicit acceptance item: **the tool
must rank `/@fs/` as informational.** It is the highest-count "scary" string in the bundle
(332) and the correct action on it is nothing. A triage tool that cries wolf here gets
ignored on the day it is right.

### Finding 3 root cause, resolved

Not a generic "credits detector cannot bind a stream." From `Plex Media Scanner Credits*.log`,
identical in all six rotations, three tracks per file:

```
[FFMPEG] - Unknown/unsupported AVCodecID S_TEXT/WEBVTT.
[FFMPEG] - Could not find codec parameters for stream 4 (Subtitle: none): unknown codec
```

The files carry **WebVTT subtitle tracks muxed into MKV**. Legal Matroska, but the ffmpeg
bundled with PMS has no `S_TEXT/WEBVTT` mapping, so it types the stream `Subtitle: none`;
credits detection then asks for `sist#0:4/none → sost#1:0/ass`, has no decoder, and exits
234. Always stream index 4, always to `ass`, 522/522 occurrences.

**All 261 failing episodes are `[MAX WEBDL-1080p]…-AndreMor.mkv`.** One release group, one
mux habit. Distribution: Adventure Time 130, Octonauts 53, Justice League 37,
Scooby-Doo and Scrappy-Doo 26, Steven Universe 11, Aqua Teen Hunger Force 3,
Scooby-Doo Where Are You! 1.

That correlation is the deliverable for this finding class. "Which source do my failures
share" ends the ticket; "a transcode failed 522 times" does not. Added to the signature
table's requirements as a **grouping** obligation, not just a matching one.

Self-limiting, slowly: `Credits detection for item N has failed too many times, we will not
retry again` has fired **7 times** against 261 items, at ~8 s per attempt. The nightly cost
decays but the set is still ~254 deep, and both retained nights cost 261 attempts each.
Video and audio playback of these episodes is unaffected — only the subtitle tracks and the
credits/intro markers are.

Also seen once, unrelated and not chased: Deep Analysis on Teen Titans Go! S01E21 failed
`error=-2 No such file or directory` — a scan racing a file move, plus 11 similar
`last_write_time` misses on two movie folders.
