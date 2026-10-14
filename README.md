# Orquestrador da campanha experimental (ML-KEM no SSH)

Um único comando coleta todas as métricas pedidas pelo orientador, para
os três cenários (clássico, híbrido e PQC puro) e para cada condição de
rede configurada, e no final calcula as estatísticas.

```
orquestrador/
├── orquestrador.py        coleta (roda no PC hospedeiro, cliente SSH)
├── analisar.py            estatísticas (chamado ao final; também roda sozinho)
├── medidor.c              mede CPU e pico de RSS do ssh (compilado automaticamente)
├── campanha.toml          configuração: cenários, condições de rede, repetições
└── servidor/
    ├── preparar_servidor.sh   configuração única da VM (com sudo)
    └── agente_exp.sh          agente lido pelo orquestrador na VM
```

## Como funciona

```
 PC hospedeiro (cliente)                         VM (servidor)
 ┌───────────────────────────┐   porta 2222   ┌──────────────────────────────┐
 │ orquestrador.py           │ ─────────────► │ sshd experimental            │
 │  ├ ssh -v (medido, wait4) │                │  (cgroup sshd-exp.service:   │
 │  ├ tcpdump  (vboxnet0)    │                │   cpu.stat, memory.peak)     │
 │  ├ tc netem (ida)         │   porta 22     │                              │
 │  └ canal de controle ─────│ ─────────────► │ sshd do sistema              │
 │                           │                │  └ sudo agente-exp           │
 └───────────────────────────┘                │     (estado, netem da volta) │
                                              └──────────────────────────────┘
```

- As conexões **medidas** vão para um sshd experimental na porta 2222,
  rodando num cgroup próprio. Ele herda a configuração efetiva do sshd
  original (`sshd -T`), então anuncia a mesma lista de KEX, cifras e
  chaves de host da campanha anterior. Assim, a CPU e a memória do servidor
  contam só os processos dessa conexão (`sshd-session`, `sshd-auth` e o
  listener), e não o canal de controle.
- O **canal de controle** usa o sshd do sistema (porta 22) com uma
  conexão multiplexada persistente. Ele lê o cgroup antes e depois de
  cada conexão e aplica o netem do lado do servidor, sempre fora da
  janela medida.
- O RTT configurado é dividido em duas metades: o atraso de ida é
  aplicado na `vboxnet0` do hospedeiro e o de volta na interface da VM.
  O limite de banda vale para os dois sentidos.

## Preparação (uma vez)

### Na VM

Copie a pasta `servidor/` para a VM e execute:

```bash
sudo bash preparar_servidor.sh matheus /usr/local/sbin/sshd 2222
```

O script mostra se o kernel permite reiniciar `memory.peak` (kernel
>= 6.12). Sem isso, o pico de memória do servidor por conexão não é
confiável.

### No hospedeiro

- Login sem senha (chave pública) para `matheus@192.168.56.102`, tanto
  na porta 22 quanto na 2222.
- `sudo` disponível (o orquestrador pede a senha uma vez, no início),
  para `tcpdump` e `tc`.
- `tshark`, `tcpdump`, `tc`, `ping`, `ethtool` e Python 3.11+.
  O SciPy é usado pelos testes estatísticos; sem ele, só as
  descritivas são calculadas.

## Execução

```bash
cd ~/Documents/Academicos/TCC/tcc_mlkem/orquestrador

# 1) Validação curta (2 rodadas por condição) — faça sempre antes
python3 orquestrador.py --teste

# 2) Campanha oficial (4 condições, 30 rodadas, estabilidade)
python3 orquestrador.py

# Opções úteis
python3 orquestrador.py --condicoes base,rtt100
python3 orquestrador.py --com-vazao --com-primitivas   # módulos opcionais
python3 analisar.py campanhas/<id>                      # refazer a estatística
```

Cada campanha é gravada em `campanhas/<data-hora>/` e nunca sobrescreve
uma anterior. Com a configuração padrão, a campanha leva em torno de
30 a 40 minutos sem os módulos opcionais. A condição `banda10` é a mais
lenta, por causa da transferência de 8 MiB do teste de rekey.

## Arquivos de saída

| Arquivo | Conteúdo |
|---|---|
| `manifesto.json` | Identificação da campanha, versões e *commits* do OpenSSH e da liboqs, compilador e flags, hardware, SO, *offloads*, configuração efetiva do sshd, qdisc aplicada, RTT do ping e estatísticas do tcpdump por condição, hashes dos scripts |
| `execucoes.csv` | Uma linha por conexão medida (ver colunas abaixo) |
| `execucoes_brutas.jsonl` | Cópia de segurança gravada a cada execução |
| `captura_<condicao>.pcap` | Captura completa de cada condição |
| `estabilidade.csv` | Uma linha por teste de estabilidade |
| `contagens.csv` | Tentativas, sucessos, falhas, *timeouts*, *fallback*, taxas |
| `resumo_descritivo.csv` | n, média, mediana, DP, Q1, Q3, IQR, mín., máx., P95, CV, IC95 |
| `comparacoes.csv` | Diferenças absolutas e relativas (média e mediana) entre cenários |
| `testes.csv` | Friedman, W de Kendall, Wilcoxon pareado com correção de Holm |
| `estabilidade_resumo.csv` | Resultados agregados dos testes de estabilidade |

### Principais colunas de `execucoes.csv`

**Identificação:** `id_execucao`, `condicao`, `rtt_config_ms`,
`banda_config_mbit`, `rodada` (bloco), `posicao` (ordem), `cenario`,
`kex_solicitado`, `kex_negociado`, `fallback`, `hostkey_alg`,
`cifra_*`, `mac_*`, `compressao`, `inicio_iso`.

**Tempos (ms)**, todos a partir da captura no cliente:

| Coluna | Início | Fim |
|---|---|---|
| `T_kex_ms` (principal) | 1ª mensagem específica de KEX do cliente (30) | NEWKEYS do cliente (21) |
| `T_kex_completo_ms` | KEXINIT do cliente (20) | NEWKEYS do cliente |
| `T_kex_especifico_ms` | mensagem 30 do cliente | resposta 31 do servidor (definição da campanha antiga) |
| `T_transporte_ms` | SYN do cliente | NEWKEYS do cliente |
| `T_autenticado_proc_ms` | início do processo `ssh` | linha `Authenticated to` no log do cliente |
| `T_canal_proc_ms` | início do processo `ssh` | linha `Sending command:` (canal de sessão aberto) |
| `T_processo_ms` | início do processo `ssh` | término do processo |
| `T_autenticado_ms`, `T_canal_ms` | SYN capturado | as mesmas linhas do log (ver observação abaixo) |
| `rtt_tcp_ms` | SYN | SYN/ACK no ponto de captura (ver observação abaixo) |

**Ponto de captura e netem:** o `tcpdump` na `vboxnet0` registra os
pacotes que saem do cliente depois do atraso do netem. Por isso:
`rtt_tcp_ms` mostra só a metade do RTT aplicada na VM (o RTT completo
está no ping do manifesto); intervalos entre dois pacotes enviados
pelo cliente (`T_kex_ms`, `T_kex_completo_ms`, `T_transporte_ms`) não
são afetados; e `T_autenticado_ms`/`T_canal_ms` ficam subestimados em
RTT/2 nas condições com atraso. Para autenticação e canal, use as
versões `*_proc_ms`.

**CPU e memória:** `cli_cpu_user_ms`, `cli_cpu_sys_ms`, `cli_cpu_total_ms`,
`cli_rss_pico_kb` (processo `ssh`, via `wait4`); `srv_cpu_user_ms`,
`srv_cpu_sys_ms`, `srv_cpu_total_ms`, `srv_mem_antes_kb`,
`srv_mem_pico_kb`, `srv_mem_delta_kb`, `srv_anon_antes_kb` (cgroup do
sshd experimental). A CPU e a memória cobrem a conexão inteira:
estabelecimento, autenticação, comando e encerramento.

**Rede (do SYN até o NEWKEYS do cliente):** `bytes_c2s`, `bytes_s2c`,
`bytes_total` (carga útil TCP única, deduplicada por número de
sequência), `pacotes_*`, `segmentos_dados_*`, `kex_bytes_c`,
`kex_bytes_s`, `retrans_*`. A conexão inteira aparece em
`conexao_bytes_total`, `conexao_pacotes_total` e `conexao_retrans_total`.

**Funcionamento:** `status` (sucesso, falha, timeout), `etapa_falha`,
`erro`, `rc`, `comando_ok` (o comando `echo ok` devolveu `ok`),
`desconexao_anormal`.

### Testes de estabilidade (`estabilidade.csv`)

Executados em cada condição, com 10 repetições por padrão:

| Teste | O que faz | Resultado esperado |
|---|---|---|
| `rekey` | Transfere 8 MiB com `RekeyLimit=1M` | sucesso, com 2 ou mais NEWKEYS recebidos |
| `fallback` | Cliente prefere um método que o servidor não anuncia | negocia o método do cenário |
| `kex_nao_suportado` | Cliente oferece só um método que o servidor não anuncia | `rejeicao_controlada` |
| `banner_invalido` | Envia um banner que não é SSH | `encerramento_controlado` |
| `pacote_invalido` | Banner válido e depois um pacote com comprimento inválido | `encerramento_controlado` |

Com `"auto"` em `campanha.toml`, o método "não anunciado" é escolhido
comparando `ssh -Q kex` do cliente com o `sshd -T` do servidor; a
escolha fica registrada no manifesto. Depois de cada teste,
`servidor_ativo` confirma que o sshd experimental
continua no ar.

## Observações metodológicas

- **Bytes de KEX:** `kex_bytes_*` soma só os pacotes SSH 30 e 31
  (`packet_length` + 4). A campanha antiga contava o segmento TCP
  inteiro da resposta do servidor, que também carrega o NEWKEYS e a
  primeira mensagem cifrada. Por isso o valor do servidor aqui é 364
  bytes menor que o da tese (por exemplo, 192 em vez de 556 no
  clássico). Os volumes totais continuam idênticos aos da tese.
- **Memória do servidor:** é a memória do cgroup, que inclui o cache
  de páginas atribuído aos processos, e não a soma de RSS. Com um
  único conjunto de processos por vez, o pico do cgroup é um pico
  simultâneo verdadeiro, não a soma de picos individuais. O pico é
  reiniciado a cada conexão; como no kernel >= 6.12 esse reinício vale
  só para o descritor que o fez, o agente mantém um processo auxiliar
  com o descritor aberto durante a conexão (fora do cgroup medido).
- **Memória do cliente:** o `medidor` cria o `ssh` com `fork()` e lê o
  `ru_maxrss` dele. Medindo direto do Python, o valor herdaria o pico
  de memória do próprio interpretador.
- **Autenticação e canal:** o tráfego após o NEWKEYS é cifrado, então
  esses instantes vêm do log `-v` do cliente, com o relógio do mesmo
  hospedeiro que faz a captura. O nível de log é o mesmo nos três
  cenários.
- **Segredos:** nenhum arquivo registra chaves privadas, segredos
  compartilhados ou chaves de sessão.
- O orquestrador remove o netem do cliente e do servidor ao terminar,
  inclusive se for interrompido com Ctrl+C.
