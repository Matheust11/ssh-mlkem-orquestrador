#!/usr/bin/env python3
"""
Gera as figuras (PDF) e as tabelas LaTeX dos capítulos de Metodologia e
Resultados a partir de uma campanha do orquestrador.

  python3 gerar_tex_campanha.py campanhas/<id> <dir_figuras_do_tcc> <saida_tabelas.tex>

As tabelas são gravadas como macros \\newcommand{\\tabXxx}{...} num único
arquivo, para que nenhum número seja digitado à mão no texto.
"""

import csv
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

CEN = ["classico", "hibrido", "pqc"]
ROT = {"classico": "Clássico", "hibrido": "Híbrido", "pqc": "PQC puro"}
COND = ["base", "rtt50", "rtt100", "banda10"]
ROTC = {"base": "Base", "rtt50": "RTT 50 ms", "rtt100": "RTT 100 ms", "banda10": "Banda 10 Mbit/s"}
# Paleta categórica de referência (slots 1-3, ordem fixa) + textura para impressão
COR = {"classico": "#2a78d6", "hibrido": "#eb6834", "pqc": "#1baf7a"}
HACH = {"classico": "", "hibrido": "///", "pqc": "\\\\\\"}
TINTA, TINTA2, GRADE = "#1f1f1e", "#5f5e5a", "#d9d8d3"


def v(x, d=2):
    """Número com vírgula decimal."""
    if x is None or x == "":
        return "--"
    return f"{float(x):.{d}f}".replace(".", ",")


def pv(p):
    if p in (None, ""):
        return "--"
    p = float(p)
    return "$<0{,}001$" if p < 0.001 else v(p, 3)


def carregar(dir_c):
    dir_c = Path(dir_c)
    ler = lambda n: list(csv.DictReader(open(dir_c / n, newline="")))
    ex = ler("execucoes.csv")
    for e in ex:  # mesmas métricas derivadas do analisar.py
        t0 = float(e["t_inicio_epoch"])
        for o, d in (("t_log_autenticado", "T_autenticado_proc_ms"), ("t_log_canal", "T_canal_proc_ms")):
            e[d] = (float(e[o]) - t0) * 1000 if e[o] not in ("", "None") else None
    resumo = {(r["condicao"], r["metrica"], r["cenario"]): r for r in ler("resumo_descritivo.csv")}
    testes = {(r["condicao"], r["metrica"]): r for r in ler("testes.csv")}
    comp = {(r["condicao"], r["metrica"], r["comparacao"]): r for r in ler("comparacoes.csv")}
    est = ler("estabilidade.csv")
    cont = ler("contagens.csv")
    man = json.load(open(dir_c / "manifesto.json"))
    return ex, resumo, testes, comp, est, cont, man


def estilo():
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 9,
        "axes.edgecolor": GRADE, "axes.labelcolor": TINTA2, "axes.titlesize": 9.5,
        "axes.titlecolor": TINTA, "xtick.color": TINTA2, "ytick.color": TINTA2,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "axes.grid.axis": "y", "grid.color": GRADE, "grid.linewidth": 0.6,
        "axes.axisbelow": True, "legend.frameon": False, "hatch.linewidth": 0.6,
        "pdf.fonttype": 42,
    })


virgula = FuncFormatter(lambda x, _: f"{x:g}".replace(".", ","))


def valores(ex, cond, metrica, cen):
    return [float(e[metrica]) for e in ex
            if e["condicao"] == cond and e["cenario"] == cen and e[metrica] not in ("", "None", None)]


def fig_distribuicoes(ex, metricas, rotulos_y, destino):
    """Pequenos múltiplos: uma linha por métrica, uma coluna por condição."""
    linhas = len(metricas)
    fig, eixos = plt.subplots(linhas, 4, figsize=(7.2, 2.35 * linhas + 0.5), squeeze=False)
    for i, (m, ry) in enumerate(zip(metricas, rotulos_y)):
        for j, c in enumerate(COND):
            ax = eixos[i][j]
            for k, s in enumerate(CEN):
                vals = valores(ex, c, m, s)
                bp = ax.boxplot(vals, positions=[k], widths=0.55, patch_artist=True,
                                showfliers=False, medianprops={"color": TINTA, "linewidth": 1.4},
                                whiskerprops={"color": TINTA2, "linewidth": 0.8},
                                capprops={"color": TINTA2, "linewidth": 0.8})
                for caixa in bp["boxes"]:
                    caixa.set(facecolor=COR[s] + "55", edgecolor=COR[s], linewidth=1.0, hatch=HACH[s])
                desloc = [(n % 7 - 3) * 0.035 for n in range(len(vals))]
                ax.scatter([k + d for d in desloc], vals, s=7, color=COR[s],
                           edgecolors="white", linewidths=0.3, zorder=3)
            ax.set_xticks(range(3), ["Cláss.", "Híbr.", "PQC"])
            ax.yaxis.set_major_formatter(virgula)
            ax.tick_params(length=0)
            if i == 0:
                ax.set_title(ROTC[c])
            if j == 0:
                ax.set_ylabel(ry)
    fig.tight_layout()
    fig.savefig(destino)
    plt.close(fig)


def fig_cpu(resumo, destino):
    fig, eixos = plt.subplots(1, 2, figsize=(7.2, 2.9))
    largura = 0.26
    for ax, (m, titulo) in zip(eixos, [("cli_cpu_total_ms", "Cliente (processo ssh)"),
                                       ("srv_cpu_total_ms", "Servidor (cgroup do sshd)")]):
        for k, s in enumerate(CEN):
            xs, ys, err = [], [], []
            for j, c in enumerate(COND):
                r = resumo[(c, m, s)]
                xs.append(j + (k - 1) * largura)
                ys.append(float(r["media"]))
                err.append(float(r["media"]) - float(r["ic95_inf"]))
            ax.bar(xs, ys, width=largura - 0.03, color=COR[s] + "aa", edgecolor=COR[s],
                   hatch=HACH[s], linewidth=0.9, label=ROT[s])
            ax.errorbar(xs, ys, yerr=err, fmt="none", ecolor=TINTA, elinewidth=0.9, capsize=2)
        ax.set_xticks(range(4), ["Base", "RTT\n50 ms", "RTT\n100 ms", "Banda\n10 Mbit/s"])
        ax.set_title(titulo)
        ax.set_ylabel("Tempo de CPU (ms)")
        ax.yaxis.set_major_formatter(virgula)
        ax.tick_params(length=0)
    eixos[0].legend(loc="upper left", ncols=3, fontsize=8, bbox_to_anchor=(0, -0.22))
    fig.tight_layout()
    fig.savefig(destino)
    plt.close(fig)


def fig_bytes(resumo, destino):
    fig, ax = plt.subplots(figsize=(7.2, 2.4))
    for k, s in enumerate(CEN):
        tot = float(resumo[("base", "bytes_total", s)]["media"])
        kc = float(resumo[("base", "kex_bytes_c", s)]["media"])
        ks = float(resumo[("base", "kex_bytes_s", s)]["media"])
        comum = tot - kc - ks
        y = 2 - k
        ax.barh(y, comum, color=GRADE, edgecolor="white", linewidth=2, height=0.55,
                label="Demais mensagens" if k == 0 else None)
        ax.barh(y, kc, left=comum, color=COR[s], edgecolor="white", linewidth=2, height=0.55,
                hatch=HACH[s])
        ax.barh(y, ks, left=comum + kc, color=COR[s] + "88", edgecolor="white", linewidth=2,
                height=0.55, hatch=HACH[s])
        ax.text(tot + 80, y, f"{int(tot)} B  (KEX: {int(kc + ks)} B)", va="center",
                color=TINTA, fontsize=8.5)
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(facecolor=GRADE, label="Demais mensagens"),
                       Patch(facecolor="#7a7974", hatch="///", edgecolor="white", label="KEX do cliente (msg. 30)"),
                       Patch(facecolor="#b8b7b1", hatch="///", edgecolor="white", label="KEX do servidor (msg. 31)")],
              loc="lower center", bbox_to_anchor=(0.5, 1.0), ncols=3, fontsize=8)
    ax.set_yticks([2, 1, 0], [ROT[s] for s in CEN])
    ax.set_xlabel("Carga útil TCP única até o NEWKEYS do cliente (bytes)")
    ax.set_xlim(0, 10500)
    ax.grid(axis="x", color=GRADE, linewidth=0.6)
    ax.grid(axis="y", visible=False)
    ax.tick_params(length=0)
    fig.tight_layout()
    fig.savefig(destino)
    plt.close(fig)


# ------------------------------------------------------------------
# Tabelas
# ------------------------------------------------------------------

def tab_descritiva(resumo, metrica, legenda, rotulo, unidade="ms", dec=2):
    linhas = []
    for c in COND:
        for i, s in enumerate(CEN):
            r = resumo[(c, metrica, s)]
            cel = ROTC[c] if i == 0 else ""
            linhas.append(
                f"        {cel} & {ROT[s]} & {r['n']} & {v(r['media'],dec)} & {v(r['mediana'],dec)} & "
                f"{v(r['dp'],dec)} & {v(r['iqr'],dec)} & {v(r['minimo'],dec)} & {v(r['maximo'],dec)} & "
                f"{v(r['ic95_inf'],dec)} & {v(r['ic95_sup'],dec)} \\\\")
        if c != COND[-1]:
            linhas.append("        \\addlinespace")
    return f"""\\begin{{table}}[H]
    \\centering
    \\caption{{{legenda}}}
    \\label{{{rotulo}}}
    \\small
    \\setlength{{\\tabcolsep}}{{3pt}}
    \\resizebox{{\\linewidth}}{{!}}{{%
    \\begin{{tabular}}{{llrrrrrrrrr}}
        \\toprule
        \\textbf{{Condição}} & \\textbf{{Cenário}} & \\textbf{{\\textit{{n}}}} & \\textbf{{Média}} & \\textbf{{Mediana}} &
        \\textbf{{DP}} & \\textbf{{IQR}} & \\textbf{{Mín.}} & \\textbf{{Máx.}} & \\textbf{{IC95 mín.}} & \\textbf{{IC95 máx.}} \\\\
        \\midrule
{chr(10).join(linhas)}
        \\bottomrule
    \\end{{tabular}}}}
    \\fonte{{Elaborada pelo autor (2026). Valores em {unidade}.}}
\\end{{table}}"""


def tab_testes(testes, metricas, legenda, rotulo):
    linhas = []
    for m, nome in metricas:
        for i, c in enumerate(COND):
            t = testes.get((c, m), {})
            cel = nome if i == 0 else ""
            linhas.append(
                f"        {cel} & {ROTC[c]} & {v(t.get('friedman_chi2'),2)} & {pv(t.get('friedman_p'))} & "
                f"{v(t.get('kendall_w'),3)} & {pv(t.get('p_holm[hibrido-classico]'))} & "
                f"{pv(t.get('p_holm[pqc-classico]'))} & {pv(t.get('p_holm[hibrido-pqc]'))} \\\\")
        if m != metricas[-1][0]:
            linhas.append("        \\addlinespace")
    return f"""\\begin{{table}}[H]
    \\centering
    \\caption{{{legenda}}}
    \\label{{{rotulo}}}
    \\small
    \\setlength{{\\tabcolsep}}{{3pt}}
    \\resizebox{{\\linewidth}}{{!}}{{%
    \\begin{{tabular}}{{llrrrrrr}}
        \\toprule
        & & \\multicolumn{{3}}{{c}}{{\\textbf{{Friedman}}}} & \\multicolumn{{3}}{{c}}{{\\textbf{{Wilcoxon, $p_{{\\mathrm{{Holm}}}}$}}}} \\\\
        \\cmidrule(lr){{3-5}} \\cmidrule(lr){{6-8}}
        \\textbf{{Medida}} & \\textbf{{Condição}} & $\\chi^2_F$ & $p$ & $W$ &
        \\textbf{{Híbr.--Cláss.}} & \\textbf{{PQC--Cláss.}} & \\textbf{{Híbr.--PQC}} \\\\
        \\midrule
{chr(10).join(linhas)}
        \\bottomrule
    \\end{{tabular}}}}
    \\fonte{{Elaborada pelo autor (2026). Trinta blocos pareados por condição; $\\chi^2_F$ com 2 graus de liberdade.}}
\\end{{table}}"""


def tab_diferencas(comp, metricas, legenda, rotulo):
    linhas = []
    for c in COND:
        for i, (m, nome) in enumerate(metricas):
            cel = ROTC[c] if i == 0 else ""
            partes = []
            for par in ("hibrido - classico", "pqc - classico"):
                r = comp[(c, m, par)]
                partes += [v(r["dif_media"], 2), v(r["dif_media_pct"], 1)]
            linhas.append(f"        {cel} & {nome} & " + " & ".join(partes) + " \\\\")
        if c != COND[-1]:
            linhas.append("        \\addlinespace")
    return f"""\\begin{{table}}[H]
    \\centering
    \\caption{{{legenda}}}
    \\label{{{rotulo}}}
    \\small
    \\begin{{tabular}}{{llrrrr}}
        \\toprule
        & & \\multicolumn{{2}}{{c}}{{\\textbf{{Híbrido -- Clássico}}}} & \\multicolumn{{2}}{{c}}{{\\textbf{{PQC puro -- Clássico}}}} \\\\
        \\cmidrule(lr){{3-4}} \\cmidrule(lr){{5-6}}
        \\textbf{{Condição}} & \\textbf{{Medida}} & \\textbf{{ms}} & \\textbf{{\\%}} & \\textbf{{ms}} & \\textbf{{\\%}} \\\\
        \\midrule
{chr(10).join(linhas)}
        \\bottomrule
    \\end{{tabular}}
    \\fonte{{Elaborada pelo autor (2026). Diferenças entre médias; valores negativos indicam menor tempo que o clássico.}}
\\end{{table}}"""


def tab_cpu(resumo):
    linhas = []
    for c in COND:
        for i, s in enumerate(CEN):
            g = lambda m: resumo[(c, m, s)]
            cel = ROTC[c] if i == 0 else ""
            linhas.append(
                f"        {cel} & {ROT[s]} & {v(g('cli_cpu_user_ms')['media'])} & {v(g('cli_cpu_sys_ms')['media'])} & "
                f"{v(g('cli_cpu_total_ms')['media'])} & {v(g('cli_cpu_total_ms')['ic95_inf'])} & {v(g('cli_cpu_total_ms')['ic95_sup'])} & "
                f"{v(g('srv_cpu_user_ms')['media'])} & {v(g('srv_cpu_sys_ms')['media'])} & "
                f"{v(g('srv_cpu_total_ms')['media'])} & {v(g('srv_cpu_total_ms')['ic95_inf'])} & {v(g('srv_cpu_total_ms')['ic95_sup'])} \\\\")
        if c != COND[-1]:
            linhas.append("        \\addlinespace")
    return f"""\\begin{{table}}[H]
    \\centering
    \\caption{{Tempo de CPU por conexão no cliente e no servidor (médias, em ms).}}
    \\label{{tab:c2_cpu}}
    \\small
    \\setlength{{\\tabcolsep}}{{3pt}}
    \\resizebox{{\\linewidth}}{{!}}{{%
    \\begin{{tabular}}{{llrrrrrrrrrr}}
        \\toprule
        & & \\multicolumn{{5}}{{c}}{{\\textbf{{Cliente (processo \\texttt{{ssh}})}}}} & \\multicolumn{{5}}{{c}}{{\\textbf{{Servidor (cgroup do \\texttt{{sshd}})}}}} \\\\
        \\cmidrule(lr){{3-7}} \\cmidrule(lr){{8-12}}
        \\textbf{{Condição}} & \\textbf{{Cenário}} & \\textbf{{Usuário}} & \\textbf{{Sistema}} & \\textbf{{Total}} & \\textbf{{IC95 mín.}} & \\textbf{{IC95 máx.}} &
        \\textbf{{Usuário}} & \\textbf{{Sistema}} & \\textbf{{Total}} & \\textbf{{IC95 mín.}} & \\textbf{{IC95 máx.}} \\\\
        \\midrule
{chr(10).join(linhas)}
        \\bottomrule
    \\end{{tabular}}}}
    \\fonte{{Elaborada pelo autor (2026). Trinta conexões por cenário e condição.}}
\\end{{table}}"""


def tab_memoria(resumo, testes):
    linhas = []
    for c in COND:
        for i, s in enumerate(CEN):
            g = lambda m: resumo[(c, m, s)]
            cel = ROTC[c] if i == 0 else ""
            linhas.append(
                f"        {cel} & {ROT[s]} & {v(g('cli_rss_pico_kb')['mediana'],0)} & {v(g('cli_rss_pico_kb')['iqr'],0)} & "
                f"{v(g('srv_mem_antes_kb')['mediana'],0)} & {v(g('srv_mem_pico_kb')['mediana'],0)} & "
                f"{v(g('srv_mem_pico_kb')['iqr'],0)} & {v(g('srv_mem_delta_kb')['mediana'],0)} \\\\")
        if c != COND[-1]:
            linhas.append("        \\addlinespace")
    return f"""\\begin{{table}}[H]
    \\centering
    \\caption{{Memória no cliente e no servidor por conexão (medianas e IQR, em KiB).}}
    \\label{{tab:c2_memoria}}
    \\small
    \\setlength{{\\tabcolsep}}{{4pt}}
    \\resizebox{{\\linewidth}}{{!}}{{%
    \\begin{{tabular}}{{llrrrrrr}}
        \\toprule
        & & \\multicolumn{{2}}{{c}}{{\\textbf{{Cliente: pico de RSS}}}} & \\multicolumn{{4}}{{c}}{{\\textbf{{Servidor: memória do cgroup}}}} \\\\
        \\cmidrule(lr){{3-4}} \\cmidrule(lr){{5-8}}
        \\textbf{{Condição}} & \\textbf{{Cenário}} & \\textbf{{Mediana}} & \\textbf{{IQR}} &
        \\textbf{{Antes}} & \\textbf{{Pico}} & \\textbf{{IQR do pico}} & \\textbf{{Variação}} \\\\
        \\midrule
{chr(10).join(linhas)}
        \\bottomrule
    \\end{{tabular}}}}
    \\fonte{{Elaborada pelo autor (2026). ``Variação'' é o pico durante a conexão menos a memória antes dela.}}
\\end{{table}}"""


def tab_rede(resumo, ex):
    linhas = []
    ref = {m: float(resumo[("base", m, "classico")]["media"]) for m in ("bytes_c2s", "bytes_s2c", "bytes_total")}
    for s in CEN:
        g = lambda m: float(resumo[("base", m, s)]["media"])
        tot = g("bytes_total")
        ov = tot - ref["bytes_total"]
        ovp = 100 * ov / ref["bytes_total"]
        kex = g("kex_bytes_c") + g("kex_bytes_s")
        ov_txt = "--" if s == "classico" else f"+{int(ov)} (+{v(ovp,2)}\\,\\%)"
        linhas.append(
            f"        {ROT[s]} & {int(g('bytes_c2s'))} & {int(g('bytes_s2c'))} & {int(tot)} & "
            f"{int(g('kex_bytes_c'))} & {int(g('kex_bytes_s'))} & {int(kex)} & {ov_txt} \\\\")
    return f"""\\begin{{table}}[H]
    \\centering
    \\caption{{Volume de dados do estabelecimento da sessão (do SYN ao NEWKEYS do cliente), em bytes.}}
    \\label{{tab:c2_rede}}
    \\small
    \\setlength{{\\tabcolsep}}{{4pt}}
    \\resizebox{{\\linewidth}}{{!}}{{%
    \\begin{{tabular}}{{lrrrrrrr}}
        \\toprule
        & \\multicolumn{{3}}{{c}}{{\\textbf{{Carga útil TCP única}}}} & \\multicolumn{{3}}{{c}}{{\\textbf{{Mensagens de KEX (30 e 31)}}}} & \\\\
        \\cmidrule(lr){{2-4}} \\cmidrule(lr){{5-7}}
        \\textbf{{Cenário}} & \\textbf{{Cli.$\\rightarrow$Serv.}} & \\textbf{{Serv.$\\rightarrow$Cli.}} & \\textbf{{Total}} &
        \\textbf{{Cliente}} & \\textbf{{Servidor}} & \\textbf{{Soma}} & \\textbf{{Overhead vs. clássico}} \\\\
        \\midrule
{chr(10).join(linhas)}
        \\bottomrule
    \\end{{tabular}}}}
    \\fonte{{Elaborada pelo autor (2026). Valores idênticos nas 30 execuções de cada cenário e nas quatro condições de rede.}}
\\end{{table}}"""


def tab_pacotes(resumo):
    linhas = []
    for c in COND:
        cel = []
        for s in CEN:
            cel.append(f"{v(resumo[(c,'pacotes_c2s',s)]['mediana'],0)} / {v(resumo[(c,'pacotes_s2c',s)]['mediana'],0)} / "
                       f"{v(resumo[(c,'pacotes_total',s)]['mediana'],0)}")
        retr = sum(float(resumo[(c, 'retrans_total', s)]['media']) * int(resumo[(c, 'retrans_total', s)]['n']) for s in CEN)
        linhas.append(f"        {ROTC[c]} & " + " & ".join(cel) + f" & {int(round(retr))} \\\\")
    return f"""\\begin{{table}}[H]
    \\centering
    \\caption{{Pacotes capturados no estabelecimento da sessão (medianas: cliente$\\rightarrow$servidor / servidor$\\rightarrow$cliente / total) e retransmissões TCP.}}
    \\label{{tab:c2_pacotes}}
    \\small
    \\begin{{tabular}}{{lcccc}}
        \\toprule
        \\textbf{{Condição}} & \\textbf{{Clássico}} & \\textbf{{Híbrido}} & \\textbf{{PQC puro}} & \\textbf{{Retransmissões (90 conexões)}} \\\\
        \\midrule
{chr(10).join(linhas)}
        \\bottomrule
    \\end{{tabular}}
    \\fonte{{Elaborada pelo autor (2026). Contagem no ponto de captura, com \\textit{{offloads}} habilitados.}}
\\end{{table}}"""


def tab_contagens(cont):
    idx = {(r["condicao"], r["cenario"]): r for r in cont}
    linhas = []
    for c in COND:
        cel = []
        for s in CEN:
            r = idx[(c, s)]
            cel.append(f"{r['sucessos']}/{r['tentativas']}")
        tot_f = sum(int(idx[(c, s)]["falhas"]) for s in CEN)
        tot_t = sum(int(idx[(c, s)]["timeouts"]) for s in CEN)
        tot_fb = sum(int(idx[(c, s)]["fallback"]) for s in CEN)
        tot_ok = sum(int(idx[(c, s)]["comando_verificado"]) for s in CEN)
        linhas.append(f"        {ROTC[c]} & " + " & ".join(cel) + f" & {tot_f} & {tot_t} & {tot_fb} & {tot_ok}/90 \\\\")
    return f"""\\begin{{table}}[H]
    \\centering
    \\caption{{Resultado das conexões medidas por condição de rede.}}
    \\label{{tab:c2_contagens}}
    \\small
    \\setlength{{\\tabcolsep}}{{4pt}}
    \\begin{{tabular}}{{lccccccc}}
        \\toprule
        & \\multicolumn{{3}}{{c}}{{\\textbf{{Sucessos/tentativas}}}} & & & & \\\\
        \\cmidrule(lr){{2-4}}
        \\textbf{{Condição}} & \\textbf{{Cláss.}} & \\textbf{{Híbr.}} & \\textbf{{PQC}} & \\textbf{{Falhas}} &
        \\textbf{{Timeouts}} & \\textbf{{Fallback}} & \\textbf{{Comando verificado}} \\\\
        \\midrule
{chr(10).join(linhas)}
        \\bottomrule
    \\end{{tabular}}
    \\fonte{{Elaborada pelo autor (2026).}}
\\end{{table}}"""


def tab_estabilidade(est):
    nomes = {"rekey": "Rekey durante transferência de 8 MiB",
             "fallback": "Fallback (método preferido não anunciado)",
             "kex_nao_suportado": "KEX não anunciado pelo servidor",
             "banner_invalido": "Banner de identificação inválido",
             "pacote_invalido": "Pacote com comprimento inválido"}
    agreg = defaultdict(lambda: defaultdict(int))
    ativo = defaultdict(int)
    for e in est:
        agreg[e["teste"]][e["resultado"]] += 1
        agreg[e["teste"]]["_n"] += 1
        if e["servidor_ativo"] == "True":
            ativo[e["teste"]] += 1
    rot_res = {"sucesso": "sucesso", "rejeicao_controlada": "rejeição controlada",
               "encerramento_controlado": "encerramento controlado"}
    linhas = []
    for t in ("rekey", "fallback", "kex_nao_suportado", "banner_invalido", "pacote_invalido"):
        g = agreg[t]
        res = "; ".join(f"{n} {rot_res.get(k, k.replace('_', ' '))}" for k, n in g.items() if k != "_n")
        linhas.append(f"        {nomes[t]} & {g['_n']} & {res} & {ativo[t]}/{g['_n']} \\\\")
    return f"""\\begin{{table}}[H]
    \\centering
    \\caption{{Resultados dos testes de estabilidade e de tratamento de erros (quatro condições de rede somadas).}}
    \\label{{tab:c2_estabilidade}}
    \\small
    \\resizebox{{\\linewidth}}{{!}}{{%
    \\begin{{tabular}}{{lrlc}}
        \\toprule
        \\textbf{{Teste}} & \\textbf{{Tentativas}} & \\textbf{{Resultado}} & \\textbf{{Servidor ativo após o teste}} \\\\
        \\midrule
{chr(10).join(linhas)}
        \\bottomrule
    \\end{{tabular}}}}
    \\fonte{{Elaborada pelo autor (2026). Rekey e fallback foram executados para os três cenários.}}
\\end{{table}}"""


def tab_condicoes(man):
    linhas = []
    for c in man["condicoes"]:
        atraso = c["rtt_config_ms"] / 2
        cfg_txt = []
        if atraso:
            cfg_txt.append(f"atraso de {v(atraso,0)} ms em cada sentido")
        if c["banda_config_mbit"]:
            cfg_txt.append(f"taxa de {c['banda_config_mbit']} Mbit/s em cada sentido")
        p = c["ping"]
        linhas.append(f"        {ROTC[c['nome']]} & {'; '.join(cfg_txt) or 'sem \\texttt{netem}'} & "
                      f"{v(p['rtt_media_ms'],3)} & {v(p['rtt_mdev_ms'],3)} & {v(p['perda_pct'],0)} \\\\")
    return f"""\\begin{{table}}[H]
    \\centering
    \\caption{{Condições de rede emuladas e RTT observado antes de cada bloco de medições.}}
    \\label{{tab:c2_condicoes}}
    \\small
    \\resizebox{{\\linewidth}}{{!}}{{%
    \\begin{{tabular}}{{llrrr}}
        \\toprule
        \\textbf{{Condição}} & \\textbf{{Configuração (\\texttt{{tc netem}})}} & \\textbf{{RTT médio (ms)}} &
        \\textbf{{Desvio (ms)}} & \\textbf{{Perda (\\%)}} \\\\
        \\midrule
{chr(10).join(linhas)}
        \\bottomrule
    \\end{{tabular}}}}
    \\fonte{{Elaborada pelo autor (2026). RTT medido com dez requisições de eco do \\texttt{{ping}}; nenhuma perda foi induzida.}}
\\end{{table}}"""


def main():
    dir_c, dir_fig, saida = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    ex, resumo, testes, comp, est, cont, man = carregar(dir_c)
    estilo()
    fig_distribuicoes(ex, ["T_kex_ms"], ["$T_{\\mathrm{KEX}}$ (ms)"], dir_fig / "c2_kex_distribuicao.pdf")
    fig_distribuicoes(ex, ["T_transporte_ms", "T_canal_proc_ms"],
                      ["$T_{\\mathrm{transporte}}$ (ms)", "$T_{\\mathrm{canal}}$ (ms)"],
                      dir_fig / "c2_transporte_canal_distribuicao.pdf")
    fig_cpu(resumo, dir_fig / "c2_cpu.pdf")
    fig_bytes(resumo, dir_fig / "c2_bytes.pdf")

    macros = {
        "tabCDoisCondicoes": tab_condicoes(man),
        "tabCDoisContagens": tab_contagens(cont),
        "tabCDoisEstabilidade": tab_estabilidade(est),
        "tabCDoisKex": tab_descritiva(resumo, "T_kex_ms",
                                      "Estatísticas descritivas de $T_{\\mathrm{KEX}}$ por condição de rede.",
                                      "tab:c2_kex"),
        "tabCDoisTransporte": tab_descritiva(resumo, "T_transporte_ms",
                                             "Estatísticas descritivas de $T_{\\mathrm{transporte}}$ por condição de rede.",
                                             "tab:c2_transporte"),
        "tabCDoisCanal": tab_descritiva(resumo, "T_canal_proc_ms",
                                        "Estatísticas descritivas de $T_{\\mathrm{canal}}$ por condição de rede.",
                                        "tab:c2_canal"),
        "tabCDoisTestesTempo": tab_testes(testes, [("T_kex_ms", "$T_{\\mathrm{KEX}}$"),
                                                   ("T_transporte_ms", "$T_{\\mathrm{transporte}}$"),
                                                   ("T_autenticado_proc_ms", "$T_{\\mathrm{aut}}$"),
                                                   ("T_canal_proc_ms", "$T_{\\mathrm{canal}}$")],
                                          "Testes estatísticos das medidas de tempo.", "tab:c2_testes_tempo"),
        "tabCDoisDiferencas": tab_diferencas(comp, [("T_kex_ms", "$T_{\\mathrm{KEX}}$"),
                                                    ("T_transporte_ms", "$T_{\\mathrm{transporte}}$"),
                                                    ("T_canal_proc_ms", "$T_{\\mathrm{canal}}$")],
                                             "Diferenças absolutas e relativas das médias de tempo em relação ao cenário clássico.",
                                             "tab:c2_diferencas"),
        "tabCDoisCpu": tab_cpu(resumo),
        "tabCDoisTestesRecursos": tab_testes(testes, [("cli_cpu_total_ms", "CPU cliente"),
                                                      ("srv_cpu_total_ms", "CPU servidor"),
                                                      ("cli_rss_pico_kb", "RSS cliente"),
                                                      ("srv_mem_pico_kb", "Pico servidor")],
                                             "Testes estatísticos das medidas de CPU e memória.",
                                             "tab:c2_testes_recursos"),
        "tabCDoisMemoria": tab_memoria(resumo, testes),
        "tabCDoisRede": tab_rede(resumo, ex),
        "tabCDoisPacotes": tab_pacotes(resumo),
    }
    with open(saida, "w") as f:
        f.write("% Gerado automaticamente por gerar_tex_campanha.py a partir de\n")
        f.write(f"% {Path(dir_c).resolve()}\n% Não edite à mão: regenere a partir da campanha.\n\n")
        for nome, corpo in macros.items():
            f.write(f"\\newcommand{{\\{nome}}}{{%\n{corpo}%\n}}\n\n")
    print("figuras e tabelas geradas")


if __name__ == "__main__":
    main()
