#!/usr/bin/env python3
"""Refuse to deploy a configuration that is not finished.

Three settings in this system deliberately refuse to act while they are zero:
the monthly budget, the tier 3 spend cap, and the model rate table. That is
correct behaviour, but discovering it after `terraform apply` has built
everything means a deployed assistant that answers every request with "no
budget is set". This checks first.

Nothing here talks to AWS. It reads the tfvars file and says what is missing.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PLACEHOLDER = re.compile(r"PUT-|CHANGE-?ME|xxx|TODO|<.*>", re.I)


def parse(path: Path) -> dict[str, str]:
    """A deliberately small HCL reader: key = value, one per line."""
    values: dict[str, str] = {}
    text = path.read_text()

    # Heredocs first, so their bodies are not mistaken for assignments.
    for key, _marker, body in re.findall(
        r"^(\w+)\s*=\s*<<-?(\w+)\n(.*?)\n\2", text, re.M | re.S
    ):
        values[key] = body
    text = re.sub(r"^(\w+)\s*=\s*<<-?(\w+)\n.*?\n\2", "", text, flags=re.M | re.S)

    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"')
    return values


REQUIRED = {
    "owner_number": "Andrew's mobile in E.164. Rule 4: the only number answered.",
    "twilio_from_number": "The Twilio number Errand texts from.",
    "default_model_id": "Claude Haiku 4.5. Look it up in the Bedrock console.",
    "escalation_model_id": "Claude Sonnet 5. Look it up in the Bedrock console.",
    "reader_model_id": "The quarantined reader's model. Look it up too.",
}

MUST_BE_POSITIVE = {
    "monthly_budget_usd": "Zero refuses every model call (hard rule 8).",
    "tier3_cap_cents": "Zero refuses every purchase.",
}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: preflight.py <terraform.tfvars>", file=sys.stderr)
        return 2

    path = Path(argv[1])
    if not path.exists():
        print(f"FAIL {path} does not exist.")
        print(f"     cp {path.with_suffix('.tfvars.example').name} {path.name} and fill it in.")
        return 1

    values = parse(path)
    problems: list[str] = []

    for key, why in REQUIRED.items():
        value = values.get(key, "").strip()
        if not value:
            problems.append(f"{key} is not set. {why}")
        elif PLACEHOLDER.search(value):
            problems.append(f"{key} is still a placeholder ({value!r}). {why}")

    for key, why in MUST_BE_POSITIVE.items():
        raw = values.get(key, "0").strip()
        try:
            if float(raw) <= 0:
                problems.append(f"{key} is {raw}. {why}")
        except ValueError:
            problems.append(f"{key} is not a number ({raw!r}). {why}")

    rates = values.get("model_rates_json", "").strip()
    if not rates or rates in ("{}", '"{}"'):
        problems.append(
            "model_rates_json is empty. The budget cannot price a call without "
            "it, so every call is refused. Take the numbers from the Bedrock "
            "pricing page."
        )
    else:
        for model_key in ("default_model_id", "escalation_model_id"):
            model = values.get(model_key, "").strip()
            if model and not PLACEHOLDER.search(model) and model not in rates:
                problems.append(
                    f"{model_key} ({model}) has no entry in model_rates_json, "
                    f"so any call routed to it is refused as unpriced."
                )

    if problems:
        print(f"FAIL {path} is not ready to deploy:\n")
        for problem in problems:
            print(f"  - {problem}")
        print("\nNothing was deployed.")
        return 1

    print(f"OK   {path} looks complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
