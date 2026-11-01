# Dados das campanhas

Dados licenciados sob [CC BY 4.0](LICENSE.md).

| Pasta | Descrição |
|---|---|
| [`20261013T200602/`](20261013T200602) | **Campanha oficial usada no TCC**: 3 cenários × 4 condições × 30 rodadas, mais os testes de estabilidade. |
| [`pilotos/20261013T195056_teste/`](pilotos/20261013T195056_teste) | Primeira execução piloto (2 rodadas por condição). Revelou problemas de instrumentação, corrigidos antes da campanha oficial. |
| [`pilotos/20261013T200217_teste/`](pilotos/20261013T200217_teste) | Segunda execução piloto, que confirmou a coleta. |

Os dados dos pilotos **não** foram usados nos resultados; são mantidos por
transparência. O nome de cada pasta é o instante de início (`AAAAMMDDTHHMMSS`).

## Arquivos de cada campanha

| Arquivo | Conteúdo |
|---|---|
| `campanha.toml` | Configuração exata usada na execução |
| `manifesto.json` | Versões e *commits* do OpenSSH e da liboqs, compilador e flags, hardware, SO, *offloads*, configuração efetiva do sshd, qdisc aplicada, RTT do ping, estatísticas do tcpdump e hashes dos scripts |
| `log_execucao.txt` | Saída do orquestrador no terminal durante a execução |
| `execucoes.csv` | Uma linha por conexão medida |
| `execucoes_brutas.jsonl` | Cópia de segurança gravada a cada execução |
| `captura_<condicao>.pcap` | Captura completa de cada condição (com o log do `tcpdump`) |
| `estabilidade.csv` | Uma linha por teste de estabilidade |
| `estabilidade_resumo.csv` | Resultados agregados dos testes de estabilidade |
| `contagens.csv` | Tentativas, sucessos, falhas, *timeouts*, *fallback* e taxas |
| `resumo_descritivo.csv` | n, média, mediana, DP, Q1, Q3, IQR, mín., máx., P95, CV, IC 95% |
| `comparacoes.csv` | Diferenças absolutas e relativas (média e mediana) entre cenários |
| `testes.csv` | Friedman, W de Kendall e Wilcoxon pareado com correção de Holm (somente na campanha oficial) |

## Colunas de `execucoes.csv`

**Identificação:** `id_execucao`, `condicao`, `rtt_config_ms`,
`banda_config_mbit`, `rodada` (bloco), `posicao` (ordem na rodada), `cenario`,
`kex_solicitado`, `kex_negociado`, `fallback`, `hostkey_alg`, `cifra_*`,
`mac_*`, `compressao`, `inicio_iso`.

**Tempos (ms):** `T_kex_ms`, `T_kex_completo_ms`, `T_kex_especifico_ms`,
`T_transporte_ms`, `T_autenticado_ms`, `T_canal_ms`, `T_processo_ms`,
`rtt_tcp_ms`. As definições de início e fim de cada intervalo estão em
[`docs/metodologia.md`](../docs/metodologia.md#métricas-de-tempo).
`T_autenticado_proc_ms` e `T_canal_proc_ms` são derivadas de
`t_inicio_epoch`, `t_log_autenticado` e `t_log_canal`.

**CPU e memória:**

- cliente (processo `ssh`, via `wait4`): `cli_cpu_user_ms`, `cli_cpu_sys_ms`,
  `cli_cpu_total_ms`, `cli_rss_pico_kb`;
- servidor (cgroup do sshd experimental): `srv_cpu_user_ms`, `srv_cpu_sys_ms`,
  `srv_cpu_total_ms`, `srv_mem_antes_kb`, `srv_mem_pico_kb`,
  `srv_mem_delta_kb`, `srv_anon_antes_kb`.

**Rede (do SYN até o NEWKEYS do cliente):** `bytes_c2s`, `bytes_s2c`,
`bytes_total`, `pacotes_*`, `segmentos_dados_*`, `kex_bytes_c`,
`kex_bytes_s`, `retrans_*`. Totais da conexão inteira:
`conexao_bytes_total`, `conexao_pacotes_total`, `conexao_retrans_total`.

**Funcionamento:** `status` (sucesso, falha, timeout), `etapa_falha`, `erro`,
`rc`, `comando_ok` (o comando `echo ok` devolveu `ok`), `desconexao_anormal`.

## Segurança

Nenhum arquivo contém chaves privadas, segredos compartilhados ou chaves de
sessão; o tráfego SSH das capturas é cifrado.
