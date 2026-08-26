# ansible/callback_plugins/homelab.py
#
# The stdout callback every job in this repository runs under.
#
# WHY THIS EXISTS. An operator's whole view of a deploy is one Rundeck "Log Output"
# pane. The stock `default` callback writes a stream of `ok: [localhost]` lines that
# say nothing, buries the lines that do say something, and ends with a PLAY RECAP of
# counters answering "how many tasks ran" rather than "did my app deploy". This
# callback keeps every line that carries information, drops the ones that do not, and
# frames the run with a header and a result block of a FIXED SHAPE, so an operator
# learns one layout once instead of re-reading a wall of YAML per job.
#
# IT IS UI-AGNOSTIC ON PURPOSE. Nothing here knows about Rundeck. The same output is
# what a terminal run, a Semaphore run and a gate run produce, which is the standing
# rule for everything under ansible/. Rundeck's contribution is colour rendering,
# which it gets for free from the ANSI sequences ansible already emits.
#
# WHAT IS KEPT AND WHAT IS DROPPED:
#   kept     - every TASK banner (so a long deploy still shows progress and a hang
#              names the task it hung in), every `changed`, `failed` and `unreachable`
#              line, and every `debug`/`assert` message, which is where this platform
#              deliberately reports its findings
#   dropped  - the silent `ok: [host]` and `skipping: [host]` result lines, which are
#              the bulk of the log and carry nothing the recap does not
# The drop is two options in ansible.cfg, not code here. To get the old firehose back
# for one run:  ANSIBLE_DISPLAY_OK_HOSTS=1 lab-run playbooks/apps/sonarr.yml ...
#
# The class derives from `default` rather than replacing it. Failure rendering, the
# yaml result format, diff output, loop handling and colour are ansible's, stay
# ansible's, and keep working across upgrades; this file adds a header, a result
# block, task timing and a degradation summary, and overrides exactly one display
# decision.

from __future__ import annotations

DOCUMENTATION = '''
    name: homelab
    type: stdout
    short_description: homelab-infra one-click job output
    version_added: historical
    description:
      - Wraps the default stdout callback with a run header, per-task timing and a
        fixed-shape result block, and keeps debug messages visible when silent ok
        results are suppressed.
    extends_documentation_fragment:
      - default_callback
      - result_format_callback
    requirements:
      - set as stdout in configuration
'''

import os
import re
import time

from ansible import constants as C
from ansible import context
from ansible.plugins.callback.default import CallbackModule as DefaultCallback

# Defence in depth only. This repository's contract is that no secret travels in argv
# (see rundeck/jobs/store-secret.yaml, which passes the value through the environment),
# so the header prints extra-vars as given. If that contract is ever broken, this
# redacts the value rather than publishing it to a job log.
_SECRETISH = re.compile(r'(pass|secret|token|key|cred)', re.I)

# A task slower than this is worth naming in the result block. Anything faster is
# noise in a list whose only job is to answer "what took so long".
_SLOW_SECONDS = 5.0
_SLOW_COUNT = 5

_RULE = '-' * 68


class CallbackModule(DefaultCallback):

    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = 'stdout'
    CALLBACK_NAME = 'homelab'

    def __init__(self):
        super(CallbackModule, self).__init__()
        self._run_started = time.time()
        self._task_started = {}
        self._task_elapsed = []
        self._degradations = []
        self._problems = []

    # -- framing helpers -----------------------------------------------------

    def _rule(self, title, color):
        self._display.display('', screen_only=True)
        width = max(0, len(_RULE) - len(title) - 1)
        self._display.display('%s %s' % (title, _RULE[:width]), color=color)

    def _row(self, label, value):
        self._display.display('  %-10s %s' % (label, value))

    @staticmethod
    def _duration(seconds):
        seconds = int(round(seconds))
        if seconds < 60:
            return '%ds' % seconds
        if seconds < 3600:
            return '%dm %02ds' % (seconds // 60, seconds % 60)
        return '%dh %02dm' % (seconds // 3600, (seconds % 3600) // 60)

    @staticmethod
    def _extra_vars():
        # CLIARGS['extra_vars'] is a tuple of raw `key=value` strings, or of @file
        # references. Both are printed as the operator would have typed them.
        pairs = []
        for raw in (context.CLIARGS.get('extra_vars') or ()):
            text = str(raw)
            key, sep, value = text.partition('=')
            if sep and _SECRETISH.search(key):
                text = '%s=<redacted>' % key
            pairs.append(text)
        return ' '.join(pairs)

    # -- run header ----------------------------------------------------------

    def v2_playbook_on_start(self, playbook):
        self._rule('HOMELAB RUN', C.COLOR_HIGHLIGHT)
        self._row('Playbook', os.path.basename(playbook._file_name))

        # lab-run.sh exports these. A terminal run has neither and simply omits the
        # rows - the callback never depends on the runner being present.
        revision = os.environ.get('LAB_REVISION')
        if revision:
            self._row('Revision', revision)
        branch = os.environ.get('LAB_BRANCH')
        if branch:
            self._row('Branch', branch)

        options = self._extra_vars()
        if options:
            self._row('Options', options)

        self._row('Started', time.strftime('%Y-%m-%d %H:%M:%S'))
        self._display.display('')

        super(CallbackModule, self).v2_playbook_on_start(playbook)

    # -- progress and timing -------------------------------------------------

    def _task_start(self, task, prefix=None):
        # Always banner the task, even though silent ok results are suppressed. The
        # stock callback ties the two together and prints the banner only when
        # nothing is being filtered, which would turn a fully-successful deploy into
        # a blank pane - an operator watching a 40-minute bootstrap would have no way
        # to tell a slow step from a hung one.
        if prefix is not None:
            self._task_type_cache[task._uuid] = prefix
        self._last_task_name = task.get_name().strip()
        self._task_started.setdefault(task._uuid, time.time())
        self._print_task_banner(task)

    def _note_elapsed(self, result):
        started = self._task_started.pop(result._task._uuid, None)
        if started is not None:
            self._task_elapsed.append(
                (time.time() - started, result._task.get_name().strip()))

    # -- result handling -----------------------------------------------------

    def v2_runner_on_ok(self, result):
        self._note_elapsed(result)
        self._absorb_degradations(result)

        # The one display decision this callback overrides. With display_ok_hosts
        # off, `default` returns before printing any unchanged result - which would
        # also swallow every debug and assert message, and those messages are how
        # this platform reports what it found. A result carrying
        # _ansible_verbose_always (debug, assert, and anything run with -v) is
        # displayed regardless.
        if (not result._result.get('changed', False)
                and not self.get_option('display_ok_hosts')
                and self._run_is_verbose(result)):
            self._handle_warnings(result._result)
            if result._task.loop and 'results' in result._result:
                self._process_items(result)
            else:
                self._clean_results(result._result, result._task.action)
                self._display.display(
                    'ok: [%s] => %s' % (self.host_label(result),
                                        self._dump_results(result._result)),
                    color=C.COLOR_OK)
            return

        super(CallbackModule, self).v2_runner_on_ok(result)

    def v2_runner_on_failed(self, result, ignore_errors=False):
        self._note_elapsed(result)
        if not ignore_errors:
            self._problems.append((self.host_label(result),
                                   result._task.get_name().strip(),
                                   self._problem_text(result)))
        super(CallbackModule, self).v2_runner_on_failed(
            result, ignore_errors=ignore_errors)

    def v2_runner_on_unreachable(self, result):
        self._note_elapsed(result)
        self._problems.append((self.host_label(result), 'UNREACHABLE',
                               self._problem_text(result)))
        super(CallbackModule, self).v2_runner_on_unreachable(result)

    def v2_runner_on_skipped(self, result):
        self._note_elapsed(result)
        super(CallbackModule, self).v2_runner_on_skipped(result)

    @staticmethod
    def _problem_text(result):
        data = result._result or {}
        for field in ('msg', 'stderr', 'reason', 'module_stderr'):
            value = data.get(field)
            if value:
                text = ' '.join(str(value).split())
                return text if len(text) <= 300 else text[:297] + '...'
        return 'see the failure above'

    def _absorb_degradations(self, result):
        # tasks/report-degradation.yml records into the per-host fact
        # homelabinfra_degradations, and set_fact hands the new value to the callback
        # in ansible_facts. Reading it here is what lets the result block name every
        # wiring problem without any playbook knowing this file exists.
        try:
            facts = result._result.get('ansible_facts') or {}
            recorded = facts.get('homelabinfra_degradations')
            if isinstance(recorded, list):
                self._degradations = recorded
        except Exception:
            # A callback must never be the reason a deploy reports failure.
            pass

    # -- result block --------------------------------------------------------

    def v2_playbook_on_stats(self, stats):
        super(CallbackModule, self).v2_playbook_on_stats(stats)

        totals = {'ok': 0, 'changed': 0, 'failures': 0, 'unreachable': 0,
                  'skipped': 0, 'rescued': 0, 'ignored': 0}
        for host in stats.processed:
            summary = stats.summarize(host)
            for key in totals:
                totals[key] += summary.get(key, 0)

        fatal = [d for d in self._degradations if d.get('fatal', True)]
        soft = [d for d in self._degradations if not d.get('fatal', True)]
        broken = totals['failures'] + totals['unreachable']

        if broken:
            verdict, color = 'FAILED', C.COLOR_ERROR
        elif soft:
            verdict, color = 'SUCCEEDED WITH DEGRADATION', C.COLOR_WARN
        else:
            verdict, color = 'SUCCEEDED', C.COLOR_OK

        self._rule('HOMELAB RESULT', color)
        self._display.display('  %-10s %s' % ('Result', verdict), color=color)
        self._row('Duration', self._duration(time.time() - self._run_started))
        self._row('Changed', '%d task(s) on %d host(s)'
                  % (totals['changed'], len(stats.processed)))
        if totals['skipped']:
            self._row('Skipped', '%d' % totals['skipped'])
        if broken:
            self._row('Failed', '%d task(s)' % broken)

        if self._problems:
            self._display.display('')
            self._display.display('  What failed:', color=C.COLOR_ERROR)
            for host, task, why in self._problems:
                self._display.display('    %s on %s - %s' % (task, host, why),
                                      color=C.COLOR_ERROR)

        if fatal or soft:
            self._display.display('')
            self._display.display('  What is degraded:', color=C.COLOR_WARN)
            for entry in fatal + soft:
                is_fatal = entry.get('fatal', True)
                self._display.display(
                    '    %s: %s - %s' % ('FAILED' if is_fatal else 'DEGRADED',
                                         entry.get('component', '?'),
                                         entry.get('reason', '?')),
                    color=C.COLOR_ERROR if is_fatal else C.COLOR_WARN)

        slow = sorted((e for e in self._task_elapsed if e[0] >= _SLOW_SECONDS),
                      reverse=True)
        if slow:
            self._display.display('')
            self._display.display('  Slowest tasks:')
            for elapsed, name in slow[:_SLOW_COUNT]:
                self._display.display('    %8s  %s' % (self._duration(elapsed), name))

        self._display.display('')
