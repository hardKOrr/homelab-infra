#!/usr/bin/env python3
"""Hardware acceptance needs output, not merely a hardware-looking command line."""
import runpy
from pathlib import Path

probe = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'tools/lab-jellyfin-transcode'))
check = probe['hardware_evidence']
assert not check('ffmpeg -codec:v h264_vaapi')
assert not check('h264_vaapi frame=    0')
assert not check('h264_vaapi frame= 12\nConversion failed!')
assert not check('libx264 frame= 100')
assert check('h264_vaapi frame=   12 fps=24')
assert check('h264_qsv frame:120')
print('Hardware log evidence: command-only, zero frames, software and failed conversions rejected')
