# Spec: Jinja type discipline

Pure Jinja expressions can preserve native values, but inventory, environment, command output,
concatenation, and serialized data can still supply strings. A task must not rely on the type
inferred by an earlier `set_fact` or template step.

## Rule

- Cast at the point of use: arithmetic (`x + 1`), numeric comparison (`x < y`), and `range()`
  arguments on values that can originate as strings must apply `| int` inline in the expression
  that uses them. `"10" < "9"` is lexicographically true; `"1" - 1` is a type error.
- Do not build loop state across `set_fact`+`until` retries; compute collections in a single
  expression (`range | map | reject | first`) instead of incrementing counters.
- When a value must round-trip as a number (for example, `vmid`), watch operator precedence: `|` binds
  tighter than `~`, so `a ~ b | int` casts only `b`. Parenthesize the whole expression.
- **Subscript any config key whose name is also a `dict` method**: `update`, `items`, `keys`,
  `values`, `get`, `pop`, `copy`, `clear`, `setdefault`, `fromkeys`. Jinja resolves `foo.bar`
  by trying `getattr(foo, 'bar')` *before* `foo['bar']`, so `app_config.update` evaluates to
  the bound `dict.update` method **whether or not the key exists** — the config value is
  simply unreachable by dot notation. The symptom is
  `'builtin_function_or_method object' has no attribute '<subkey>'`. Write
  `app_config['update'].binary_path`. This is why `ansible/vars/app-defaults/*.yml` may declare an
  `update:` block that dot access can never read.

## Enforced by

- inspection — cite this spec in findings; `ansible-lint` catches some but not all instances
