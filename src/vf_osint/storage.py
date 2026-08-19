from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .graph_models import GraphFeedbackInput, GraphOpportunity
from .models import Claim, Dossier, PublicDocument


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS documents (
    content_hash TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    source_class TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    published_at TEXT,
    http_status INTEGER NOT NULL,
    content_type TEXT NOT NULL,
    text TEXT NOT NULL,
    links_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claims (
    claim_id TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL,
    predicate TEXT NOT NULL,
    value_json TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence REAL NOT NULL,
    source_json TEXT NOT NULL,
    excerpt TEXT NOT NULL,
    observed_event_at TEXT,
    rationale TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_claim_subject ON claims(subject_id, predicate);

CREATE TABLE IF NOT EXISTS dossiers (
    dossier_id TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dossier_id TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    label TEXT NOT NULL,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS learning_stats (
    key TEXT PRIMARY KEY,
    successes INTEGER NOT NULL DEFAULT 0,
    failures INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS graph_snapshots (
    graph_id TEXT PRIMARY KEY,
    parent_graph_id TEXT,
    generated_at TEXT NOT NULL,
    origin_status TEXT NOT NULL,
    opportunity_score REAL NOT NULL,
    opportunity_classification TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS graph_process_origins (
    graph_id TEXT NOT NULL,
    process_node_id TEXT NOT NULL,
    PRIMARY KEY (graph_id, process_node_id),
    FOREIGN KEY (graph_id) REFERENCES graph_snapshots(graph_id)
);
CREATE INDEX IF NOT EXISTS idx_graph_process ON graph_process_origins(process_node_id);

CREATE TABLE IF NOT EXISTS graph_nodes (
    graph_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    node_type TEXT NOT NULL,
    properties_json TEXT NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    PRIMARY KEY (graph_id, node_id),
    FOREIGN KEY (graph_id) REFERENCES graph_snapshots(graph_id)
);

CREATE TABLE IF NOT EXISTS graph_relationships (
    graph_id TEXT NOT NULL,
    relationship_id TEXT NOT NULL,
    from_node TEXT NOT NULL,
    to_node TEXT NOT NULL,
    relationship_type TEXT NOT NULL,
    evidence_node TEXT NOT NULL,
    source_url TEXT NOT NULL,
    evidence_excerpt TEXT NOT NULL,
    observed_at TEXT,
    score REAL NOT NULL,
    classification TEXT NOT NULL,
    justification TEXT NOT NULL,
    PRIMARY KEY (graph_id, relationship_id),
    FOREIGN KEY (graph_id) REFERENCES graph_snapshots(graph_id)
);

CREATE TABLE IF NOT EXISTS graph_feedback_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_graph_id TEXT NOT NULL,
    resulting_graph_id TEXT NOT NULL,
    feedback_type TEXT NOT NULL,
    target_node_id TEXT NOT NULL,
    value TEXT NOT NULL,
    operator TEXT NOT NULL,
    note TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);
"""


class Repository:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def save_document(self, document: PublicDocument) -> None:
        with self.connection() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    document.content_hash,
                    document.url,
                    document.title,
                    document.source_class.value,
                    document.captured_at.isoformat(),
                    document.published_at.isoformat() if document.published_at else None,
                    document.http_status,
                    document.content_type,
                    document.text,
                    json.dumps(document.links, ensure_ascii=False),
                ),
            )

    def save_claim(self, claim: Claim) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO claims (
                    claim_id, subject_id, predicate, value_json, status, confidence,
                    source_json, excerpt, observed_event_at, rationale, tags_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(claim_id) DO UPDATE SET
                    status=excluded.status, confidence=excluded.confidence,
                    rationale=excluded.rationale, tags_json=excluded.tags_json
                """,
                (
                    claim.claim_id,
                    claim.subject_id,
                    claim.predicate,
                    json.dumps(claim.value, ensure_ascii=False),
                    claim.status.value,
                    claim.confidence,
                    claim.source.model_dump_json(),
                    claim.excerpt,
                    claim.observed_event_at.isoformat() if claim.observed_event_at else None,
                    claim.rationale,
                    json.dumps(claim.tags, ensure_ascii=False),
                ),
            )

    def claims_for(self, subject_id: str) -> list[Claim]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM claims WHERE subject_id=? ORDER BY predicate, confidence DESC",
                (subject_id,),
            ).fetchall()
        claims = []
        for row in rows:
            source = json.loads(row["source_json"])
            claims.append(
                Claim.model_validate(
                    {
                        "claim_id": row["claim_id"],
                        "subject_id": row["subject_id"],
                        "predicate": row["predicate"],
                        "value": json.loads(row["value_json"]),
                        "status": row["status"],
                        "confidence": row["confidence"],
                        "source": source,
                        "excerpt": row["excerpt"],
                        "observed_event_at": row["observed_event_at"],
                        "rationale": row["rationale"],
                        "tags": json.loads(row["tags_json"]),
                    }
                )
            )
        return claims

    def save_dossier(self, subject_id: str, dossier: Dossier) -> None:
        with self.connection() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO dossiers VALUES (?, ?, ?, ?)""",
                (
                    dossier.dossier_id,
                    subject_id,
                    dossier.generated_at.isoformat(),
                    dossier.model_dump_json(),
                ),
            )

    def get_dossier(self, dossier_id: str) -> Dossier | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM dossiers WHERE dossier_id=?", (dossier_id,)
            ).fetchone()
        return Dossier.model_validate_json(row[0]) if row else None

    def record_feedback(
        self, dossier_id: str, target_type: str, target_id: str, label: str, note: str = ""
    ) -> None:
        if label not in {"useful", "not_useful", "confirmed", "rejected"}:
            raise ValueError("Rotulo de feedback invalido")
        success = label in {"useful", "confirmed"}
        key = f"{target_type}:{target_id}"
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO feedback(dossier_id,target_type,target_id,label,note) VALUES(?,?,?,?,?)",
                (dossier_id, target_type, target_id, label, note),
            )
            connection.execute(
                """
                INSERT INTO learning_stats(key,successes,failures) VALUES(?,?,?)
                ON CONFLICT(key) DO UPDATE SET
                    successes=successes+excluded.successes,
                    failures=failures+excluded.failures,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (key, 1 if success else 0, 0 if success else 1),
            )

    def learned_probability(self, key: str) -> float:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT successes,failures FROM learning_stats WHERE key=?", (key,)
            ).fetchone()
        if not row:
            return 0.5
        return (row["successes"] + 1) / (row["successes"] + row["failures"] + 2)

    def save_graph(self, graph: GraphOpportunity) -> None:
        """Persiste snapshot imutável e projeções relacionais para auditoria local."""
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO graph_snapshots(
                    graph_id,parent_graph_id,generated_at,origin_status,
                    opportunity_score,opportunity_classification,payload_json
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    graph.graph_id,
                    graph.parent_graph_id,
                    graph.generated_at.isoformat(),
                    graph.origin_status,
                    graph.opportunity_score,
                    graph.opportunity_classification,
                    graph.model_dump_json(),
                ),
            )
            connection.executemany(
                "INSERT INTO graph_process_origins(graph_id,process_node_id) VALUES(?,?)",
                [(graph.graph_id, process_id) for process_id in graph.origin_process_ids],
            )
            connection.executemany(
                """
                INSERT INTO graph_nodes(
                    graph_id,node_id,node_type,properties_json,valid_from,valid_to
                ) VALUES(?,?,?,?,?,?)
                """,
                [
                    (
                        graph.graph_id,
                        node.node_id,
                        node.node_type.value,
                        json.dumps(node.properties, ensure_ascii=False),
                        node.valid_from.isoformat() if node.valid_from else None,
                        node.valid_to.isoformat() if node.valid_to else None,
                    )
                    for node in graph.nodes
                ],
            )
            connection.executemany(
                """
                INSERT INTO graph_relationships(
                    graph_id,relationship_id,from_node,to_node,relationship_type,
                    evidence_node,source_url,evidence_excerpt,observed_at,score,
                    classification,justification
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        graph.graph_id,
                        relationship.relationship_id,
                        relationship.from_node,
                        relationship.to_node,
                        relationship.relationship_type.value,
                        relationship.evidence_node,
                        relationship.source_url,
                        relationship.evidence_excerpt,
                        relationship.observed_at.isoformat() if relationship.observed_at else None,
                        relationship.score,
                        relationship.classification.value,
                        relationship.justification,
                    )
                    for relationship in graph.relationships
                ],
            )

    def get_graph(self, graph_id: str) -> GraphOpportunity | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM graph_snapshots WHERE graph_id=?", (graph_id,)
            ).fetchone()
        return GraphOpportunity.model_validate_json(row[0]) if row else None

    def graphs_for_process(self, process_node_id: str) -> list[GraphOpportunity]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT s.payload_json
                FROM graph_snapshots s
                JOIN graph_process_origins o ON o.graph_id=s.graph_id
                WHERE o.process_node_id=?
                ORDER BY s.generated_at DESC
                """,
                (process_node_id,),
            ).fetchall()
        return [GraphOpportunity.model_validate_json(row[0]) for row in rows]

    def record_graph_feedback(
        self,
        source_graph_id: str,
        resulting_graph_id: str,
        feedback: GraphFeedbackInput,
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO graph_feedback_events(
                    source_graph_id,resulting_graph_id,feedback_type,target_node_id,
                    value,operator,note,occurred_at
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    source_graph_id,
                    resulting_graph_id,
                    feedback.feedback_type,
                    feedback.target_node_id,
                    feedback.value,
                    feedback.operator,
                    feedback.note,
                    feedback.occurred_at.isoformat(),
                ),
            )
