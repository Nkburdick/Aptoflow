---
name: deploy
description: Deploy a workflow to Modal
user-invocable: true
---

# /deploy

Deploy a workflow to Modal with pre- and post-deployment validation.

## Instructions

1. **Identify workflow**: If no workflow name is provided, check CATALOG.md for workflows with Status "Done" and ask which one to deploy.

2. **Pre-deploy validation**:
   - Run `python -m pytest tests/test_{name_underscored}.py -v` — all tests must pass
   - Verify `.env` has required keys (OPENROUTER_API_KEY, MODAL_BEARER_TOKEN for webhooks)
   - Verify Modal secrets are configured: `modal secret list`
   - For webhooks: verify `verify_bearer_token` and `RateLimiter` are used in `main.py`

3. **Deploy**: Run `modal deploy workflows/{name}/main.py`

4. **Post-deploy validation** (for webhooks):
   - Test 422: Send request with invalid body → expect 422
   - Test 401: Send request without auth → expect 401
   - Test 200: Send valid request with auth → expect 200
   - Use `curl` or `httpx` for these checks

5. **Update CATALOG.md**: Set status to "Deployed".

6. **Update workflow README**: Add the deployment URL and example curl commands.

7. **Report**: Show deployment URL, test results, and any warnings.
