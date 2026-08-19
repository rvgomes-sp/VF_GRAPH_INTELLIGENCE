from fastapi.testclient import TestClient

from vf_osint.api import create_app


def test_health_endpoint(tmp_path):
    client = TestClient(create_app(tmp_path / "api.db"))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_search_page_has_cnpj_field(tmp_path):
    client = TestClient(create_app(tmp_path / "api.db"))
    response = client.get("/")
    assert response.status_code == 200
    assert 'id="cnpj"' in response.text
    assert "Abrir relatório PDF" in response.text


def test_cnpj_case_generates_downloadable_pdf_without_external_provider(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = TestClient(create_app(tmp_path / "api.db"))
    response = client.post(
        "/api/search/cnpj",
        json={
            "cnpj": "12.345.678/0001-90",
            "legal_name": "EMPRESA TESTE LTDA",
            "deep": False,
            "use_tavily": False,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["dossier"]["organization"]["cnpj"] == "12345678000190"
    pdf = client.get(payload["report_pdf"])
    assert pdf.status_code == 200
    assert pdf.headers["content-type"].startswith("application/pdf")
    assert pdf.content.startswith(b"%PDF")


def test_process_first_endpoint_builds_graph_and_neo4j_export(tmp_path):
    client = TestClient(create_app(tmp_path / "api.db"))
    response = client.post(
        "/api/graph/processes",
        json={
            "enrich_tavily": False,
            "process": {
                "process_number": "5001234-22.2026.4.03.6100",
                "tribunal": "TRF3",
                "process_class": "Execução Fiscal",
                "amount": 12000000,
                "active": True,
                "company_cnpj": "12.345.678/0001-90",
                "company_legal_name": "EMPRESA TESTE LTDA",
                "events": [
                    {
                        "event_type": "Penhora",
                        "event_date": "2026-08-19T10:00:00Z",
                        "description": "Penhora registrada nos autos.",
                    }
                ],
                "source_system": "DATAJUD",
                "source_url": "https://api-publica.datajud.cnj.jus.br/processo/5001234",
                "source_excerpt": (
                    "Processo 5001234-22.2026.4.03.6100 afeta EMPRESA TESTE LTDA. "
                    "Penhora em 19/08/2026."
                ),
                "evidence_score": 75,
            },
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "PROCESS_FIRST_GRAPH"
    assert payload["graph"]["origin_status"] == "PROCESSO_COMO_ORIGEM"
    neo4j = client.get(payload["neo4j_export_url"])
    assert neo4j.status_code == 200
    assert neo4j.json()["counts"]["relationships"] > 0
    crm = client.get(payload["crm_projection_url"])
    assert crm.status_code == 200
    assert crm.json()["source_of_truth"] == "GRAPH"
