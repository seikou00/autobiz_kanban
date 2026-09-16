# Plan v2 task semantics

One task owns one meaningful delivery result. It may span several files and implementation layers when they are needed for that result. Split only when outcomes can be delivered independently, require a real ordering edge, or live in different repositories.

`dependsOn` expresses a delivery dependency, never a guess based on shared files. Code runs in isolated worktrees and records actual changes; Merge Train resolves real conflicts.

Every in-scope scenario must occur in one or more Task `refs.scenarios`. References are individual `specs/.../spec.md#SCN-NNN` strings so coverage remains mechanically verifiable. UI references come from `UI_CONTEXT.json` and are only needed for UI tasks.

`implementationPoints` give Code the intended behavior and boundaries without guessing files or methods. `testPoints` and `verification.intent` tell UTest what to demonstrate, including important boundaries or failure paths. They are not shell commands; UTest selects test files, framework and executable commands after Code has established the implementation surface.
