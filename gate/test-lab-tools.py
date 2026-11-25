#!/usr/bin/env python3
"""tools/lab: lab-start, lab-wait and lab-run, run for real against short local jobs."""
import os
import re
import subprocess
import sys
import tempfile
import time

repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tools = os.path.join(repo, "tools")
failures = []


def run(*args, env):
    return subprocess.run([os.path.join(tools, args[0]), *args[1:]], env=env,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True)


def check(name, condition, detail=""):
    if not condition:
        failures.append(f"{name}: {detail}")


with tempfile.TemporaryDirectory() as jobs:
    env = {**os.environ, "LAB_JOB_DIR": jobs}

    run("lab-start", "ok", "--", "bash", "-c", "echo one; echo 'fatal: boom'; exit 3", env=env)
    out = run("lab-wait", "ok", "--timeout", "30", env=env)
    check("finished job returns its exit code", out.returncode == 3, out.stdout)
    check("finished job reports done", "done rc=3" in out.stdout, out.stdout)
    check("error lines are listed", "--- error lines (1" in out.stdout, out.stdout)

    run("lab-start", "slow", "--", "sleep", "30", env=env)
    began = time.monotonic()
    out = run("lab-wait", "slow", "--timeout", "1", env=env)
    check("running job returns 124", out.returncode == 124, out.stdout)
    check("timeout is honoured", time.monotonic() - began < 10, "waited too long")
    dup = run("lab-start", "slow", "--", "true", env=env)
    check("a running name is refused", dup.returncode != 0, dup.stderr)
    os.kill(int(open(os.path.join(jobs, "slow.pid")).read()), 15)
    time.sleep(0.5)
    out = run("lab-wait", "slow", "--timeout", "5", env=env)
    check("a killed job is reported", "died without an exit code" in out.stdout, out.stdout)

    out = run("lab-wait", "--log", os.path.join(jobs, "ok.log"), "--until", "fatal", env=env)
    check("--until matches a pattern", out.returncode == 0 and "matched" in out.stdout, out.stdout)
    check("an unknown job is a usage error", run("lab-wait", "nope", env=env).returncode == 2)

    out = run("lab-run", "both", "--timeout", "30", "--", "bash", "-c", "echo hi; exit 4", env=env)
    check("lab-run starts and waits in one call", out.returncode == 4 and "done rc=4" in out.stdout, out.stdout)
    run("lab-start", "busy", "--", "bash", "-c", "sleep 3; echo first", env=env)
    out = run("lab-run", "busy", "--timeout", "30", "--", "bash", "-c", "echo second", env=env)
    check("lab-run waits on a running job instead of starting another",
          out.returncode == 0 and "already running" in out.stdout and "first" in out.stdout
          and "second" not in out.stdout, out.stdout)
    out = run("lab-run", "fast", "--timeout", "1", "--", "sleep", "20", env=env)
    check("lab-run returns 124 while the job runs", out.returncode == 124, out.stdout)
    os.kill(int(open(os.path.join(jobs, "fast.pid")).read()), 15)
    check("lab-run without a command is a usage error", run("lab-run", "x", env=env).returncode == 2)

# The hard cap is the point of lab-wait: a wait must return inside a 10-minute tool limit,
# whatever --timeout asks for.
source = open(os.path.join(tools, "lab-wait")).read()
cap = re.search(r"^MAX_WAIT = (\d+)$", source, re.M)
check("MAX_WAIT is at most 570", cap and int(cap.group(1)) <= 570, "missing or too large")
check("--timeout is clamped to MAX_WAIT", "min(max(int(opts[\"--timeout\"]), 0), MAX_WAIT)" in source)

if failures:
    print("\n".join(failures))
    sys.exit(1)
print("lab tools: ok")
