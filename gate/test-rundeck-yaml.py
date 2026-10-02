#!/usr/bin/env python3
"""Large rendered job documents must import without YAML collection aliases."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import yaml

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('render_job_alias_test', root / 'rundeck/render-job.py')
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)
for name in ('configure-app.yaml', 'remove-app.yaml', 'restart-app.yaml',
             'rollback-container.yaml', 'tail-applog.yaml'):
    source = root / 'rundeck/jobs' / name
    output = subprocess.check_output([sys.executable, str(root / 'rundeck/render-job.py'), str(source)], text=True)
    assert not any(isinstance(token, yaml.tokens.AliasToken) for token in yaml.scan(output)), name
    assert yaml.safe_load(output) == renderer.render(source), name
print('Rundeck YAML: 5 expanded job documents preserve content without aliases')
