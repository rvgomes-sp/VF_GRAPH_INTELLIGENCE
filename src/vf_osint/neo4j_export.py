from __future__ import annotations

from typing import Any

from .graph_models import GraphNodeType, GraphOpportunity


def graph_to_neo4j_batch(graph: GraphOpportunity) -> dict[str, Any]:
    """Gera Cypher parametrizado; não exige driver nem credenciais Neo4j."""
    constraints = [
        {
            "query": (
                f"CREATE CONSTRAINT `{node_type.value.lower()}_node_id` IF NOT EXISTS "
                f"FOR (n:`{node_type.value}`) REQUIRE n.node_id IS UNIQUE"
            ),
            "parameters": {},
        }
        for node_type in GraphNodeType
    ]
    nodes = []
    for node in graph.nodes:
        properties = dict(node.properties)
        properties.update(
            {
                "graph_id": graph.graph_id,
                "valid_from": node.valid_from.isoformat() if node.valid_from else None,
                "valid_to": node.valid_to.isoformat() if node.valid_to else None,
                "created_at": node.created_at.isoformat(),
            }
        )
        nodes.append(
            {
                "query": (
                    f"MERGE (n:`{node.node_type.value}` {{node_id: $node_id}}) "
                    "SET n += $properties"
                ),
                "parameters": {"node_id": node.node_id, "properties": properties},
            }
        )
    relationships = []
    for relationship in graph.relationships:
        relationships.append(
            {
                "query": (
                    "MATCH (a {node_id: $from_node}), (b {node_id: $to_node}) "
                    f"MERGE (a)-[r:`{relationship.relationship_type.value}` "
                    "{relationship_id: $relationship_id}]->(b) SET r += $properties"
                ),
                "parameters": {
                    "from_node": relationship.from_node,
                    "to_node": relationship.to_node,
                    "relationship_id": relationship.relationship_id,
                    "properties": {
                        "graph_id": graph.graph_id,
                        "evidence_node": relationship.evidence_node,
                        "source_url": relationship.source_url,
                        "evidence_excerpt": relationship.evidence_excerpt,
                        "observed_at": relationship.observed_at.isoformat() if relationship.observed_at else None,
                        "score": relationship.score,
                        "classification": relationship.classification.value,
                        "justification": relationship.justification,
                        "created_at": relationship.created_at.isoformat(),
                    },
                },
            }
        )
    return {
        "graph_id": graph.graph_id,
        "database_role": "VERDADE_RELACIONAL",
        "statements": constraints + nodes + relationships,
        "counts": {
            "constraints": len(constraints),
            "nodes": len(nodes),
            "relationships": len(relationships),
        },
        "execution_note": (
            "Execute em transação usando o driver oficial Neo4j. Credenciais e conexão "
            "não fazem parte deste artefato."
        ),
    }
