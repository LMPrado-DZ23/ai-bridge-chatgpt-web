# AI Bridge

> Proxy local que conecta Claude Code, Claude Desktop e Codex a qualquer modelo OpenAI-compatible — DeepSeek, GPT-4o, Gemini, Groq, Mistral e mais.

<div align="center">

![AI Bridge Home](docs/screen-home.svg)

</div>

---

## O que é

**AI Bridge** é um aplicativo de desktop que roda um proxy HTTP local traduzindo o protocolo Anthropic Messages API (usado por Claude Code, Claude Desktop e Codex) para o formato OpenAI-compatible. Com ele você usa qualquer provedor de LLM no lugar dos modelos Claude — sem modificar as ferramentas.

### Como funciona

```
Claude Code / Claude Desktop / Codex
          │  (Anthropic API)
          ▼
    AI Bridge Proxy
     127.0.0.1:38771
          │  (OpenAI API)
          ▼
  DeepSeek / GPT-4o / Gemini / Groq / …
```

---

## Download

| Plataforma | Link |
|------------|------|
| **Windows** (.exe) | [⬇ AI-Bridge-Windows.exe](https://github.com/LMPrado-DZ23/ai-bridge/releases/latest/download/AI-Bridge-Windows.exe) |
| **macOS** (binário) | [⬇ AI-Bridge-macOS](https://github.com/LMPrado-DZ23/ai-bridge/releases/latest/download/AI-Bridge-macOS) |
| **Linux** (binário) | [⬇ AI-Bridge-Linux](https://github.com/LMPrado-DZ23/ai-bridge/releases/latest/download/AI-Bridge-Linux) |

Ou veja todas as versões na [página de releases](https://github.com/LMPrado-DZ23/ai-bridge/releases).

---

## Interface

<table>
<tr>
<td align="center" width="33%">

**Destinos**

![Home](docs/screen-home.svg)

Conecte Claude Code, Claude Desktop ou Codex ao proxy

</td>
<td align="center" width="33%">

**Provedores**

![Providers](docs/screen-providers.svg)

Gerencie múltiplos provedores e seus modelos

</td>
<td align="center" width="33%">

**Adicionar Provedor**

![Add Provider](docs/screen-add.svg)

Cadastre qualquer API OpenAI-compatible com presets

</td>
</tr>
</table>

---

## Provedores suportados

| Provedor | Endpoint padrão | Modelos incluídos |
|----------|-----------------|-------------------|
| **DeepSeek** | `api.deepseek.com/v1` | DeepSeek Chat, Reasoner |
| **OpenAI** | `api.openai.com/v1` | GPT-4o, GPT-4o Mini, o1, o3 Mini |
| **Groq** | `api.groq.com/openai/v1` | Llama 3.3 70B, Llama 3.1 8B, Gemma 2 9B |
| **Gemini** | `generativelanguage.googleapis.com/v1beta/openai` | Gemini 2.0 Flash, 2.5 Pro, 2.5 Flash |
| **Mistral** | `api.mistral.ai/v1` | Mistral Large, Small, Codestral |
| **OpenRouter** | `openrouter.ai/api/v1` | GPT-4o, Claude Opus 4.5 (via OpenRouter) |
| **Together** | `api.together.xyz/v1` | Llama 3.3 70B Turbo, Qwen 2.5 Coder 32B |
| **Ollama** | `localhost:11434/v1` | Llama 3.2, Qwen 2.5 Coder, DeepSeek R1 |
| **Claude Gateway** | `claude-ss.ia.br/v1` | Claude Sonnet 5, Opus 5.5, Fable 5.1, Haiku 4.5 |

Qualquer provedor com API OpenAI-compatible pode ser adicionado manualmente.

---

## Destinos suportados

| Destino | Protocolo | Como conecta |
|---------|-----------|--------------|
| **Claude Code** | Anthropic | Configura `ANTHROPIC_BASE_URL` + `ANTHROPIC_API_KEY` |
| **Claude Desktop** | Anthropic | Injeta `claude_desktop_config.json` |
| **Codex** | OpenAI | Configura variáveis de ambiente do Codex |

---

## Segurança

- **Chaves de API nunca gravadas em disco**: armazenadas no keyring do sistema operacional (Credential Manager no Windows, Keychain no macOS, Secret Service no Linux)
- **Proxy exclusivamente local**: vinculado a `127.0.0.1` — nunca exposto à rede
- **Frontend não recebe a chave**: o backend retorna apenas um booleano `has_key` para o UI; a chave real nunca trafega para o lado web
- **Arquivos de configuração limpos**: `~/.ai-bridge/config.json` contém apenas metadados dos provedores, sem segredos

---

## Instalação a partir do código-fonte

```bash
# Requisitos: Python 3.10+
git clone https://github.com/LMPrado-DZ23/ai-bridge.git
cd ai-bridge

pip install -r requirements.txt
python main.py
```

### Dependências principais

```
flask          # Proxy HTTP
httpx          # Chamadas aos provedores
pywebview      # Janela desktop (Chromium embutido)
keyring        # Armazenamento seguro de chaves
```

---

## Testes

```bash
pip install pytest
python -m pytest tests/ -v
```

A suíte cobre **24 casos de teste** para a camada de tradução Anthropic ↔ OpenAI:

- Conversão de mensagens (sistema, usuário, assistente)
- Passagem de parâmetros opcionais (temperatura, top_p, max_tokens, stream)
- Tool use / function calling (ambas as direções)
- Streaming SSE (texto e tool calls)
- Mapeamento de stop_reason
- Fallback para argumentos inválidos
- Decodificação de bytes vs strings

```
tests/test_translate.py::TestToOpenaiRequest::test_simple_message PASSED
tests/test_translate.py::TestToOpenaiRequest::test_system_string PASSED
tests/test_translate.py::TestToOpenaiRequest::test_tool_choice_auto PASSED
...
24 passed in 0.12s
```

---

## Estrutura do projeto

```
ai_bridge/
├── __init__.py
├── bridge.py       # Bridge pywebview — métodos JS↔Python
├── claude_code.py  # Integração Claude Code (env vars)
├── claude_desktop.py # Integração Claude Desktop (config JSON)
├── codex.py        # Integração Codex
├── config.py       # Configuração + keyring
├── proxy.py        # Servidor Flask (proxy HTTP)
└── translate.py    # Tradução Anthropic ↔ OpenAI

ui/
├── index.html      # Interface principal (Tailwind CSS)
└── assets/

tests/
└── test_translate.py  # 24 testes unitários

docs/
├── screen-home.svg
├── screen-providers.svg
└── screen-add.svg
```

---

## Licença

MIT

---

<div align="center">
<sub>Feito com Python + Flask + pywebview · Interface inspirada em Vercel / Linear / Stripe</sub>
</div>
