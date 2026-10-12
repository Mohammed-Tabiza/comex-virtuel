"""Journal d'événements par séance (alimente l'interface) et registre des séances en cours.

Le journal est distinct de l'état LangGraph : il sert à animer l'interface et peut être
rejoué après une déconnexion (paramètre since / en-tête Last-Event-ID).
"""
import asyncio
import time
from dataclasses import dataclass, field

from .comites import Composition
from .dossier import Dossier


@dataclass
class Seance:
    id: str
    dossier: Dossier
    # Composition propre à la séance : membres, mandats envoyés, modèles. Aucune autre séance ne la voit.
    composition: Composition | None = None
    schemas: dict = field(default_factory=dict)
    statut: str = "creee"            # creee | en_cours | attente_validation | terminee | erreur
    debut: float = field(default_factory=time.time)
    decision_humaine: str = ""
    evenements: list[dict] = field(default_factory=list)
    _cond: asyncio.Condition = field(default_factory=asyncio.Condition)

    def fixer_composition(self, composition: Composition) -> None:
        """Associe la composition à la séance et construit les schémas qui en dépendent."""
        from .agents import schemas_seance
        self.composition = composition
        self.schemas = schemas_seance(composition.experts)

    async def publier(self, type_: str, **donnees) -> None:
        async with self._cond:
            evt = {"id": len(self.evenements) + 1, "t": round(time.time() - self.debut, 2), "type": type_, **donnees}
            self.evenements.append(evt)
            self._cond.notify_all()

    async def suivre(self, depuis: int = 0):
        """Générateur asynchrone : rejoue les événements après `depuis`, puis attend les suivants."""
        position = depuis
        while True:
            async with self._cond:
                if position >= len(self.evenements):
                    try:
                        await asyncio.wait_for(self._cond.wait(), timeout=15)
                    except asyncio.TimeoutError:
                        pass
                nouveaux = self.evenements[position:]
            if not nouveaux:
                yield None                    # battement de cœur pour garder la connexion ouverte
                continue
            for evt in nouveaux:
                yield evt
            position += len(nouveaux)


SEANCES: dict[str, Seance] = {}


def seance_de(config: dict) -> Seance:
    return SEANCES[config["configurable"]["thread_id"]]
