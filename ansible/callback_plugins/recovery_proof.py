"""Private child-playbook transport for the recovery proof dispatcher.

Only explicitly named observations and aggregate counters cross this boundary. Never
serialize arbitrary task results, exceptions, invocation arguments or no_log results.
"""

import json
from ansible.plugins.callback import CallbackBase


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "stdout"
    CALLBACK_NAME = "recovery_proof"

    def __init__(self):
        super().__init__()
        self.observations = []
        self.failed = False

    def v2_runner_on_ok(self, result):
        if result._task.no_log or result._result.get("_ansible_no_log"):
            return
        message = result._result.get("msg")
        if isinstance(message, dict) and set(message) == {"proof_observation"}:
            self.observations.append(message["proof_observation"])

    def v2_runner_on_failed(self, result, ignore_errors=False):
        self.failed = True  # Ignored failures still bound the evidence.

    def v2_runner_on_unreachable(self, result):
        self.failed = True

    def v2_playbook_on_stats(self, stats):
        counters = {host: stats.summarize(host) for host in stats.processed}
        self._display.display(
            json.dumps(
                {
                    "observations": self.observations,
                    "stats": counters,
                    "failed": self.failed,
                }
            )
        )
