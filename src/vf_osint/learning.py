from __future__ import annotations

from dataclasses import dataclass

from .storage import Repository


@dataclass(frozen=True)
class LearningDecision:
    selected_variant: str
    probability: float
    basis: str


class GuardedLearner:
    """Aprende preferencia de formato; nunca reescreve regras protegidas."""

    VARIANTS = ("consultiva_direta", "juridico_primeiro", "financeiro_primeiro")

    def __init__(self, repository: Repository):
        self.repository = repository

    def select_approach_variant(self, persona: str) -> LearningDecision:
        candidates = []
        for variant in self.VARIANTS:
            key = f"approach:{persona}:{variant}"
            candidates.append((self.repository.learned_probability(key), variant))
        probability, variant = max(candidates, key=lambda item: (item[0], -self.VARIANTS.index(item[1])))
        return LearningDecision(
            selected_variant=variant,
            probability=probability,
            basis="feedback_operacional_com_prior_beta_1_1",
        )

    def record_approach_feedback(
        self, dossier_id: str, persona: str, variant: str, useful: bool, note: str = ""
    ) -> None:
        if variant not in self.VARIANTS:
            raise ValueError("Variante desconhecida")
        target = f"{persona}:{variant}"
        self.repository.record_feedback(
            dossier_id,
            "approach",
            target,
            "useful" if useful else "not_useful",
            note,
        )

