# AI Bridge — plano de paridade funcional com Invision

Este documento define a paridade de fluxo desejada sem copiar código, marca, ícones proprietários ou depender de serviços privados da Invision.

## Fluxo-alvo

1. Detectar Claude Code, Claude Desktop e Codex e mostrar estado real (instalado, conectado, provedor ativo).
2. Selecionar um destino e validar a credencial do provedor antes de alterar qualquer configuração.
3. Buscar modelos disponíveis na API; permitir selecionar o modelo padrão e atualizar a lista depois.
4. Conectar com alterações reversíveis e backup das configurações originais.
5. Alternar entre AI Bridge e o provedor/conta original com uma ação explícita.
6. Desconectar um destino ou remover todas as integrações sem apagar configurações preexistentes.
7. Mostrar erros de validação, rede, autenticação e configuração com instruções claras.
8. Validar a integração com um teste real de ponta a ponta; não inferir sucesso apenas porque um arquivo foi escrito.

## Lacunas identificadas na auditoria inicial

- A tela atual já cadastra provedores, testa a conexão e alterna destinos, mas ainda não tem um fluxo único de validação + seleção de modelo + conexão.
- A integração do Codex grava `api_base`, `base_url` e `api_key` no nível superior de `config.toml`; isso precisa ser substituído por configuração de provedor customizado compatível com a versão suportada do Codex e coberto por testes.
- A integração do Claude Desktop define variáveis de ambiente e remove-as ao desconectar sem guardar os valores anteriores; isso pode interferir numa configuração existente.
- Não há operação única de “remover todas as integrações” com restauração validada.
- O teste de provedor deve apresentar o número real de modelos descobertos e permitir importá-los sem redigitação.

## Critérios para declarar pronto

- Testes unitários de edição/restauração dos arquivos de configuração, incluindo arquivos já existentes, comentários e chaves não relacionadas.
- Testes de API simulados para sucesso, credencial inválida, timeout e lista vazia de modelos.
- CI verde e artefato Windows produzido.
- Teste manual no Windows para cada destino, incluindo conexão, chamada de modelo e restauração do estado original.

## Limite importante

A Invision usa um serviço próprio e um fluxo de código de ativação. O AI Bridge pode reproduzir a experiência funcional com provedores que o usuário configurar, mas não deve imitar ou contornar a autenticação/licenciamento privado da Invision nem afirmar compatibilidade com o serviço dela sem API/documentação autorizada.
