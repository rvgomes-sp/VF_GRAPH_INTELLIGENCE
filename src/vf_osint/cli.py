from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from .graph_models import ProcessGraphInput
from .models import OrganizationSeed, SourceClass
from .neo4j_export import graph_to_neo4j_batch
from .pipeline import OSINTPipeline


def _seed(path: str | Path) -> OrganizationSeed:
    return OrganizationSeed.model_validate_json(Path(path).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vf-osint")
    parser.add_argument("--database", default="data/vf_osint.db")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser("ingest-legacy")
    ingest.add_argument("--seed", required=True)
    ingest.add_argument("--input", required=True)

    crawl = subparsers.add_parser("crawl")
    crawl.add_argument("--seed", required=True)
    crawl.add_argument("--sources", required=True)

    build = subparsers.add_parser("build-dossier")
    build.add_argument("--seed", required=True)
    build.add_argument("--output", action="append", required=True)

    feedback = subparsers.add_parser("feedback")
    feedback.add_argument("--dossier-id", required=True)
    feedback.add_argument("--persona", required=True)
    feedback.add_argument("--variant", required=True)
    feedback.add_argument("--useful", action=argparse.BooleanOptionalAction, required=True)
    feedback.add_argument("--note", default="")

    run_case = subparsers.add_parser("run-case")
    run_case.add_argument("--seed", required=True)
    run_case.add_argument("--legacy")
    run_case.add_argument("--sources")
    run_case.add_argument("--crawl", action="store_true")
    run_case.add_argument("--output-dir", default="output/casos")

    cnpj_search = subparsers.add_parser("search-cnpj")
    cnpj_search.add_argument("--cnpj", required=True)
    cnpj_search.add_argument("--legal-name")
    cnpj_search.add_argument("--state")
    cnpj_search.add_argument("--deep", action="store_true")
    cnpj_search.add_argument(
        "--tavily", action=argparse.BooleanOptionalAction, default=True
    )
    cnpj_search.add_argument("--seed-url", action="append", default=[])
    cnpj_search.add_argument("--output-dir", default="output/casos")

    graph_ingest = subparsers.add_parser("ingest-process-graph")
    graph_ingest.add_argument("--input", required=True)
    graph_ingest.add_argument("--enrich-tavily", action="store_true")
    graph_ingest.add_argument("--deep", action=argparse.BooleanOptionalAction, default=True)
    graph_ingest.add_argument("--output-dir", default="output/grafos")

    neo4j_export = subparsers.add_parser("export-neo4j")
    neo4j_export.add_argument("--graph-id", required=True)
    neo4j_export.add_argument("--output", required=True)

    args = parser.parse_args(argv)
    pipeline = OSINTPipeline(args.database)
    if args.command == "ingest-legacy":
        count = pipeline.ingest_legacy(_seed(args.seed), args.input)
        print(json.dumps({"claims_ingested": count}, ensure_ascii=False))
        return 0
    if args.command == "crawl":
        source_payload = json.loads(Path(args.sources).read_text(encoding="utf-8"))
        sources = [(item["url"], SourceClass(item["source_class"])) for item in source_payload]
        print(json.dumps(pipeline.crawl(_seed(args.seed), sources), ensure_ascii=False, indent=2))
        return 0
    if args.command == "build-dossier":
        dossier = pipeline.build_dossier(_seed(args.seed))
        for output in args.output:
            pipeline.write_dossier(dossier, output)
        print(
            json.dumps(
                {
                    "dossier_id": dossier.dossier_id,
                    "outputs": [str(Path(output).resolve()) for output in args.output],
                }
            )
        )
        return 0
    if args.command == "feedback":
        pipeline.learner.record_approach_feedback(
            args.dossier_id, args.persona, args.variant, args.useful, args.note
        )
        print(json.dumps({"status": "recorded"}))
        return 0
    if args.command == "run-case":
        case_seed = _seed(args.seed)
        ingested = pipeline.ingest_legacy(case_seed, args.legacy) if args.legacy else 0
        collection = None
        if args.crawl:
            if not args.sources:
                parser.error("run-case --crawl exige --sources")
            source_payload = json.loads(Path(args.sources).read_text(encoding="utf-8"))
            sources = [(item["url"], SourceClass(item["source_class"])) for item in source_payload]
            collection = pipeline.crawl(case_seed, sources)
        dossier = pipeline.build_dossier(case_seed)
        slug = re.sub(r"[^0-9A-Za-z]+", "_", case_seed.legal_name).strip("_").lower()[:48]
        output_dir = Path(args.output_dir)
        outputs = [
            output_dir / f"{slug}_dossie.md",
            output_dir / f"{slug}_dossie.json",
            output_dir / f"{slug}_dossie.pdf",
        ]
        for output in outputs:
            pipeline.write_dossier(dossier, output)
        print(
            json.dumps(
                {
                    "dossier_id": dossier.dossier_id,
                    "claims_ingested": ingested,
                    "collection": collection,
                    "classification": dossier.decision["classification"],
                    "sendable": dossier.decision["sendable"],
                    "outputs": [str(output.resolve()) for output in outputs],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "search-cnpj":
        dossier, collection = pipeline.investigate_cnpj(
            args.cnpj,
            legal_name=args.legal_name,
            state=args.state,
            deep=args.deep,
            use_tavily=args.tavily,
            seed_urls=args.seed_url,
        )
        slug = re.sub(r"\D", "", args.cnpj)
        output_dir = Path(args.output_dir)
        outputs = [
            output_dir / f"{slug}_dossie.md",
            output_dir / f"{slug}_dossie.json",
            output_dir / f"{slug}_dossie.pdf",
        ]
        for output in outputs:
            pipeline.write_dossier(dossier, output)
        print(
            json.dumps(
                {
                    "dossier_id": dossier.dossier_id,
                    "collection": collection,
                    "classification": dossier.decision["classification"],
                    "outputs": [str(output.resolve()) for output in outputs],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "ingest-process-graph":
        record = ProcessGraphInput.model_validate_json(
            Path(args.input).read_text(encoding="utf-8")
        )
        graph, dossier, collection = pipeline.ingest_process_graph(
            record,
            enrich_tavily=args.enrich_tavily,
            deep=args.deep,
        )
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        graph_path = output_dir / f"grafo_{graph.graph_id}.json"
        neo4j_path = output_dir / f"grafo_{graph.graph_id}_neo4j.json"
        graph_path.write_text(graph.model_dump_json(indent=2), encoding="utf-8")
        neo4j_path.write_text(
            json.dumps(graph_to_neo4j_batch(graph), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "graph_id": graph.graph_id,
                    "mode": "PROCESS_FIRST_GRAPH",
                    "classification": graph.opportunity_classification,
                    "score": graph.opportunity_score,
                    "dossier_id": dossier.dossier_id if dossier else None,
                    "collection": collection,
                    "outputs": [str(graph_path.resolve()), str(neo4j_path.resolve())],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "export-neo4j":
        graph = pipeline.repository.get_graph(args.graph_id)
        if not graph:
            parser.error("Grafo não encontrado")
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(graph_to_neo4j_batch(graph), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps({"graph_id": graph.graph_id, "output": str(output.resolve())}))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
