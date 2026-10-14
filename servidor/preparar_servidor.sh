#!/usr/bin/env bash
# ============================================================
# preparar_servidor.sh — executar UMA VEZ na VM, com sudo:
#
#   sudo bash preparar_servidor.sh <usuario> [sshd_bin] [porta]
#
# Cria:
#   - /etc/ssh-exp/sshd_config      sshd experimental (porta 2222)
#   - sshd-exp.service              unidade systemd em cgroup próprio,
#                                   com contabilização de CPU e memória
#   - /usr/local/sbin/agente-exp    agente lido pelo orquestrador
#   - /etc/sudoers.d/agente-exp     sudo sem senha APENAS para o agente
#
# O sshd do sistema (porta 22) continua intacto e passa a ser o
# canal de controle do orquestrador.
# ============================================================

set -euo pipefail

USUARIO="${1:?informe o usuário do experimento (ex.: matheus)}"
SSHD_BIN="${2:-/usr/local/sbin/sshd}"
PORTA="${3:-2222}"

DIR="$(cd "$(dirname "$0")" && pwd)"

[[ $EUID -eq 0 ]] || { echo "Execute com sudo."; exit 1; }
[[ -x "$SSHD_BIN" ]] || { echo "sshd não encontrado em $SSHD_BIN"; exit 1; }

# cgroup v2 é necessário para cpu.stat e memory.peak
[[ -f /sys/fs/cgroup/cgroup.controllers ]] || { echo "cgroup v2 não disponível."; exit 1; }

echo ">> Versão do sshd:"
"$SSHD_BIN" -V 2>&1 || true

# O sshd experimental herda a configuração EFETIVA do sshd do sistema
# (sshd -T): mesma lista de KEX, cifras, MACs, chaves de host etc.
# Assim o servidor anuncia exatamente o mesmo KEXINIT da campanha
# original. Só a porta e o arquivo de PID são trocados.
mkdir -p /etc/ssh-exp /run/sshd
EFETIVA=$("$SSHD_BIN" -T)
{
    echo "# sshd experimental do TCC (gerado por preparar_servidor.sh em $(date --iso-8601=seconds))"
    echo "# Base: configuração efetiva do sshd do sistema (sshd -T)."
    echo "Port $PORTA"
    echo "PidFile /run/sshd-exp.pid"
    echo "$EFETIVA" | grep -vE '^(port|listenaddress|pidfile|addressfamily) '
} > /etc/ssh-exp/sshd_config

if ! "$SSHD_BIN" -t -f /etc/ssh-exp/sshd_config 2>/tmp/sshd-exp-erro.txt; then
    echo "!! A cópia integral da configuração não foi aceita:"
    cat /tmp/sshd-exp-erro.txt
    echo ">> Gerando configuração mínima com a MESMA lista de KEX do sistema."
    {
        echo "# sshd experimental do TCC (configuração mínima)"
        echo "Port $PORTA"
        echo "PidFile /run/sshd-exp.pid"
        echo "$EFETIVA" | grep -E '^(hostkey|kexalgorithms|ciphers|macs|hostkeyalgorithms|pubkeyacceptedalgorithms|maxstartups|usepam|compression) '
        echo "PubkeyAuthentication yes"
        echo "PasswordAuthentication no"
        echo "KbdInteractiveAuthentication no"
    } > /etc/ssh-exp/sshd_config
fi

"$SSHD_BIN" -t -f /etc/ssh-exp/sshd_config
echo ">> KEX anunciados pelo sshd experimental:"
"$SSHD_BIN" -T -f /etc/ssh-exp/sshd_config | grep '^kexalgorithms' | tr ',' '\n' | wc -l
echo ">> Configuração do sshd experimental válida."

cat > /etc/systemd/system/sshd-exp.service <<EOF
[Unit]
Description=sshd experimental do TCC (porta $PORTA, cgroup dedicado)
After=network.target

[Service]
ExecStartPre=/bin/mkdir -p /run/sshd
ExecStart=$SSHD_BIN -D -f /etc/ssh-exp/sshd_config
KillMode=process
Restart=on-failure
CPUAccounting=yes
MemoryAccounting=yes

[Install]
WantedBy=multi-user.target
EOF

install -m 0755 "$DIR/agente_exp.sh" /usr/local/sbin/agente-exp

echo "$USUARIO ALL=(root) NOPASSWD: /usr/local/sbin/agente-exp" > /etc/sudoers.d/agente-exp
chmod 0440 /etc/sudoers.d/agente-exp
visudo -c -f /etc/sudoers.d/agente-exp

systemctl daemon-reload
systemctl enable sshd-exp.service
systemctl restart sshd-exp.service
sleep 1
systemctl --no-pager status sshd-exp.service | head -5

echo
echo ">> Teste do agente:"
/usr/local/sbin/agente-exp estado
echo
if /usr/local/sbin/agente-exp pico_iniciar >/dev/null 2>&1 \
   && /usr/local/sbin/agente-exp pico_ler | grep -q '^memory_peak_conexao=[0-9]'; then
    echo ">> Pico de memória por conexão (memory.peak por descritor): OK"
else
    echo "!! ATENÇÃO: não foi possível medir o pico de memória por conexão."
    echo "   Verifique se o kernel é >= 6.12 (uname -r) e se python3 está instalado."
fi

echo
echo "Servidor pronto. sshd experimental na porta $PORTA; controle pela porta 22."
