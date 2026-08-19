"""Pesquisa exploratoria com Tavily sem despejar resultados brutos no contexto.

Este utilitario e usado apenas durante o desenvolvimento para descobrir fontes.
O motor OSINT de producao nao depende da API da Tavily.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--tvly", required=True)
    parser.add_argument("--query", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-results", type=int, default=10)
    parser.add_argument("--allowed-domain", action="append", default=[])
    args = parser.parse_args()

    combined: list[dict] = []
    for query in args.query:
        command = [
            args.tvly,
            "search",
            query,
            "--depth",
            "advanced",
            "--max-results",
            str(args.max_results),
            "--country",
            "brazil",
            "--json",
        ]
        child_env = os.environ.copy()
        child_env["PYTHONIOENCODING"] = "utf-8"
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            env=child_env,
        )
        if completed.returncode:
            error_bytes = completed.stderr or completed.stdout or b""
            diagnostic = " ".join(error_bytes.decode("utf-8", errors="replace").split())[:2000]
            raise RuntimeError(f"Tavily falhou com codigo {completed.returncode}: {diagnostic}")
        try:
            decoded = completed.stdout.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            decoded = completed.stdout.decode("cp1252", errors="replace")
        payload = json.loads(decoded)
        for result in payload.get("results", []):
            result["_query"] = query
            combined.append(result)

    unique: dict[str, dict] = {}
    for result in combined:
        url = result.get("url", "")
        if url and url not in unique:
            unique[url] = result

    ranked = sorted(unique.values(), key=lambda item: item.get("score", 0), reverse=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(ranked, ensure_ascii=False, indent=2), encoding="utf-8")

    allowed = tuple(domain.lower() for domain in args.allowed_domain)
    selected = []
    for result in ranked:
        host = (urlparse(result.get("url", "")).hostname or "").lower()
        if allowed and not any(host == domain or host.endswith(f".{domain}") for domain in allowed):
            continue
        selected.append(result)

    print(f"{len(ranked)} resultados unicos salvos; {len(selected)} fontes selecionadas.\n")
    for index, result in enumerate(selected[:15], start=1):
        snippet = " ".join((result.get("content") or "").split())[:420]
        print(f"[{index}] [{result.get('score', 0):.2f}] {result.get('title', '')[:110]}")
        print(result.get("url", ""))
        if snippet:
            print(snippet)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
