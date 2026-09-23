"""
One question to an AI model, one JSON answer back. Shared by the tools
that need it (vibe playlists, genre suggestions).

Where the answer comes from (setting 'AI through', or a --backend flag):
  auto   — cli if Claude Code is installed, else gemini if its key is set,
           else api.
  cli    — the Claude Code command line, on a Claude subscription; log in
           once by running `claude` and typing /login. No API costs.
  gemini — Google Gemini; a free key from aistudio.google.com in
           GEMINI_API_KEY. For anyone without a Claude subscription.
  api    — the Anthropic API, paid per use; needs ANTHROPIC_API_KEY.

Every backend returns the parsed JSON object or exits with a message that
says what to do.
"""

import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import settings

BACKENDS = ("auto", "cli", "gemini", "api")


def pick_backend(cfg, requested=None):
    """'auto' becomes cli, gemini or api, depending on what this machine has."""
    backend = requested or str(cfg.get("vibe_backend") or "auto")
    if backend != "auto":
        return backend
    if settings.claude_cli(cfg):
        return "cli"
    if os.environ.get("GEMINI_API_KEY", "").strip():
        return "gemini"
    return "api"


def backend_name(backend):
    return "Gemini" if backend == "gemini" else f"Claude ({backend})"


def ask_json(cfg, system, user, schema, backend=None):
    """Ask once; returns the answer as a dict matching schema."""
    backend = pick_backend(cfg, backend)
    if backend == "api":
        return _ask_api(cfg, system, user, schema)
    if backend == "gemini":
        return _ask_gemini(cfg, system, user, schema)
    return _ask_cli(cfg, system, user, schema)


# ------------------------------------------------------------ Claude Code


def _ask_cli(cfg, system, user, schema):
    """Through the Claude Code command line (subscription).

    Long texts don't fit a Windows command line (32k characters), so the
    system text goes in as a file. Tools, MCP servers, skills and project
    settings are switched off: this is a single question, not an agent
    session, and nothing is saved to the session history.
    """
    exe = settings.claude_cli(cfg)
    if not exe:
        sys.exit("Claude Code (claude) not found. Install it, or set its path in "
                 "Settings -> AI, or switch to another mode there.")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8",
                                     delete=False) as f:
        f.write(system)
        system_file = f.name
    cmd = [exe, "-p", user,
           "--output-format", "json", "--json-schema", json.dumps(schema),
           "--system-prompt-file", system_file,
           "--tools", "", "--strict-mcp-config", "--setting-sources", "",
           "--disable-slash-commands", "--no-session-persistence"]
    if str(cfg.get("claude_cli_model") or "").strip():
        cmd += ["--model", str(cfg["claude_cli_model"]).strip()]
    try:
        r = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=900)
    except subprocess.TimeoutExpired:
        sys.exit("Claude Code didn't answer within 15 minutes.")
    finally:
        os.remove(system_file)

    out = r.stdout.decode("utf-8", "replace").strip()
    try:
        data = json.loads(out)
    except ValueError:
        err = r.stderr.decode("utf-8", "replace").strip()
        sys.exit(f"Unexpected answer from Claude Code (exit {r.returncode}):\n{(err or out)[:1000]}")
    if data.get("is_error"):
        msg = str(data.get("result") or data.get("subtype") or "unknown error")
        if "login" in msg.lower():
            sys.exit("Claude Code isn't logged in. Once, in a terminal:\n"
                     f"  \"{exe}\"\n"
                     "then type /login, sign in with your Claude account, and /exit.")
        sys.exit(f"Claude Code: {msg}")
    answer = data.get("structured_output")
    if answer is None:
        try:
            answer = json.loads(data.get("result") or "")
        except ValueError:
            sys.exit(f"Claude Code didn't return the expected JSON:\n{str(data.get('result'))[:1000]}")
    return answer


# ----------------------------------------------------------------- Gemini


GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"


def _ask_gemini(cfg, system, user, schema):
    """Through Google Gemini's OpenAI-compatible endpoint.

    The free tier (a key from aistudio.google.com, no card) is enough here:
    the largest question, the whole library, is ~50k tokens, under the
    per-minute limit.
    """
    import requests

    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        sys.exit("No Gemini API key found.\n\n"
                 "Get a free one at aistudio.google.com -> Get API key, then set it once:\n"
                 "  setx GEMINI_API_KEY \"...\"\n"
                 "and open a new terminal (or restart Music Utility).")
    model = str(cfg.get("gemini_model") or "gemini-3.8-flash")
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "answer", "schema": schema}},
    }
    try:
        r = requests.post(GEMINI_URL, json=body, timeout=300,
                          headers={"Authorization": f"Bearer {key}"})
    except requests.RequestException as e:
        sys.exit(f"Could not reach Gemini: {e}")
    if r.status_code in (400, 401, 403) and "key" in r.text.lower():
        sys.exit("Gemini rejected the API key. Check GEMINI_API_KEY.")
    if r.status_code == 404:
        sys.exit(f"Gemini doesn't know the model '{model}'. Set another one in Settings -> AI.")
    if r.status_code == 429:
        sys.exit("Gemini's free-tier limit is reached for now. "
                 "Wait a minute (or until tomorrow for the daily limit).")
    if r.status_code != 200:
        sys.exit(f"Gemini error {r.status_code}: {r.text[:500]}")
    try:
        text = r.json()["choices"][0]["message"]["content"]
        # tolerate a ```json fence around the object
        return json.loads(text[text.index("{"):text.rindex("}") + 1])
    except (ValueError, KeyError, IndexError):
        sys.exit(f"Gemini didn't return the expected JSON:\n{r.text[:1000]}")


# ---------------------------------------------------------- Anthropic API


def _ask_api(cfg, system, user, schema):
    """Through the Anthropic API (paid per use)."""
    try:
        import anthropic
    except ImportError:
        sys.exit("The anthropic package is missing: python\\python.exe -m pip install anthropic")

    model = str(cfg.get("anthropic_model") or "claude-opus-5")
    try:
        client = anthropic.Anthropic()
        # The system text goes into a cached block: asking again within the
        # hour reuses it instead of paying for it again.
        # fallbacks="default": if a safety classifier declines the request,
        # the API retries it on a fallback model instead of refusing.
        response = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            thinking={"type": "adaptive"},
            system=[{"type": "text", "text": system,
                     "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
    except anthropic.AuthenticationError:
        sys.exit("The Anthropic API key was rejected. Check ANTHROPIC_API_KEY.")
    except anthropic.PermissionDeniedError as e:
        sys.exit(f"The API key may not use {model}: {e.message}")
    except anthropic.RateLimitError:
        sys.exit("Rate limited by the Anthropic API. Try again in a minute.")
    except anthropic.APIStatusError as e:
        sys.exit(f"Anthropic API error {e.status_code}: {e.message}")
    except anthropic.APIConnectionError:
        sys.exit("Could not reach the Anthropic API. Check the internet connection.")
    except TypeError as e:
        # the SDK reports missing credentials as a TypeError
        if "authentication" not in str(e):
            raise
        sys.exit("No Anthropic API key found.\n\n"
                 "Create one at console.anthropic.com -> API keys, then set it once:\n"
                 "  setx ANTHROPIC_API_KEY \"sk-ant-...\"\n"
                 "and open a new terminal (or restart Music Utility).")

    if response.stop_reason == "refusal":
        sys.exit("Claude declined this request. Try wording it differently.")
    if response.stop_reason == "max_tokens":
        sys.exit("The answer was cut off (too long).")
    text = next((b.text for b in response.content if b.type == "text"), "")
    return json.loads(text)
