# RFC Process

This document describes the Request for Comments (RFC) process for proposing significant changes to PSP.

## When to Write an RFC

Write an RFC for:

- **Breaking changes** to public API
- **New domain concepts** (new event types, new services)
- **Architectural changes** (new dependencies, schema redesign)
- **Invariant changes** (adding or removing guarantees)

Don't write an RFC for:

- Bug fixes
- Documentation improvements
- Refactoring (no behavior change)
- Adding optional fields to events

## RFC Lifecycle

```
Draft → Proposed → Accepted → Implemented
                 ↘ Rejected
                 ↘ Withdrawn
```

1. **Draft**: Author is still working on it
2. **Proposed**: Ready for review
3. **Accepted**: Will be implemented
4. **Rejected**: Won't be implemented (with explanation)
5. **Withdrawn**: Author withdrew
6. **Implemented**: Code merged

## How to Submit an RFC

1. Copy the template below
2. Create a new file: `docs/rfcs/NNNN-short-title.md`
3. Fill in the template
4. Open a PR with the RFC
5. Gather feedback, iterate
6. Maintainers will accept or reject

## RFC Template

```markdown
# RFC NNNN: Short Title

- **Status**: Draft | Proposed | Accepted | Rejected | Withdrawn | Implemented
- **Author**: Your Name (@github-handle)
- **Created**: YYYY-MM-DD
- **Updated**: YYYY-MM-DD

## Summary

One paragraph explaining the change.

## Motivation

Why are we doing this? What problem does it solve?

## Detailed Design

Technical details of the proposal. Include:
- API changes
- Database changes
- Event changes
- Migration path

## Drawbacks

Why should we NOT do this?

## Alternatives

What other designs were considered?

## Unresolved Questions

What needs to be figured out during implementation?

## Implementation Plan

- [ ] Phase 1: ...
- [ ] Phase 2: ...
- [ ] Phase 3: ...
```

## Review Criteria

RFCs are evaluated on:

1. **Necessity**: Is this change needed?
2. **Compatibility**: Does it break existing users?
3. **Migration**: Can existing deployments upgrade?
4. **Invariants**: Does it maintain or strengthen guarantees?
5. **Complexity**: Is the added complexity justified?

## RFC Index

| RFC | Title | Status |
|-----|-------|--------|
| - | - | - |

*No RFCs yet. This is a new project.*
