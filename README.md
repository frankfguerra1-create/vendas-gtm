# Briefing Executivo GTM

Painel diário para varejo alimentar. **Regra central: nenhum número sem fonte.** Cada valor exibido é automático (API oficial) ou manual (com fonte, data de referência e responsável). Sem isso, o painel mostra "Sem dado".

## Como funciona

```
config/fontes.json ──┐
data/entrada_manual.json ──┼─> scripts/coletar_dados.py ─> data/briefing.json ─> index.html
APIs do BACEN ───────┘                                  └> data/auditoria/execucoes.jsonl
```

- **Automático (BACEN):** dólar e euro PTAX (API Olinda), Meta Selic (SGS 432), IPCA mensal (SGS 433) e IPCA 12 meses (SGS 13522).
- **Manual com fonte:** proteínas, grãos, café e hortifrúti (CEPEA, Conab). Preencha `data/entrada_manual.json` (todos os campos são obrigatórios; entradas incompletas são rejeitadas).
- **Agendamento:** `.github/workflows/briefing-diario.yml` roda todo dia às 06:30 (Brasília), testa as regras, coleta, grava a trilha de auditoria e publica no GitHub Pages. Se nenhuma fonte automática responder, o job falha e o GitHub avisa você.

## Ativação (uma vez)

1. Faça merge desta branch na `main` (agendamentos só rodam na branch padrão).
2. Settings → Pages → Source: **GitHub Actions** (Pages em repositório privado depende do plano).
3. Actions → "Briefing diário" → **Run workflow**.
4. **Confira a primeira execução** (checklist abaixo). Os parsers foram escritos a partir da documentação e testados só com respostas simuladas, porque o ambiente de desenvolvimento não alcançou os servidores do BACEN.

### Checklist da primeira execução

- [ ] O log mostra `{"ok": 5, ...}` ou o motivo de cada falha (esquema inesperado aparece com as chaves recebidas).
- [ ] Dólar do painel = PTAX de fechamento no site do BACEN para a mesma data.
- [ ] Euro, Selic e IPCA batem com a fonte para a data de referência.
- [ ] A coluna SHA-256 na aba Auditoria está preenchida.

## Rotina diária de entrada manual (≈10 min)

1. Abra a fonte oficial (links na aba Auditoria). Confira que o **indicador vigente** é o mesmo (ex.: o indicador do boi usado pela B3 pode ter mudado).
2. Acrescente uma entrada em `data/entrada_manual.json` com `valor`, `unidade`, `indicador_oficial` (nome exato na fonte), `data_referencia`, `fonte_url` (https), `inserido_por`, `inserido_em`.
3. Commit na `main`: o workflow roda sozinho. Variação acima de 15% contra a entrada anterior gera alerta de conferência.

## Critérios de auditoria

| Regra | Efeito |
|---|---|
| Resposta fora do esquema esperado | `erro_coleta` (valor não exibido) |
| Valor fora do intervalo de sanidade (`config/fontes.json`) | `invalido` (não exibido) |
| Data de referência no futuro | `invalido` |
| Dado mais antigo que o limite do indicador | `desatualizado` (exibido com selo) |
| Entrada manual incompleta, sem https ou valor ≤ 0 | `invalido` |
| Variação manual > 15% | alerta de conferência |
| Edição com 2+ dias de atraso | faixa vermelha no painel |

Os intervalos de sanidade são travas contra erro de digitação/parse, **não previsões**; ajuste-os se o mercado sair deles de forma legítima.

## Monitoramento sugerido

- **Diário:** abrir o painel e ler os selos; qualquer item que não seja "Verificado" tem motivo na aba Auditoria.
- **Semanal:** comparar 2 indicadores aleatórios com a fonte e registrar o resultado.
- **Mensal:** revisar `config/fontes.json` (URLs, limites) e se algum órgão mudou formato, termos de uso ou indicador. Atualizar sempre que uma fonte mudar o esquema (o coletor falha com a lista de chaves recebidas).

## Limitações (não resolvidas)

- **CEPEA/B3:** não confirmei API aberta nem os termos de reprodução/automação dos dados; verifique antes de automatizar ou redistribuir. Registro público indica que o indicador de liquidação do boi na B3 pode ter mudado de gestor.
- **CEAGESP:** só encontrei o Boletim Hortigranjeiro **mensal** da Conab (Prohort); não há cotação diária de fonte oficial automatizada aqui.
- Removidos por falta de fonte verificável: ouro, dados atribuídos a Scanntech/ABRAS, manchetes de veículos de imprensa, logos de terceiros, estatísticas dos textos editoriais.
- Segurança: removidos PIN de "CEO" no navegador, upload para serviço de terceiros e geração de links; controle de acesso real deve ser feito pelo GitHub (repositório/Pages privados ou protegidos).

## Testes

```
python3 -m unittest discover -s tests -v
```

Cobrem parsing, faixas, defasagem, entradas manuais e o comportamento sem rede (nenhum valor inventado).

> O arquivo `index.html.html` na raiz é a versão anterior, mantida só como histórico; a versão corrigida é `index.html`.
