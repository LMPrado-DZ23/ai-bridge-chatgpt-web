# AI Bridge — relatório de auditoria e validação

Data: 2026-10-28
Branch de trabalho: `fix/e2e-stream-provider-cache`

## Resumo

A auditoria foi executada na cópia Windows em `C:\Users\lmpra\ai-bridge-chatgpt-web`. A branch `main` local não foi usada para o commit de correção; as alterações foram isoladas numa branch dedicada.

## Defeitos corrigidos

1. **Ciclo de vida do streaming Anthropic** — a rota `/v1/messages` encerrava o turno no `finally` da função Flask antes de o gerador SSE ser consumido. O gerador então interpretava o turno como cancelado e emitia apenas `event: error`. Agora a rota mantém o turno registrado até o `finally` do gerador.
2. **Teste de conectividade de provedor** — `Bridge.test_provider` podia retornar sucesso de cache antes de validar a chave enviada ou verificar se o endpoint estava disponível. A verificação de conectividade agora faz a chamada HTTP real; resultados anteriores de catálogo não substituem a autenticação atual.
3. **Documentação** — atualizado o total real de testes, o comando de inicialização para `entry.py` e os links de release para o repositório atual.

## Validação executada no Windows

- Python: 3.12.10
- Pytest: **136 passed**
- Compilação sintática: `python -m compileall -q ai_bridge entry.py` — passou
- `git diff --check` — passou
- PyInstaller 6.22.3: build `build.spec` — concluído com exit code 0; gerado `dist/AI-Bridge.exe`
- GitHub CLI: instalado e autenticado como `LMPrado-DZ23`

## Limites e próximos passos obrigatórios

- O build do executável comprova empacotamento, mas não comprova que a interface desktop abriu e foi operada manualmente.
- Não foram feitas chamadas reais a provedores externos, porque isso exigiria credenciais válidas e autorização para tráfego de rede.
- O teste visual completo de Claude Desktop, Claude Code e Codex Native2 requer validar a UI real, o seletor de modelos e o comportamento de alternância no computador do usuário.
- Nenhuma credencial fornecida em conversa foi incorporada ao código, à documentação ou ao Git.
- A publicação no GitHub e a criação do Pull Request só podem ser consideradas concluídas após o push e a verificação do PR na plataforma.

## Segurança e preservação

A configuração pessoal do Codex e do Claude não foi alterada durante os testes automatizados. O código de integração do Codex mantém a lógica de backup/restauração existente; ela precisa de um teste manual de ida e volta na instalação real antes de declarar a integração desktop validada.
