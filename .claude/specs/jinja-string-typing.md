# Spec: Jinja string typing

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

## Enforced by

- inspection — cite this spec in findings; `ansible-lint` catches some but not all instances
