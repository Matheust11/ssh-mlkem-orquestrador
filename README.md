# ML-KEM-768 no SSH — orquestrador e dados experimentais

[![Código: MIT](https://img.shields.io/badge/código-MIT-blue.svg)](LICENSE)
[![Dados: CC BY 4.0](https://img.shields.io/badge/dados-CC%20BY%204.0-lightgrey.svg)](campanhas/LICENSE.md)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)
![OpenSSH 10.3 + liboqs](https://img.shields.io/badge/OpenSSH-10.3%20%2B%20liboqs-555.svg)

Material complementar do Trabalho de Conclusão de Curso
**_Implementação do Algoritmo Pós-Quântico ML-KEM no Protocolo SSH e Avaliação de Desempenho_**
— Matheus Almeida Torres Sobrinho, Universidade Federal de Campina Grande (UFCG), 2026.

O repositório reúne a ferramenta que conduziu a avaliação experimental e os
dados brutos e processados da campanha usada no trabalho, para que os
resultados possam ser conferidos e reproduzidos.

## O experimento em resumo

Três métodos de troca de chaves do SSH foram comparados em quatro condições
de rede, com 30 rodadas pareadas por condição e ordem dos cenários
alternada a cada rodada:

| Cenário | Método de KEX | Natureza |
|---|---|---|
| Clássico | `curve25519-sha256` | Curva elíptica (X25519) |
| Híbrido | `mlkem768x25519-sha256` | ML-KEM-768 + X25519 |
| PQC puro | `mlkem768-sha256` | Somente ML-KEM-768 |

Condições de rede emuladas com `tc netem`: **base** (sem atraso), **RTT 50 ms**,
**RTT 100 ms** e **banda limitada a 10 Mbit/s**.

Para cada conexão foram medidos o tempo de troca de chaves, de autenticação e
de abertura de canal, o consumo de CPU e memória no cliente e no servidor e o
volume de tráfego. Também foram executados testes de estabilidade (rekey,
negociação com *fallback*, método não suportado e entradas malformadas).

## Principais resultados

Medianas da campanha oficial ([`campanhas/20261013T200602`](campanhas/20261013T200602)):

| Métrica (condição base) | Clássico | Híbrido | PQC puro |
|---|---:|---:|---:|
| Tempo de KEX — `T_kex_ms` | 7,15 ms | 7,53 ms | 2,08 ms |
| Tempo até autenticar — `T_autenticado_proc_ms` | 146,5 ms | 149,2 ms | 142,9 ms |
| CPU do cliente por conexão | 17,2 ms | 17,7 ms | 13,6 ms |
| CPU do servidor por conexão | 61,0 ms | 65,7 ms | 60,7 ms |
| Bytes até o NEWKEYS | 5 742 | 8 014 | 7 950 |

- O custo computacional do ML-KEM-768 é pequeno: o PQC puro foi o mais rápido
  na troca de chaves, e o híbrido ficou poucos décimos de milissegundo acima do
  clássico.
- O principal custo é de **tráfego**: cerca de 2,2 KB a mais por handshake
  (≈ +39%), por causa do tamanho das chaves e cifrotextos do ML-KEM.
- Com RTT de 50 e 100 ms, a latência da rede domina e as diferenças entre os
  cenários ficam em poucos milissegundos.
- **360 de 360** conexões medidas tiveram sucesso, sem *fallback*, e todos os
  testes de estabilidade terminaram com o resultado esperado.

A análise completa (estatística descritiva, testes de Friedman e Wilcoxon com
correção de Holm) está nos arquivos `resumo_descritivo.csv`, `comparacoes.csv`
e `testes.csv` da campanha e no texto do TCC.

## Estrutura do repositório

```
.
├── orquestrador.py         coleta completa (roda no hospedeiro, como cliente SSH)
├── analisar.py             estatísticas de uma campanha
├── medidor.c               mede CPU e pico de memória do processo ssh
├── campanha.toml           configuração: cenários, condições de rede, repetições
├── servidor/               preparação da VM servidora e agente de controle
├── campanhas/              dados publicados (dicionário em campanhas/README.md)
│   ├── 20261013T200602/    campanha oficial usada no TCC
│   └── pilotos/            execuções de validação (não usadas nos resultados)
└── docs/
    ├── metodologia.md      arquitetura, instrumentação e decisões de medição
    └── reproducao.md       como preparar o ambiente e executar uma campanha
```

## Documentação

| Documento | Conteúdo |
|---|---|
| [`docs/metodologia.md`](docs/metodologia.md) | Arquitetura do experimento, definição de cada métrica e observações metodológicas |
| [`docs/reproducao.md`](docs/reproducao.md) | Pré-requisitos, preparação do servidor e do hospedeiro, execução |
| [`campanhas/README.md`](campanhas/README.md) | Descrição dos arquivos e das colunas dos dados |

## Reprodução rápida

```bash
# na VM servidora (uma vez)
sudo bash servidor/preparar_servidor.sh <usuario> /usr/local/sbin/sshd 2222

# no hospedeiro
python3 orquestrador.py --teste      # validação curta
python3 orquestrador.py              # campanha completa (~30–40 min)
python3 analisar.py campanhas/<id>   # refaz a estatística
```

Detalhes em [`docs/reproducao.md`](docs/reproducao.md).

## Como citar

> TORRES SOBRINHO, M. A. **ssh-mlkem-orquestrador**: orquestrador de coleta e
> dados experimentais do ML-KEM-768 no SSH. 2026. Disponível em:
> https://github.com/Matheust11/ssh-mlkem-orquestrador.

## Licença

- **Código** (scripts, `medidor.c`, scripts do servidor e configuração): [MIT](LICENSE).
- **Dados** (pasta `campanhas/`): [CC BY 4.0](campanhas/LICENSE.md).
