# Reprodução do experimento

## Ambiente utilizado no TCC

| Componente | Cliente (hospedeiro) | Servidor (VM VirtualBox) |
|---|---|---|
| CPU | Intel Core i7-3770 @ 3,40 GHz, 8 threads | 3 vCPUs do mesmo processador |
| Memória | 8 GB | 4 GB |
| Sistema | Ubuntu 24.04.5 LTS, kernel 6.8 | Ubuntu 24.04.3 LTS, kernel 7.0 |
| SSH | OpenSSH 10.3 + Open Quantum Safe (liboqs) | mesmo build |
| Rede | rede *host-only* `vboxnet0` | — |

Os valores exatos de cada execução estão no `manifesto.json` da campanha.

## Pré-requisitos

**Servidor (VM)**

- OpenSSH com suporte a ML-KEM compilado (por exemplo, em `/usr/local/sbin/sshd`);
- systemd com cgroup v2; kernel ≥ 6.12 para reiniciar `memory.peak` por conexão.

**Hospedeiro**

- Python 3.11+ (SciPy para os testes estatísticos; sem ele, apenas as descritivas são calculadas);
- `tshark`, `tcpdump`, `tc`, `ping`, `ethtool` e um compilador C (`cc`);
- `sudo` disponível (a senha é pedida uma vez, no início) para `tcpdump` e `tc`;
- login por chave pública para `<usuario>@<ip-do-servidor>` nas portas 22 e 2222.

## 1. Preparar o servidor (uma vez)

Copie a pasta `servidor/` para a VM e execute:

```bash
sudo bash preparar_servidor.sh <usuario> /usr/local/sbin/sshd 2222
```

O script cria o serviço `sshd-exp` em um cgroup dedicado, instala o
`agente_exp.sh` e informa se o kernel permite reiniciar `memory.peak`.
Sem isso, o pico de memória do servidor por conexão não é confiável.

## 2. Configurar a campanha

Edite `campanha.toml`: binário do `ssh`, interface e IP do cliente, IP e
usuário do servidor, cenários, condições de rede e número de repetições.
Nada disso fica fixo no código.

## 3. Executar

```bash
# Validação curta (2 rodadas por condição) — faça sempre antes
python3 orquestrador.py --teste

# Campanha completa (4 condições, 30 rodadas, testes de estabilidade)
python3 orquestrador.py

# Opções úteis
python3 orquestrador.py --condicoes base,rtt100
python3 orquestrador.py --com-vazao --com-primitivas   # módulos opcionais
python3 analisar.py campanhas/<id>                      # refazer a estatística
```

Cada campanha é gravada em `campanhas/<data-hora>/` e nunca sobrescreve uma
anterior. Com a configuração padrão, leva de 30 a 40 minutos; a condição
`banda10` é a mais lenta por causa da transferência de 8 MiB do teste de rekey.
