# Agent workflow rules

## Autonomous commit and sharing gate

For work explicitly authorized by the user in this repository, an agent may commit and publish a task branch without asking again only when every condition below is met:

1. The completed changes stay within the assigned task and the agent has reviewed the exact diff and `git status`.
2. The agent owns every staged change. Stage explicit paths; never use a broad add that could include another agent's edits. If work overlaps or ownership is unclear, stop and coordinate first.
3. The changes meet the task's stated acceptance criteria. For documentation-only work, review factual claims, source links, and scope. Do not run tests unless the user asks for testing or verification; report when tests were not run.
4. No credentials, tokens, personal or customer data, real quote data, local configuration, logs, or temporary artifacts are included.
5. The `origin` remote is verified as the intended repository, and the work is on a task branch named `codex/<task>` based on an up-to-date `origin/main`.
6. The task instructions do not prohibit commits, pushes, or sharing.

When all conditions pass, create a focused commit, push only the task branch, and open a Pull Request with a factual summary and known limitations. Give the user and collaborating agents the branch/PR reference and commit SHA. Follow `docs/TEAM.md`: a human reviews and merges; agents do not merge the PR.

Never push directly to `main`, force-push, delete another agent's branch, merge a PR, or deploy. If content, ownership, privacy, or repository checks fail, do not publish and report the blocker. If only network access prevents sharing, a local task-branch commit is allowed when the other conditions pass, but clearly report that it was not pushed and is not available to other clones.
