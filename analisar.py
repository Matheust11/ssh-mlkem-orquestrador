#!/usr/bin/env python3
"""
Estatísticas de uma campanha do orquestrador.

Uso isolado:
  python3 analisar.py campanhas/<id_campanha>

Gera, no diretório da campanha:
  contagens.csv            tentativas, sucessos, falhas, timeouts e taxas
  resumo_descritivo.csv    n, média, mediana, DP, Q1, Q3, IQR, mín., máx.,
                           P95 e IC95 (t de Student) por condição/cenário/métrica
  comparacoes.csv          diferenças absolutas e relativas entre cenários
  testes.csv               Friedman (W de Kendall) e Wilcoxon pareado com Holm
  estabilidade_resumo.csv  resultados dos testes de estabilidade
  vazao_resumo.csv         (se o módulo opcional de vazão foi executado)
"""

import csv
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

try:
    from scipy import stats as sps
except ImportError:  # as estatísticas descritivas não dependem do SciPy
    sps = None

METRICAS_CAPTURA = [
    "T_kex_ms", "T_kex_completo_ms", "T_kex_especifico_ms",
    "T_transporte_ms", "T_autenticado_ms", "T_canal_ms", "rtt_tcp_ms",
    "bytes_c2s", "bytes_s2c", "bytes_total",
    "pacotes_c2s", "pacotes_s2c", "pacotes_total",
    "kex_bytes_c", "kex_bytes_s", "retrans_total",
]
METRICAS_PROCESSO = [
    "T_autenticado_proc_ms", "T_canal_proc_ms", "T_processo_ms",
    "cli_cpu_user_ms", "cli_cpu_sys_ms", "cli_cpu_total_ms", "cli_rss_pico_kb",
    "srv_cpu_user_ms", "srv_cpu_sys_ms", "srv_cpu_total_ms",
    "srv_mem_antes_kb", "srv_mem_pico_kb", "srv_mem_delta_kb",
]
METRICAS = METRICAS_CAPTURA + METRICAS_PROCESSO

PARES = [("hibrido", "classico"), ("pqc", "classico"), ("hibrido", "pqc")]


def num(v):
    if v in (None, "", "None"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def verdadeiro(v):
    return str(v).strip().lower() in ("true", "1", "sim")


def percentil(ordenados, p):
    """Interpolação linear entre postos (mesmo método padrão do NumPy)."""
    if not ordenados:
        return None
    pos = (len(ordenados) - 1) * p
    i = math.floor(pos)
    j = min(i + 1, len(ordenados) - 1)
    return ordenados[i] + (ordenados[j] - ordenados[i]) * (pos - i)


def t_critico(gl):
    if sps is not None:
        return float(sps.t.ppf(0.975, gl))
    return 1.96  # aproximação normal quando o SciPy não está disponível


def descrever(valores):
    v = sorted(valores)
    n = len(v)
    if n == 0:
        return {"n": 0}
    media = statistics.fmean(v)
    dp = statistics.stdev(v) if n > 1 else 0.0
    q1, q3 = percentil(v, 0.25), percentil(v, 0.75)
    meia = t_critico(n - 1) * dp / math.sqrt(n) if n > 1 else 0.0
    return {
        "n": n, "media": media, "mediana": statistics.median(v), "dp": dp,
        "q1": q1, "q3": q3, "iqr": q3 - q1, "minimo": v[0], "maximo": v[-1],
        "p95": percentil(v, 0.95),
        "cv_pct": 100 * dp / media if media else None,
        "ic95_inf": media - meia, "ic95_sup": media + meia,
        "ic95_metodo": "t de Student" if sps is not None else "aproximação normal",
    }


def holm(pvalores):
    """Correção de Holm-Bonferroni; preserva a ordem de entrada."""
    m = len(pvalores)
    ordem = sorted(range(m), key=lambda i: pvalores[i])
    ajustados = [None] * m
    maior = 0.0
    for posto, i in enumerate(ordem):
        maior = max(maior, min(1.0, (m - posto) * pvalores[i]))
        ajustados[i] = maior
    return ajustados


def gravar(path, linhas):
    if not linhas:
        return
    campos = []
    for linha in linhas:
        for k in linha:
            if k not in campos:
                campos.append(k)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        for linha in linhas:
            w.writerow({k: (f"{v:.6g}" if isinstance(v, float) else v) for k, v in linha.items()})


def analisar(dir_campanha):
    dir_campanha = Path(dir_campanha)
    with open(dir_campanha / "execucoes.csv", newline="") as f:
        execucoes = list(csv.DictReader(f))

    # Autenticação e canal medidos a partir do início do processo ssh, no
    # mesmo relógio do log. As versões *_ms partem do SYN capturado, que
    # é registrado depois do atraso do netem de saída do cliente.
    for e in execucoes:
        t0 = num(e.get("t_inicio_epoch"))
        for origem, destino in (("t_log_autenticado", "T_autenticado_proc_ms"),
                                ("t_log_canal", "T_canal_proc_ms")):
            t = num(e.get(origem))
            e[destino] = round((t - t0) * 1000, 3) if t is not None and t0 is not None else None

    condicoes = list(dict.fromkeys(e["condicao"] for e in execucoes))
    cenarios = list(dict.fromkeys(e["cenario"] for e in execucoes))

    def valida(e, metrica):
        if e["status"] != "sucesso" or verdadeiro(e["fallback"]) or not verdadeiro(e["comando_ok"]):
            return False
        if metrica in METRICAS_CAPTURA and e.get("fluxos_na_janela") != "1":
            return False
        return num(e.get(metrica)) is not None

    # ---- Contagens por condição e cenário
    contagens = []
    for c in condicoes:
        for s in cenarios:
            grupo = [e for e in execucoes if e["condicao"] == c and e["cenario"] == s]
            sucessos = sum(1 for e in grupo if e["status"] == "sucesso")
            validas = sum(1 for e in grupo if valida(e, "T_transporte_ms"))
            contagens.append({
                "condicao": c, "cenario": s, "tentativas": len(grupo),
                "sucessos": sucessos,
                "falhas": sum(1 for e in grupo if e["status"] == "falha"),
                "timeouts": sum(1 for e in grupo if e["status"] == "timeout"),
                "fallback": sum(1 for e in grupo if verdadeiro(e["fallback"])),
                "comando_verificado": sum(1 for e in grupo if verdadeiro(e["comando_ok"])),
                "desconexao_anormal": sum(1 for e in grupo if verdadeiro(e["desconexao_anormal"])),
                "sem_fluxo_na_captura": sum(1 for e in grupo if e.get("fluxos_na_janela") in ("0", "")),
                "validas_protocolo": validas,
                "excluidas_protocolo": len(grupo) - validas,
                "taxa_sucesso_pct": 100 * sucessos / len(grupo) if grupo else None,
            })
    gravar(dir_campanha / "contagens.csv", contagens)

    # ---- Estatísticas descritivas
    resumo, medias = [], {}
    for c in condicoes:
        for m in METRICAS:
            for s in cenarios:
                vals = [num(e[m]) for e in execucoes
                        if e["condicao"] == c and e["cenario"] == s and valida(e, m)]
                d = descrever(vals)
                resumo.append({"condicao": c, "metrica": m, "cenario": s, **d})
                if d["n"]:
                    medias[(c, m, s)] = d
    gravar(dir_campanha / "resumo_descritivo.csv", resumo)

    # ---- Diferenças absolutas e relativas entre cenários
    comparacoes = []
    for c in condicoes:
        for m in METRICAS:
            for a, b in PARES:
                da, db = medias.get((c, m, a)), medias.get((c, m, b))
                if not da or not db:
                    continue
                comparacoes.append({
                    "condicao": c, "metrica": m, "comparacao": f"{a} - {b}",
                    "dif_media": da["media"] - db["media"],
                    "dif_media_pct": 100 * (da["media"] - db["media"]) / db["media"] if db["media"] else None,
                    "dif_mediana": da["mediana"] - db["mediana"],
                    "dif_mediana_pct": 100 * (da["mediana"] - db["mediana"]) / db["mediana"] if db["mediana"] else None,
                })
    gravar(dir_campanha / "comparacoes.csv", comparacoes)

    # ---- Testes pareados por rodada
    testes = []
    if sps is None:
        print("SciPy não encontrado: testes estatísticos não calculados.")
    else:
        for c in condicoes:
            for m in METRICAS:
                por_rodada = defaultdict(dict)
                for e in execucoes:
                    if e["condicao"] == c and valida(e, m):
                        por_rodada[e["rodada"]][e["cenario"]] = num(e[m])
                blocos = [r for r in por_rodada.values() if all(s in r for s in cenarios)]
                n = len(blocos)
                if n < 3 or len(cenarios) < 3:
                    continue
                amostras = [[b[s] for b in blocos] for s in cenarios]
                linha = {"condicao": c, "metrica": m, "blocos_pareados": n}
                if all(len(set(a)) == 1 for a in amostras):
                    linha["friedman_obs"] = "valores constantes em cada cenário; teste não aplicável"
                    testes.append(linha)
                    continue
                try:
                    chi2, p = sps.friedmanchisquare(*amostras)
                    linha.update({"friedman_chi2": float(chi2), "friedman_p": float(p),
                                  "kendall_w": float(chi2) / (n * (len(cenarios) - 1))})
                except ValueError as erro:
                    linha["friedman_obs"] = str(erro)
                brutos, rotulos = [], []
                for a, b in PARES:
                    xa = [blk[a] for blk in blocos]
                    xb = [blk[b] for blk in blocos]
                    try:
                        w, p = sps.wilcoxon(xa, xb)
                        linha[f"wilcoxon_W[{a}-{b}]"] = float(w)
                        brutos.append(float(p))
                        rotulos.append(f"{a}-{b}")
                    except ValueError:
                        linha[f"wilcoxon_W[{a}-{b}]"] = "diferenças nulas"
                for rot, p, ph in zip(rotulos, brutos, holm(brutos)):
                    linha[f"p[{rot}]"] = p
                    linha[f"p_holm[{rot}]"] = ph
                testes.append(linha)
        gravar(dir_campanha / "testes.csv", testes)

    # ---- Estabilidade
    arq_est = dir_campanha / "estabilidade.csv"
    if arq_est.exists():
        with open(arq_est, newline="") as f:
            est = list(csv.DictReader(f))
        grupos = defaultdict(lambda: defaultdict(int))
        for e in est:
            chave = (e["condicao"], e["teste"], e["cenario"])
            grupos[chave][e["resultado"]] += 1
            grupos[chave]["_tentativas"] += 1
            if not verdadeiro(e["servidor_ativo"]):
                grupos[chave]["_servidor_inativo"] += 1
        gravar(dir_campanha / "estabilidade_resumo.csv", [
            {"condicao": c, "teste": t, "cenario": s,
             "tentativas": g.pop("_tentativas"),
             "servidor_inativo_apos": g.pop("_servidor_inativo", 0), **g}
            for (c, t, s), g in grupos.items()
        ])

    # ---- Vazão (módulo opcional)
    arq_vazao = dir_campanha / "opcional_vazao.csv"
    if arq_vazao.exists():
        with open(arq_vazao, newline="") as f:
            vz = list(csv.DictReader(f))
        linhas = []
        for s in dict.fromkeys(e["cenario"] for e in vz):
            for n in sorted({int(e["concorrencia"]) for e in vz}):
                vals = [num(e["vazao_conn_s"]) for e in vz
                        if e["cenario"] == s and int(e["concorrencia"]) == n
                        and num(e["vazao_conn_s"]) is not None]
                linhas.append({"cenario": s, "concorrencia": n, **descrever(vals)})
        gravar(dir_campanha / "vazao_resumo.csv", linhas)

    print(f"Estatísticas gravadas em {dir_campanha}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    analisar(sys.argv[1])
