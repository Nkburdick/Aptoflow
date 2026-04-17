# Workflow Catalog

| Workflow | Status | Complexity | Deploy Target | Modal Mode | Model | Args | Description |
|----------|--------|------------|---------------|------------|-------|------|-------------|
| car_scout | In Progress | Complex | Modal (cron) | Scheduled | `google/gemini-2.0-flash-001` (extraction) + `anthropic/claude-sonnet-4` (summary) | `SCOUT_ZIP`, `SCOUT_RADIUS_MI` | Daily PNW used-car digest + rare SMS unicorn alerts for Owen's first car |

## Status Key

| Status | Meaning |
|--------|---------|
| Planned | Scaffolded, plan written, not yet implemented |
| In Progress | Implementation underway |
| Done | Implemented and tests passing |
| Deployed | Live on Modal |
| Archived | No longer active |
