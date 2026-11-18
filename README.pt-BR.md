# API Sentinel Forge

**Observe. Correlate. Diagnose. Verify.**

Plataforma local-first de confiabilidade, inteligência de runtime e
investigação de incidentes para APIs — uma especialista do ecossistema FORGE.
Ela observa como APIs em execução evoluem ao longo do tempo, transforma
telemetria em investigações apoiadas por evidências e delega diagnósticos
especializados às demais Forjas em vez de reimplementá-los.

> Status: **0.1.2 — Ciclos 0–10 consolidados; superfície install-2.1 de
> serviço entregue (Ciclo 16 wave 11)** (ver `CHANGELOG.md` e
> `docs/cycles/`).

## O que é (e o que não é)

| API Sentinel é dona de | Delegado a outras Forjas |
| --- | --- |
| Inventário e identidade de runtime de APIs | Engenharia de APIs → `api-forge` |
| Ingestão de telemetria e estado de qualidade | Diagnóstico de contratos/config → `forge-doctor-api` |
| SLIs, SLOs, error budgets | Grafo estrutural de código → `code-atlas-forge` |
| Detecção determinística de anomalias | Roteamento/governança → `the-forge` |
| Ciclo de vida de incidentes e evidências | Sessões de agentes → `forge-agent-bridge` |
| Dados operacionais para o Portal | Tracing agêntico → `forge-lens` |

## Instalação

```bash
./install.sh            # bash — bootstrap canônico (venv isolado + launcher)
install.ps1             # powershell
# ou diretamente (rota só-CLI)
pip install api-sentinel-forge
api-sentinel install --yes            # headless; TTY executa guiado
```

O API Sentinel instala como **forja de serviço**: um workspace de estado
governado (`<root>/.api-sentinel/` — SQLite + config + token), registro
de contexto no workspace (`<root>/.forge/context/`) e manifesto de conta
(`~/.forge/installations/`). Nenhum asset de host é escrito.

```bash
api-sentinel install --root ./proj --scope project --runtime-profile local
api-sentinel install status|doctor|repair|uninstall|mcp-verify
api-sentinel uninstall [--purge]      # padrão preserva dados; purge remove estado
```

Dois "perfis" ficam distintos: `--profile` é o tier de instalação
(`minimal|recommended|full`); `--runtime-profile` é o perfil funcional
persistido em `config.yaml` (`local|aws|opentelemetry`). A superfície
completa de flags, escopos, receipts e guardas de segurança está em
[`docs/12-installation-and-distribution.md`](docs/12-installation-and-distribution.md).

## Primeiros comandos

```bash
api-sentinel doctor                 # saúde do ambiente
api-sentinel status                 # estado do workspace
api-sentinel capabilities           # o que este build realmente oferece
```

Importação de telemetria, análise por serviço e incidentes chegam nos
Ciclos 2–4 (ver `factory/specs/inbox/`).

## Códigos de saída

| Código | Significado |
| --- | --- |
| 0 | sucesso |
| 1 | erro de domínio/usuário |
| 2 | erro de uso |
| 3 | erro interno inesperado |

## Licença

Apache-2.0 — ver `LICENSE`.
# Status operacional do Cycle 10.2

As evidências de recuperação e segurança estão em
[`docs/cycles/cycle-10.2/`](docs/cycles/cycle-10.2/). Use `api-sentinel install`
para criar o workspace, `api-sentinel tui` para o painel no terminal e
`api-sentinel backup create` / `api-sentinel backup restore --from <dir>` para
recuperação verificada. Um restore recusa um workspace ocupado sem `--force`
explícito. A decisão atual é `CONDITIONAL GO`: os providers opcionais Bridge e
Lens e a verificação Linux hospedada permanecem limitações nomeadas.
