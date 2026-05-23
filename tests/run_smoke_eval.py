"""Run the smoke eval cases against the agent and print a scoreboard."""
import asyncio
import json
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv()  # load ANTHROPIC_API_KEY from .env at project root

from src.agent import run_agent_single_turn  # noqa: E402

_CASES = Path(__file__).parent / "eval_cases" / "smoke.jsonl"
console = Console()


def _extract_fields(response_text: str) -> dict:
    """Pull product/version/target/sdks out of the assistant's final reply or generated command."""
    out = {}
    m = re.search(r"--product\s+(\S+)", response_text)
    if m: out["product"] = m.group(1)
    m = re.search(r"--version\s+(\S+)", response_text)
    if m: out["version"] = m.group(1)
    m = re.search(r"--target\s+(\S+)", response_text)
    if m: out["target"] = m.group(1)
    out["additional_sdks"] = re.findall(r"--additional-sdk\s+'([^']+)'", response_text)
    return out


def _score(actual: dict, expected: dict) -> tuple[int, int, list[str]]:
    score = 0
    total = 0
    misses = []
    if "product" in expected:
        total += 1
        if actual.get("product") == expected["product"]: score += 1
        else: misses.append(f"product: want {expected['product']}, got {actual.get('product')}")
    if "target" in expected:
        total += 1
        if actual.get("target") == expected["target"]: score += 1
        else: misses.append(f"target: want {expected['target']}, got {actual.get('target')}")
    if "version_starts_with" in expected:
        total += 1
        v = actual.get("version", "")
        if v.startswith(expected["version_starts_with"]): score += 1
        else: misses.append(f"version prefix: want {expected['version_starts_with']}, got {v}")
    if "additional_sdks_contains" in expected:
        total += 1
        if expected["additional_sdks_contains"] in actual.get("additional_sdks", []): score += 1
        else: misses.append(f"sdks: want {expected['additional_sdks_contains']} in {actual.get('additional_sdks')}")
    return score, total, misses


def main() -> None:
    cases = [json.loads(line) for line in _CASES.read_text(encoding="utf-8").splitlines() if line.strip()]
    table = Table(title="Smoke eval")
    table.add_column("#"); table.add_column("Input"); table.add_column("Score"); table.add_column("Misses")
    overall_correct = 0
    overall_total = 0
    responses = []
    for i, c in enumerate(cases, 1):
        console.print(f"[dim]running case {i}…[/dim]")
        response = asyncio.run(run_agent_single_turn(c["input"]))
        responses.append((i, c["input"], response))
        actual = _extract_fields(response)
        score, total, misses = _score(actual, c["expected"])
        overall_correct += score
        overall_total += total
        table.add_row(str(i), c["input"][:40], f"{score}/{total}", "; ".join(misses) or "[green]OK[/green]")
    console.print(table)
    pct = 100.0 * overall_correct / overall_total if overall_total else 0
    console.print(f"\n[bold]Overall:[/bold] {overall_correct}/{overall_total} = {pct:.1f}%")

    # Write responses to file for debugging
    with open("test_responses.txt", "w", encoding="utf-8") as f:
        for idx, inp, resp in responses:
            f.write(f"\n{'='*80}\nCase {idx}: {inp}\n{'='*80}\n{resp}\n")
    console.print("[dim]Responses written to test_responses.txt[/dim]")

    if pct < 80:
        sys.exit(1)


if __name__ == "__main__":
    main()
