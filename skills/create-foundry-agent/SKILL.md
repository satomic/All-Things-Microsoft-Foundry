---
name: create-foundry-agent
description: |
  Create, deploy, and test a versioned Prompt Agent on Microsoft Foundry (new Foundry, NOT
  classic) from any user input: a natural-language description of the desired agent, a raw
  instructions/system-prompt text, or an existing Markdown skill file (e.g., Anthropic-style
  SKILL.md). Handles Foundry environment discovery and project selection, agent creation via
  the Python SDK, end-to-end testing through the OpenAI Responses API, and downloading
  generated artifacts.

  Use when asked to: create/deploy/build an agent on Azure AI Foundry / Microsoft Foundry,
  convert a skill or prompt into a Foundry agent, wire instructions to Foundry Agent Service,
  test a Foundry prompt agent, or download files produced by a Foundry code-interpreter agent.
---

# Create a Microsoft Foundry (New) Prompt Agent from Any User Input

## ⚠️ CRITICAL: New Foundry vs Classic — pick the right API first

There are TWO incompatible agent systems. Agents created in one are INVISIBLE in the other portal.
**Always use the NEW API unless the user explicitly asks for classic.**

| | Classic (deprecated, retires 2027-03-31) | New Foundry (use this) |
|---|---|---|
| SDK | `azure-ai-projects` **1.x** | `azure-ai-projects` **>= 2.0.0** |
| Create | `agents.create_agent(...)` → `asst_xxx` | `agents.create_version(agent_name=..., definition=PromptAgentDefinition(...))` → versioned agent (`name:version`) |
| Run | threads / messages / runs | OpenAI **Responses API** via `project_client.get_openai_client()` + `extra_body={"agent_reference": {...}}` |
| Tool class | `azure.ai.agents.models.CodeInterpreterTool` (definitions/resources) | `azure.ai.projects.models.CodeInterpreterTool()` (plain list item) |
| Generated files | message `file_citation` annotation + `agents.files` | response `container_file_citation` annotation + `openai_client.containers.files.content.retrieve()` |
| Portal | classic portal "Create and debug your agents" | new portal **Build > Agents** (Type=Prompt, has Version column) |

Sanity check before starting: `pip show azure-ai-projects` must report version >= 2.0.0.
If 1.x is installed, upgrade: `pip install --upgrade "azure-ai-projects>=2.0.0"`.

## Dependencies

**Local tooling:**
- Python >= 3.9
- `pip install "azure-ai-projects>=2.0.0" azure-identity openai`
- `openpyxl` (only if the agent produces .xlsx files you want to auto-verify)
- Azure CLI installed and logged in: `az login` (DefaultAzureCredential picks up CLI credentials)

**Azure-side requirements:**
- A Microsoft Foundry account (kind `AIServices`) with at least one **Foundry project**
  (endpoint format: `https://<account>.services.ai.azure.com/api/projects/<project>`)
- At least one **chat model deployment** in that account (e.g., gpt-4.1, gpt-5.x)
- The signed-in identity needs the **Foundry User** role (formerly "Azure AI User") or higher
  **at the project scope**. Minimum permissions: `agents/*/read`, `agents/*/action`, `agents/*/delete`.

## Step 1: Discover the environment and SELECT a project

Run [scripts/discover_projects.py](scripts/discover_projects.py) to enumerate subscriptions →
AIServices accounts → Foundry projects → model deployments:

```bash
python scripts/discover_projects.py            # uses current az subscription
python scripts/discover_projects.py --all-subscriptions
```

It prints a JSON list like:

```json
[
  {
    "account": "my-foundry",
    "resource_group": "my-rg",
    "location": "eastus2",
    "project": "my-project",
    "endpoint": "https://my-foundry.services.ai.azure.com/api/projects/my-project",
    "chat_models": ["gpt-4.1", "gpt-5.4"]
  }
]
```

**Project selection rules (MANDATORY):**
1. If the user already specified a project/endpoint → use it, but verify it exists in the discovery output.
2. If discovery finds exactly **one** project → use it, and tell the user which one you picked.
3. If discovery finds **multiple** projects and the user did NOT specify one →
   **STOP and ask the user to choose** (in an interactive agent environment, present the options
   as a question/pop-up with one option per project, showing account/resource group/region).
   **NEVER silently guess** — deploying to the wrong project is the #1 rework cause.
4. Apply the same rule to model deployments: if several chat models exist and the user didn't
   specify, prefer asking; a reasonable default is the newest general-purpose GPT model, but say so.

## Step 2: Turn the user's input into agent instructions

The user's input can arrive in three forms. Handle each differently:

### Case A — Natural-language description ("I want an agent that ...")
Author the instructions yourself. A solid instruction set contains:
1. **Role & specialty** — one paragraph: who the agent is, what it produces.
2. **Runtime environment** — which tools it has (see Step 3) and how to use them;
   what data sources are / are not available and the fallback hierarchy
   (user-provided data first, then model knowledge labeled "verify before use").
3. **Working rules** — output format contract, quality bars, what to do when input is
   incomplete (ask vs. assume-and-label).
4. **Interaction model** — support a "demo mode / one shot" request that produces a complete
   result without intermediate confirmations (critical for automated testing).

Show the drafted instructions to the user for confirmation before deploying if the request
was vague.

### Case B — Raw instructions / system prompt text provided by the user
Use it verbatim as the core. Only prepend a minimal runtime preamble if the text assumes
capabilities the Foundry runtime doesn't have (e.g., web browsing) or lacks a tool usage note.

### Case C — Existing skill file (e.g., Anthropic-style SKILL.md)
Do NOT edit the skill file (keep upstream attribution intact). Prepend a **runtime wrapper**
that overrides environment assumptions:
1. **Execution path**: the agent runs with the Code Interpreter tool (Python sandbox,
   openpyxl/python-pptx available). If the skill has Office JS vs Python branches, force Python.
2. **Data sources**: declare which assumed sources (MCP servers, Bloomberg, web search) are
   unavailable; define the fallback hierarchy.
3. **Interaction model**: if the skill demands step-by-step confirmation, allow a
   "demo mode / one shot" bypass so automated runs don't stall.
4. **Artifact contract**: require a downloadable file as the final output plus a summary.

In all cases: keep total instructions well under the service limit (~256 KB; a 30 KB skill file
is fine). For skills with `references/` or `scripts/` folders, inline the essential parts or
upload them as code-interpreter files.

## Step 3: Choose tools

Match tools to what the instructions require. Available in `azure.ai.projects.models`:
- `CodeInterpreterTool()` — Python sandbox; needed for file generation (xlsx/pptx/csv/charts),
  data analysis, computation. Default choice for document/analysis agents.
- File search / web search / MCP / OpenAPI / function tools — add only when the agent's job
  needs them (see the SDK tool catalog).
- A pure Q&A / advisory agent may need **no tools** at all.

[scripts/create_agent.py](scripts/create_agent.py) takes `--tools code_interpreter` (default)
or `--tools none`; extend it for other tools when needed.

## Step 4: Create the versioned agent

**Agent naming rules (`--agent-name`):**
- **No spaces.** The agent name is used as an identifier (`name:version`) and referenced by the
  Responses API via `agent_reference`; a name with spaces fails at creation or invocation time.
- Use only letters, digits, hyphens, and underscores. Recommended style: kebab-case,
  e.g. `fsi-comps-analysis-agent`.
- Must start with a letter or digit; keep it reasonably short and descriptive.
- If the user proposes a name containing spaces or other illegal characters, convert it
  (e.g. "My Comps Agent" → `my-comps-agent`) and tell the user about the adjustment.

```bash
python scripts/create_agent.py `
  --endpoint https://<account>.services.ai.azure.com/api/projects/<project> `
  --model gpt-5.4 `
  --agent-name my-agent `
  --instructions-file instructions.md `      # assembled in Step 2 (preamble/wrapper included)
  --tools code_interpreter
```

Key call (re-running creates version 2, 3, ... under the same agent name):

```python
agent = client.agents.create_version(
    agent_name=args.agent_name,
    definition=PromptAgentDefinition(
        model=args.model,
        instructions=instructions_text,
        tools=[CodeInterpreterTool()],   # per --tools
    ),
)
```

The script writes `agent_info.json` (name/version/endpoint/model) consumed by the test script.
Verify in the new portal: **Build > Agents** should show the agent with Type=Prompt.

## Step 5: Test end-to-end

```bash
python scripts/test_agent.py --prompt-file test_prompt.txt
```

The test script:
1. Creates a conversation: `openai_client.conversations.create()`
2. Invokes the agent:
   ```python
   response = openai_client.responses.create(
       conversation=conversation.id,
       input=prompt,
       extra_body={"agent_reference": {"name": agent_name, "type": "agent_reference"}},
   )
   ```
3. Extracts generated files from `response.output[*].content[*].annotations` where
   `annotation.type == "container_file_citation"` (holds `container_id`, `file_id`, `filename`)
4. Downloads via `openai_client.containers.files.content.retrieve(file_id=..., container_id=...)`
5. Optionally verifies .xlsx artifacts with openpyxl (`data_only=False` to read formulas)

**Design the test prompt from the agent's purpose**: include all input data inline (no external
sources), invoke the demo mode (no interactive confirmation), and assert on artifact properties
or response structure — not exact text (LLM output is non-deterministic). For agents without
file output, pass `--no-files` and judge the response text against the instruction contract.

## Cleanup

```python
client.agents.delete_version(agent_name=name, agent_version=version)
```

## Known pitfalls

| Pitfall | Symptom | Fix |
|---|---|---|
| Agent name contains spaces or illegal characters | Creation fails, or `agent_reference` lookup errors at run time | Follow the naming rules in Step 4: kebab-case, letters/digits/hyphens/underscores only |
| Using SDK 1.x (`create_agent`) | Agent invisible in new portal Build > Agents | Upgrade to >=2.0.0, use `create_version`; delete the stray classic agent with `delete_agent(asst_id)` |
| Wrong project (multiple exist) | Agent lands where nobody looks | Follow Step 1 selection rules; ask the user |
| Looking for files in `attachments` or `file_path` annotations | "no file produced" despite the agent saying it made one | New API puts files in `container_file_citation` annotations |
| Instructions assume unavailable data sources (MCP/Bloomberg/web) | Agent refuses or hallucinates sources | Preamble/wrapper declares availability + fallback hierarchy |
| Instructions mandate step-by-step confirmation | Automated run stalls mid-way | Support "demo mode" one-shot execution |
| `DefaultAzureCredential` fails | 401/credential errors | `az login`, check `az account show`, confirm Foundry User role on the project |

## Files

| File | Purpose |
|---|---|
| [scripts/discover_projects.py](scripts/discover_projects.py) | Enumerate Foundry projects + chat model deployments (JSON output) |
| [scripts/create_agent.py](scripts/create_agent.py) | Create versioned Prompt Agent from an instructions file (any origin) |
| [scripts/test_agent.py](scripts/test_agent.py) | Responses API E2E test + container file download + optional xlsx verification |
