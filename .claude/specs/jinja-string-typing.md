# Spec: Jinja string typing

<!-- isotope:section jinja-string-typing:start -->

Jinja2 native mode is off (no `jinja2_native` in `ansible/ansible.cfg`), so every `set_fact`
result and every `{{ ... }}` template resolves to a **string**, regardless of trailing `| int`
filters at assignment time.

## Rule

- Cast at the point of use, not at assignment: arithmetic (`x + 1`), numeric comparison
  (`x < y`), and `range()` arguments on fact-sourced values must apply `| int` inline in the
  expression that uses them. `"10" < "9"` is lexicographically true; `"1" - 1` is a TypeError.
- Do not build loop state across `set_fact`+`until` retries; compute collections in a single
  expression (`range | map | reject | first`) instead of incrementing counters.
- When a value must round-trip as a number (e.g. `vmid`), watch operator precedence: `|` binds
  tighter than `~`, so `a ~ b | int` casts only `b`. Parenthesize the whole expression.
- **Subscript any config key whose name is also a `dict` method**: `update`, `items`, `keys`,
  `values`, `get`, `pop`, `copy`, `clear`, `setdefault`, `fromkeys`. Jinja resolves `foo.bar`
  by trying `getattr(foo, 'bar')` *before* `foo['bar']`, so `app_config.update` evaluates to
  the bound `dict.update` method **whether or not the key exists** — the config value is
  simply unreachable by dot notation. The symptom is
  `'builtin_function_or_method object' has no attribute '<subkey>'`. Write
  `app_config['update'].binary_path`. This is why `vars/app-defaults/*.yml` may declare an
  `update:` block that dot access can never read.

## Enforced by

- inspection — cite this spec in findings; `ansible-lint` catches some but not all instances

<!-- isotope:section jinja-string-typing:end -->
