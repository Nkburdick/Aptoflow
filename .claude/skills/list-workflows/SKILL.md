---
name: list-workflows
description: Show all workflows and their status
user-invocable: true
---

# /list-workflows

Display all Aptoflow workflows with their current status.

## Instructions

1. **Read CATALOG.md**: Parse the workflow registry table.

2. **Filter**: By default, show workflows with status Done or Deployed. If the user asks for all, show everything.

3. **Display** for each workflow:
   - **Name** and description
   - **Status** (with visual indicator)
   - **Model** being used
   - **Deploy target** and Modal mode
   - **Run command**:
     - Local: `.venv/bin/python workflows/{name}/main.py`
     - Modal webhook: `curl` command with auth header
     - Modal scheduled: `modal app logs {name}`
   - **Deploy status**: URL if deployed

4. **Summary**: Total workflows, how many in each status.
