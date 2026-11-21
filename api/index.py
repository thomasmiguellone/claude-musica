"""
Claude Música: controla o Spotify por voz via Claude.

Endpoint principal: POST /api/comando
  Header:  Authorization: Bearer <APP_SECRET>
  Body:    {"pedido": "toca algo parecido com Tame Impala"}
  Retorno: {"resposta": "Tocando The Less I Know The Better."}
"""

import base64
import hmac
import json
import os
import time

import anthropic
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

API = "https://api.spotify.com/v1"
MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-5-5")
MAX_PASSOS = 10  # limite de rodadas de ferramentas por pedido

_token = {"valor": None, "expira": 0.0}


# --------------------------------------------------------------------------
# Spotify: autenticação e chamadas
# --------------------------------------------------------------------------

def token_spotify() -> str:
    """Renova o access token a partir do refresh token (cacheado por ~55 min)."""
    if _token["valor"] and time.time() < _token["expira"]:
        return _token["valor"]
    cred = f"{os.environ['SPOTIFY_CLIENT_ID']}:{os.environ['SPOTIFY_CLIENT_SECRET']}"
    r = requests.post(
        "https://accounts.spotify.com/api/token",
        data={"grant_type": "refresh_token", "refresh_token": os.environ["SPOTIFY_REFRESH_TOKEN"]},
        headers={"Authorization": "Basic " + base64.b64encode(cred.encode()).decode()},
        timeout=10,
    )
    r.raise_for_status()
    dados = r.json()
    _token["valor"] = dados["access_token"]
    _token["expira"] = time.time() + dados.get("expires_in", 3600) - 300
    return _token["valor"]


def sp(metodo: str, caminho: str, params=None, corpo=None):
    """Chama a API do Spotify e devolve o JSON (ou um status curto)."""
    r = requests.request(
        metodo,
        API + caminho,
        params={k: v for k, v in (params or {}).items() if v is not None},
        json=corpo,
        headers={"Authorization": f"Bearer {token_spotify()}"},
        timeout=15,
    )
    if r.status_code == 404 and caminho.startswith("/me/player"):
        return {"erro": "Nenhum dispositivo ativo. Peça para abrir o Spotify ou use transferir_reproducao."}
    if r.status_code >= 400:
        return {"erro": r.status_code, "detalhe": r.text[:300]}
    if r.status_code == 204 or not r.content:
        return {"ok": True}
    try:
        return r.json()
    except ValueError:
        return {"ok": True}


# Campos que só gastam tokens do Claude e não ajudam a decidir nada
DESCARTAR = {
    "available_markets", "images", "external_urls", "external_ids", "href",
    "preview_url", "is_local", "disc_number", "linked_from", "restrictions",
    "copyrights", "label", "popularity_label", "followers", "primary_color",
    "snapshot_id", "video_thumbnail", "is_playable", "explicit", "track_number",
    "release_date_precision", "total_tracks", "album_group", "genres",
    "actions", "smart_shuffle", "html_description",
}


def enxugar(obj, profundidade=0):
    """Remove ruído do JSON do Spotify e resume artistas a nomes."""
    if profundidade > 6:
        return "…"
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in DESCARTAR or v is None:
                continue
            if k == "artists" and isinstance(v, list):
                out["artistas"] = ", ".join(a.get("name", "") for a in v if isinstance(a, dict))
                continue
            if k == "album" and isinstance(v, dict):
                out["album"] = v.get("name")
                continue
            if k == "owner" and isinstance(v, dict):
                out["dono"] = v.get("display_name") or v.get("id")
                continue
            out[k] = enxugar(v, profundidade + 1)
        return out
    if isinstance(obj, list):
        return [enxugar(x, profundidade + 1) for x in obj]
    return obj


def em_lotes(lista, tamanho):
    for i in range(0, len(lista), tamanho):
        yield lista[i:i + tamanho]


# --------------------------------------------------------------------------
# Ferramentas que o Claude pode usar
# --------------------------------------------------------------------------

def _f(nome, descricao, props=None, obrigatorios=None):
    return {
        "name": nome,
        "description": descricao,
        "input_schema": {"type": "object", "properties": props or {}, "required": obrigatorios or []},
    }

URIS = {"type": "array", "items": {"type": "string"}, "description": "URIs do Spotify, ex. spotify:track:ID"}

FERRAMENTAS = [
    _f("buscar", "Busca no catálogo do Spotify. Máximo 10 resultados por tipo.",
       {"consulta": {"type": "string", "description": "Texto da busca. Aceita filtros como artist:Nome track:Nome year:1990"},
        "tipos": {"type": "string", "description": "Separados por vírgula: track, album, artist, playlist, show, episode, audiobook. Padrão: track"},
        "limite": {"type": "integer", "description": "1 a 10, padrão 5"}},
       ["consulta"]),
    _f("estado_reproducao", "O que está tocando agora, em qual dispositivo, volume, aleatório e repetição."),
    _f("tocar", "Toca músicas. Use 'uris' para uma lista de faixas (tocam em sequência) OU 'contexto' para álbum, playlist ou artista. Sem argumentos, retoma a reprodução.",
       {"uris": URIS,
        "contexto": {"type": "string", "description": "URI de álbum, playlist ou artista"},
        "comecar_na_posicao": {"type": "integer", "description": "Índice da faixa inicial dentro do contexto"},
        "dispositivo_id": {"type": "string"}}),
    _f("pausar", "Pausa a reprodução."),
    _f("proxima", "Pula para a próxima faixa."),
    _f("anterior", "Volta para a faixa anterior."),
    _f("ir_para", "Avança ou volta para um ponto da faixa atual.",
       {"posicao_ms": {"type": "integer"}}, ["posicao_ms"]),
    _f("volume", "Define o volume (0 a 100). Alguns dispositivos, como iPhone, podem não aceitar.",
       {"percentual": {"type": "integer"}}, ["percentual"]),
    _f("aleatorio", "Liga ou desliga o modo aleatório.", {"ligado": {"type": "boolean"}}, ["ligado"]),
    _f("repetir", "Modo de repetição.",
       {"modo": {"type": "string", "enum": ["track", "context", "off"]}}, ["modo"]),
    _f("adicionar_a_fila", "Adiciona faixas ou episódios ao fim da fila, na ordem dada.", {"uris": URIS}, ["uris"]),
    _f("ver_fila", "Mostra a faixa atual e as próximas da fila. Obs.: a API não permite remover nem reordenar a fila."),
    _f("dispositivos", "Lista os dispositivos Spotify disponíveis."),
    _f("transferir_reproducao", "Muda a reprodução para outro dispositivo.",
       {"dispositivo_id": {"type": "string"}, "tocar": {"type": "boolean"}}, ["dispositivo_id"]),
    _f("tocadas_recentemente", "Últimas faixas ouvidas.", {"limite": {"type": "integer", "description": "1 a 50"}}),
    _f("mais_ouvidas", "Artistas ou faixas mais ouvidas do usuário.",
       {"tipo": {"type": "string", "enum": ["tracks", "artists"]},
        "periodo": {"type": "string", "enum": ["short_term", "medium_term", "long_term"],
                    "description": "short_term ≈ 4 semanas, medium_term ≈ 6 meses, long_term ≈ anos"},
        "limite": {"type": "integer"}},
       ["tipo"]),
    _f("minhas_playlists", "Lista as playlists do usuário (com id e nome).",
       {"limite": {"type": "integer"}, "offset": {"type": "integer"}}),
    _f("itens_playlist", "Faixas de uma playlist (só funciona em playlists do usuário ou colaborativas).",
       {"playlist_id": {"type": "string"}, "limite": {"type": "integer"}, "offset": {"type": "integer"}},
       ["playlist_id"]),
    _f("criar_playlist", "Cria uma playlist nova e opcionalmente já adiciona faixas.",
       {"nome": {"type": "string"}, "descricao": {"type": "string"},
        "publica": {"type": "boolean", "description": "Padrão: false"}, "uris": URIS},
       ["nome"]),
    _f("adicionar_a_playlist", "Adiciona faixas a uma playlist.",
       {"playlist_id": {"type": "string"}, "uris": URIS,
        "posicao": {"type": "integer", "description": "Opcional, padrão é o fim"}},
       ["playlist_id", "uris"]),
    _f("remover_da_playlist", "Remove faixas de uma playlist.",
       {"playlist_id": {"type": "string"}, "uris": URIS}, ["playlist_id", "uris"]),
    _f("reordenar_playlist", "Move um bloco de faixas dentro da playlist.",
       {"playlist_id": {"type": "string"}, "inicio": {"type": "integer"},
        "inserir_antes": {"type": "integer"}, "quantidade": {"type": "integer", "description": "Padrão 1"}},
       ["playlist_id", "inicio", "inserir_antes"]),
    _f("editar_playlist", "Muda nome, descrição ou visibilidade de uma playlist.",
       {"playlist_id": {"type": "string"}, "nome": {"type": "string"},
        "descricao": {"type": "string"}, "publica": {"type": "boolean"}},
       ["playlist_id"]),
    _f("salvar_na_biblioteca", "Curte faixas, salva álbuns, segue playlists ou artistas (passe as URIs).",
       {"uris": URIS}, ["uris"]),
    _f("remover_da_biblioteca", "Descurte faixas, remove álbuns, deixa de seguir playlists ou artistas.",
       {"uris": URIS}, ["uris"]),
    _f("esta_na_biblioteca", "Verifica se itens já estão salvos ou seguidos.", {"uris": URIS}, ["uris"]),
    _f("musicas_curtidas", "Lista as músicas curtidas do usuário.",
       {"limite": {"type": "integer"}, "offset": {"type": "integer"}}),
    _f("artistas_seguidos", "Lista os artistas que o usuário segue.", {"limite": {"type": "integer"}}),
    _f("albuns_do_artista", "Discografia de um artista.",
       {"artista_id": {"type": "string"}, "tipos": {"type": "string", "description": "album,single,compilation"},
        "limite": {"type": "integer"}},
       ["artista_id"]),
    _f("faixas_do_album", "Faixas de um álbum.", {"album_id": {"type": "string"}}, ["album_id"]),
]


def _id(valor: str) -> str:
    """Aceita ID puro, URI (spotify:playlist:ID) ou link e devolve o ID."""
    valor = valor.strip()
    if valor.startswith("spotify:"):
        return valor.split(":")[-1]
    if "open.spotify.com" in valor:
        return valor.rstrip("/").split("/")[-1].split("?")[0]
    return valor


def executar(nome: str, a: dict):
    lim = lambda padrao, maximo: max(1, min(int(a.get("limite", padrao)), maximo))

    if nome == "buscar":
        return sp("GET", "/search", {"q": a["consulta"], "type": a.get("tipos", "track"), "limit": lim(5, 10)})
    if nome == "estado_reproducao":
        return sp("GET", "/me/player") or {"info": "Nada tocando"}
    if nome == "tocar":
        corpo = {}
        if a.get("uris"):
            corpo["uris"] = a["uris"]
        elif a.get("contexto"):
            corpo["context_uri"] = a["contexto"]
            if a.get("comecar_na_posicao") is not None:
                corpo["offset"] = {"position": a["comecar_na_posicao"]}
        return sp("PUT", "/me/player/play", {"device_id": a.get("dispositivo_id")}, corpo or None)
    if nome == "pausar":
        return sp("PUT", "/me/player/pause")
    if nome == "proxima":
        return sp("POST", "/me/player/next")
    if nome == "anterior":
        return sp("POST", "/me/player/previous")
    if nome == "ir_para":
        return sp("PUT", "/me/player/seek", {"position_ms": a["posicao_ms"]})
    if nome == "volume":
        return sp("PUT", "/me/player/volume", {"volume_percent": max(0, min(100, a["percentual"]))})
    if nome == "aleatorio":
        return sp("PUT", "/me/player/shuffle", {"state": "true" if a["ligado"] else "false"})
    if nome == "repetir":
        return sp("PUT", "/me/player/repeat", {"state": a["modo"]})
    if nome == "adicionar_a_fila":
        resultados = [sp("POST", "/me/player/queue", {"uri": u}) for u in a["uris"]]
        erros = [r for r in resultados if "erro" in r]
        return {"adicionadas": len(resultados) - len(erros), "erros": erros[:3]}
    if nome == "ver_fila":
        r = sp("GET", "/me/player/queue")
        if isinstance(r, dict) and "queue" in r:
            r["queue"] = r["queue"][:15]
        return r
    if nome == "dispositivos":
        return sp("GET", "/me/player/devices")
    if nome == "transferir_reproducao":
        return sp("PUT", "/me/player", corpo={"device_ids": [a["dispositivo_id"]], "play": a.get("tocar", True)})
    if nome == "tocadas_recentemente":
        return sp("GET", "/me/player/recently-played", {"limit": lim(10, 50)})
    if nome == "mais_ouvidas":
        return sp("GET", f"/me/top/{a['tipo']}", {"time_range": a.get("periodo", "medium_term"), "limit": lim(10, 50)})
    if nome == "minhas_playlists":
        r = sp("GET", "/me/playlists", {"limit": lim(50, 50), "offset": a.get("offset", 0)})
        if isinstance(r, dict) and "items" in r:
            r["items"] = [{"nome": p.get("name"), "id": p.get("id"), "uri": p.get("uri"),
                           "dono": (p.get("owner") or {}).get("display_name")} for p in r["items"] if p]
        return r
    if nome == "itens_playlist":
        return sp("GET", f"/playlists/{_id(a['playlist_id'])}/items",
                  {"limit": lim(50, 50), "offset": a.get("offset", 0)})
    if nome == "criar_playlist":
        nova = sp("POST", "/me/playlists", corpo={
            "name": a["nome"], "description": a.get("descricao", ""), "public": a.get("publica", False)})
        if "erro" in nova:
            return nova
        if a.get("uris"):
            for lote in em_lotes(a["uris"], 100):
                sp("POST", f"/playlists/{nova['id']}/items", corpo={"uris": lote})
        return {"criada": nova.get("name"), "id": nova.get("id"), "uri": nova.get("uri"),
                "faixas_adicionadas": len(a.get("uris") or [])}
    if nome == "adicionar_a_playlist":
        pid = _id(a["playlist_id"])
        pos = a.get("posicao")
        for i, lote in enumerate(em_lotes(a["uris"], 100)):
            corpo = {"uris": lote}
            if pos is not None:
                corpo["position"] = pos + i * 100
            r = sp("POST", f"/playlists/{pid}/items", corpo=corpo)
            if "erro" in r:
                return r
        return {"ok": True, "adicionadas": len(a["uris"])}
    if nome == "remover_da_playlist":
        pid = _id(a["playlist_id"])
        for lote in em_lotes(a["uris"], 100):
            r = sp("DELETE", f"/playlists/{pid}/items", corpo={"items": [{"uri": u} for u in lote]})
            if "erro" in r:
                return r
        return {"ok": True, "removidas": len(a["uris"])}
    if nome == "reordenar_playlist":
        return sp("PUT", f"/playlists/{_id(a['playlist_id'])}/items", corpo={
            "range_start": a["inicio"], "insert_before": a["inserir_antes"], "range_length": a.get("quantidade", 1)})
    if nome == "editar_playlist":
        corpo = {k2: a[k1] for k1, k2 in (("nome", "name"), ("descricao", "description"), ("publica", "public")) if k1 in a}
        return sp("PUT", f"/playlists/{_id(a['playlist_id'])}", corpo=corpo)
    if nome in ("salvar_na_biblioteca", "remover_da_biblioteca"):
        metodo = "PUT" if nome == "salvar_na_biblioteca" else "DELETE"
        for lote in em_lotes(a["uris"], 40):
            r = sp(metodo, "/me/library", {"uris": ",".join(lote)})
            if "erro" in r:
                return r
        return {"ok": True, "itens": len(a["uris"])}
    if nome == "esta_na_biblioteca":
        r = sp("GET", "/me/library/contains", {"uris": ",".join(a["uris"][:40])})
        return dict(zip(a["uris"], r)) if isinstance(r, list) else r
    if nome == "musicas_curtidas":
        return sp("GET", "/me/tracks", {"limit": lim(20, 50), "offset": a.get("offset", 0)})
    if nome == "artistas_seguidos":
        return sp("GET", "/me/following", {"type": "artist", "limit": lim(20, 50)})
    if nome == "albuns_do_artista":
        return sp("GET", f"/artists/{_id(a['artista_id'])}/albums",
                  {"include_groups": a.get("tipos", "album,single"), "limit": lim(20, 50)})
    if nome == "faixas_do_album":
        return sp("GET", f"/albums/{_id(a['album_id'])}/tracks", {"limit": 50})
    return {"erro": f"Ferramenta desconhecida: {nome}"}


# --------------------------------------------------------------------------
# Loop do Claude
# --------------------------------------------------------------------------

SISTEMA = """Você controla o Spotify do usuário por voz. O pedido chega transcrito do ditado do iPhone, \
então pode vir com erros de transcrição em nomes de músicas e artistas: interprete pelo sentido.

Como agir:
- Execute o pedido de ponta a ponta usando as ferramentas, sem pedir confirmação.
- Para achar músicas, use 'buscar' com filtros (ex.: track:Nome artist:Nome). A busca devolve no máximo 10 itens.
- Pedidos vagos ("aquela do filme X", "algo parecido com Y", "músicas pra estudar"): use seu próprio \
conhecimento musical para escolher faixas concretas e depois busque cada uma. A API de recomendações do \
Spotify não existe mais, então as sugestões são suas.
- Para tocar várias músicas, prefira 'tocar' com a lista de uris (tocam em sequência). Use \
'adicionar_a_fila' quando ele pedir fila ou "depois dessa".
- "Essa música" se refere ao que está tocando: confira com 'estado_reproducao'.
- O aparelho padrão é o iPhone (type Smartphone), porque os pedidos vêm do fone dele. Antes de tocar, \
chame 'dispositivos': se o iPhone não for o ativo, passe o dispositivo_id dele no 'tocar' (ou use \
'transferir_reproducao' para retomar lá). Só use outro aparelho se ele pedir (ex.: "no computador", "na TV"). \
Se o iPhone não aparecer na lista, avise em uma frase que o app do Spotify precisa estar aberto no iPhone.
- A API não permite remover nem reordenar a fila; se pedirem, explique em uma frase.

Resposta final: UMA frase curta em português do Brasil, natural para ser falada em voz alta pela Siri. \
Sem markdown, sem emojis, sem listas, sem URIs."""

cliente = anthropic.Anthropic() if os.environ.get("ANTHROPIC_API_KEY") else None


def processar(pedido: str) -> str:
    print(f"PEDIDO: {pedido}", flush=True)
    mensagens = [{"role": "user", "content": pedido}]
    for _ in range(MAX_PASSOS):
        resp = cliente.messages.create(
            model=MODEL, max_tokens=1500, system=SISTEMA, tools=FERRAMENTAS, messages=mensagens)
        if resp.stop_reason != "tool_use":
            texto = " ".join(b.text for b in resp.content if b.type == "text").strip()
            print(f"RESPOSTA: {texto}", flush=True)
            return texto or "Feito."
        mensagens.append({"role": "assistant", "content": resp.content})
        resultados = []
        for bloco in resp.content:
            if bloco.type != "tool_use":
                continue
            try:
                saida = enxugar(executar(bloco.name, bloco.input))
            except Exception as e:  # erro de rede, campo faltando etc.
                saida = {"erro": f"{type(e).__name__}: {e}"}
            print(f"FERRAMENTA {bloco.name} {json.dumps(bloco.input, ensure_ascii=False)[:200]} -> "
                  f"{json.dumps(saida, ensure_ascii=False)[:300]}", flush=True)
            resultados.append({
                "type": "tool_result",
                "tool_use_id": bloco.id,
                "content": json.dumps(saida, ensure_ascii=False)[:8000],
            })
        mensagens.append({"role": "user", "content": resultados})
    return "Não consegui terminar esse pedido, tenta de novo de outro jeito."


# --------------------------------------------------------------------------
# Rotas HTTP
# --------------------------------------------------------------------------

def autorizado() -> bool:
    esperado = os.environ.get("APP_SECRET", "")
    recebido = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    return bool(esperado) and hmac.compare_digest(recebido, esperado)


@app.post("/api/comando")
def comando():
    if not autorizado():
        return jsonify({"resposta": "Senha do atalho incorreta."}), 401
    pedido = ((request.get_json(silent=True) or {}).get("pedido") or "").strip()
    if not pedido:
        return jsonify({"resposta": "Não ouvi nenhum pedido."}), 400
    try:
        return jsonify({"resposta": processar(pedido)})
    except Exception as e:
        return jsonify({"resposta": "Deu um erro no servidor.", "detalhe": str(e)[:300]}), 500


@app.get("/api/comando")
@app.get("/")
def saude():
    return jsonify({"status": "ok", "modelo": MODEL})


# A Vercel reescreve todas as rotas para /api/index; aqui POST vira comando e GET vira checagem
@app.route("/<path:_caminho>", methods=["GET", "POST"])
def qualquer_rota(_caminho):
    return comando() if request.method == "POST" else saude()
