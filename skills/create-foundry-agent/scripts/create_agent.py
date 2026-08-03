"""
Create a versioned Prompt Agent on Microsoft Foundry (new API) from an instructions file.

The instructions file can come from any source: authored from a natural-language description,
a user-provided system prompt, or an existing skill file with a runtime wrapper prepended
(see SKILL.md Step 2 for how to assemble it).

Usage:
    python create_agent.py \
        --endpoint https://<account>.services.ai.azure.com/api/projects/<project> \
        --model gpt-5.4 \
        --agent-name my-agent \
        --instructions-file instructions.md \
        [--tools code_interpreter|none] \
        [--description "..."]

    Arguments fall back to env vars: FOUNDRY_PROJECT_ENDPOINT, FOUNDRY_MODEL_NAME,
    FOUNDRY_AGENT_NAME, FOUNDRY_INSTRUCTIONS_FILE.

Dependencies:
    pip install "azure-ai-projects>=2.0.0" azure-identity
    az login (DefaultAzureCredential uses Azure CLI credentials)
    Signed-in identity needs the "Foundry User" role (or higher) on the project.

Notes:
    - Re-running creates a new version (v2, v3, ...) under the same agent name.
"""
import argparse
import json
import os
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import CodeInterpreterTool, PromptAgentDefinition
from azure.identity import DefaultAzureCredential


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=os.environ.get("FOUNDRY_PROJECT_ENDPOINT"))
    parser.add_argument("--model", default=os.environ.get("FOUNDRY_MODEL_NAME"))
    parser.add_argument("--agent-name", default=os.environ.get("FOUNDRY_AGENT_NAME"))
    parser.add_argument(
        "--instructions-file", default=os.environ.get("FOUNDRY_INSTRUCTIONS_FILE")
    )
    parser.add_argument("--tools", choices=["code_interpreter", "none"], default="code_interpreter")
    parser.add_argument("--description", default=None)
    args = parser.parse_args()

    missing = [n for n in ("endpoint", "model", "agent_name", "instructions_file") if not getattr(args, n)]
    if missing:
        parser.error(f"missing required arguments: {', '.join('--' + m.replace('_', '-') for m in missing)}")
    if not Path(args.instructions_file).is_file():
        parser.error(f"instructions file not found: {args.instructions_file}")
    return args


def main() -> None:
    args = parse_args()
    instructions = Path(args.instructions_file).read_text(encoding="utf-8")
    tools = [CodeInterpreterTool()] if args.tools == "code_interpreter" else []

    with (
        DefaultAzureCredential() as credential,
        AIProjectClient(endpoint=args.endpoint, credential=credential) as client,
    ):
        agent = client.agents.create_version(
            agent_name=args.agent_name,
            definition=PromptAgentDefinition(
                model=args.model,
                instructions=instructions,
                tools=tools,
            ),
            description=args.description
            or f"Prompt agent generated from {Path(args.instructions_file).name}",
        )

        print(f"Agent created: name={agent.name} version={agent.version} id={agent.id}")
        print(f"Instructions length: {len(instructions)} chars; tools: {args.tools}")

        out = Path(__file__).parent / "agent_info.json"
        out.write_text(
            json.dumps(
                {
                    "agent_name": agent.name,
                    "agent_version": str(agent.version),
                    "agent_id": agent.id,
                    "endpoint": args.endpoint,
                    "model": args.model,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"Saved to {out}")


if __name__ == "__main__":
    main()
