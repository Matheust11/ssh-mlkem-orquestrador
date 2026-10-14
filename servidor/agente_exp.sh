#!/usr/bin/env bash
# ============================================================
# agente_exp.sh — agente do servidor (VM) para o orquestrador
#
# Instalado como /usr/local/sbin/agente-exp pelo preparar_servidor.sh
# e executado pelo orquestrador via "sudo -n agente-exp <comando>",
# sempre pelo sshd do sistema (porta de controle), nunca pelo sshd
# experimental, para não contaminar as medições.
#
# Comandos:
#   estado                      CPU e memória do cgroup do sshd experimental
#   pico_iniciar                inicia a medição do pico de memória
#   pico_ler                    devolve o pico desde pico_iniciar
#   iface_para <ip>             interface usada para alcançar <ip>
#   netem_aplicar <iface> <atraso_ms> <banda_mbit>
#   netem_limpar <iface>
#   netem_mostrar <iface>
#   info                        dados do ambiente para o manifesto
# ============================================================

set -euo pipefail

UNIDADE="sshd-exp.service"
CG="/sys/fs/cgroup/system.slice/${UNIDADE}"
CONFIG="/etc/ssh-exp/sshd_config"

cmd="${1:-}"
shift || true

case "$cmd" in

estado)
    # Saída no formato chave=valor, uma por linha.
    [[ -d "$CG" ]] || { echo "erro=cgroup_inexistente"; exit 2; }
    awk '{print "cpu_" $1 "=" $2}' "$CG/cpu.stat"
    echo "memory_current=$(cat "$CG/memory.current")"
    echo "memory_peak=$(cat "$CG/memory.peak")"
    if [[ -f "$CG/memory.stat" ]]; then
        awk '$1=="anon"{print "memory_anon=" $2}' "$CG/memory.stat"
    fi
    echo "nprocs=$(wc -l < "$CG/cgroup.procs")"
    ;;

pico_iniciar)
    # Em kernels >= 6.12, escrever em memory.peak reinicia o pico APENAS
    # para o descritor que fez a escrita. Por isso um processo auxiliar
    # mantém o descritor aberto durante a conexão e lê o pico pelo mesmo
    # descritor quando solicitado (pico_ler). Ele fica bloqueado num FIFO,
    # sem consumir CPU, e roda fora do cgroup do sshd experimental.
    D=/run/agente-exp
    if [[ -f $D/pid ]]; then kill "$(cat $D/pid)" 2>/dev/null || true; fi
    rm -rf "$D"; mkdir -p "$D"; mkfifo "$D/pedido"
    setsid python3 -c '
import os, sys
d = "/run/agente-exp"
fd = os.open(sys.argv[1], os.O_RDWR)
os.write(fd, b"0")
open(d + "/pronto", "w").close()
with open(d + "/pedido") as f:
    f.read()
os.lseek(fd, 0, 0)
valor = os.read(fd, 64).decode().strip()
with open(d + "/resposta.tmp", "w") as f:
    f.write(valor)
os.rename(d + "/resposta.tmp", d + "/resposta")
' "$CG/memory.peak" </dev/null >/dev/null 2>&1 &
    echo $! > "$D/pid"
    for _ in $(seq 500); do [[ -f $D/pronto ]] && break; sleep 0.01; done
    [[ -f $D/pronto ]] || { echo "erro=pico_nao_iniciou"; exit 2; }
    echo "ok=1"
    ;;

pico_ler)
    D=/run/agente-exp
    [[ -p $D/pedido ]] || { echo "erro=pico_nao_iniciado"; exit 2; }
    echo x > "$D/pedido"
    for _ in $(seq 500); do [[ -f $D/resposta ]] && break; sleep 0.01; done
    [[ -f $D/resposta ]] || { echo "erro=pico_sem_resposta"; exit 2; }
    echo "memory_peak_conexao=$(cat "$D/resposta")"
    rm -rf "$D"
    ;;

iface_para)
    ip route get "$1" | awk '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}'
    ;;

netem_aplicar)
    iface="$1"; atraso="$2"; banda="$3"
    args=()
    [[ "$atraso" != "0" ]] && args+=(delay "${atraso}ms")
    [[ "$banda" != "0" ]] && args+=(rate "${banda}mbit")
    if [[ ${#args[@]} -eq 0 ]]; then
        tc qdisc del dev "$iface" root 2>/dev/null || true
    else
        tc qdisc replace dev "$iface" root netem "${args[@]}" limit 10000
    fi
    tc qdisc show dev "$iface"
    ;;

netem_limpar)
    tc qdisc del dev "$1" root 2>/dev/null || true
    tc qdisc show dev "$1"
    ;;

netem_mostrar)
    tc qdisc show dev "$1"
    ;;

info)
    echo "### uname"; uname -a
    echo "### os-release"; cat /etc/os-release
    echo "### nproc"; nproc
    echo "### lscpu"; lscpu | grep -E "Model name|^CPU\(s\)|Thread|Core|Socket" || true
    echo "### memoria"; free -b
    echo "### sshd_bin"; systemctl show -p ExecStart "$UNIDADE"
    echo "### sshd_config_efetiva"
    sshd_bin=$(systemctl show -p ExecStart "$UNIDADE" | grep -oP 'path=\K[^ ;]+')
    "$sshd_bin" -T -f "$CONFIG" 2>&1 | grep -E "^(port|kexalgorithms|ciphers|macs|hostkeyalgorithms|compression|maxstartups|rekeylimit|usepam|loglevel) " || true
    echo "### sshd_config_sistema_kex"
    "$sshd_bin" -T 2>&1 | grep -E "^kexalgorithms " || true
    echo "### sshd_versao"; "$sshd_bin" -V 2>&1 || true
    echo "### offloads"
    for i in $(ls /sys/class/net | grep -v '^lo$'); do
        echo "-- $i"; ethtool -k "$i" 2>/dev/null | grep -E "^(tcp-segmentation-offload|generic-segmentation-offload|generic-receive-offload):" || true
    done
    echo "### kernel"; uname -r
    ;;

*)
    echo "uso: agente-exp {estado|pico_iniciar|pico_ler|iface_para|netem_aplicar|netem_limpar|netem_mostrar|info}" >&2
    exit 1
    ;;
esac
