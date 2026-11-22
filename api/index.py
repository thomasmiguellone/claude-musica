"""
Claude Música: control Spotify by voice through Claude.

Main endpoint: POST /api/comando
  Header:  Authorization: Bearer <APP_SECRET>
  Body:    {"pedido": "play something like Tame Impala"}
  Returns: {"resposta": "Playing The Less I Know The Better."}

(The "pedido"/"resposta" field names are kept for compatibility with existing iPhone Shortcuts.)
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
MAX_STEPS = 10  # max tool-use rounds per request

_token = {"value": None, "expires": 0.0}


# --------------------------------------------------------------------------
# Spotify: auth and requests
# --------------------------------------------------------------------------

def spotify_token() -> str:
    """Refresh the access token from the refresh token (cached for ~55 min)."""
    if _token["value"] and time.time() < _token["expires"]:
        return _token["value"]
    cred = f"{os.environ['SPOTIFY_CLIENT_ID']}:{os.environ['SPOTIFY_CLIENT_SECRET']}"
    r = requests.post(
        "https://accounts.spotify.com/api/token",
        data={"grant_type": "refresh_token", "refresh_token": os.environ["SPOTIFY_REFRESH_TOKEN"]},
        headers={"Authorization": "Basic " + base64.b64encode(cred.encode()).decode()},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    _token["value"] = data["access_token"]
    _token["expires"] = time.time() + data.get("expires_in", 3600) - 300
    return _token["value"]


def sp(method: str, path: str, params=None, body=None):
    """Call the Spotify API and return the JSON (or a short status)."""
    r = requests.request(
        method,
        API + path,
        params={k: v for k, v in (params or {}).items() if v is not None},
        json=body,
        headers={"Authorization": f"Bearer {spotify_token()}"},
        timeout=15,
    )
    if r.status_code == 404 and path.startswith("/me/player"):
        return {"error": "No active device. Ask the user to open Spotify, or use transfer_playback."}
    if r.status_code >= 400:
        return {"error": r.status_code, "detail": r.text[:300]}
    if r.status_code == 204 or not r.content:
        return {"ok": True}
    try:
        return r.json()
    except ValueError:
        return {"ok": True}


# Fields that only cost tokens and don't help the model decide anything
DROP = {
    "available_markets", "images", "external_urls", "external_ids", "href",
    "preview_url", "is_local", "disc_number", "linked_from", "restrictions",
    "copyrights", "label", "popularity_label", "followers", "primary_color",
    "snapshot_id", "video_thumbnail", "is_playable", "explicit", "track_number",
    "release_date_precision", "total_tracks", "album_group", "genres",
    "actions", "smart_shuffle", "html_description",
}


def slim(obj, depth=0):
    """Strip noise from Spotify JSON and collapse artists to names."""
    if depth > 6:
        return "…"
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in DROP or v is None:
                continue
            if k == "artists" and isinstance(v, list):
                out["artists"] = ", ".join(a.get("name", "") for a in v if isinstance(a, dict))
                continue
            if k == "album" and isinstance(v, dict):
                out["album"] = v.get("name")
                continue
            if k == "owner" and isinstance(v, dict):
                out["owner"] = v.get("display_name") or v.get("id")
                continue
            out[k] = slim(v, depth + 1)
        return out
    if isinstance(obj, list):
        return [slim(x, depth + 1) for x in obj]
    return obj


def batches(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


# --------------------------------------------------------------------------
# Tools available to Claude
# --------------------------------------------------------------------------

def _t(name, description, props=None, required=None):
    return {
        "name": name,
        "description": description,
        "input_schema": {"type": "object", "properties": props or {}, "required": required or []},
    }

URIS = {"type": "array", "items": {"type": "string"}, "description": "Spotify URIs, e.g. spotify:track:ID"}

TOOLS = [
    _t("search", "Search the Spotify catalog. At most 10 results per type.",
       {"query": {"type": "string", "description": "Search text. Supports filters like artist:Name track:Name year:1990"},
        "types": {"type": "string", "description": "Comma separated: track, album, artist, playlist, show, episode, audiobook. Default: track"},
        "limit": {"type": "integer", "description": "1 to 10, default 5"}},
       ["query"]),
    _t("playback_state", "What is playing now, on which device, volume, shuffle and repeat."),
    _t("play", "Play music. Use 'uris' for a list of tracks (played in order) OR 'context' for an album, playlist or artist. With no arguments, resumes playback.",
       {"uris": URIS,
        "context": {"type": "string", "description": "Album, playlist or artist URI"},
        "start_at": {"type": "integer", "description": "Index of the first track within the context"},
        "device_id": {"type": "string"}}),
    _t("pause", "Pause playback."),
    _t("next_track", "Skip to the next track."),
    _t("previous_track", "Go back to the previous track."),
    _t("seek", "Jump to a position in the current track.",
       {"position_ms": {"type": "integer"}}, ["position_ms"]),
    _t("set_volume", "Set the volume (0 to 100). Some devices, like iPhones, may not accept it.",
       {"percent": {"type": "integer"}}, ["percent"]),
    _t("set_shuffle", "Turn shuffle on or off.", {"on": {"type": "boolean"}}, ["on"]),
    _t("set_repeat", "Repeat mode.",
       {"mode": {"type": "string", "enum": ["track", "context", "off"]}}, ["mode"]),
    _t("add_to_queue", "Add tracks or episodes to the end of the queue, in the given order.", {"uris": URIS}, ["uris"]),
    _t("get_queue", "Show the current track and what's next in the queue. Note: the API cannot remove or reorder queue items."),
    _t("devices", "List the available Spotify devices."),
    _t("transfer_playback", "Move playback to another device.",
       {"device_id": {"type": "string"}, "play": {"type": "boolean"}}, ["device_id"]),
    _t("recently_played", "Recently played tracks.", {"limit": {"type": "integer", "description": "1 to 50"}}),
    _t("top_items", "The user's top artists or tracks.",
       {"type": {"type": "string", "enum": ["tracks", "artists"]},
        "time_range": {"type": "string", "enum": ["short_term", "medium_term", "long_term"],
                       "description": "short_term ≈ 4 weeks, medium_term ≈ 6 months, long_term ≈ years"},
        "limit": {"type": "integer"}},
       ["type"]),
    _t("my_playlists", "List the user's playlists (with id and name).",
       {"limit": {"type": "integer"}, "offset": {"type": "integer"}}),
    _t("playlist_items", "Tracks in a playlist (only works for playlists the user owns or collaborates on).",
       {"playlist_id": {"type": "string"}, "limit": {"type": "integer"}, "offset": {"type": "integer"}},
       ["playlist_id"]),
    _t("create_playlist", "Create a new playlist and optionally add tracks right away.",
       {"name": {"type": "string"}, "description": {"type": "string"},
        "public": {"type": "boolean", "description": "Default: false"}, "uris": URIS},
       ["name"]),
    _t("add_to_playlist", "Add tracks to a playlist.",
       {"playlist_id": {"type": "string"}, "uris": URIS,
        "position": {"type": "integer", "description": "Optional, default is the end"}},
       ["playlist_id", "uris"]),
    _t("remove_from_playlist", "Remove tracks from a playlist.",
       {"playlist_id": {"type": "string"}, "uris": URIS}, ["playlist_id", "uris"]),
    _t("reorder_playlist", "Move a block of tracks within a playlist.",
       {"playlist_id": {"type": "string"}, "range_start": {"type": "integer"},
        "insert_before": {"type": "integer"}, "range_length": {"type": "integer", "description": "Default 1"}},
       ["playlist_id", "range_start", "insert_before"]),
    _t("edit_playlist", "Change a playlist's name, description or visibility.",
       {"playlist_id": {"type": "string"}, "name": {"type": "string"},
        "description": {"type": "string"}, "public": {"type": "boolean"}},
       ["playlist_id"]),
    _t("save_to_library", "Like tracks, save albums, follow playlists or artists (pass their URIs).",
       {"uris": URIS}, ["uris"]),
    _t("remove_from_library", "Unlike tracks, remove albums, unfollow playlists or artists.",
       {"uris": URIS}, ["uris"]),
    _t("library_contains", "Check whether items are already saved or followed.", {"uris": URIS}, ["uris"]),
    _t("liked_songs", "List the user's liked songs.",
       {"limit": {"type": "integer"}, "offset": {"type": "integer"}}),
    _t("followed_artists", "List the artists the user follows.", {"limit": {"type": "integer"}}),
    _t("artist_albums", "An artist's discography.",
       {"artist_id": {"type": "string"}, "groups": {"type": "string", "description": "album,single,compilation"},
        "limit": {"type": "integer"}},
       ["artist_id"]),
    _t("album_tracks", "Tracks on an album.", {"album_id": {"type": "string"}}, ["album_id"]),
]


def _id(value: str) -> str:
    """Accept a raw ID, a URI (spotify:playlist:ID) or a link, and return the ID."""
    value = value.strip()
    if value.startswith("spotify:"):
        return value.split(":")[-1]
    if "open.spotify.com" in value:
        return value.rstrip("/").split("/")[-1].split("?")[0]
    return value


def run_tool(name: str, a: dict):
    lim = lambda default, maximum: max(1, min(int(a.get("limit", default)), maximum))

    if name == "search":
        return sp("GET", "/search", {"q": a["query"], "type": a.get("types", "track"), "limit": lim(5, 10)})
    if name == "playback_state":
        return sp("GET", "/me/player") or {"info": "Nothing playing"}
    if name == "play":
        body = {}
        if a.get("uris"):
            body["uris"] = a["uris"]
        elif a.get("context"):
            body["context_uri"] = a["context"]
            if a.get("start_at") is not None:
                body["offset"] = {"position": a["start_at"]}
        return sp("PUT", "/me/player/play", {"device_id": a.get("device_id")}, body or None)
    if name == "pause":
        return sp("PUT", "/me/player/pause")
    if name == "next_track":
        return sp("POST", "/me/player/next")
    if name == "previous_track":
        return sp("POST", "/me/player/previous")
    if name == "seek":
        return sp("PUT", "/me/player/seek", {"position_ms": a["position_ms"]})
    if name == "set_volume":
        return sp("PUT", "/me/player/volume", {"volume_percent": max(0, min(100, a["percent"]))})
    if name == "set_shuffle":
        return sp("PUT", "/me/player/shuffle", {"state": "true" if a["on"] else "false"})
    if name == "set_repeat":
        return sp("PUT", "/me/player/repeat", {"state": a["mode"]})
    if name == "add_to_queue":
        results = [sp("POST", "/me/player/queue", {"uri": u}) for u in a["uris"]]
        errors = [r for r in results if "error" in r]
        return {"added": len(results) - len(errors), "errors": errors[:3]}
    if name == "get_queue":
        r = sp("GET", "/me/player/queue")
        if isinstance(r, dict) and "queue" in r:
            r["queue"] = r["queue"][:15]
        return r
    if name == "devices":
        return sp("GET", "/me/player/devices")
    if name == "transfer_playback":
        return sp("PUT", "/me/player", body={"device_ids": [a["device_id"]], "play": a.get("play", True)})
    if name == "recently_played":
        return sp("GET", "/me/player/recently-played", {"limit": lim(10, 50)})
    if name == "top_items":
        return sp("GET", f"/me/top/{a['type']}", {"time_range": a.get("time_range", "medium_term"), "limit": lim(10, 50)})
    if name == "my_playlists":
        r = sp("GET", "/me/playlists", {"limit": lim(50, 50), "offset": a.get("offset", 0)})
        if isinstance(r, dict) and "items" in r:
            r["items"] = [{"name": p.get("name"), "id": p.get("id"), "uri": p.get("uri"),
                           "owner": (p.get("owner") or {}).get("display_name")} for p in r["items"] if p]
        return r
    if name == "playlist_items":
        return sp("GET", f"/playlists/{_id(a['playlist_id'])}/items",
                  {"limit": lim(50, 50), "offset": a.get("offset", 0)})
    if name == "create_playlist":
        new = sp("POST", "/me/playlists", body={
            "name": a["name"], "description": a.get("description", ""), "public": a.get("public", False)})
        if "error" in new:
            return new
        if a.get("uris"):
            for batch in batches(a["uris"], 100):
                sp("POST", f"/playlists/{new['id']}/items", body={"uris": batch})
        return {"created": new.get("name"), "id": new.get("id"), "uri": new.get("uri"),
                "tracks_added": len(a.get("uris") or [])}
    if name == "add_to_playlist":
        pid = _id(a["playlist_id"])
        pos = a.get("position")
        for i, batch in enumerate(batches(a["uris"], 100)):
            body = {"uris": batch}
            if pos is not None:
                body["position"] = pos + i * 100
            r = sp("POST", f"/playlists/{pid}/items", body=body)
            if "error" in r:
                return r
        return {"ok": True, "added": len(a["uris"])}
    if name == "remove_from_playlist":
        pid = _id(a["playlist_id"])
        for batch in batches(a["uris"], 100):
            r = sp("DELETE", f"/playlists/{pid}/items", body={"items": [{"uri": u} for u in batch]})
            if "error" in r:
                return r
        return {"ok": True, "removed": len(a["uris"])}
    if name == "reorder_playlist":
        return sp("PUT", f"/playlists/{_id(a['playlist_id'])}/items", body={
            "range_start": a["range_start"], "insert_before": a["insert_before"],
            "range_length": a.get("range_length", 1)})
    if name == "edit_playlist":
        body = {k: a[k] for k in ("name", "description", "public") if k in a}
        return sp("PUT", f"/playlists/{_id(a['playlist_id'])}", body=body)
    if name in ("save_to_library", "remove_from_library"):
        method = "PUT" if name == "save_to_library" else "DELETE"
        for batch in batches(a["uris"], 40):
            r = sp(method, "/me/library", {"uris": ",".join(batch)})
            if "error" in r:
                return r
        return {"ok": True, "items": len(a["uris"])}
    if name == "library_contains":
        r = sp("GET", "/me/library/contains", {"uris": ",".join(a["uris"][:40])})
        return dict(zip(a["uris"], r)) if isinstance(r, list) else r
    if name == "liked_songs":
        return sp("GET", "/me/tracks", {"limit": lim(20, 50), "offset": a.get("offset", 0)})
    if name == "followed_artists":
        return sp("GET", "/me/following", {"type": "artist", "limit": lim(20, 50)})
    if name == "artist_albums":
        return sp("GET", f"/artists/{_id(a['artist_id'])}/albums",
                  {"include_groups": a.get("groups", "album,single"), "limit": lim(20, 50)})
    if name == "album_tracks":
        return sp("GET", f"/albums/{_id(a['album_id'])}/tracks", {"limit": 50})
    return {"error": f"Unknown tool: {name}"}


# --------------------------------------------------------------------------
# Claude loop
# --------------------------------------------------------------------------

SYSTEM = """You control the user's Spotify by voice. Requests come from iPhone dictation, \
so song and artist names may be mistranscribed: interpret them by meaning.

How to act:
- Carry out the request end to end using the tools, without asking for confirmation.
- To find songs, use 'search' with filters (e.g. track:Name artist:Name). Search returns at most 10 items.
- Vague requests ("that song from movie X", "something like Y", "music to study to"): use your own \
music knowledge to pick concrete tracks, then search for each one. Spotify's recommendations API \
no longer exists, so the picks are yours.
- To play several songs, prefer 'play' with a list of uris (they play in order). Use 'add_to_queue' \
when the user asks for the queue or "after this one".
- "This song" means what is playing now: check it with 'playback_state'.
- The default device is the iPhone (type Smartphone), because requests come from its headphones. Before \
playing, call 'devices': if the iPhone is not the active one, pass its device_id to 'play' (or use \
'transfer_playback' to resume there). Only use another device if the user asks (e.g. "on the computer", \
"on the TV"). If the iPhone is not in the list, say in one sentence that the Spotify app needs to be open on it.
- The API cannot remove or reorder queue items; if asked, explain that in one sentence.

Final answer: ONE short sentence, natural to be read aloud by Siri, in the same language the user spoke. \
No markdown, no emojis, no lists, no URIs."""

client = anthropic.Anthropic() if os.environ.get("ANTHROPIC_API_KEY") else None

# Prompt caching: the system prompt and tool list are identical on every call,
# so they are cached and billed at a fraction of the input price after the first call.
SYSTEM_BLOCKS = [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}]
TOOLS_CACHED = TOOLS[:-1] + [{**TOOLS[-1], "cache_control": {"type": "ephemeral"}}]


def handle(request_text: str) -> str:
    print(f"REQUEST: {request_text}", flush=True)
    messages = [{"role": "user", "content": request_text}]
    for _ in range(MAX_STEPS):
        resp = client.messages.create(
            model=MODEL, max_tokens=1500, system=SYSTEM_BLOCKS, tools=TOOLS_CACHED, messages=messages)
        u = resp.usage
        print(f"USAGE in={u.input_tokens} cache_read={getattr(u, 'cache_read_input_tokens', 0)} "
              f"cache_write={getattr(u, 'cache_creation_input_tokens', 0)} out={u.output_tokens}", flush=True)
        if resp.stop_reason != "tool_use":
            text = " ".join(b.text for b in resp.content if b.type == "text").strip()
            print(f"REPLY: {text}", flush=True)
            return text or "Done."
        messages.append({"role": "assistant", "content": resp.content})
        results = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            try:
                output = slim(run_tool(block.name, block.input))
            except Exception as e:  # network error, missing field, etc.
                output = {"error": f"{type(e).__name__}: {e}"}
            print(f"TOOL {block.name} {json.dumps(block.input, ensure_ascii=False)[:200]} -> "
                  f"{json.dumps(output, ensure_ascii=False)[:300]}", flush=True)
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(output, ensure_ascii=False)[:8000],
            })
        messages.append({"role": "user", "content": results})
    return "I couldn't finish that request, try asking another way."


# --------------------------------------------------------------------------
# HTTP routes
# --------------------------------------------------------------------------

def authorized() -> bool:
    expected = os.environ.get("APP_SECRET", "")
    received = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    return bool(expected) and hmac.compare_digest(received, expected)


@app.post("/api/comando")
def command():
    if not authorized():
        return jsonify({"resposta": "Wrong shortcut password."}), 401
    text = ((request.get_json(silent=True) or {}).get("pedido") or "").strip()
    if not text:
        return jsonify({"resposta": "I didn't hear a request."}), 400
    try:
        return jsonify({"resposta": handle(text)})
    except Exception as e:
        return jsonify({"resposta": "Server error.", "detail": str(e)[:300]}), 500


@app.get("/api/comando")
@app.get("/")
def health():
    return jsonify({"status": "ok", "model": MODEL})


# Vercel rewrites every route to /api/index; here POST becomes a command and GET a health check
@app.route("/<path:_path>", methods=["GET", "POST"])
def any_route(_path):
    return command() if request.method == "POST" else health()
