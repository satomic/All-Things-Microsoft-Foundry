"""
Enumerate Microsoft Foundry projects (and their chat model deployments) visible to the
signed-in Azure CLI identity. Prints a JSON array; use it to let the user pick a project
when more than one exists.

Usage:
    python discover_projects.py                    # current az subscription only
    python discover_projects.py --all-subscriptions

Dependencies: Azure CLI (logged in via `az login`). No pip packages required.
"""
import argparse
import json
import subprocess
import sys

# Non-chat deployments to exclude from the model suggestion list
NON_CHAT_HINTS = ("embedding", "tts", "whisper", "dall-e", "image", "flux", "rerank", "sora")


def az(*args: str):
    result = subprocess.run(
        ["az", *args, "--output", "json"], capture_output=True, text=True, shell=True
    )
    if result.returncode != 0:
        print(f"az {' '.join(args)} failed: {result.stderr.strip()}", file=sys.stderr)
        return None
    return json.loads(result.stdout) if result.stdout.strip() else None


def discover_subscription(sub_id: str) -> list[dict]:
    entries = []
    accounts = az("cognitiveservices", "account", "list", "--subscription", sub_id) or []
    for acct in accounts:
        if acct.get("kind") != "AIServices":
            continue
        name, rg = acct["name"], acct["resourceGroup"]
        projects = az(
            "rest", "--method", "GET", "--url",
            f"https://management.azure.com/subscriptions/{sub_id}/resourceGroups/{rg}"
            f"/providers/Microsoft.CognitiveServices/accounts/{name}/projects?api-version=2025-06-01",
        )
        if not projects or not projects.get("value"):
            continue

        deployments = az(
            "cognitiveservices", "account", "deployment", "list",
            "-n", name, "-g", rg, "--subscription", sub_id,
        ) or []
        chat_models = [
            d["name"] for d in deployments
            if not any(h in d["name"].lower() for h in NON_CHAT_HINTS)
        ]

        for proj in projects["value"]:
            endpoints = proj.get("properties", {}).get("endpoints", {})
            entries.append(
                {
                    "subscription": sub_id,
                    "account": name,
                    "resource_group": rg,
                    "location": acct.get("location"),
                    "project": proj["name"].split("/")[-1],
                    "endpoint": endpoints.get("AI Foundry API"),
                    "is_default": proj.get("properties", {}).get("isDefault", False),
                    "chat_models": chat_models,
                }
            )
    return entries


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all-subscriptions", action="store_true")
    args = parser.parse_args()

    if args.all_subscriptions:
        subs = [s["id"] for s in (az("account", "list") or [])]
    else:
        current = az("account", "show")
        if not current:
            sys.exit("Not logged in. Run `az login` first.")
        subs = [current["id"]]

    all_entries = []
    for sub in subs:
        all_entries.extend(discover_subscription(sub))

    print(json.dumps(all_entries, indent=2))
    if not all_entries:
        print("No Foundry projects found.", file=sys.stderr)
    elif len(all_entries) > 1:
        print(
            f"\n{len(all_entries)} projects found. If the user did not specify one, "
            "ASK THE USER to choose before deploying.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
