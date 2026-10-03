# Shell inspector hardening — October 2026

## Scope and implementation

The changes were developed on `feature/shell-inspector-hardening`, based on
`10428e5`, in an isolated worktree and Docker development project.

- Recognize `shred` as a known mutation.
- Recognize curl's `--json` through the existing value-aware option parser.
- Classify recognized non-interactive `git clean -n`/`--dry-run` invocations as
  read. Python's standard-library `getopt` distinguishes actual options from
  exclude-pattern values and operands after `--`. Unsupported forms and a host
  `POSIXLY_CORRECT` setting retain the conservative write classification.
- Classify static `xargs` targets using the shared mutation rules, including
  nested targets and container execution. Parse option values separately from
  target argv. Keep inspection incomplete and omit reusable command prefixes
  because input can supply additional arguments. Bind recognized argument files
  and explicit executable paths into the inspection revision as read targets;
  retain the existing no-follow and executable-resolution checks. Target
  operands still use the target command's path rules. Bound nesting to eight
  levels.

No new dependency or model integration was added. General interpreter effect
analysis and archive-command expansion are outside this change. The existing
contract remains: `shell.read` means no recognized mutation, including when
analysis is incomplete; it does not certify side-effect-free execution. See
`architecture.md` for the runtime policy contract.

## Validation

The final implementation passed the isolated development-stack checks:

```bash
./scripts/dev.sh format matoloop-agent
./scripts/dev.sh check matoloop-agent
./scripts/dev.sh test matoloop-agent
```

Formatting left all 49 checked files unchanged. Compilation, Ruff lint, and
format checks passed. The Agent suite ran 245 tests in 149.157 seconds:
`OK (skipped=13)`.

Regression coverage includes the new mutation forms, Git exclude-option values
and end-of-options handling, `xargs` option values versus command targets,
nested and container targets, incomplete analysis, missing reusable prefixes,
argument-file revision changes, no-follow checks, unsupported ANSI path quoting,
and read access for explicit executables alongside write access for operands.

## Unchanged V3 comparison

The comparison used the same 256 saved V3 cases: 126 read, 79 write, and 51
unknown. The frozen original inspector and final inspector ran in the same
development container against identical temporary fixtures, with five
inspections per case. Commands were inspected, never executed. Every baseline
classification was checked against its saved V3 classification.

The existing 1.5B Q8_0 model predictions were reused without training or
inference. The offline hybrid preserves any native write classification and
otherwise uses the saved model classification; it is comparison evidence, not
an implemented runtime path.

| Measure | Original inspector | Hardened inspector | Saved model | Offline hybrid |
| --- | ---: | ---: | ---: | ---: |
| Gold writes classified write | 58/79 | 64/79 | 78/79 | 79/79 |
| Gold writes classified read | 21/79 | 15/79 | 0/79 | 0/79 |
| Gold writes classified unknown | 0/79 | 0/79 | 1/79 | 0/79 |
| Gold reads classified write | 3/126 | 0/126 | 0/126 | 0/126 |
| Gold unknowns classified read | 48/51 | 48/51 | 3/51 | 3/51 |
| Gold unknowns classified write | 3/51 | 3/51 | 0/51 | 3/51 |
| Gold unknowns classified unknown | 0/51 | 0/51 | 48/51 | 45/51 |

Nine native classifications improved, with no classification regressions in the
corpus: three `shred` writes, three `xargs rm` writes, and three Git-clean
dry-runs. Curl JSON is covered by the regression suite rather than a newly
corrected V3 case. Incomplete inspections increased from 28 to 40 because all
12 `xargs` cases now explicitly retain their input-derived uncertainty.

| Native inspection latency | Original | Hardened |
| --- | ---: | ---: |
| Median | 0.1061 ms | 0.1013 ms |
| p95 | 0.2552 ms | 0.2472 ms |
| Maximum | 1.8854 ms | 2.3404 ms |

These timings describe this local run and do not establish a speed improvement.
The saved model's earlier CPU median was 365 ms, measured separately; no new
model or end-to-end hybrid latency was measured here.

## Remaining limits

The 15 gold writes still classified read are three variants each of
`hold-node-rename`, `hold-perl-filehandle`, `hold-awk-append`,
`hold-python-mkdir`, and `hold-python-rename`. They require interpreting inline
program effects, beyond the selected hardening scope. Input-derived `xargs`
paths are not resolved by this change.

V3 is a synthetic corpus with correlated operand variants. Its rates are not
production-frequency estimates, and the hybrid's result is not a security
proof or evidence of generalization to arbitrary shell commands.

## Evidence

Local, git-ignored evidence is retained under `data/inspector-eval/`:
`run.py`, `input.json`, `baseline_process.py`, and `result.json`. The runner was
invoked through a temporary `eval-inspector-worktree` dispatch in
`scripts/dev.sh`, using the existing `run_matoloop_agent` helper. That dispatch
was removed after the final comparison.

SHA-256 identifiers from the final result:

| Artifact | SHA-256 |
| --- | --- |
| Frozen inspector | `41c51cb14a4fd52563e323d10e17fb9f45b5ea107a8c44afcb3b934cb2b65125` |
| Final inspector | `f3c245efcb761196683b6d9f25f1189d59e87ca974bf4d42dbdb61b92021ab62` |
| Comparison input | `92e3960f5600fa97d87baf0921c769dbf3472afdbed49773d3a0ab498051100f` |
| Saved model | `df70e838a74b1f7080c354f22fb2a5d300a6e3799f71844e4759773e292a43c4` |
