# Custom Command: /agent-team

You are operating as the Orchestrator for the 5-Agent Delivery Pipeline.

The user passed the following request:
"$ARGUMENTS$"

Execute the end-to-end change workflow sequentially by adopting each agent's role defined in `.claude/agents/`:

1. **Planner (`.claude/agents/01-planner.md`):** Generate `docs/change/CHG-XXX/plan.md` using the user request.
2. **Architect (`.claude/agents/02-architect.md`):** Draft `docs/change/CHG-XXX/architecture.md`.
3. **Developer (`.claude/agents/03-developer.md`):** Draft OpenSpec specs under `docs/change/CHG-XXX/open-spec/changes/`, then implement the feature code and unit tests.
4. **Critic (`.claude/agents/04-critic.md`):** Audit the implementation and produce `docs/change/CHG-XXX/critic-report.md`.
5. **PR Agent (`.claude/agents/05-pr.md`):** Verify 0 blockers, run local checks, push branch, and open the GitHub PR.

Begin immediately with Step 1.