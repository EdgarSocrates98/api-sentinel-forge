# factory/ — loop-factory convention

This directory queues and tracks implementation cycles for the API
Sentinel Forge, following the Loop Factory convention used across the
FORJAS workspace.

## Spec lifecycle

```
factory/specs/inbox  →  factory/specs/active  →  factory/specs/archive
```

- **inbox/** — committed queue of upcoming cycles. Specs are markdown
  files with YAML frontmatter (`id`, `title`, `agent`, `risk`,
  `verification`) and the sections Context / Acceptance Criteria /
  Constraints / Review Notes / Grill Gate.
- **active/** — the spec currently being implemented. Promotion happens
  via `loop-factory dispatch --stage` (binary lives in
  `../Loop-Factory/bin/loop-factory`; if it is not in the workspace,
  move the file manually and record the dispatch in the run notes).
- **archive/** — completed specs, archived only after review
  (`loop-factory archive <id> --accepted`, or manual move + run record).

## Rules

- **Grill gate before dispatch.** Every spec carries a Grill Gate
  section (owner, out of scope, riskiest assumption, smallest acceptable
  version). Open product questions must be resolved by the owner —
  Edgar (repo owner) — before the spec leaves `inbox/`.
- **Review before archive.** A spec may be archived only after the
  checklist in `factory/quality-gate.md` passes. Run records and
  captured verification output live under `factory/runs/`.
- **Specs are the source of truth, not generated prompts.** They encode
  blueprint decisions (§19–§21), docs/ contracts and invariants. If the
  implementation diverges from the spec, fix the code or amend the spec
  with owner sign-off — never silently widen scope.
- **Working specs stay separate.** `.kiro/specs/` holds local,
  uncommitted working specs; `factory/specs/` is the committed queue.
