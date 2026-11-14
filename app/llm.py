"""Appels aux modèles : endpoints vLLM compatibles OpenAI, ou simulation.

Chaque appel diffuse sa progression via un callback `on_flux(nature, texte)` où
nature vaut "reflexion" (raisonnement du modèle) ou "redaction" (réponse en cours).
"""
import asyncio
import json
import random
import re
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import httpx
from openai import AsyncOpenAI

from .agents import SCHEMAS, ecarts_schema
from .dossier import estimer_tokens
from . import simulation

OnFlux = Callable[[str, str], Awaitable[None]]


class ErreurModele(Exception):
    pass


def extraire_json(texte: str) -> dict:
    """Extrait l'objet JSON d'une réponse, même entourée de balises <think> ou de ```."""
    if "</think>" in texte:
        texte = texte.split("</think>", 1)[1]
    texte = re.sub(r"^```(?:json)?|```$", "", texte.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(texte)
    except json.JSONDecodeError:
        debut, fin = texte.find("{"), texte.rfind("}")
        if debut >= 0 and fin > debut:
            return json.loads(texte[debut:fin + 1])
        raise


def dernier_json(texte: str, schema: dict | None = None) -> dict | None:
    """Dernier objet JSON complet d'un texte libre (ex. canal de réflexion), ou None.

    Avec un schéma, seuls les objets portant tous les champs obligatoires de premier niveau comptent :
    un fragment imbriqué d'une ébauche inachevée n'est pas pris pour la réponse.
    """
    decodeur, candidats, i = json.JSONDecoder(), [], 0
    while (debut := texte.find("{", i)) >= 0:
        try:
            objet, fin = decodeur.raw_decode(texte, debut)
        except json.JSONDecodeError:
            i = debut + 1
            continue
        if isinstance(objet, dict):
            candidats.append(objet)
        i = fin
    if schema:
        candidats = [c for c in candidats if all(k in c for k in schema.get("required", []))]
    return candidats[-1] if candidats else None


@dataclass
class Sortie:
    """Ce que le serveur a produit pour un appel."""
    contenu: str = ""
    raisonnement: str = ""
    fin: str | None = None                      # finish_reason : "stop", "length"...
    usage: dict = field(default_factory=dict)


def contexte_ollama(base_url: str, modele: str, delai: float = 3.0) -> tuple[int | None, str]:
    """Fenêtre de contexte réellement appliquée par Ollama à un modèle : (num_ctx, explication).

    Sans paramètre num_ctx dans le modèle, Ollama applique sa valeur par défaut (4 096 tokens, ou
    OLLAMA_CONTEXT_LENGTH du serveur) et tronque sans erreur au-delà : constaté avec Nemotron.
    """
    racine = re.sub(r"/v1/?$", "", base_url.rstrip("/"))
    try:
        reponse = httpx.post(f"{racine}/api/show", json={"model": modele}, timeout=delai)
        reponse.raise_for_status()
    except Exception as e:  # noqa: BLE001 - serveur absent ou pas Ollama : rien à vérifier
        return None, f"contexte non vérifié ({type(e).__name__})"
    trouve = re.search(r"^\s*num_ctx\s+(\d+)", reponse.json().get("parameters", "") or "", re.MULTILINE)
    if trouve:
        return int(trouve.group(1)), "num_ctx du modèle"
    return 4096, "aucun num_ctx dans le modèle : valeur par défaut d'Ollama supposée (4 096)"


class Modeles:
    def __init__(self, config: dict):
        self.config = config
        self.contextes_reels: dict[str, int] = {}   # renseigné par verifier_contextes()
        self.simulation = config["mode"] != "reel"
        self.sortie = config["protocole"].get("sortie_structuree", "json_schema")
        self.clients: dict[str, AsyncOpenAI] = {}
        self.verrous: dict[str, asyncio.Semaphore] = {}
        for nom, m in config["modeles"].items():
            self.verrous[nom] = asyncio.Semaphore(m.get("concurrence_max", 4))
            if not self.simulation and m.get("base_url"):
                self.clients[nom] = AsyncOpenAI(base_url=m["base_url"], api_key=m.get("api_key") or "EMPTY",
                                                timeout=600, max_retries=1)

    def verifier_contextes(self) -> list[str]:
        """Interroge Ollama sur la fenêtre réelle de chaque modèle utilisé ; renvoie des messages à afficher."""
        messages = []
        if self.simulation:
            return messages
        utilises = {r["modele"] for r in self.config["agents"].values()}
        for nom in sorted(utilises):
            m = self.config["modeles"][nom]
            if m.get("famille") != "ollama" or not m.get("base_url"):
                continue
            reel, origine = contexte_ollama(m["base_url"], m["model"])
            if reel is None:
                messages.append(f"{nom} : {origine}")
                continue
            self.contextes_reels[nom] = reel
            declare = m.get("contexte_max")
            if declare and declare > reel:
                messages.append(f"{nom} : contexte_max {declare} déclaré, mais Ollama applique {reel} ({origine}). "
                                f"La valeur {reel} est retenue.")
            else:
                messages.append(f"{nom} : contexte effectif {min(reel, declare or reel)} ({origine}).")
        return messages

    def contexte_de(self, nom: str, modele: dict) -> int | None:
        valeurs = [v for v in (modele.get("contexte_max"), self.contextes_reels.get(nom)) if v]
        return min(valeurs) if valeurs else None

    def modele_de(self, agent_id: str) -> tuple[str, dict, bool]:
        reglage = self.config["agents"][agent_id]
        return reglage["modele"], self.config["modeles"][reglage["modele"]], reglage.get("thinking", False)

    def max_tokens_de(self, modele: dict, etape: str) -> int:
        max_tokens = modele.get("max_tokens", 4096)
        if etape == "synthese":
            max_tokens = self.config["protocole"].get("synthese_max_tokens", max_tokens)
        return max_tokens

    async def generer(self, agent_id: str, etape: str, messages: list[dict], on_flux: OnFlux,
                      schema: dict | None = None) -> tuple[dict, dict]:
        """schema : schéma propre à cet appel (ex. synthèse contrainte par le cadrage), sinon celui de l'étape."""
        nom, modele, thinking = self.modele_de(agent_id)
        estimes = estimer_tokens("".join(m["content"] for m in messages))
        if not self.simulation:
            # Un appel qui ne tient pas dans la fenêtre du modèle serait tronqué sans erreur par le serveur
            contexte, sortie = self.contexte_de(nom, modele), self.max_tokens_de(modele, etape)
            if contexte and estimes + sortie > contexte:
                raise ErreurModele(f"message trop long pour {nom} : ~{estimes} tokens en entrée + {sortie} en sortie "
                                   f"> contexte effectif {contexte}")
        debut = time.time()
        async with self.verrous[nom]:
            if self.simulation:
                resultat, usage = await self._simuler(agent_id, etape, on_flux)
            else:
                resultat, usage = await self._appeler(nom, modele, thinking, etape, messages, on_flux,
                                                      schema or SCHEMAS[etape])
        meta = {"modele": nom, "libelle": modele["libelle"], "duree": round(time.time() - debut, 1),
                "tokens_estimes": estimes, "simulation": self.simulation, **usage}
        return resultat, meta

    # ------------------------------------------------------------------ appel réel
    async def _appeler(self, nom: str, modele: dict, thinking: bool, etape: str, messages: list[dict],
                       on_flux: OnFlux, schema: dict):
        """Appel avec contrôle de la réponse : JSON valide ET conforme au schéma, sinon une relance corrective.

        La sortie contrainte du serveur n'est pas une garantie (constaté avec Ollama : un champ
        « recommendation » au lieu de « recommandation ») : la conformité est vérifiée ici.
        """
        client = self.clients[nom]
        mode, reflexion = self.sortie, thinking
        delai = self.config["protocole"].get("delai_max_appel", 600)
        for tentative in range(2):
            try:
                # Un appel qui s'emballe (constaté : Ministral bloqué 3 h 50 à répéter des objets vides,
                # sur une carte saturée) est interrompu au lieu de bloquer la séance.
                sortie = await asyncio.wait_for(
                    self._flux(client, modele, reflexion, etape, messages, mode, on_flux, schema), timeout=delai)
            except asyncio.TimeoutError:
                if tentative == 1:
                    raise ErreurModele(f"Délai maximal de {delai} s dépassé deux fois (delai_max_appel) : "
                                       "modèle trop lent ou sortie qui s'emballe")
                reflexion = False
                continue
            except Exception as e:  # noqa: BLE001 - on tente une fois sans sortie contrainte
                if tentative == 1 or mode == "consigne":
                    raise ErreurModele(f"{type(e).__name__} : {e}") from e
                mode = "consigne"
                continue
            try:
                resultat = extraire_json(sortie.contenu)
            except json.JSONDecodeError:
                # Constaté avec Nemotron sous Ollama : le JSON complet écrit dans le canal de réflexion,
                # la réponse elle-même restant vide.
                resultat = dernier_json(sortie.raisonnement, schema)
            if resultat is None:
                if sortie.fin == "length":
                    cause = self._cause_coupure(nom, modele, etape, sortie)
                elif not sortie.contenu.strip():
                    cause = "réponse vide" + (", JSON absent de la réflexion" if sortie.raisonnement else "")
                else:
                    cause = "réponse qui n'est pas un JSON valide"
                if tentative == 1:
                    raise ErreurModele(f"Réponse inexploitable après deux tentatives ({cause})")
                if reflexion:
                    # La réflexion consomme le budget de sortie ou capte la réponse : relance sans réflexion
                    reflexion = False
                else:
                    messages = messages + [{"role": "user", "content":
                                            "Ta réponse précédente n'était pas un JSON valide. "
                                            "Réponds uniquement par l'objet JSON demandé."}]
                    mode = "consigne"
                continue
            usage = {**sortie.usage, "reflexion": reflexion}
            if reflexion != thinking:
                usage["reflexion_coupee"] = True
            ecarts = ecarts_schema(resultat, schema)
            if not ecarts:
                return resultat, usage
            if tentative == 1:
                raise ErreurModele("Réponse non conforme au schéma après deux tentatives : " + " ; ".join(ecarts[:5]))
            messages = messages + [
                {"role": "assistant", "content": json.dumps(resultat, ensure_ascii=False)},
                {"role": "user", "content": "Ta réponse ne respecte pas le schéma demandé :\n- "
                 + "\n- ".join(ecarts[:10]) + "\nRenvoie l'objet JSON complet corrigé, avec exactement les noms "
                 "de champs et les valeurs autorisées du schéma, sans texte autour."}]

    def _cause_coupure(self, nom: str, modele: dict, etape: str, sortie: "Sortie") -> str:
        """Explique une réponse coupée : fenêtre de contexte pleine, ou budget de sortie épuisé."""
        lus, produits = sortie.usage.get("tokens_entree"), sortie.usage.get("tokens_sortie")
        contexte, budget = self.contexte_de(nom, modele), self.max_tokens_de(modele, etape)
        if lus and produits and contexte and lus + produits >= 0.95 * contexte:
            cause = (f"réponse coupée : fenêtre de contexte de {nom} pleine ({lus} tokens lus + {produits} produits "
                     f"≈ {contexte})")
        elif produits:
            cause = f"réponse coupée : budget de sortie de {budget} tokens épuisé ({produits} produits)"
        else:
            cause = f"réponse coupée : budget de sortie de {budget} tokens ou fenêtre de contexte atteint"
        if sortie.raisonnement:
            cause += f", dont {len(sortie.raisonnement)} caractères de réflexion"
        return cause

    async def _flux(self, client, modele, thinking, etape, messages, mode, on_flux, schema=None):
        max_tokens = self.max_tokens_de(modele, etape)
        params = dict(model=modele["model"], messages=messages, stream=True,
                      temperature=modele.get("temperature", 0.6), max_tokens=max_tokens,
                      stream_options={"include_usage": True})
        if mode == "json_schema":
            params["response_format"] = {"type": "json_schema",
                                         "json_schema": {"name": etape, "schema": schema or SCHEMAS[etape], "strict": True}}
        if modele.get("famille") == "qwen":
            params["extra_body"] = {"chat_template_kwargs": {"enable_thinking": thinking}}
        elif modele.get("famille") == "ollama" and not thinking:
            # Seul moyen de couper la réflexion via l'API compatible OpenAI d'Ollama (extra_body think=false
            # est ignoré). Sinon les modèles « thinking » réfléchissent par défaut, et cette réflexion compte
            # dans max_tokens.
            params["reasoning_effort"] = "none"

        sortie = Sortie()
        flux = await client.chat.completions.create(**params)
        async for morceau in flux:
            if getattr(morceau, "usage", None):
                sortie.usage = {"tokens_entree": morceau.usage.prompt_tokens,
                                "tokens_sortie": morceau.usage.completion_tokens}
            if not morceau.choices:
                continue
            sortie.fin = morceau.choices[0].finish_reason or sortie.fin
            delta = morceau.choices[0].delta
            extra = getattr(delta, "model_extra", None) or {}
            raisonnement = getattr(delta, "reasoning_content", None) or extra.get("reasoning_content") \
                or extra.get("reasoning")
            if raisonnement:
                sortie.raisonnement += raisonnement
                await on_flux("reflexion", raisonnement)
            if delta.content:
                sortie.contenu += delta.content
                # Sans reasoning parser côté vLLM, le raisonnement arrive dans le contenu entre <think>
                en_reflexion = "<think>" in sortie.contenu and "</think>" not in sortie.contenu
                await on_flux("reflexion" if en_reflexion else "redaction", delta.content)
        return sortie

    # ------------------------------------------------------------------ simulation
    async def _simuler(self, agent_id: str, etape: str, on_flux: OnFlux):
        resultat = simulation.reponse(agent_id, etape)
        mini, maxi = self.config["protocole"].get("simulation_duree", [3, 8])
        duree = random.uniform(mini, maxi)
        reflexion = simulation.reflexion(agent_id, etape)
        redaction = json.dumps(resultat, ensure_ascii=False)
        # 40 % du temps en réflexion, 60 % en rédaction, diffusés par petits morceaux
        for texte, nature, part in ((reflexion, "reflexion", 0.4), (redaction, "redaction", 0.6)):
            pas = max(1, len(texte) // 30)
            for i in range(0, len(texte), pas):
                await on_flux(nature, texte[i:i + pas])
                await asyncio.sleep(duree * part / 30)
        return resultat, {"tokens_entree": 4200 + random.randint(0, 2500), "tokens_sortie": len(redaction) // 3}
