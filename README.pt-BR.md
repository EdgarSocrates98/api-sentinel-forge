# API Sentinel Forge

**Observe. Correlate. Diagnose. Verify.**

Plataforma local-first de confiabilidade, inteligência de runtime e
investigação de incidentes para APIs — uma especialista do ecossistema FORGE.
Ela observa como APIs em execução evoluem ao longo do tempo, transforma
telemetria em investigações apoiadas por evidências e delega diagnósticos
especializados às demais Forjas em vez de reimplementá-los.

> Status: **Cycle 1 — Core, Identity & Persistence** (ver `docs/cycles/`).

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
./install.sh            # bash
install.ps1             # powershell
# ou diretamente
pip install api-sentinel-forge
api-sentinel install
```

`api-sentinel install` é um assistente guiado (perfil, escopo do workspace,
integração com o Portal quando detectada). Nada externo é obrigatório: o
núcleo funciona offline sobre arquivos locais.

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
