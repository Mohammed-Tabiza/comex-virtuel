"""Appels aux modèles : endpoints vLLM compatibles OpenAI, ou simulation.

Chaque appel diffuse sa progression via un callback `on_flux(nature, texte)` où
nature vaut "reflexion" (raisonnement du modèle) ou "redaction" (réponse en cours).
"""
import asyncio
import json
import random
import re
import time
from typing import Awaitable, Callable

from openai import AsyncOpenAI

from .agents import SCHEMAS
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


class Modeles:
    def __init__(self, config: dict):
        self.config = config
        self.simulation = config["mode"] != "reel"
        self.sortie = config["protocole"].get("sortie_structuree", "json_schema")
        self.clients: dict[str, AsyncOpenAI] = {}
        self.verrous: dict[str, asyncio.Semaphore] = {}
        for nom, m in config["modeles"].items():
            self.verrous[nom] = asyncio.Semaphore(m.get("concurrence_max", 4))
            if not self.simulation and m.get("base_url"):
                self.clients[nom] = AsyncOpenAI(base_url=m["base_url"], api_key=m.get("api_key") or "EMPTY",
                                                timeout=600, max_retries=1)

    def modele_de(self, agent_id: str) -> tuple[str, dict, bool]:
        reglage = self.config["agents"][agent_id]
        return reglage["modele"], self.config["modeles"][reglage["modele"]], reglage.get("thinking", False)

    async def generer(self, agent_id: str, etape: str, messages: list[dict], on_flux: OnFlux) -> tuple[dict, dict]:
        nom, modele, thinking = self.modele_de(agent_id)
        debut = time.time()
        async with self.verrous[nom]:
            if self.simulation:
                resultat, usage = await self._simuler(agent_id, etape, on_flux)
            else:
                resultat, usage = await self._appeler(nom, modele, thinking, etape, messages, on_flux)
        meta = {"modele": nom, "libelle": modele["libelle"], "duree": round(time.time() - debut, 1), **usage}
        return resultat, meta

    # ------------------------------------------------------------------ appel réel
    async def _appeler(self, nom: str, modele: dict, thinking: bool, etape: str, messages: list[dict],
                       on_flux: OnFlux):
        client = self.clients[nom]
        mode = self.sortie
        for tentative in range(2):
            try:
                texte, usage = await self._flux(client, modele, thinking, etape, messages, mode, on_flux)
                return extraire_json(texte), usage
            except json.JSONDecodeError:
                if tentative == 1:
                    raise ErreurModele("Réponse non conforme au format JSON après deux tentatives")
                messages = messages + [{"role": "user", "content":
                                        "Ta réponse précédente n'était pas un JSON valide. "
                                        "Réponds uniquement par l'objet JSON demandé."}]
                mode = "consigne"
            except Exception as e:  # noqa: BLE001 - on tente une fois sans sortie contrainte
                if tentative == 1 or mode == "consigne":
                    raise ErreurModele(f"{type(e).__name__} : {e}") from e
                mode = "consigne"

    async def _flux(self, client, modele, thinking, etape, messages, mode, on_flux):
        params = dict(model=modele["model"], messages=messages, stream=True,
                      temperature=modele.get("temperature", 0.6), max_tokens=modele.get("max_tokens", 4096),
                      stream_options={"include_usage": True})
        if mode == "json_schema":
            params["response_format"] = {"type": "json_schema",
                                         "json_schema": {"name": etape, "schema": SCHEMAS[etape], "strict": True}}
        if modele.get("famille") == "qwen":
            params["extra_body"] = {"chat_template_kwargs": {"enable_thinking": thinking}}

        contenu, usage = "", {}
        flux = await client.chat.completions.create(**params)
        async for morceau in flux:
            if getattr(morceau, "usage", None):
                usage = {"tokens_entree": morceau.usage.prompt_tokens, "tokens_sortie": morceau.usage.completion_tokens}
            if not morceau.choices:
                continue
            delta = morceau.choices[0].delta
            extra = getattr(delta, "model_extra", None) or {}
            raisonnement = getattr(delta, "reasoning_content", None) or extra.get("reasoning_content") \
                or extra.get("reasoning")
            if raisonnement:
                await on_flux("reflexion", raisonnement)
            if delta.content:
                contenu += delta.content
                # Sans reasoning parser côté vLLM, le raisonnement arrive dans le contenu entre <think>
                en_reflexion = "<think>" in contenu and "</think>" not in contenu
                await on_flux("reflexion" if en_reflexion else "redaction", delta.content)
        return contenu, usage

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
