"""
One question to an AI model, one JSON answer back. Shared by the tools
that need it (vibe playlists, genre suggestions).

Where the answer comes from (setting 'AI through', or a --backend flag):
  auto   — codex if its CLI is available, then cli if Claude Code is installed,
           then gemini if its key is set, else api.
  cli    — the Claude Code command line, on a Claude subscription; log in
           once by running `claude` and typing /login. No API costs.
  codex  — the Codex command line, using the signed-in Codex account.
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
from i18n import _

BACKENDS = ("auto", "cli", "codex", "gemini", "api")


def pick_backend(cfg, requested=None):
    """'auto' picks the first available CLI or configured API backend."""
    backend = requested or str(cfg.get("vibe_backend") or "auto")
    if backend != "auto":
        return backend
    if settings.codex_cli(cfg):
        return "codex"
    if settings.claude_cli(cfg):
        return "cli"
    if os.environ.get("GEMINI_API_KEY", "").strip():
        return "gemini"
    return "api"


def _backend_chain(cfg, requested):
    """Return the requested backend followed by available quota fallbacks."""
    setting = requested or str(cfg.get("vibe_backend") or "auto")
    first = pick_backend(cfg, requested)
    if setting != "auto":
        return [first]
    chain = [first]
    if settings.codex_cli(cfg) and "codex" not in chain:
        chain.append("codex")
    if settings.claude_cli(cfg) and "cli" not in chain:
        chain.append("cli")
    if os.environ.get("GEMINI_API_KEY", "").strip() and "gemini" not in chain:
        chain.append("gemini")
    if os.environ.get("ANTHROPIC_API_KEY", "").strip() and "api" not in chain:
        chain.append("api")
    return chain


def backend_name(backend):
    return {"gemini": "Gemini", "codex": "Codex"}.get(backend, f"Claude ({backend})")


def ask_json(cfg, system, user, schema, backend=None):
    """Ask for JSON; in auto mode, try the next backend after any failure."""
    chain = _backend_chain(cfg, backend)
    calls = {"api": _ask_api, "gemini": _ask_gemini,
             "codex": _ask_codex, "cli": _ask_cli}
    for index, selected in enumerate(chain):
        try:
            return calls[selected](cfg, system, user, schema)
        except (Exception, SystemExit) as error:
            if index + 1 >= len(chain):
                raise
            following = chain[index + 1]
            print(_("  {ai} failed ({error}); trying {next}...").format(
                ai=backend_name(selected), error=str(error).strip()[:240],
                next=backend_name(following)), flush=True)


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
        sys.exit(_("Claude Code (claude) not found. Install it, or set its path in "
                   "Settings -> AI, or switch to another mode there."))
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
        sys.exit(_("Claude Code didn't answer within 15 minutes."))
    finally:
        os.remove(system_file)

    out = r.stdout.decode("utf-8", "replace").strip()
    try:
        data = json.loads(out)
    except ValueError:
        err = r.stderr.decode("utf-8", "replace").strip()
        sys.exit(_("Unexpected answer from Claude Code (exit {code}):").format(code=r.returncode)
                 + "\n" + (err or out)[:1000])
    if data.get("is_error"):
        msg = str(data.get("result") or data.get("subtype") or _("unknown error"))
        if "login" in msg.lower():
            sys.exit(_("Claude Code isn't logged in. Once, in a terminal:\n"
                       "  \"{exe}\"\n"
                       "then type /login, sign in with your Claude account, and /exit.").format(exe=exe))
        sys.exit(f"Claude Code: {msg}")
    answer = data.get("structured_output")
    if answer is None:
        try:
            answer = json.loads(data.get("result") or "")
        except ValueError:
            sys.exit(_("Claude Code didn't return the expected JSON:") + "\n"
                     + str(data.get('result'))[:1000])
    return answer


# ------------------------------------------------------------------- Codex


def _ask_codex(cfg, system, user, schema):
    """Ask Codex CLI for one structured answer, without a persistent session."""
    exe = settings.codex_cli(cfg)
    if not exe:
        sys.exit(_("Codex CLI (codex) not found. Install it, or set its path in Settings -> AI."))

    with tempfile.TemporaryDirectory(prefix="podvault-codex-") as work:
        schema_file = os.path.join(work, "schema.json")
        answer_file = os.path.join(work, "answer.json")
        with open(schema_file, "w", encoding="utf-8") as f:
            json.dump(schema, f, ensure_ascii=False)
        cmd = [exe, "exec", "--ephemeral", "--sandbox", "read-only",
               "--skip-git-repo-check", "--output-schema", schema_file,
               "--output-last-message", answer_file]
        model = str(cfg.get("codex_cli_model") or "").strip()
        if model:
            cmd += ["--model", model]
        cmd.append("-")
        try:
            r = subprocess.run(cmd, input=system + "\n\n" + user, text=True,
                               capture_output=True, timeout=900, cwd=work)
        except subprocess.TimeoutExpired:
            sys.exit(_("Codex didn't answer within 15 minutes."))
        except OSError as e:
            sys.exit(_("Could not start Codex CLI: {error}").format(error=e))

        if r.returncode:
            detail = (r.stderr or r.stdout).strip()[:1000]
            if "login" in detail.lower() or "auth" in detail.lower():
                sys.exit(_("Codex CLI isn't signed in. Run `codex` in a terminal and sign in."))
            sys.exit(_("Codex CLI failed (exit {code}): {error}").format(
                code=r.returncode, error=detail or _("unknown error")))
        try:
            with open(answer_file, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError) as e:
            sys.exit(_("Codex CLI didn't return the expected JSON: {error}").format(error=e))


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
        sys.exit(_("No Gemini API key found.\n\n"
                   "Get a free one at aistudio.google.com -> Get API key, then set it once:\n"
                   "  Windows:        setx GEMINI_API_KEY \"...\"\n"
                   "  macOS / Linux:  export GEMINI_API_KEY=\"...\"  (in ~/.zshrc or ~/.bashrc)\n"
                   "and open a new terminal (or restart PodVault)."))
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
        sys.exit(_("Could not reach Gemini: {error}").format(error=e))
    if r.status_code in (400, 401, 403) and "key" in r.text.lower():
        sys.exit(_("Gemini rejected the API key. Check GEMINI_API_KEY."))
    if r.status_code == 404:
        sys.exit(_("Gemini doesn't know the model '{model}'. Set another one in Settings -> AI.").format(model=model))
    if r.status_code == 429:
        sys.exit(_("Gemini's free-tier limit is reached for now. "
                   "Wait a minute (or until tomorrow for the daily limit)."))
    if r.status_code != 200:
        sys.exit(_("Gemini error {code}: {text}").format(code=r.status_code, text=r.text[:500]))
    try:
        text = r.json()["choices"][0]["message"]["content"]
        # tolerate a ```json fence around the object
        return json.loads(text[text.index("{"):text.rindex("}") + 1])
    except (ValueError, KeyError, IndexError):
        sys.exit(_("Gemini didn't return the expected JSON:") + "\n" + r.text[:1000])


# ---------------------------------------------------------- Anthropic API


def _ask_api(cfg, system, user, schema):
    """Through the Anthropic API (paid per use)."""
    try:
        import anthropic
    except ImportError:
        sys.exit(_("The anthropic package is missing: {command}").format(
            command=f"{sys.executable} -m pip install anthropic"))

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
        sys.exit(_("The Anthropic API key was rejected. Check ANTHROPIC_API_KEY."))
    except anthropic.PermissionDeniedError as e:
        sys.exit(_("The API key may not use {model}: {error}").format(model=model, error=e.message))
    except anthropic.RateLimitError:
        sys.exit(_("Rate limited by the Anthropic API. Try again in a minute."))
    except anthropic.APIStatusError as e:
        sys.exit(_("Anthropic API error {code}: {error}").format(code=e.status_code, error=e.message))
    except anthropic.APIConnectionError:
        sys.exit(_("Could not reach the Anthropic API. Check the internet connection."))
    except TypeError as e:
        # the SDK reports missing credentials as a TypeError
        if "authentication" not in str(e):
            raise
        sys.exit(_("No Anthropic API key found.\n\n"
                   "Create one at console.anthropic.com -> API keys, then set it once:\n"
                   "  Windows:        setx ANTHROPIC_API_KEY \"sk-ant-...\"\n"
                   "  macOS / Linux:  export ANTHROPIC_API_KEY=\"sk-ant-...\"  (in ~/.zshrc or ~/.bashrc)\n"
                   "and open a new terminal (or restart PodVault)."))

    if response.stop_reason == "refusal":
        sys.exit(_("Claude declined this request. Try wording it differently."))
    if response.stop_reason == "max_tokens":
        sys.exit(_("The answer was cut off (too long)."))
    text = next((b.text for b in response.content if b.type == "text"), "")
    return json.loads(text)
