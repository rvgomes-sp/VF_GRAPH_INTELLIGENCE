from pathlib import Path

from vf_osint.storage import Repository
from vf_osint.models import PublicDocument, SourceClass


def test_feedback_uses_smoothed_probability(tmp_path: Path):
    repository = Repository(tmp_path / "test.db")
    assert repository.learned_probability("x") == 0.5
    repository.record_feedback("d1", "source", "x", "confirmed")
    assert repository.learned_probability("source:x") == 2 / 3


def test_distinct_documents_are_persisted(tmp_path: Path):
    repository = Repository(tmp_path / "test.db")
    for index in (1, 2):
        repository.save_document(
            PublicDocument(
                url=f"https://source{index}.example/company",
                title=f"source {index}",
                text="same visible text",
                source_class=SourceClass.AGGREGATOR,
            )
        )
    with repository.connection() as connection:
        count = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert count == 2
