"""
Skill file attachments.

Skill files are plain-text context documents injected as extra system
messages before the user's own system prompt. They are identified by a
minimal XML envelope:

    <skill>
      <name>my-skill</name>
      <version>1.0.0</version>   <!-- optional -->
      <description>…</description>  <!-- optional -->
      … body …
    </skill>

Files are stored under ~/.ai-bridge/skills/ with names following the
pattern  {stem}--{sha256[:16]}.txt  so updates to content are detected
even when the logical name stays the same.

Constraints (matching codex-chatgpt-web behaviour):
  - Max 10 skill files active at once
  - Max combined size 20 MB
  - Injected as {"role": "system", "content": "<file text>"} prepended
    to the messages list (after any existing system message).
"""
import hashlib
import os
import re
from pathlib import Path
from typing import Optional

from . import config as cfg

_MAX_FILES = 10
_MAX_BYTES = 20 * 1024 * 1024  # 20 MB combined
_SKILLS_DIR: Path = cfg.CONFIG_DIR / 'skills'

# Minimal XML envelope detector — we do NOT load an XML parser just for this
_ENVELOPE_RE = re.compile(r'<skill\b[^>]*>(.+?)</skill>', re.DOTALL | re.IGNORECASE)
_NAME_RE = re.compile(r'<name\b[^>]*>([^<]+)</name>', re.DOTALL | re.IGNORECASE)


def _skills_dir() -> Path:
    _SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    return _SKILLS_DIR


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _safe_stem(name: str) -> str:
    """Sanitize a skill name to a safe filename stem."""
    stem = re.sub(r'[^\w\-]', '_', name.strip())[:60] or 'skill'
    return stem


# ── Storage ──────────────────────────────────────────────────────────────────

def add_skill(name: str, content: str) -> dict:
    """Store a skill file. Returns {'ok': True, 'path': ...} or {'ok': False, ...}."""
    existing = list_skills()
    if len(existing) >= _MAX_FILES:
        return {'ok': False, 'error': f'Limite de {_MAX_FILES} skills atingido'}

    total = sum(s['size'] for s in existing) + len(content.encode())
    if total > _MAX_BYTES:
        return {'ok': False, 'error': 'Limite de tamanho combinado (20 MB) excedido'}

    stem = _safe_stem(name)
    digest = _digest(content)
    filename = f'{stem}--{digest}.txt'
    path = _skills_dir() / filename

    tmp = path.with_suffix(f'.tmp-{os.getpid()}')
    tmp.write_text(content, encoding='utf-8')
    tmp.rename(path)

    return {'ok': True, 'name': name, 'filename': filename, 'path': str(path)}


def remove_skill(filename: str) -> dict:
    path = _skills_dir() / Path(filename).name  # prevent traversal
    if not path.exists():
        return {'ok': False, 'error': 'Skill não encontrada'}
    path.unlink()
    return {'ok': True}


def list_skills() -> list[dict]:
    d = _skills_dir()
    result = []
    for p in sorted(d.glob('*.txt')):
        try:
            size = p.stat().st_size
            content = p.read_text(encoding='utf-8', errors='replace')
            name = _extract_name(content) or p.stem.split('--')[0]
            result.append({
                'filename': p.name,
                'name': name,
                'size': size,
                'path': str(p),
            })
        except OSError:
            pass
    return result


def _extract_name(content: str) -> Optional[str]:
    m = _NAME_RE.search(content)
    return m.group(1).strip() if m else None


# ── Injection ────────────────────────────────────────────────────────────────

def inject_into_messages(messages: list[dict]) -> list[dict]:
    """
    Prepend active skill files as system messages.

    Existing system messages are kept in place; skills come *before* them
    so that provider-level system prompts take precedence.
    """
    skills = list_skills()[:_MAX_FILES]
    if not skills:
        return messages

    injected = []
    for skill in skills:
        try:
            text = Path(skill['path']).read_text(encoding='utf-8', errors='replace')
            injected.append({'role': 'system', 'content': text})
        except OSError:
            pass

    return injected + messages


def inject_into_anthropic_request(req: dict) -> dict:
    """
    Inject skill files into an Anthropic Messages API request.

    Skills are prepended to the `system` field (as a list of text blocks).
    """
    skills = list_skills()
    if not skills:
        return req

    skill_blocks = []
    for skill in skills:
        try:
            text = Path(skill['path']).read_text(encoding='utf-8', errors='replace')
            skill_blocks.append({'type': 'text', 'text': text})
        except OSError:
            pass

    if not skill_blocks:
        return req

    req = dict(req)  # shallow copy — don't mutate caller's dict
    existing = req.get('system', [])
    if isinstance(existing, str):
        existing = [{'type': 'text', 'text': existing}]
    elif not isinstance(existing, list):
        existing = []
    req['system'] = skill_blocks + existing
    return req
