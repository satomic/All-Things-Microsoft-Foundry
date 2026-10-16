"""Triage one support ticket with three decision models: Microsoft Decision-1, TypeSafe Jev and OpenAI Decisions.

One call per model asks three questions about the same ticket:
  team          choice    which team should handle it
  urgency       score     Low / Normal / High / Critical
  needs_review  yes / no  should a person review it before any automated reply

Each response is normalised to the same shape, then turned into an action.

Environment variables (set only the ones for the models you want to call):
  FOUNDRY_ENDPOINT     https://<resource>.services.ai.azure.com  (or *.openai.azure.com / *.cognitiveservices.azure.com)
  FOUNDRY_API_KEY      key of that Foundry resource
  FOUNDRY_DEPLOYMENT   deployment name of Microsoft-Decision-1, e.g. msft-decision-1
  TYPESAFE_API_KEY     TypeSafe key
  OPENAI_API_KEY       OpenAI key

Usage: python3 support_ticket.py
"""
import json
import os
import time
import urllib.error
import urllib.request

TICKET = ("Checkout has been failing for all of our EU customers since 09:00 with error 500. "
          "This is the third time I'm writing about it. Please fix it now.")
TEAMS = {
    "billing": "Charges, invoices, refunds and subscription payments.",
    "technical": "Errors, bugs, outages and integration failures.",
    "account": "Sign-in, passwords and account access.",
}
LEVELS = ["Low", "Normal", "High", "Critical"]          # lowest first
Q_TEAM = "Which team should handle this ticket?"
Q_URGENCY = "How urgent is this ticket?"
Q_REVIEW = "Should a human agent review this ticket before any automated reply is sent?"


def post(url, headers, body):
    req = urllib.request.Request(url, json.dumps(body).encode(), {**headers, "Content-Type": "application/json"})
    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    return data, round((time.perf_counter() - start) * 1000)


# Decision-1 and Jev share the System One request format: questions is an object keyed by name.
SYSTEM_ONE_QUESTIONS = {
    "team": {"type": "choice", "instructions": Q_TEAM, "criteria": TEAMS},
    "urgency": {"type": "score", "instructions": Q_URGENCY, "criteria": LEVELS},
    "needs_review": {"type": "noul", "instructions": Q_REVIEW},
}


def call_decision1():
    url = os.environ["FOUNDRY_ENDPOINT"].rstrip("/") + "/providers/microsoft/v1/systemone"
    body = {"model": os.environ["FOUNDRY_DEPLOYMENT"],               # the deployment name, not the model name
            "state": {"ticket": TICKET}, "questions": SYSTEM_ONE_QUESTIONS}
    return post(url, {"api-key": os.environ["FOUNDRY_API_KEY"]}, body)


def call_jev():
    body = {"model": "jev-latest", "state": {"ticket": TICKET}, "questions": SYSTEM_ONE_QUESTIONS}
    return post("https://api.typesafe.ai/v1/systemone", {"Authorization": "Bearer " + os.environ["TYPESAFE_API_KEY"]}, body)


def call_openai():
    # OpenAI Decisions: context goes in `input`, questions are a list and each carries its own name.
    body = {
        "model": "gpt-6-luna",
        "input": TICKET,
        "questions": [
            {"type": "choice", "name": "team", "instructions": Q_TEAM,
             "choices": [{"value": k, "description": v} for k, v in TEAMS.items()]},
            {"type": "score", "name": "urgency", "instructions": Q_URGENCY,
             "levels": [{"label": label} for label in LEVELS]},
            {"type": "predicate", "name": "needs_review", "instructions": Q_REVIEW},
        ],
    }
    return post("https://api.openai.com/v1/decisions", {"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"]}, body)


def normalise_system_one(data):
    a = data["answers"]
    return {"team": a["team"]["choice"], "team_p": a["team"]["probabilities"][a["team"]["choice"]],
            "urgency": a["urgency"]["score"], "review_p": a["needs_review"]["noul"]}


def normalise_openai(data):
    a = {x["name"]: x for x in data["answers"]}
    if any(x["type"] == "refusal" for x in data["answers"]):
        raise RuntimeError("the model refused one of the questions")
    team = a["team"]["choice"]
    return {"team": team, "team_p": next(p["probability"] for p in a["team"]["probabilities"] if p["value"] == team),
            "urgency": a["urgency"]["score"], "review_p": a["needs_review"]["probability"]}


def act(d):
    """Illustrative thresholds: set your own on one labelled sample and confirm them on another."""
    if d["team_p"] < 0.6:
        return "send to the triage queue (team unclear)"
    steps = [f"route to {d['team']}"]
    if d["urgency"] >= 2.5:
        steps.append("mark as P1")
    if d["review_p"] >= 0.5:
        steps.append("hold the automated reply for a person")
    return ", ".join(steps)


if __name__ == "__main__":
    for name, call, norm, keys in [
        ("Microsoft Decision-1", call_decision1, normalise_system_one, ["FOUNDRY_ENDPOINT", "FOUNDRY_API_KEY", "FOUNDRY_DEPLOYMENT"]),
        ("TypeSafe Jev", call_jev, normalise_system_one, ["TYPESAFE_API_KEY"]),
        ("OpenAI Decisions", call_openai, normalise_openai, ["OPENAI_API_KEY"]),
    ]:
        if not all(os.environ.get(k) for k in keys):
            print(f"{name}: skipped, set {', '.join(keys)}")
            continue
        try:
            data, ms = call()
        except urllib.error.HTTPError as e:
            print(f"{name}: HTTP {e.code} {e.read()[:300]!r}")
            continue
        d = norm(data)
        print(f"{name} ({data.get('model')}, {ms} ms, {data['usage']['input_tokens']} input tokens)")
        print(f"  team={d['team']} p={d['team_p']:.3f}  urgency={d['urgency']:.2f}  needs_review p={d['review_p']:.2f}")
        print(f"  -> {act(d)}")
