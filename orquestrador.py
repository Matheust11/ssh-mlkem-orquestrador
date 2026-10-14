#!/usr/bin/env python3
"""
Orquestrador da campanha experimental do TCC (ML-KEM no SSH).

Coleta, em um único fluxo e para cada condição de rede configurada:
  - tempos do protocolo (KEX, transporte seguro, autenticação, canal),
    extraídos da captura (tshark) e do log -v do cliente;
  - CPU (usuário/sistema/total) e pico de RSS do cliente (wait4);
  - CPU e memória do servidor (cgroup do sshd experimental, via agente);
  - comunicação de rede (bytes, pacotes, mensagens de KEX, retransmissões);
  - funcionamento e estabilidade (status, etapa de falha, fallback,
    rekey, KEX recusado, entradas inválidas).

Módulos opcionais (só na condição base): ciclos das primitivas
(--com-primitivas) e vazão concorrente (--com-vazao).

Uso:
  python3 orquestrador.py                       # campanha completa
  python3 orquestrador.py --teste               # 2 rodadas, para validar
  python3 orquestrador.py --condicoes base,rtt50
  python3 orquestrador.py --com-vazao --com-primitivas

Nenhuma chave privada, segredo compartilhado ou chave de sessão é
registrada: o cliente roda com -v (debug1), que não imprime material
criptográfico.
"""

import argparse
import csv
import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from datetime import datetime
from pathlib import Path

import analisar

AQUI = Path(__file__).resolve().parent
AGENTE = "/usr/local/sbin/agente-exp"
MEDIDOR = AQUI / "medidor"

# Marcos do log -v do cliente, em ordem. A etapa de falha é a primeira
# etapa não alcançada.
ETAPAS = [
    ("conexao_tcp", "Connection established"),
    ("banner", "Remote protocol version"),
    ("negociacao", "kex: algorithm:"),
    ("troca_de_chaves", "SSH2_MSG_NEWKEYS received"),
    ("autenticacao", "Authenticated to"),
    ("canal", "Sending command:"),
]

SINAIS_DESCONEXAO = (
    "Connection closed by",
    "Connection reset",
    "Broken pipe",
    "client_loop: send disconnect",
)


# ============================================================
# Utilidades
# ============================================================

def agora_iso():
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def rodar(cmd, timeout=60, check=False):
    """Executa um comando e devolve stdout+stderr como texto."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, shell=isinstance(cmd, str))
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        if check:
            raise
        return f"<erro: {e}>"
    if check and r.returncode != 0:
        raise RuntimeError(f"falhou ({r.returncode}): {cmd}\n{r.stdout}{r.stderr}")
    return (r.stdout + r.stderr).strip()


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 16), b""):
            h.update(bloco)
    return h.hexdigest()


def expandir(p):
    return Path(os.path.expanduser(p))


# ============================================================
# Canal de controle com o servidor (sshd do sistema, porta 22)
# ============================================================

class Controle:
    def __init__(self, cfg):
        srv = cfg["servidor"]
        self.sock = Path(tempfile.gettempdir()) / f"orq-ctl-{os.getpid()}.sock"
        self.base = [
            cfg["cliente"]["ssh_bin"],
            "-p", str(srv["porta_controle"]),
            "-o", "BatchMode=yes",
            "-o", "ControlMaster=auto",
            "-o", f"ControlPath={self.sock}",
            "-o", "ControlPersist=yes",
            "-o", "LogLevel=ERROR",
            f"{srv['usuario']}@{srv['host']}",
        ]

    def agente(self, *args, timeout=60):
        r = subprocess.run(self.base + ["sudo", "-n", AGENTE, *args],
                           capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError(f"agente {args} falhou: {r.stdout}{r.stderr}")
        return r.stdout

    def estado(self):
        valores = {}
        for linha in self.agente("estado").splitlines():
            if "=" in linha:
                k, v = linha.split("=", 1)
                valores[k] = int(v) if v.strip().lstrip("-").isdigit() else v
        if "erro" in valores:
            raise RuntimeError(f"agente: {valores['erro']}")
        return valores

    def esperar_ocioso(self, limite_s=10.0):
        """Aguarda os processos da conexão encerrarem (só o listener resta)."""
        fim = time.time() + limite_s
        while True:
            e = self.estado()
            if e["nprocs"] <= 1 or time.time() > fim:
                return e
            time.sleep(0.05)

    def encerrar(self):
        subprocess.run(self.base[:1] + ["-o", f"ControlPath={self.sock}",
                                        "-O", "exit", self.base[-1]],
                       capture_output=True)


# ============================================================
# sudo, netem e captura no cliente
# ============================================================

class SudoVivo:
    """Mantém as credenciais do sudo válidas durante a campanha."""

    def __init__(self):
        subprocess.run(["sudo", "-v"], check=True)
        self._parar = threading.Event()
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _loop(self):
        while not self._parar.wait(60):
            subprocess.run(["sudo", "-n", "-v"], capture_output=True)

    def parar(self):
        self._parar.set()


def netem_cliente(iface, atraso_ms, banda_mbit):
    subprocess.run(["sudo", "-n", "tc", "qdisc", "del", "dev", iface, "root"],
                   capture_output=True)
    args = []
    if atraso_ms:
        args += ["delay", f"{atraso_ms}ms"]
    if banda_mbit:
        args += ["rate", f"{banda_mbit}mbit"]
    if args:
        subprocess.run(["sudo", "-n", "tc", "qdisc", "replace", "dev", iface,
                        "root", "netem", *args, "limit", "10000"], check=True)
    return rodar(["tc", "qdisc", "show", "dev", iface])


def medir_rtt_ping(host, n=10):
    saida = rodar(["ping", "-c", str(n), "-i", "0.2", "-q", host], timeout=60)
    m = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms", saida)
    perda = re.search(r"([\d.]+)% packet loss", saida)
    return {
        "rtt_min_ms": float(m.group(1)) if m else None,
        "rtt_media_ms": float(m.group(2)) if m else None,
        "rtt_max_ms": float(m.group(3)) if m else None,
        "rtt_mdev_ms": float(m.group(4)) if m else None,
        "perda_pct": float(perda.group(1)) if perda else None,
    }


class Captura:
    """tcpdump contínuo durante as medições de uma condição."""

    def __init__(self, iface, host, porta, pcap):
        self.pcap = pcap
        self.pidfile = pcap.with_suffix(".pid")
        self.logfile = pcap.with_suffix(".tcpdump.log")
        self.filtro = f"host {host} and tcp port {porta}"
        self.iface = iface

    def iniciar(self):
        cmd = (f"echo $$ > {shlex.quote(str(self.pidfile))}; exec tcpdump "
               f"-i {shlex.quote(self.iface)} -nn -s 0 -U "
               f"-w {shlex.quote(str(self.pcap))} {shlex.quote(self.filtro)}")
        self._log = open(self.logfile, "w")
        self.proc = subprocess.Popen(["sudo", "-n", "sh", "-c", cmd],
                                     stdout=subprocess.DEVNULL, stderr=self._log)
        limite = time.time() + 10
        while time.time() < limite:
            if "listening on" in self.logfile.read_text(errors="replace"):
                return
            time.sleep(0.05)
        raise RuntimeError("tcpdump não iniciou; veja " + str(self.logfile))

    def parar(self):
        time.sleep(1.0)  # deixa chegar o encerramento da última conexão
        pid = self.pidfile.read_text().strip()
        subprocess.run(["sudo", "-n", "kill", "-INT", pid], capture_output=True)
        self.proc.wait(timeout=30)
        self._log.close()
        # tcpdump grava como root; devolve o arquivo ao usuário para o tshark
        subprocess.run(["sudo", "-n", "chown", f"{os.getuid()}:{os.getgid()}",
                        str(self.pcap), str(self.pidfile)], capture_output=True)
        texto = self.logfile.read_text(errors="replace")
        cap = re.search(r"(\d+) packets captured", texto)
        drop = re.search(r"(\d+) packets dropped by kernel", texto)
        return {
            "pacotes_capturados": int(cap.group(1)) if cap else None,
            "descartados_kernel": int(drop.group(1)) if drop else None,
        }


# ============================================================
# Execução de uma conexão SSH instrumentada
# ============================================================

def executar_ssh(cfg, kex, comando, extras=(), timeout=None):
    """
    Executa o cliente ssh com -v, registrando o instante de chegada de
    cada linha do log e os recursos do processo (wait4 feito pelo medidor).
    """
    cli, srv = cfg["cliente"], cfg["servidor"]
    timeout = timeout or cfg["campanha"]["timeout_conexao_s"]
    fd, arq_uso = tempfile.mkstemp(prefix="uso-", suffix=".txt")
    os.close(fd)
    cmd = [
        str(MEDIDOR), arq_uso,
        cli["ssh_bin"], "-v",
        "-p", str(srv["porta_experimento"]),
        "-o", "BatchMode=yes",
        "-o", f"KexAlgorithms={kex}",
        "-o", "Compression=no",
        "-o", "ControlMaster=no",
        "-o", "ControlPath=none",
        "-o", "ControlPersist=no",
        "-o", "ConnectTimeout=10",
        "-o", "ConnectionAttempts=1",
        "-o", "StrictHostKeyChecking=accept-new",
        *extras,
        f"{srv['usuario']}@{srv['host']}",
        comando,
    ]

    linhas = []
    saida = {"bytes": 0, "inicio": b""}

    t0 = time.time()
    p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def ler_stderr():
        for bruto in p.stderr:
            linhas.append((time.time(), bruto.decode(errors="replace").rstrip()))

    def ler_stdout():
        while True:
            bloco = p.stdout.read(65536)
            if not bloco:
                break
            if len(saida["inicio"]) < 4096:
                saida["inicio"] += bloco[:4096]
            saida["bytes"] += len(bloco)

    t_err = threading.Thread(target=ler_stderr, daemon=True)
    t_out = threading.Thread(target=ler_stdout, daemon=True)
    t_err.start()
    t_out.start()

    estourou = threading.Event()

    def matar():
        estourou.set()
        try:
            p.terminate()  # o medidor repassa como SIGKILL ao ssh
        except ProcessLookupError:
            pass

    timer = threading.Timer(timeout, matar)
    timer.start()
    p.wait()
    t_fim = time.time()
    timer.cancel()
    t_err.join(5)
    t_out.join(5)

    try:
        u_us, s_us, rss_kb = (int(x) for x in Path(arq_uso).read_text().split())
    except (OSError, ValueError):
        u_us = s_us = rss_kb = None
    finally:
        Path(arq_uso).unlink(missing_ok=True)

    return {
        "rc": p.returncode,
        "timeout": estourou.is_set(),
        "t_inicio": t0,
        "t_fim": t_fim,
        "linhas": linhas,
        "stdout_bytes": saida["bytes"],
        "stdout_inicio": saida["inicio"].decode(errors="replace"),
        "cpu_user_ms": u_us / 1000 if u_us is not None else None,
        "cpu_sys_ms": s_us / 1000 if s_us is not None else None,
        "rss_pico_kb": rss_kb,
    }


def interpretar_log(r):
    """Extrai negociação, etapas alcançadas, erros e instantes do log -v."""
    info = {
        "kex_negociado": "", "hostkey_alg": "",
        "cifra_c2s": "", "mac_c2s": "", "cifra_s2c": "", "mac_s2c": "",
        "compressao": "", "t_log_autenticado": None, "t_log_canal": None,
        "newkeys_recebidos": 0,
    }
    alcancadas = set()
    erro = ""
    for t, linha in r["linhas"]:
        for etapa, marca in ETAPAS:
            if marca in linha:
                alcancadas.add(etapa)
        if "kex: algorithm:" in linha and not info["kex_negociado"]:
            info["kex_negociado"] = linha.split("kex: algorithm:")[1].strip()
        elif "kex: host key algorithm:" in linha:
            info["hostkey_alg"] = linha.split("kex: host key algorithm:")[1].strip()
        m = re.search(r"kex: (server->client|client->server) cipher: (\S+) MAC: (\S+) compression: (\S+)", linha)
        if m:
            sentido = "s2c" if m.group(1) == "server->client" else "c2s"
            info[f"cifra_{sentido}"] = m.group(2)
            info[f"mac_{sentido}"] = m.group(3)
            info["compressao"] = m.group(4)
        if "SSH2_MSG_NEWKEYS received" in linha:
            info["newkeys_recebidos"] += 1
        if "Authenticated to" in linha and info["t_log_autenticado"] is None:
            info["t_log_autenticado"] = t
        if "Sending command:" in linha and info["t_log_canal"] is None:
            info["t_log_canal"] = t
        if not linha.startswith("debug") and linha.strip():
            erro = linha.strip()

    if r["timeout"]:
        status = "timeout"
    elif r["rc"] == 0:
        status = "sucesso"
    else:
        status = "falha"

    etapa_falha = ""
    if status != "sucesso":
        etapa_falha = "execucao_comando"
        for etapa, _ in ETAPAS:
            if etapa not in alcancadas:
                etapa_falha = etapa
                break

    info["status"] = status
    info["etapa_falha"] = etapa_falha
    info["erro"] = erro if status != "sucesso" else ""
    info["desconexao_anormal"] = (
        status != "sucesso" and "autenticacao" in alcancadas
        and any(s in l for _, l in r["linhas"] for s in SINAIS_DESCONEXAO)
    )
    return info


# ============================================================
# Fases da campanha
# ============================================================

def ordem_balanceada(cenarios, rodada):
    """Rotação cíclica: cada cenário ocupa cada posição igualmente."""
    k = (rodada - 1) % len(cenarios)
    return cenarios[k:] + cenarios[:k]


def medir_execucao(cfg, ctl, cond, rodada, posicao, cen):
    """Uma conexão medida: servidor antes/depois + cliente instrumentado."""
    ctl.agente("pico_iniciar")
    antes = ctl.estado()

    r = executar_ssh(cfg, cen["kex"], cfg["campanha"]["comando_verificacao"])

    depois = ctl.esperar_ocioso()
    pico = int(ctl.agente("pico_ler").strip().split("=")[1])
    info = interpretar_log(r)

    esperado = cfg["campanha"]["comando_verificacao"].removeprefix("echo ").strip()
    comando_ok = info["status"] == "sucesso" and r["stdout_inicio"].strip() == esperado

    def delta(chave):
        return (depois.get(chave, 0) - antes.get(chave, 0)) / 1000.0  # usec -> ms

    def r3(v):
        return round(v, 3) if v is not None else None

    cpu_total = (r["cpu_user_ms"] + r["cpu_sys_ms"]
                 if r["cpu_user_ms"] is not None else None)

    return {
        "id_execucao": f"{cond['nome']}-r{rodada:02d}-p{posicao}",
        "condicao": cond["nome"],
        "rtt_config_ms": cond["rtt_ms"],
        "banda_config_mbit": cond["banda_mbit"],
        "rodada": rodada,
        "posicao": posicao,
        "cenario": cen["nome"],
        "kex_solicitado": cen["kex"],
        "kex_negociado": info["kex_negociado"],
        "fallback": bool(info["kex_negociado"]) and info["kex_negociado"] != cen["kex"],
        "hostkey_alg": info["hostkey_alg"],
        "cifra_c2s": info["cifra_c2s"], "mac_c2s": info["mac_c2s"],
        "cifra_s2c": info["cifra_s2c"], "mac_s2c": info["mac_s2c"],
        "compressao": info["compressao"],
        "inicio_iso": datetime.fromtimestamp(r["t_inicio"]).astimezone().isoformat(timespec="milliseconds"),
        "t_inicio_epoch": f"{r['t_inicio']:.6f}",
        "t_fim_epoch": f"{r['t_fim']:.6f}",
        "status": info["status"],
        "etapa_falha": info["etapa_falha"],
        "erro": info["erro"],
        "rc": r["rc"],
        "comando_ok": comando_ok,
        "desconexao_anormal": info["desconexao_anormal"],
        "T_processo_ms": round((r["t_fim"] - r["t_inicio"]) * 1000, 3),
        "t_log_autenticado": info["t_log_autenticado"],
        "t_log_canal": info["t_log_canal"],
        "cli_cpu_user_ms": r3(r["cpu_user_ms"]),
        "cli_cpu_sys_ms": r3(r["cpu_sys_ms"]),
        "cli_cpu_total_ms": r3(cpu_total),
        "cli_rss_pico_kb": r["rss_pico_kb"],
        "srv_cpu_user_ms": round(delta("cpu_user_usec"), 3),
        "srv_cpu_sys_ms": round(delta("cpu_system_usec"), 3),
        "srv_cpu_total_ms": round(delta("cpu_usage_usec"), 3),
        "srv_mem_antes_kb": antes["memory_current"] // 1024,
        "srv_mem_pico_kb": pico // 1024,
        "srv_mem_delta_kb": (pico - antes["memory_current"]) // 1024,
        "srv_anon_antes_kb": antes.get("memory_anon", 0) // 1024,
        "srv_nprocs_final": depois["nprocs"],
    }


CAMPOS_CAPTURA = [
    "fluxos_na_janela", "rtt_tcp_ms",
    "T_kex_ms", "T_kex_completo_ms", "T_kex_especifico_ms",
    "T_transporte_ms", "T_autenticado_ms", "T_canal_ms",
    "bytes_c2s", "bytes_s2c", "bytes_total",
    "pacotes_c2s", "pacotes_s2c", "pacotes_total",
    "segmentos_dados_c2s", "segmentos_dados_s2c",
    "kex_bytes_c", "kex_bytes_s",
    "retrans_c2s", "retrans_s2c", "retrans_total",
    "conexao_bytes_total", "conexao_pacotes_total", "conexao_retrans_total",
]


def associar_capturas(execucoes, fluxos):
    """Associa cada execução ao fluxo TCP cujo SYN caiu na sua janela."""
    for ex in execucoes:
        ex.update(dict.fromkeys(CAMPOS_CAPTURA))
        ini = float(ex["t_inicio_epoch"]) - 0.05
        fim = float(ex["t_fim_epoch"]) + 0.05
        candidatos = [f for f in fluxos if f.get("t_syn") and ini <= f["t_syn"] <= fim]
        ex["fluxos_na_janela"] = len(candidatos)
        if not candidatos:
            continue
        f = candidatos[0]

        def ms(a, b):
            if f.get(a) is None or f.get(b) is None:
                return None
            return round((f[a] - f[b]) * 1000, 3)
        ex.update({
            "rtt_tcp_ms": ms("t_synack", "t_syn"),
            "T_kex_ms": ms("t_newkeys_c", "t_kex_init_c"),
            "T_kex_completo_ms": ms("t_newkeys_c", "t_kexinit_c"),
            "T_kex_especifico_ms": ms("t_kex_reply_s", "t_kex_init_c"),
            "T_transporte_ms": ms("t_newkeys_c", "t_syn"),
            "T_autenticado_ms": (round((ex["t_log_autenticado"] - f["t_syn"]) * 1000, 3)
                                 if ex["t_log_autenticado"] else None),
            "T_canal_ms": (round((ex["t_log_canal"] - f["t_syn"]) * 1000, 3)
                           if ex["t_log_canal"] else None),
        })
        for chave in CAMPOS_CAPTURA[8:]:
            ex[chave] = f.get(chave)


def analisar_pcap(pcap, ip_cliente, porta):
    """Extrai, por fluxo TCP, instantes das mensagens SSH e volumes."""
    campos = ["frame.number", "frame.time_epoch", "tcp.stream", "ip.src",
              "tcp.flags.syn", "tcp.flags.ack", "tcp.seq_raw", "tcp.len",
              "ssh.message_code", "ssh.packet_length"]
    base = ["tshark", "-r", str(pcap), "-n",
            "-d", f"tcp.port=={porta},ssh",
            "-o", "tcp.desegment_tcp_streams:TRUE"]
    saida = subprocess.run(
        base + ["-T", "fields", "-E", "separator=\t", "-E", "occurrence=a",
                "-E", "aggregator=,"] + sum([["-e", c] for c in campos], []),
        capture_output=True, text=True, check=True).stdout
    retrans = set(subprocess.run(
        base + ["-Y", "tcp.analysis.retransmission", "-T", "fields", "-e", "frame.number"],
        capture_output=True, text=True, check=True).stdout.split())

    quadros = {}
    for linha in saida.splitlines():
        c = linha.split("\t")
        if len(c) < len(campos) or not c[2]:
            continue
        quadros.setdefault(int(c[2]), []).append({
            "num": c[0], "t": float(c[1]),
            "dir": "c" if c[3] == ip_cliente else "s",
            "syn": c[4] in ("1", "True"), "ack": c[5] in ("1", "True"),
            "seq": int(c[6]) if c[6] else 0, "len": int(c[7]) if c[7] else 0,
            "codigos": [int(x) for x in c[8].split(",") if x.strip().isdigit()],
            "tamanhos": [int(x) for x in c[9].split(",") if x.strip().isdigit()],
            "retrans": c[0] in retrans,
        })

    def bytes_unicos(lista):
        intervalos = sorted((q["seq"], q["seq"] + q["len"]) for q in lista if q["len"] > 0)
        total, fim_atual = 0, None
        for a, b in intervalos:
            if fim_atual is None or a >= fim_atual:
                total += b - a
                fim_atual = b
            elif b > fim_atual:
                total += b - fim_atual
                fim_atual = b
        return total

    fluxos = []
    for stream, qs in sorted(quadros.items()):
        f = {"stream": stream}
        primeiros = {}
        for q in qs:
            if q["syn"] and not q["ack"] and q["dir"] == "c":
                f.setdefault("t_syn", q["t"])
            if q["syn"] and q["ack"] and q["dir"] == "s":
                f.setdefault("t_synack", q["t"])
            for i, cod in enumerate(q["codigos"]):
                primeiros.setdefault((cod, q["dir"]), q["t"])
                if cod in (30, 31) and i < len(q["tamanhos"]):
                    chave = "kex_bytes_c" if q["dir"] == "c" else "kex_bytes_s"
                    f[chave] = f.get(chave, 0) + q["tamanhos"][i] + 4
        f["t_kexinit_c"] = primeiros.get((20, "c"))
        f["t_kex_init_c"] = primeiros.get((30, "c"))
        f["t_kex_reply_s"] = primeiros.get((31, "s"))
        f["t_newkeys_s"] = primeiros.get((21, "s"))
        f["t_newkeys_c"] = primeiros.get((21, "c"))

        # Janela do estabelecimento: do SYN até o NEWKEYS do cliente
        limite = f["t_newkeys_c"]
        janela = [q for q in qs if limite is not None and q["t"] <= limite]
        for d, nome in (("c", "c2s"), ("s", "s2c")):
            qd = [q for q in janela if q["dir"] == d]
            f[f"bytes_{nome}"] = bytes_unicos(qd)
            f[f"pacotes_{nome}"] = len(qd)
            f[f"segmentos_dados_{nome}"] = sum(1 for q in qd if q["len"] > 0)
            f[f"retrans_{nome}"] = sum(1 for q in qd if q["retrans"])
        f["bytes_total"] = f["bytes_c2s"] + f["bytes_s2c"]
        f["pacotes_total"] = f["pacotes_c2s"] + f["pacotes_s2c"]
        f["retrans_total"] = f["retrans_c2s"] + f["retrans_s2c"]
        # Conexão inteira (inclui autenticação, comando e encerramento)
        f["conexao_bytes_total"] = (bytes_unicos([q for q in qs if q["dir"] == "c"])
                                    + bytes_unicos([q for q in qs if q["dir"] == "s"]))
        f["conexao_pacotes_total"] = len(qs)
        f["conexao_retrans_total"] = sum(1 for q in qs if q["retrans"])
        fluxos.append(f)
    return fluxos


# ------------------------------------------------------------
# Estabilidade e tratamento de erros
# ------------------------------------------------------------

def teste_socket(host, porta, payload, timeout=15):
    """Envia uma entrada inválida e verifica se o servidor encerra a conexão."""
    try:
        s = socket.create_connection((host, porta), timeout=10)
    except OSError as e:
        return "falha_inesperada", f"conexao: {e}"
    try:
        s.settimeout(timeout)
        banner = s.recv(256)
        s.sendall(payload)
        recebido = b""
        while True:
            bloco = s.recv(4096)
            if not bloco:
                return "encerramento_controlado", f"FIN; banner={banner[:40]!r}; resposta={recebido[:80]!r}"
            recebido += bloco
    except ConnectionResetError:
        return "encerramento_controlado", "RST"
    except socket.timeout:
        return "timeout", "servidor não encerrou a conexão"
    except OSError as e:
        return "falha_inesperada", str(e)
    finally:
        s.close()


def fase_estabilidade(cfg, ctl, cond, kex_cliente, escrever):
    est = cfg["estabilidade"]
    srv = cfg["servidor"]
    reps = est["repeticoes"]
    suporta_fallback = bool(est["kex_preferido_fallback"])

    def registrar(teste, cen, rep, resultado, r=None, info=None, detalhe="",
                  fallback=""):
        try:
            ativo = ctl.estado()["nprocs"] >= 1
        except Exception:
            ativo = False
        escrever({
            "condicao": cond["nome"], "teste": teste,
            "cenario": cen["nome"] if cen else "",
            "repeticao": rep, "resultado": resultado,
            "rc": r["rc"] if r else "",
            "kex_solicitado": cen["kex"] if cen else "",
            "kex_negociado": info["kex_negociado"] if info else "",
            "fallback": fallback,
            "etapa_falha": info["etapa_falha"] if info else "",
            "erro": info["erro"] if info else "",
            "detalhe": detalhe,
            "servidor_ativo": ativo,
            "instante": agora_iso(),
        })

    for rep in range(1, reps + 1):
        for cen in cfg["cenarios"]:
            # Rekey forçado durante transferência
            r = executar_ssh(cfg, cen["kex"], f"head -c {est['rekey_bytes']} /dev/zero",
                             extras=["-o", f"RekeyLimit={est['rekey_limite']}"],
                             timeout=max(120, cfg["campanha"]["timeout_conexao_s"]))
            info = interpretar_log(r)
            ok = (info["status"] == "sucesso" and r["stdout_bytes"] == est["rekey_bytes"]
                  and info["newkeys_recebidos"] >= 2)
            res = "sucesso" if ok else ("timeout" if info["status"] == "timeout" else "falha")
            registrar("rekey", cen, rep, res, r, info,
                      f"newkeys={info['newkeys_recebidos']}; bytes={r['stdout_bytes']}")

            # Fallback: o cliente prefere um método que o servidor não oferece
            if suporta_fallback:
                lista = f"{est['kex_preferido_fallback']},{cen['kex']}"
                r = executar_ssh(cfg, lista, cfg["campanha"]["comando_verificacao"])
                info = interpretar_log(r)
                ok = info["status"] == "sucesso" and info["kex_negociado"] == cen["kex"]
                registrar("fallback", cen, rep, "sucesso" if ok else info["status"], r, info,
                          f"oferecido={lista}",
                          fallback=info["kex_negociado"] != est["kex_preferido_fallback"])

        # KEX não suportado pelo servidor: deve haver rejeição controlada
        if est["kex_nao_suportado"]:
            r = executar_ssh(cfg, est["kex_nao_suportado"], "true")
            info = interpretar_log(r)
            if r["rc"] != 0 and "no matching key exchange method" in info["erro"]:
                res = "rejeicao_controlada"
            elif info["status"] == "timeout":
                res = "timeout"
            else:
                res = "falha_inesperada"
            registrar("kex_nao_suportado", {"nome": "", "kex": est["kex_nao_suportado"]},
                      rep, res, r, info)
        elif rep == 1:
            log("ATENÇÃO: nenhum KEX do cliente fica fora da lista do servidor; teste pulado")

        # Banner inválido
        res, det = teste_socket(srv["host"], srv["porta_experimento"], b"ISTO-NAO-E-SSH\r\n")
        registrar("banner_invalido", None, rep, res, detalhe=det)

        # Pacote com comprimento inválido após um banner válido
        res, det = teste_socket(srv["host"], srv["porta_experimento"],
                                b"SSH-2.0-orquestrador_teste\r\n" + b"\xff\xff\xff\xff" + b"\x00" * 508)
        registrar("pacote_invalido", None, rep, res, detalhe=det)


# ------------------------------------------------------------
# Módulos opcionais
# ------------------------------------------------------------

def modulo_primitivas(cfg, dir_campanha):
    op = cfg["opcionais"]["primitivas"]
    origem = expandir(op["dir_origem"])
    destino = dir_campanha / "opcional_primitivas"
    destino.mkdir()
    for nome in ("coletar_cpu_cycles.sh", "bench_mlkem_pmu", "bench_curve25519_pmu"):
        if not (origem / nome).exists():
            raise RuntimeError(f"arquivo ausente para o módulo de primitivas: {origem / nome}")
        shutil.copy2(origem / nome, destino / nome)
    log("Módulo opcional: ciclos das primitivas (VM ociosa; sshd experimental sem conexões).")
    subprocess.run(["bash", str(destino / "coletar_cpu_cycles.sh")], cwd=destino,
                   env={**os.environ, "RUNS": str(op["repeticoes"])}, check=True)


def modulo_vazao(cfg, dir_campanha):
    op = cfg["opcionais"]["vazao"]
    destino = dir_campanha / "opcional_vazao.csv"
    condicoes = [(c, n) for n in op["concorrencias"] for c in cfg["cenarios"]]
    campos = ["rodada", "posicao", "cenario", "kex", "concorrencia", "sucessos",
              "falhas", "timeouts", "duracao_lote_s", "vazao_conn_s",
              "dispersao_inicio_ms"]
    with open(destino, "w", newline="") as fout:
        w = csv.DictWriter(fout, fieldnames=campos)
        w.writeheader()
        for rodada in range(1, op["rodadas"] + 1):
            k = (rodada - 1) % len(condicoes)
            ordem = condicoes[k:] + condicoes[:k]
            log(f"Vazão: rodada {rodada}/{op['rodadas']}")
            for pos, (cen, n) in enumerate(ordem, 1):
                barreira = threading.Barrier(n + 1)
                resultados = [None] * n

                def trabalhador(i):
                    barreira.wait()
                    t_ini = time.perf_counter()
                    r = executar_ssh(cfg, cen["kex"], "true")
                    resultados[i] = (t_ini, time.perf_counter(), r)

                ts = [threading.Thread(target=trabalhador, args=(i,)) for i in range(n)]
                for t in ts:
                    t.start()
                barreira.wait()
                t_lib = time.perf_counter()
                for t in ts:
                    t.join()
                sucessos = sum(1 for _, _, r in resultados if r["rc"] == 0 and not r["timeout"])
                timeouts = sum(1 for _, _, r in resultados if r["timeout"])
                duracao = max(fim for _, fim, _ in resultados) - t_lib
                inicios = [ini for ini, _, _ in resultados]
                w.writerow({
                    "rodada": rodada, "posicao": pos, "cenario": cen["nome"],
                    "kex": cen["kex"], "concorrencia": n, "sucessos": sucessos,
                    "falhas": n - sucessos - timeouts, "timeouts": timeouts,
                    "duracao_lote_s": round(duracao, 6),
                    "vazao_conn_s": round(sucessos / duracao, 4) if duracao > 0 else "",
                    "dispersao_inicio_ms": round((max(inicios) - min(inicios)) * 1000, 3),
                })
                fout.flush()
                time.sleep(op["pausa_s"])


# ============================================================
# Manifesto
# ============================================================

def coletar_manifesto(cfg, ctl, args):
    cli = cfg["cliente"]
    ssh_bin = cli["ssh_bin"]
    osrc, oqsrc = expandir(cli["openssh_src"]), expandir(cli["liboqs_src"])

    def ler_primeira(path, padrao):
        try:
            for linha in Path(path).read_text(errors="replace").splitlines():
                if re.search(padrao, linha):
                    return linha.strip()
        except OSError:
            pass
        return ""

    oqs_versao = rodar(f"grep -rh 'define OQS_VERSION_TEXT' {shlex.quote(str(oqsrc))}/build/include 2>/dev/null | head -1")
    return {
        "inicio": agora_iso(),
        "linha_de_comando": " ".join(sys.argv),
        "argumentos": vars(args),
        "configuracao": cfg,
        "hashes_scripts": {str(p.relative_to(AQUI)): sha256(p)
                           for p in sorted([*AQUI.glob("*"), *(AQUI / "servidor").glob("*")])
                           if p.is_file() and p.suffix in (".py", ".sh", ".toml")},
        "cliente": {
            "uname": rodar(["uname", "-a"]),
            "os": ler_primeira("/etc/os-release", r"^PRETTY_NAME="),
            "cpu": ler_primeira("/proc/cpuinfo", r"^model name"),
            "nproc": rodar(["nproc"]),
            "memoria": ler_primeira("/proc/meminfo", r"^MemTotal"),
            "governador_cpu": rodar("cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"),
            "ssh_versao": rodar([ssh_bin, "-V"]),
            "openssl": rodar(["openssl", "version"]),
            "compilador_ssh": rodar(f"strings {shlex.quote(ssh_bin)} | grep -m1 'GCC:'"),
            "tcpdump": rodar(["tcpdump", "--version"]).splitlines()[:2],
            "tshark": rodar(["tshark", "--version"]).splitlines()[:1],
            "python": sys.version,
            "offloads": rodar(f"ethtool -k {shlex.quote(cli['interface'])} | grep -E "
                              "'^(tcp-segmentation-offload|generic-segmentation-offload|generic-receive-offload):'"),
        },
        "compilacao": {
            "openssh_commit": rodar(["git", "-C", str(osrc), "rev-parse", "HEAD"]),
            "openssh_describe": rodar(["git", "-C", str(osrc), "describe", "--always", "--dirty"]),
            "openssh_configure": ler_primeira(osrc / "config.log", r"^\s*\$ \./configure"),
            "openssh_cc": ler_primeira(osrc / "Makefile", r"^CC="),
            "openssh_cflags": ler_primeira(osrc / "Makefile", r"^CFLAGS="),
            "liboqs_commit": rodar(["git", "-C", str(oqsrc), "rev-parse", "HEAD"]),
            "liboqs_versao": oqs_versao,
            "liboqs_build_type": ler_primeira(oqsrc / "build/CMakeCache.txt", r"^CMAKE_BUILD_TYPE:"),
            "liboqs_compilador": ler_primeira(oqsrc / "build/CMakeCache.txt", r"^CMAKE_C_COMPILER:"),
            "liboqs_cflags": ler_primeira(oqsrc / "build/CMakeCache.txt", r"^CMAKE_C_FLAGS:"),
        },
        "servidor": ctl.agente("info"),
        "condicoes": [],
    }


def resolver_kex_de_teste(cfg, ctl, kex_cliente):
    """
    Resolve "auto" nos testes de KEX não suportado e de fallback: escolhe
    o primeiro método que o cliente oferece e o servidor não anuncia.
    """
    texto = ctl.agente("info")
    m = re.search(r"^kexalgorithms (\S+)", texto, re.M)
    kex_servidor = m.group(1).split(",") if m else []
    fora = [k for k in kex_cliente if k not in kex_servidor]
    est = cfg["estabilidade"]
    for chave in ("kex_nao_suportado", "kex_preferido_fallback"):
        if est[chave] == "auto":
            est[chave] = fora[0] if fora else ""
        elif est[chave] in kex_servidor or est[chave] not in kex_cliente:
            log(f"ATENÇÃO: {chave}={est[chave]} não serve para o teste; usando seleção automática")
            est[chave] = fora[0] if fora else ""
    est["kex_servidor_anunciados"] = len(kex_servidor)
    log(f"KEX anunciados pelo servidor: {len(kex_servidor)}; "
        f"método fora da lista usado nos testes: {est['kex_nao_suportado'] or 'nenhum'}")


def salvar_json(path, dados):
    path.write_text(json.dumps(dados, indent=2, ensure_ascii=False, default=str))


# ============================================================
# Principal
# ============================================================

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(AQUI / "campanha.toml"))
    ap.add_argument("--saida", default=str(AQUI / "campanhas"))
    ap.add_argument("--condicoes", help="lista separada por vírgulas (padrão: todas)")
    ap.add_argument("--repeticoes", type=int, help="sobrescreve [campanha].repeticoes")
    ap.add_argument("--teste", action="store_true",
                    help="campanha curta de validação (2 rodadas, 1 repetição de estabilidade)")
    ap.add_argument("--sem-estabilidade", action="store_true")
    ap.add_argument("--com-primitivas", action="store_true")
    ap.add_argument("--com-vazao", action="store_true")
    args = ap.parse_args()

    cfg = tomllib.loads(Path(args.config).read_text())
    if args.teste:
        cfg["campanha"]["repeticoes"] = 2
        cfg["campanha"]["aquecimento"] = 1
        cfg["estabilidade"]["repeticoes"] = 1
        cfg["opcionais"]["vazao"]["rodadas"] = 1
        cfg["opcionais"]["primitivas"]["repeticoes"] = 1
    if args.repeticoes:
        cfg["campanha"]["repeticoes"] = args.repeticoes
    if args.sem_estabilidade:
        cfg["estabilidade"]["executar"] = False

    condicoes = cfg["condicoes"]
    if args.condicoes:
        pedidas = args.condicoes.split(",")
        condicoes = [c for c in condicoes if c["nome"] in pedidas]
        if len(condicoes) != len(pedidas):
            sys.exit(f"condição desconhecida em {pedidas}")

    if not MEDIDOR.exists() or MEDIDOR.stat().st_mtime < (AQUI / "medidor.c").stat().st_mtime:
        subprocess.run(["cc", "-O2", "-Wall", "-o", str(MEDIDOR), str(AQUI / "medidor.c")],
                       check=True)

    for ferramenta in ("tcpdump", "tshark", "tc", "ping", "ethtool", "cc"):
        if not shutil.which(ferramenta):
            sys.exit(f"ferramenta ausente: {ferramenta}")

    cli, srv = cfg["cliente"], cfg["servidor"]
    kex_cliente = rodar([cli["ssh_bin"], "-Q", "kex"]).split()
    for cen in cfg["cenarios"]:
        if cen["kex"] not in kex_cliente:
            sys.exit(f"o cliente não suporta {cen['kex']}")

    id_campanha = datetime.now().strftime("%Y%m%dT%H%M%S") + ("_teste" if args.teste else "")
    dir_campanha = Path(args.saida) / id_campanha
    dir_campanha.mkdir(parents=True)
    shutil.copy2(args.config, dir_campanha / "campanha.toml")
    log(f"Campanha {id_campanha} -> {dir_campanha}")

    sudo = SudoVivo()
    ctl = Controle(cfg)
    iface_srv = None
    captura = None

    arq_exec = dir_campanha / "execucoes.csv"
    arq_est = dir_campanha / "estabilidade.csv"
    arq_brutos = dir_campanha / "execucoes_brutas.jsonl"
    escritor_est = {"w": None, "f": None}

    def escrever_estabilidade(linha):
        if escritor_est["w"] is None:
            escritor_est["f"] = open(arq_est, "w", newline="")
            escritor_est["w"] = csv.DictWriter(escritor_est["f"], fieldnames=list(linha))
            escritor_est["w"].writeheader()
        escritor_est["w"].writerow(linha)
        escritor_est["f"].flush()

    try:
        log("Verificando o servidor (agente e sshd experimental)...")
        ctl.estado()
        iface_srv = ctl.agente("iface_para", cli["ip"]).strip()
        resolver_kex_de_teste(cfg, ctl, kex_cliente)
        manifesto = coletar_manifesto(cfg, ctl, args)
        manifesto["id_campanha"] = id_campanha
        manifesto["servidor_interface"] = iface_srv
        salvar_json(dir_campanha / "manifesto.json", manifesto)

        cabecalho_gravado = False
        for cond in condicoes:
            log(f"===== Condição {cond['nome']}: RTT {cond['rtt_ms']} ms, "
                f"banda {cond['banda_mbit'] or 'sem limite'} Mbit/s =====")
            meio = cond["rtt_ms"] / 2
            atraso = int(meio) if float(meio).is_integer() else meio
            reg = {"nome": cond["nome"], "rtt_config_ms": cond["rtt_ms"],
                   "banda_config_mbit": cond["banda_mbit"], "inicio": agora_iso()}
            reg["qdisc_cliente"] = netem_cliente(cli["interface"], atraso, cond["banda_mbit"])
            reg["qdisc_servidor"] = ctl.agente("netem_aplicar", iface_srv,
                                               str(atraso), str(cond["banda_mbit"]))
            reg["perda_induzida"] = "nenhuma"
            time.sleep(1)
            reg["ping"] = medir_rtt_ping(srv["host"])
            log(f"RTT observado (ping): {reg['ping']['rtt_media_ms']} ms; "
                f"perda {reg['ping']['perda_pct']}%")

            pcap = dir_campanha / f"captura_{cond['nome']}.pcap"
            captura = Captura(cli["interface"], srv["host"], srv["porta_experimento"], pcap)
            captura.iniciar()

            log(f"Aquecimento: {cfg['campanha']['aquecimento']} conexão(ões) por cenário")
            for cen in cfg["cenarios"]:
                for _ in range(cfg["campanha"]["aquecimento"]):
                    executar_ssh(cfg, cen["kex"], "true")
                    time.sleep(cfg["campanha"]["pausa_s"])
            reg["aquecimento_por_cenario"] = cfg["campanha"]["aquecimento"]
            time.sleep(1)

            execucoes = []
            R = cfg["campanha"]["repeticoes"]
            for rodada in range(1, R + 1):
                for pos, cen in enumerate(ordem_balanceada(cfg["cenarios"], rodada), 1):
                    ex = medir_execucao(cfg, ctl, cond, rodada, pos, cen)
                    execucoes.append(ex)
                    with open(arq_brutos, "a") as f:
                        f.write(json.dumps(ex, default=str) + "\n")
                    marca = "OK" if ex["status"] == "sucesso" and not ex["fallback"] else ex["status"].upper()
                    log(f"  r{rodada:02d}/{R} p{pos} {cen['nome']:9s} {marca:8s} "
                        f"proc={ex['T_processo_ms']:8.1f} ms  "
                        f"cpu_cli={ex['cli_cpu_total_ms'] or 0:6.1f} ms  "
                        f"cpu_srv={ex['srv_cpu_total_ms']:6.1f} ms")
                    time.sleep(cfg["campanha"]["pausa_s"])

            reg["tcpdump"] = captura.parar()
            captura = None
            log(f"Captura encerrada: {reg['tcpdump']}. Analisando com tshark...")
            fluxos = analisar_pcap(pcap, cli["ip"], srv["porta_experimento"])
            associar_capturas(execucoes, fluxos)

            sem_fluxo = sum(1 for e in execucoes if not e.get("fluxos_na_janela"))
            if sem_fluxo:
                log(f"ATENÇÃO: {sem_fluxo} execução(ões) sem fluxo TCP associado na captura")

            with open(arq_exec, "a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(execucoes[0]), extrasaction="ignore")
                if not cabecalho_gravado:
                    w.writeheader()
                    cabecalho_gravado = True
                w.writerows(execucoes)

            if cfg["estabilidade"]["executar"]:
                log("Testes de estabilidade e de tratamento de erros...")
                fase_estabilidade(cfg, ctl, cond, kex_cliente, escrever_estabilidade)

            reg["fim"] = agora_iso()
            netem_cliente(cli["interface"], 0, 0)
            ctl.agente("netem_limpar", iface_srv)
            manifesto["condicoes"].append(reg)
            salvar_json(dir_campanha / "manifesto.json", manifesto)

        if args.com_vazao:
            log("Módulo opcional: vazão concorrente (condição base)")
            modulo_vazao(cfg, dir_campanha)
        if args.com_primitivas:
            modulo_primitivas(cfg, dir_campanha)

        manifesto["fim"] = agora_iso()
        salvar_json(dir_campanha / "manifesto.json", manifesto)

    except KeyboardInterrupt:
        log("Interrompido pelo usuário. Limpando o ambiente...")
    finally:
        if captura is not None:
            try:
                captura.parar()
            except Exception as e:
                log(f"falha ao parar tcpdump: {e}")
        netem_cliente(cli["interface"], 0, 0)
        if iface_srv:
            try:
                ctl.agente("netem_limpar", iface_srv)
            except Exception as e:
                log(f"ATENÇÃO: não foi possível limpar o netem do servidor: {e}")
        if escritor_est["f"]:
            escritor_est["f"].close()
        ctl.encerrar()
        sudo.parar()

    if arq_exec.exists():
        log("Calculando estatísticas...")
        analisar.analisar(dir_campanha)
    log(f"Concluído. Resultados em {dir_campanha}")


if __name__ == "__main__":
    main()
