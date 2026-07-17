# Spec: namespace merge discipline

<!-- isotope:section namespace-merge-discipline:start -->

The three shared dicts — `homelabinfra_config`, `homelabinfra_instance`, `homelabinfra_infra` —
are grown incrementally by many task files. A destructive write anywhere silently corrupts every
downstream consumer.

## Rule

- Never bare-assign a namespace dict: `set_fact: homelabinfra_instance: {key: val}` destroys all
  sibling keys. Always `{{ homelabinfra_instance | default({}) | combine({...}, recursive=True) }}`.
- Never store `default(omit)` inside a dict built by `set_fact`: the omit placeholder becomes a
  literal `__omit_place_holder__<hex>` string in the fact, passes later `is defined` checks, and
  leaks into rendered output. Build optional keys conditionally (ternary-combine of `{}`) or leave
  them absent; `omit` is valid only as a top-level module argument at task execution time.
- A task file that mutates a namespace documents its inputs and outputs in a header comment
  (see `tasks/stack/find-or-create-host.yml` for the pattern).

## Enforced by

- inspection — cite this spec in findings (source: `.claude/CLAUDE.md` "CRITICAL" clause;
  meta slice 006 tracks the known violation in `tasks/network/generate-ip.yml`)

<!-- isotope:section namespace-merge-discipline:end -->
