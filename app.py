"""
Gestão de Aluguéis — versão com Supabase (Postgres) como banco de dados.

Backend: projeto Supabase "Diversos", 3 tabelas (alugueis_lojas,
alugueis_pagamentos, alugueis_reajustes — ver schema_alugueis.sql). O app
lê/escreve via supabase-py, usando a service_role key (mantida só nos
secrets do Streamlit, nunca chega ao navegador). Os cálculos continuam
feitos em Python — o banco guarda só os campos que o usuário digita.

Registro de Pagamentos:
    Loja | Dt Lcto | Referente | Dt Vcto | Valor Lcto | Valor Pago | Observação

    Total Pago    = Valor Pago                        (multa/juros/CM viram linha
                                                         própria em Referente+Valor)
    Saldo Devedor = Valor Lcto - Total Pago           (calculado)

Configuração (Streamlit secrets):
    app_password  = "sua_senha"
    supabase_url  = "https://xxxxxxxx.supabase.co"
    supabase_key  = "eyJ..."   # service_role key (Project Settings → API)
"""

from __future__ import annotations

import datetime as dt
import time

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from fpdf import FPDF, XPos, YPos
from supabase import create_client

# ----------------------------------------------------------------------------- #
# Estrutura dos dados (nomes "de planilha" usados no resto do app — a camada
# de acesso ao Supabase abaixo converte de/para os nomes de coluna do banco)
# ----------------------------------------------------------------------------- #
H_LOJAS = ["Loja", "Responsável", "Aluguel Atual", "Dia Vcto", "Dia Pgto",
           "Assinatura Contrato", "Débito Geral", "Caução", "Índice Reajuste",
           "Vencimento Contrato", "Observação", "Próximo Reajuste"]

INDICES = ["IGP-M", "IGP-DI", "IPCA", "INCC-DI"]

MESES_PT = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho",
            "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]

H_PAG = ["Loja", "Dt Lcto", "Referente", "Dt Vcto", "Valor Lcto",
         "Valor Pago", "Observação"]

H_REAJ = ["Loja", "Data", "Índice", "%", "Valor Anterior", "Valor Novo"]


# ----------------------------------------------------------------------------- #
# Conexão com o Supabase
# ----------------------------------------------------------------------------- #
def _get_secret(*nomes):
    """Procura a chave nos secrets do Streamlit tentando cada nome da lista —
    aceita tanto 'supabase_url' (minúsculo) quanto 'SUPABASE_URL' (maiúsculo),
    já que o TOML dos secrets é case-sensitive e cada um escreve diferente."""
    for n in nomes:
        if n in st.secrets:
            return st.secrets[n]
    raise KeyError(
        "Nenhuma dessas chaves foi encontrada nos secrets do Streamlit: "
        + ", ".join(nomes))


@st.cache_resource
def get_client():
    url = _get_secret("supabase_url", "SUPABASE_URL", "Supabase_Url")
    key = _get_secret("supabase_key", "SUPABASE_KEY", "SUPABASE_SERVICE_KEY",
                       "SUPABASE_SERVICE_ROLE_KEY", "supabase_service_key")
    return create_client(url, key)


# Nome "de planilha" -> tabela/colunas reais no Postgres.
TABELAS = {"Lojas": "alugueis_lojas", "Pagamentos": "alugueis_pagamentos",
           "Reajustes": "alugueis_reajustes"}

PK_COL = {"Lojas": "loja", "Pagamentos": "id", "Reajustes": "id"}

COLMAP = {
    "Lojas": {
        "loja": "Loja", "responsavel": "Responsável", "aluguel_atual": "Aluguel Atual",
        "dia_vcto": "Dia Vcto", "dia_pgto": "Dia Pgto",
        "assinatura_contrato": "Assinatura Contrato", "debito_geral": "Débito Geral",
        "caucao": "Caução", "indice_reajuste": "Índice Reajuste",
        "vencimento_contrato": "Vencimento Contrato", "observacao": "Observação",
        "proximo_reajuste": "Próximo Reajuste",
    },
    "Pagamentos": {
        "loja": "Loja", "dt_lcto": "Dt Lcto", "referente": "Referente",
        "dt_vcto": "Dt Vcto", "valor_lcto": "Valor Lcto", "valor_pago": "Valor Pago",
        "observacao": "Observação",
    },
    "Reajustes": {
        "loja": "Loja", "data": "Data", "indice": "Índice", "percentual": "%",
        "valor_anterior": "Valor Anterior", "valor_novo": "Valor Novo",
    },
}

DATE_COLS = {
    "Lojas": {"Assinatura Contrato", "Vencimento Contrato", "Próximo Reajuste"},
    "Pagamentos": {"Dt Lcto", "Dt Vcto"},
    "Reajustes": {"Data"},
}

ORDER_COL = {"Lojas": "loja", "Pagamentos": "dt_lcto", "Reajustes": "data"}


def _com_retentativa(func, *args, tentativas: int = 3, espera_inicial: float = 1.5, **kwargs):
    """Executa uma chamada ao Supabase tentando de novo (com espera
    crescente) se der um erro passageiro de rede/conexão — evita que uma
    instabilidade momentânea derrube a página."""
    espera = espera_inicial
    for tentativa in range(tentativas):
        try:
            return func(*args, **kwargs)
        except Exception:
            if tentativa < tentativas - 1:
                time.sleep(espera)
                espera *= 2
                continue
            raise


def _fmt_data(v) -> str:
    """Valor de data vindo do Supabase (string "AAAA-MM-DD" ou None) -> texto
    "dd/mm/aaaa" (ou "") — mesmo formato que o resto do app espera, herdado
    da época da planilha."""
    if not v:
        return ""
    if isinstance(v, (dt.date, dt.datetime)):
        return v.strftime("%d/%m/%Y")
    try:
        return dt.datetime.strptime(str(v)[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return str(v)


def _parse_data_br(txt) -> str | None:
    """Texto "dd/mm/aaaa" digitado pelo usuário -> "aaaa-mm-dd" para gravar
    no Postgres, ou None se vazio/inválido."""
    txt = (txt or "").strip()
    if not txt:
        return None
    d = pd.to_datetime(txt, dayfirst=True, errors="coerce")
    return None if pd.isna(d) else d.date().isoformat()


@st.cache_data(ttl=15, show_spinner=False)
def _valores_aba(nome: str):
    """Lê a tabela inteira do Supabase e guarda em cache por alguns segundos
    — assim várias chamadas a ler() no mesmo carregamento de página (ou em
    reruns seguidos) não geram uma requisição nova para cada uma."""
    tabela = TABELAS[nome]
    ordem = ORDER_COL[nome]

    def _fetch():
        q = get_client().table(tabela).select("*").order(ordem)
        if nome != "Lojas":
            q = q.order("id")   # desempate estável quando a data repete
        return q.execute().data

    return _com_retentativa(_fetch)


def invalidar_cache_planilha():
    """Chamar sempre depois de qualquer escrita (excluir, salvar, editar) —
    limpa o cache de leitura para a página já recarregar com o dado novo."""
    _valores_aba.clear()


def ler(nome, headers, com_linha: bool = False) -> pd.DataFrame:
    """Lê a tabela e devolve um DataFrame com os mesmos nomes de coluna "de
    planilha" que o resto do app usa (H_LOJAS/H_PAG/H_REAJ).

    Se com_linha=True, devolve também '__linha' com o id real do registro no
    banco (usado para excluir/editar com segurança).
    """
    registros = _valores_aba(nome)
    colmap = COLMAP[nome]
    datecols = DATE_COLS[nome]

    linhas = []
    for reg in registros:
        linha = {}
        for col_db, col_sheet in colmap.items():
            v = reg.get(col_db)
            if col_sheet in datecols:
                v = _fmt_data(v)
            elif v is None:
                v = ""
            linha[col_sheet] = v
        if com_linha:
            linha["__linha"] = reg.get("id")
        linhas.append(linha)

    cols = list(headers) + (["__linha"] if com_linha else [])
    return pd.DataFrame(linhas, columns=cols)


def excluir_linha(aba: str, chave):
    tabela, pk = TABELAS[aba], PK_COL[aba]
    _com_retentativa(lambda: get_client().table(tabela).delete().eq(pk, chave).execute())
    invalidar_cache_planilha()


def limpar_pagamentos():
    """Apaga TODOS os lançamentos (tabela alugueis_pagamentos) de todas as
    lojas. A tabela de imóveis não é tocada."""
    _com_retentativa(
        lambda: get_client().table("alugueis_pagamentos").delete().gte("id", 0).execute())
    invalidar_cache_planilha()


# ----------------------------------------------------------------------------- #
# Utilidades
# ----------------------------------------------------------------------------- #
def brl(v) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        v = 0.0
    return ("R$ " + f"{v:,.2f}").replace(",", "X").replace(".", ",").replace("X", ".")


def num(v) -> float:
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("R$", "").replace("%", "").replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_indices(v) -> list:
    """'IGP-M, IPCA' -> ['IGP-M', 'IPCA']. Ignora o que não estiver em INDICES."""
    if not v:
        return []
    itens = [x.strip() for x in str(v).replace(";", ",").split(",")]
    return [x for x in itens if x in INDICES]


def to_date(v):
    if not v:
        return None
    return pd.to_datetime(v, dayfirst=True, errors="coerce")


def total_pago(r) -> float:
    """Total Pago = Valor Pago (multa/juros/correção monetária entram como uma
    linha própria de Referente+Valor, não como campos separados)."""
    return num(r["Valor Pago"])


def linhas_validas(df: pd.DataFrame) -> list:
    """Filtra as linhas da grade (Entradas/Recebido) com descrição e valor preenchidos."""
    out = []
    for _, row in df.iterrows():
        desc = str(row.get("Descrição") or "").strip()
        valor = num(row.get("Valor"))
        if desc and valor != 0:
            out.append((desc, valor))
    return out


# ----------------------------------------------------------------------------- #
# PDF do Demonstrativo Mensal
# ----------------------------------------------------------------------------- #
def pdf_seguro(txt) -> str:
    """A fonte core do PDF (Helvetica) só desenha Latin-1 — troca pontuação
    "esperta" (travessão, aspas curvas...) pelo equivalente simples e, se
    ainda sobrar algo fora do Latin-1, substitui em vez de quebrar."""
    if txt is None:
        return ""
    txt = str(txt)
    for k, v in {"—": "-", "–": "-", "’": "'", "‘": "'",
                 "“": '"', "”": '"', "…": "..."}.items():
        txt = txt.replace(k, v)
    return txt.encode("latin-1", "replace").decode("latin-1")


def gerar_pdf_demonstrativo(loja_nome, mes_label, contrato_info, saldo_ant,
                             entradas, tot_entradas, obs_entradas,
                             recebido, tot_recebido, obs_recebido,
                             pendente, obs_finais) -> bytes:
    ROW_MID = dict(new_x=XPos.RIGHT, new_y=YPos.TOP)
    ROW_END = dict(new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, pdf_seguro("Demonstrativo Mensal"), **ROW_END)
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 7, pdf_seguro(f"{loja_nome} — {mes_label}"), **ROW_END)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(0, 5, pdf_seguro(contrato_info))
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, pdf_seguro(f"Saldo mês anterior: {brl(saldo_ant)}"), **ROW_END)
    pdf.ln(3)

    def tabela(titulo, linhas, total, obs):
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, pdf_seguro(titulo), **ROW_END)
        pdf.set_fill_color(175, 198, 232)   # mesmo azul-claro do cabeçalho no app
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(130, 7, "Descrição", border=1, fill=True, **ROW_MID)
        pdf.cell(50, 7, pdf_seguro("R$ Valor"), border=1, fill=True, align="R", **ROW_END)
        pdf.set_font("Helvetica", "", 10)
        for desc, val in linhas:
            pdf.cell(130, 7, pdf_seguro(desc), border=1, **ROW_MID)
            pdf.cell(50, 7, pdf_seguro(brl(val)), border=1, align="R", **ROW_END)
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(130, 7, pdf_seguro(f"Total {titulo}"), border=1, **ROW_MID)
        pdf.cell(50, 7, pdf_seguro(brl(total)), border=1, align="R", **ROW_END)
        if obs:
            pdf.set_font("Helvetica", "I", 9)
            pdf.multi_cell(0, 5, pdf_seguro(f"OBS: {obs}"))
        pdf.ln(4)

    tabela("Entradas", entradas, tot_entradas, obs_entradas)
    tabela("Recebido no mês", recebido, tot_recebido, obs_recebido)

    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 10, pdf_seguro(f"Pendente transferido pro mês seguinte: {brl(pendente)}"),
             **ROW_END)
    if obs_finais:
        pdf.set_font("Helvetica", "I", 10)
        pdf.multi_cell(0, 5, pdf_seguro(f"OBS: {obs_finais}"))

    return bytes(pdf.output())


def gerar_pdf_extrato(loja_nome, periodo_label, linhas, saldo_final) -> bytes:
    """PDF do extrato de lançamentos (Descrição, Vcto, Entrada, Recebido,
    Saldo Acum., Observação) para um período — mesma paleta do app."""
    ROW_MID = dict(new_x=XPos.RIGHT, new_y=YPos.TOP)
    ROW_END = dict(new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    L = {"desc": 68, "vcto": 24, "ent": 34, "rec": 34, "saldo": 34, "obs": 75}

    pdf = FPDF(orientation="L")
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, pdf_seguro("Extrato de Lançamentos"), **ROW_END)
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 7, pdf_seguro(f"{loja_nome} — {periodo_label}"), **ROW_END)
    pdf.ln(3)

    def cabecalho():
        pdf.set_fill_color(175, 198, 232)   # mesmo azul-claro do cabeçalho no app
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(L["desc"], 7, pdf_seguro("Descrição"), border=1, fill=True, **ROW_MID)
        pdf.cell(L["vcto"], 7, "Dt Vcto", border=1, fill=True, align="C", **ROW_MID)
        pdf.cell(L["ent"], 7, pdf_seguro("Valor Entrada"), border=1, fill=True, align="R", **ROW_MID)
        pdf.cell(L["rec"], 7, pdf_seguro("Valor Recebido"), border=1, fill=True, align="R", **ROW_MID)
        pdf.cell(L["saldo"], 7, pdf_seguro("Saldo Acum."), border=1, fill=True, align="R", **ROW_MID)
        pdf.cell(L["obs"], 7, pdf_seguro("Observação"), border=1, fill=True, **ROW_END)

    def corta(txt, limite):
        txt = pdf_seguro(txt)
        return txt if len(txt) <= limite else txt[:limite - 3] + "..."

    cabecalho()
    pdf.set_font("Helvetica", "", 9)
    for item in linhas:
        if pdf.get_y() > 180:   # perto do fim da página em paisagem: nova página + cabeçalho
            pdf.add_page()
            cabecalho()
            pdf.set_font("Helvetica", "", 9)
        pdf.cell(L["desc"], 6, corta(item["Referente"], 42), border=1, **ROW_MID)
        pdf.cell(L["vcto"], 6, pdf_seguro(item["Dt Vcto"]), border=1, align="C", **ROW_MID)
        pdf.cell(L["ent"], 6, pdf_seguro(brl(item["R$ Valor Lcto"])), border=1, align="R", **ROW_MID)
        pdf.cell(L["rec"], 6, pdf_seguro(brl(item["R$ Valor Pago"])), border=1, align="R", **ROW_MID)
        pdf.cell(L["saldo"], 6, pdf_seguro(brl(item["Saldo Acum."])), border=1, align="R", **ROW_MID)
        pdf.cell(L["obs"], 6, corta(item["Observação"], 48), border=1, **ROW_END)

    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, pdf_seguro(f"Saldo atual: {brl(saldo_final)}"), **ROW_END)

    return bytes(pdf.output())


# ----------------------------------------------------------------------------- #
# Cálculo de saldo
# ----------------------------------------------------------------------------- #
def saldo_loja(loja_id, lojas: pd.DataFrame, pags: pd.DataFrame) -> float:
    """Débito Geral + Σ Valor Lcto − Σ Total Pago. Positivo = devedor."""
    base = 0.0
    linha = lojas[lojas["Loja"].astype(str) == str(loja_id)]
    if not linha.empty:
        base = num(linha.iloc[0]["Débito Geral"])
    if pags.empty:
        return base
    p = pags[pags["Loja"].astype(str) == str(loja_id)]
    lancado = sum(num(r["Valor Lcto"]) for _, r in p.iterrows())
    pago = sum(total_pago(r) for _, r in p.iterrows())
    return base + lancado - pago


# ----------------------------------------------------------------------------- #
# Login
# ----------------------------------------------------------------------------- #
def sem_gerenciador_de_senha():
    """Evita que o navegador ofereça salvar/gerar senha no campo de login.

    Streamlit não expõe o atributo HTML `autocomplete` do campo de senha, então
    isso é feito via um componente invisível que ajusta o input real (que vive
    na página-pai, fora do iframe do componente) depois que ele é renderizado.
    Reutilizável em qualquer app: chame antes do primeiro `text_input` de senha.
    """
    components.html(
        """
        <script>
        function ajustar() {
            const doc = window.parent.document;
            doc.querySelectorAll('input[type="password"]').forEach(el => {
                el.setAttribute('autocomplete', 'current-password');
                el.setAttribute('data-lpignore', 'true');   // LastPass
                el.setAttribute('data-1p-ignore', 'true');  // 1Password
                el.setAttribute('data-bwignore', 'true');   // Bitwarden
            });
        }
        ajustar();
        new MutationObserver(ajustar).observe(window.parent.document.body,
            {childList: true, subtree: true});
        </script>
        """,
        height=0,
    )


def checar_senha() -> bool:
    senha = st.secrets.get("app_password", "1234")
    if st.session_state.get("ok"):
        return True
    st.markdown("### 🔐 Gestão de Aluguéis")
    sem_gerenciador_de_senha()
    # Campo de senha + botão dentro de um st.form: sem form, clicar no botão (ou
    # dar Enter) podia disparar o rerun ANTES do valor digitado terminar de ser
    # confirmado pelo navegador, e o app comparava com um valor antigo/vazio —
    # aparecia "Senha incorreta" mesmo com a senha certa. O form envia tudo
    # junto, de uma vez, e resolve essa corrida.
    with st.form("login"):
        pwd = st.text_input("Senha", type="password")
        entrar = st.form_submit_button("Entrar", type="primary")
    if entrar:
        if pwd == senha:
            st.session_state["ok"] = True
            st.rerun()
        else:
            st.error("Senha incorreta.")
    return False


# ----------------------------------------------------------------------------- #
# Páginas
# ----------------------------------------------------------------------------- #
def pagina_painel():
    st.subheader("📊 Painel")
    lojas, pags = ler("Lojas", H_LOJAS), ler("Pagamentos", H_PAG)
    if lojas.empty:
        st.info("Cadastre imóveis na aba Imóveis.")
        return

    hoje = dt.date.today()
    mes_atual = hoje.strftime("%Y-%m")

    recebido_mes = 0.0
    if not pags.empty:
        for _, r in pags.iterrows():
            d = to_date(r.get("Dt Lcto"))
            if d is not None and not pd.isna(d) and d.strftime("%Y-%m") == mes_atual:
                recebido_mes += total_pago(r)

    saldos = {r["Loja"]: saldo_loja(r["Loja"], lojas, pags) for _, r in lojas.iterrows()}
    total_pend = sum(v for v in saldos.values() if v > 0.005)
    total_cred = -sum(v for v in saldos.values() if v < -0.005)
    aluguel_esperado = sum(num(r["Aluguel Atual"]) for _, r in lojas.iterrows()
                           if str(r["Responsável"]).strip())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Recebido no mês", brl(recebido_mes))
    c2.metric("Aluguel esperado/mês", brl(aluguel_esperado))
    c3.metric("Total pendente", brl(total_pend))
    c4.metric("Créditos a favor", brl(total_cred))

    st.divider()
    tab = []
    for _, r in lojas.iterrows():
        s = saldos[r["Loja"]]
        sit = "🔴 Em aberto" if s > 0.005 else ("🔵 Crédito" if s < -0.005 else "🟢 Em dia")
        tab.append({"Loja": r["Loja"], "Responsável": r["Responsável"],
                    "Aluguel": brl(num(r["Aluguel Atual"])), "Saldo": brl(s),
                    "Situação": sit})
    st.dataframe(pd.DataFrame(tab), use_container_width=True, hide_index=True)

    avisos = []
    for _, r in lojas.iterrows():
        d = to_date(r.get("Vencimento Contrato"))
        if d is not None and not pd.isna(d):
            dias = (d.date() - hoje).days
            if dias <= 60:
                avisos.append((dias, r["Responsável"], d.date()))
    if avisos:
        st.markdown("##### 📄 Contratos a vencer")
        for dias, resp, d in sorted(avisos):
            txt = f"**{resp}** — {d.strftime('%d/%m/%Y')}"
            (st.warning if dias < 0 else st.info)(
                txt + (f" (há {-dias} dias)" if dias < 0 else f" (em {dias} dias)"))


def mes_soma(ano: int, mes: int, n: int) -> tuple:
    """Soma/subtrai n meses de (ano, mes), tipo um relógio de 12 posições."""
    idx = (ano * 12 + (mes - 1)) + n
    return idx // 12, idx % 12 + 1


LANCAMENTOS_CSS = """
<style>
[data-testid="stMain"] p,
[data-testid="stMain"] label,
[data-testid="stMain"] .stMarkdown,
[data-testid="stMain"] [data-testid="stCaptionContainer"],
[data-testid="stMain"] [data-testid="stMetricLabel"],
[data-testid="stMain"] [data-testid="stMetricValue"],
[data-testid="stMain"] textarea {
    font-weight: 700 !important;
}
</style>
"""


def pagina_lancamentos():
    st.subheader("🧾 Lançamentos — Demonstrativo Mensal")
    # Negrito nessa página a pedido do usuário (cliente com dificuldade de
    # visão). O peso da fonte "de fábrica" do app já subiu no config.toml
    # (baseFontWeight) — isso é global, o Streamlit não permite por página —
    # e aqui reforça pra negrito de verdade nos textos que o CSS alcança.
    # O texto DENTRO da tabela (Descrição/Valor) é desenhado em canvas pelo
    # componente da grade, então só o baseFontWeight do tema chega até ele.
    st.markdown(LANCAMENTOS_CSS, unsafe_allow_html=True)
    lojas = ler("Lojas", H_LOJAS)
    if lojas.empty:
        st.info("Cadastre um imóvel primeiro.")
        return

    op = {f'{r["Loja"]} — {r["Responsável"]}': r["Loja"] for _, r in lojas.iterrows()}

    # Fora do form de propósito: assim o imóvel e o mês NÃO são resetados a
    # cada interação, e os campos que dependem deles atualizam na hora.
    c1, c2 = st.columns([2, 1])
    sel = c1.selectbox("Imóvel", list(op.keys()), key="lc_imovel")
    loja_id = op[sel]
    loja_row = lojas[lojas["Loja"] == loja_id].iloc[0]

    hoje = dt.date.today()
    opcoes_mes = [mes_soma(hoje.year, hoje.month, i) for i in range(-6, 3)]
    labels_mes = [f"{MESES_PT[m - 1]}/{a}" for a, m in opcoes_mes]
    escolha = c2.selectbox("Mês de referência", labels_mes,
                           index=opcoes_mes.index((hoje.year, hoje.month)),
                           key="lc_mes")
    ano_ref, mes_ref = opcoes_mes[labels_mes.index(escolha)]
    ini_mes = dt.date(ano_ref, mes_ref, 1)

    h1, h2, h3 = st.columns(3)
    h1.caption(f"📄 **Início do contrato:** {loja_row.get('Assinatura Contrato') or '—'}")
    h2.caption(f"📈 **Próximo reajuste:** {loja_row.get('Próximo Reajuste') or '—'}")
    h3.caption(f"⏳ **Vcto do contrato:** {loja_row.get('Vencimento Contrato') or '—'}")

    pags = ler("Pagamentos", H_PAG, com_linha=True)
    p_loja = (pags[pags["Loja"].astype(str) == str(loja_id)].copy()
              if not pags.empty else pd.DataFrame())
    if not p_loja.empty:
        p_loja["__d"] = p_loja["Dt Lcto"].apply(to_date)

    # --- saldo mês anterior (calculado a partir do histórico) ---
    saldo_ant = num(loja_row["Débito Geral"])
    if not p_loja.empty:
        antes = p_loja[p_loja["__d"].apply(
            lambda d: d is not None and not pd.isna(d) and d.date() < ini_mes)]
        for _, r in antes.iterrows():
            saldo_ant += num(r["Valor Lcto"]) - total_pago(r)
    st.metric("Saldo mês anterior", brl(saldo_ant))

    dia_vcto = int(num(loja_row["Dia Vcto"]) or 1)
    try:
        vcto_aluguel = dt.date(ano_ref, mes_ref, dia_vcto)
    except ValueError:
        vcto_aluguel = ini_mes

    reset_key = st.session_state.get(f"lc_reset_{loja_id}_{ano_ref}_{mes_ref}", 0)

    st.divider()
    st.markdown(f"**Entradas** · aluguel do mês vence em {vcto_aluguel.strftime('%d/%m/%Y')}")
    st.caption("Escreva livremente cada cobrança do mês (Aluguel, IPTU, condomínio, "
               "multa, acordo...) e o valor. Adicione ou apague linhas como numa planilha "
               "(Tab pula pro campo do valor, igual no Excel).")
    entradas_ini = pd.DataFrame(
        [{"Descrição": "Aluguel", "Valor": num(loja_row["Aluguel Atual"])}]
        + [{"Descrição": "", "Valor": 0.0}])
    entradas_df = st.data_editor(
        entradas_ini, num_rows="dynamic", use_container_width=True, hide_index=True,
        key=f"ent_{loja_id}_{ano_ref}_{mes_ref}_{reset_key}",
        column_config={
            "Descrição": st.column_config.TextColumn("Descrição", width="large"),
            "Valor": st.column_config.NumberColumn("R$ Valor", format="%.2f",
                                                    min_value=0.0, step=0.01,
                                                    width="small"),
        })
    tot_entradas = float(pd.to_numeric(entradas_df.get("Valor"), errors="coerce")
                         .fillna(0).sum()) if not entradas_df.empty else 0.0
    st.markdown(f"**Total das Entradas: {brl(tot_entradas)}**")
    obs_entradas = st.text_area("OBS (entradas)", height=60,
                                key=f"obsent_{loja_id}_{ano_ref}_{mes_ref}_{reset_key}")

    st.divider()
    st.markdown("**Recebido no mês** · valor total ou parcial")
    recebido_ini = pd.DataFrame(
        [{"Descrição": "Aluguel", "Valor": 0.0}]
        + [{"Descrição": "", "Valor": 0.0}])
    recebido_df = st.data_editor(
        recebido_ini, num_rows="dynamic", use_container_width=True, hide_index=True,
        key=f"rec_{loja_id}_{ano_ref}_{mes_ref}_{reset_key}",
        column_config={
            "Descrição": st.column_config.TextColumn("Descrição", width="large"),
            "Valor": st.column_config.NumberColumn("R$ Valor", format="%.2f",
                                                    min_value=0.0, step=0.01,
                                                    width="small"),
        })
    tot_recebido = float(pd.to_numeric(recebido_df.get("Valor"), errors="coerce")
                         .fillna(0).sum()) if not recebido_df.empty else 0.0
    st.markdown(f"**Total recebido: {brl(tot_recebido)}**")
    obs_recebido = st.text_area("OBS (recebido)", height=60,
                                key=f"obsrec_{loja_id}_{ano_ref}_{mes_ref}_{reset_key}")

    st.divider()
    pendente = saldo_ant + tot_entradas - tot_recebido
    if pendente > 0.005:
        st.error(f"**Pendente transferido pro mês seguinte: {brl(pendente)}**")
    elif pendente < -0.005:
        st.info(f"**Crédito transferido pro mês seguinte: {brl(-pendente)}**")
    else:
        st.success("**Mês fechado em dia.**")

    obs_finais = st.text_area("OBS finais", height=70,
                              key=f"obsfin_{loja_id}_{ano_ref}_{mes_ref}_{reset_key}")

    entradas_ok = linhas_validas(entradas_df)
    recebido_ok = linhas_validas(recebido_df)

    b1, b2 = st.columns(2)
    salvar = b1.button("💾 Salvar Demonstrativo do Mês", type="primary",
                       use_container_width=True)
    contrato_info = (f"Início do contrato: {loja_row.get('Assinatura Contrato') or '—'}   ·   "
                     f"Próximo reajuste: {loja_row.get('Próximo Reajuste') or '—'}   ·   "
                     f"Vcto do contrato: {loja_row.get('Vencimento Contrato') or '—'}")
    pdf_bytes = gerar_pdf_demonstrativo(
        sel, escolha, contrato_info, saldo_ant,
        entradas_ok, tot_entradas, obs_entradas,
        recebido_ok, tot_recebido, obs_recebido,
        pendente, obs_finais)
    b2.download_button(
        "📄 Exportar PDF", data=pdf_bytes, use_container_width=True,
        file_name=f"demonstrativo_loja{loja_id}_{mes_ref:02d}-{ano_ref}.pdf",
        mime="application/pdf",
        help="Gera um PDF com o que está preenchido na tela agora (salvo ou não) "
             "pra mandar pro inquilino.")

    if salvar:
        dt_lcto = hoje if (ano_ref, mes_ref) == (hoje.year, hoje.month) else ini_mes
        registros = []
        for i, (desc, valor) in enumerate(entradas_ok):
            vcto = vcto_aluguel.isoformat() if desc.strip().lower() == "aluguel" else None
            registros.append({"loja": int(loja_id), "dt_lcto": dt_lcto.isoformat(),
                               "referente": desc, "dt_vcto": vcto,
                               "valor_lcto": valor, "valor_pago": 0,
                               "observacao": obs_entradas if i == 0 else ""})
        for i, (desc, valor) in enumerate(recebido_ok):
            registros.append({"loja": int(loja_id), "dt_lcto": dt_lcto.isoformat(),
                               "referente": desc, "dt_vcto": None,
                               "valor_lcto": 0, "valor_pago": valor,
                               "observacao": obs_recebido if i == 0 else ""})
        if obs_finais.strip():
            registros.append({"loja": int(loja_id), "dt_lcto": dt_lcto.isoformat(),
                               "referente": f"OBS {escolha}", "dt_vcto": None,
                               "valor_lcto": 0, "valor_pago": 0,
                               "observacao": obs_finais.strip()})

        if not registros:
            st.warning("Nada para salvar — preencha ao menos uma linha com descrição e valor.")
        else:
            _com_retentativa(
                lambda: get_client().table("alugueis_pagamentos").insert(registros).execute())
            invalidar_cache_planilha()
            st.session_state[f"lc_reset_{loja_id}_{ano_ref}_{mes_ref}"] = reset_key + 1
            st.success(f"Demonstrativo de {escolha} salvo — {len(linhas)} linha(s) gravada(s).")
            st.rerun()

    # --- o que já está lançado neste mês ---
    st.divider()
    st.markdown(f"**Já lançado em {escolha}**")
    do_mes = (p_loja[p_loja["__d"].apply(
        lambda d: d is not None and not pd.isna(d)
        and d.date().year == ano_ref and d.date().month == mes_ref)]
        if not p_loja.empty else pd.DataFrame())
    if do_mes.empty:
        st.info("Sem lançamentos neste mês.")
        return

    for _, r in do_mes.sort_values("__d").iterrows():
        linha_planilha = int(r["__linha"])
        lado = (f'entrada {brl(num(r["Valor Lcto"]))}' if num(r["Valor Lcto"])
                else f'recebido {brl(total_pago(r))}')
        c1, c2 = st.columns([6, 1])
        c1.write(f'**{r.get("Dt Lcto","")}** · {r.get("Referente","") or "—"} · {lado}'
                 + (f' · _{r["Observação"]}_' if r.get("Observação") else ""))
        if c2.button("🗑️", key=f"dl{linha_planilha}"):
            excluir_linha("Pagamentos", linha_planilha)
            st.rerun()


def pagina_extrato():
    st.subheader("📄 Extrato por imóvel")
    lojas = ler("Lojas", H_LOJAS)
    pags = ler("Pagamentos", H_PAG, com_linha=True)
    if lojas.empty:
        st.info("Cadastre um imóvel primeiro.")
        return

    op = {f'{r["Loja"]} — {r["Responsável"]}': r["Loja"] for _, r in lojas.iterrows()}
    sel = st.selectbox("Imóvel", list(op.keys()))
    loja_id = op[sel]
    lr = lojas[lojas["Loja"] == loja_id].iloc[0]

    saldo_inicial = num(lr["Débito Geral"])
    p = (pags[pags["Loja"].astype(str) == str(loja_id)].copy()
         if not pags.empty else pd.DataFrame())

    linhas = []
    acum = saldo_inicial
    if not p.empty:
        p["__d"] = p["Dt Lcto"].apply(to_date)
        p = p.sort_values("__d", na_position="last")
        for _, r in p.iterrows():
            lcto = num(r["Valor Lcto"])
            tot = total_pago(r)
            acum += lcto - tot
            linhas.append({
                "Dt Lcto": r.get("Dt Lcto", ""),
                "Referente": r.get("Referente", "") or "—",
                "Dt Vcto": r.get("Dt Vcto", "") or "—",
                "R$ Valor Lcto": lcto,
                "R$ Valor Pago": num(r["Valor Pago"]),
                "R$ Total Pago": tot,
                "Saldo Devedor": lcto - tot,
                "Saldo Acum.": acum,
                "Observação": r.get("Observação", "") or "",
                "__linha": int(r["__linha"]),
            })

    if saldo_inicial:
        st.caption(f"Débito geral (cadastro do imóvel): **{brl(saldo_inicial)}**")

    # Filtro de período (por Dt Lcto) — mostra tudo por padrão.
    datas_lcto = [d.date() for item in linhas
                  if pd.notna(d := to_date(item["Dt Lcto"]))]
    dt_min = min(datas_lcto) if datas_lcto else dt.date.today()
    dt_max = max(datas_lcto) if datas_lcto else dt.date.today()

    fc1, fc2 = st.columns(2)
    data_ini = fc1.date_input("De", value=dt_min, format="DD/MM/YYYY", key="ext_de")
    data_fim = fc2.date_input("Até", value=dt_max, format="DD/MM/YYYY", key="ext_ate")

    def _em_periodo(item):
        d = to_date(item["Dt Lcto"])
        return pd.isna(d) or (data_ini <= d.date() <= data_fim)

    linhas_periodo = [item for item in linhas if _em_periodo(item)]

    if linhas_periodo:
        periodo_label = f"{data_ini.strftime('%d/%m/%Y')} a {data_fim.strftime('%d/%m/%Y')}"
        pdf_bytes = gerar_pdf_extrato(sel, periodo_label, linhas_periodo, acum)
        st.download_button(
            "📄 Exportar PDF do período", data=pdf_bytes,
            file_name=f"extrato_{loja_id}_{data_ini.strftime('%Y%m%d')}_{data_fim.strftime('%Y%m%d')}.pdf",
            mime="application/pdf")

    modo_edicao = st.toggle("🗑️ Habilitar exclusão de lançamentos", value=False,
                            key="ext_edit",
                            help="Ative para mostrar o botão de excluir em cada linha. "
                                 "A exclusão remove o registro da planilha.")

    # Cada item de `linhas` já traz Dt Lcto/R$ Total Pago/Saldo Devedor (usados no
    # cálculo do saldo) além das colunas exibidas abaixo.

    # Colunas exibidas: sem Dt Lcto/R$ Total Pago/Saldo Devedor, e com nomes mais
    # claros — "Descrição" com o máximo de espaço possível.
    COLS_EXIBIR = ["Referente", "Dt Vcto", "R$ Valor Lcto", "R$ Valor Pago",
                   "Saldo Acum.", "Observação"]
    RENOMEAR = {"Referente": "Descrição", "R$ Valor Lcto": "Valor Entrada",
                "R$ Valor Pago": "Valor Recebido"}
    TEXTO = ["Referente", "Dt Vcto"]
    NUMERICAS = ["R$ Valor Lcto", "R$ Valor Pago", "Saldo Acum."]

    if not linhas:
        st.info("Nenhum lançamento para este imóvel.")
    elif not linhas_periodo:
        st.info("Nenhum lançamento no período selecionado.")
    elif not modo_edicao:
        df = pd.DataFrame(linhas_periodo)[COLS_EXIBIR].copy()
        for c in NUMERICAS:
            df[c] = df[c].apply(brl)
        df = df.rename(columns=RENOMEAR)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        larguras = [3.0, 1.0, 1.1, 1.1, 1.2, 1.8, 0.9]
        titulos = [RENOMEAR.get(c, c) for c in COLS_EXIBIR] + ["Ação"]
        h = st.columns(larguras)
        for col, titulo in zip(h, titulos):
            col.markdown(f"<small><b>{titulo}</b></small>", unsafe_allow_html=True)
        st.markdown("<hr style='margin:2px 0 8px 0'>", unsafe_allow_html=True)

        alvo = st.session_state.get("ext_confirm")

        for item in linhas_periodo:
            c = st.columns(larguras)
            for i, campo in enumerate(TEXTO):
                c[i].write(item[campo])
            for i, campo in enumerate(NUMERICAS, start=len(TEXTO)):
                v = item[campo]
                c[i].write(brl(v) if v else "—")
            c[len(TEXTO) + len(NUMERICAS)].write(item["Observação"] or "—")

            linha_planilha = item["__linha"]
            acao = c[len(COLS_EXIBIR)]
            if alvo == linha_planilha:
                b1, b2 = acao.columns(2)
                if b1.button("✅", key=f"ok{linha_planilha}", help="Confirmar exclusão"):
                    excluir_linha("Pagamentos", linha_planilha)
                    st.session_state.pop("ext_confirm", None)
                    st.toast("Lançamento excluído.", icon="🗑️")
                    st.rerun()
                if b2.button("↩️", key=f"no{linha_planilha}", help="Cancelar"):
                    st.session_state.pop("ext_confirm", None)
                    st.rerun()
            else:
                if acao.button("🗑️", key=f"ex{linha_planilha}", help="Excluir lançamento"):
                    st.session_state["ext_confirm"] = linha_planilha
                    st.rerun()

        if alvo is not None:
            st.warning("Clique em ✅ para confirmar a exclusão ou ↩️ para cancelar. "
                       "A ação não pode ser desfeita.")

    st.divider()
    if acum > 0.005:
        st.error(f"Saldo devedor atual: **{brl(acum)}**")
    elif acum < -0.005:
        st.info(f"Crédito a favor: **{brl(-acum)}**")
    else:
        st.success("Conta em dia. ✅")


def pagina_reajustes():
    st.subheader("📈 Reajustes")
    lojas = ler("Lojas", H_LOJAS)
    if lojas.empty:
        st.info("Cadastre um imóvel primeiro.")
        return

    op = {f'{r["Loja"]} — {r["Responsável"]}': r["Loja"] for _, r in lojas.iterrows()}

    with st.form("reaj", clear_on_submit=True):
        sel = st.selectbox("Imóvel", list(op.keys()))
        lr = lojas[lojas["Loja"] == op[sel]].iloc[0]
        c1, c2, c3 = st.columns(3)
        data = c1.date_input("Data", value=dt.date.today(), format="DD/MM/YYYY")
        opcoes_idx = parse_indices(lr.get("Índice Reajuste", "")) or INDICES
        indice = c2.selectbox(
            "Índice", opcoes_idx,
            help="Índices previstos no contrato deste imóvel (menu Imóveis). "
                 "Sem cadastro, mostra todos.")
        perc = c3.number_input("Reajuste (%)", value=0.0, step=0.5, format="%.2f")

        atual = num(lr["Aluguel Atual"])
        novo = round(atual * (1 + perc / 100), 2)
        st.write(f"Atual: **{brl(atual)}** → Novo: **{brl(novo)}**")

        if st.form_submit_button("Aplicar reajuste", type="primary"):
            if perc == 0:
                st.warning("Informe um percentual.")
            else:
                _com_retentativa(lambda: get_client().table("alugueis_reajustes").insert({
                    "loja": int(op[sel]), "data": data.isoformat(), "indice": indice,
                    "percentual": perc, "valor_anterior": atual, "valor_novo": novo,
                }).execute())
                _com_retentativa(lambda: get_client().table("alugueis_lojas")
                                  .update({"aluguel_atual": novo}).eq("loja", int(op[sel]))
                                  .execute())
                invalidar_cache_planilha()
                st.success(f"Reajuste aplicado: {brl(atual)} → {brl(novo)}.")
                st.rerun()

    st.divider()
    st.markdown("**Histórico**")
    dfr = ler("Reajustes", H_REAJ, com_linha=True)
    if dfr.empty:
        st.info("Nenhum reajuste registrado.")
        return

    st.dataframe(dfr.drop(columns=["__linha"]), use_container_width=True, hide_index=True)

    with st.expander("🗑️ Excluir um reajuste do histórico"):
        st.caption("Excluir aqui remove apenas o registro do histórico. O 'Aluguel Atual' "
                   "do imóvel NÃO volta ao valor anterior — ajuste na aba Imóveis se precisar.")
        rot = {f'linha {int(r["__linha"])} · Loja {r["Loja"]} · {r["Data"]} · '
               f'{r["Índice"]} {r["%"]}% · {brl(num(r["Valor Anterior"]))} → '
               f'{brl(num(r["Valor Novo"]))}': int(r["__linha"])
               for _, r in dfr.iterrows()}
        escolha = st.selectbox("Registro", list(rot.keys()), key="rej_del")
        confirmar = st.checkbox("Confirmo a exclusão", key="rej_ok")
        if st.button("Excluir reajuste", type="primary", disabled=not confirmar):
            excluir_linha("Reajustes", rot[escolha])
            st.toast("Reajuste excluído.", icon="🗑️")
            st.rerun()


def pagina_imoveis():
    st.subheader("🏠 Imóveis")
    lojas = ler("Lojas", H_LOJAS)
    if lojas.empty:
        st.info("Nenhum imóvel cadastrado.")
        return

    for _, r in lojas.iterrows():
        with st.expander(f'{r["Loja"]} — {r["Responsável"]}'):
            with st.form(f'e{r["Loja"]}'):
                c1, c2 = st.columns(2)
                resp = c1.text_input("Responsável", value=str(r["Responsável"]))
                aluguel = c2.number_input("Aluguel atual", value=num(r["Aluguel Atual"]),
                                          step=50.0, format="%.2f")

                c3, c4 = st.columns(2)
                dia_vcto = c3.number_input("Dia Vcto", min_value=1, max_value=31,
                                           value=int(num(r["Dia Vcto"]) or 1),
                                           help="Dia do vencimento do aluguel.")
                dia_pgto = c4.number_input("Dia Pgto", min_value=0, max_value=31,
                                           value=int(num(r.get("Dia Pgto", 0)) or 0),
                                           help="Dia em que o inquilino costuma pagar. "
                                                "0 = não informado.")

                c5, c6 = st.columns(2)
                assinatura = c5.text_input("Início do Contrato (dd/mm/aaaa)",
                                           value=str(r.get("Assinatura Contrato", "")))
                venc_contr = c6.text_input("Vencimento do Contrato (dd/mm/aaaa)",
                                           value=str(r.get("Vencimento Contrato", "")))

                c7, c8 = st.columns(2)
                prox_reaj = c7.text_input(
                    "Próximo Reajuste (dd/mm/aaaa)",
                    value=str(r.get("Próximo Reajuste", "")),
                    help="Data prevista do próximo reajuste. Aparece no topo do "
                         "Demonstrativo Mensal.")
                indices_sel = c8.multiselect(
                    "Índice de Reajuste", INDICES,
                    default=parse_indices(r.get("Índice Reajuste", "")),
                    max_selections=2, key=f"idx{r['Loja']}",
                    help="Até 2 índices previstos em contrato. No menu Reajustes "
                         "o cliente escolhe qual dos dois aplicar.")

                c9, c10 = st.columns(2)
                debito = c9.number_input("Débito Geral", value=num(r["Débito Geral"]),
                                         step=50.0, format="%.2f",
                                         help="Positivo = devedor · Negativo = crédito. "
                                              "É o ponto de partida do extrato.")
                calcao = c10.number_input("Caução", min_value=0.0,
                                         value=num(r.get("Caução", 0)),
                                         step=50.0, format="%.2f",
                                         help="Valor do depósito de garantia (caução).")

                obs = st.text_area("Observação", value=str(r.get("Observação", "")))

                if st.form_submit_button("💾 Salvar", type="primary"):
                    _com_retentativa(lambda: get_client().table("alugueis_lojas").update({
                        "responsavel": resp, "aluguel_atual": aluguel,
                        "dia_vcto": dia_vcto, "dia_pgto": dia_pgto,
                        "assinatura_contrato": _parse_data_br(assinatura),
                        "debito_geral": debito, "caucao": calcao,
                        "indice_reajuste": ", ".join(indices_sel),
                        "vencimento_contrato": _parse_data_br(venc_contr),
                        "observacao": obs,
                        "proximo_reajuste": _parse_data_br(prox_reaj),
                    }).eq("loja", int(r["Loja"])).execute())
                    invalidar_cache_planilha()
                    st.success("Atualizado.")
                    st.rerun()

            alvo = st.session_state.get("im_confirm_excluir")
            if alvo == str(r["Loja"]):
                st.warning(f'Excluir **{r["Loja"]} — {r["Responsável"]}**? Os '
                           'lançamentos já registrados para esta loja continuam no '
                           'Extrato, mas o imóvel some do cadastro. Não pode ser desfeito.')
                b1, b2 = st.columns(2)
                if b1.button("✅ Confirmar exclusão", key=f"delok{r['Loja']}",
                             type="primary"):
                    excluir_linha("Lojas", int(r["Loja"]))
                    st.session_state.pop("im_confirm_excluir", None)
                    st.toast("Imóvel excluído.", icon="🗑️")
                    st.rerun()
                if b2.button("↩️ Cancelar", key=f"delno{r['Loja']}"):
                    st.session_state.pop("im_confirm_excluir", None)
                    st.rerun()
            else:
                if st.button("🗑️ Excluir imóvel", key=f"del{r['Loja']}"):
                    st.session_state["im_confirm_excluir"] = str(r["Loja"])
                    st.rerun()

    st.divider()
    with st.expander("➕ Cadastrar novo imóvel"):
        with st.form("novo_imovel", clear_on_submit=True):
            novo_id = int(lojas["Loja"].astype(float).max()) + 1 if not lojas.empty else 1
            st.caption(f"Será cadastrado como Loja **{novo_id}**.")
            resp = st.text_input("Responsável")
            aluguel = st.number_input("Aluguel atual", min_value=0.0, step=50.0,
                                      format="%.2f")
            dia_vcto = st.number_input("Dia Vcto", min_value=1, max_value=31, value=1)
            if st.form_submit_button("💾 Cadastrar", type="primary"):
                _com_retentativa(lambda: get_client().table("alugueis_lojas").insert({
                    "loja": novo_id, "responsavel": resp, "aluguel_atual": aluguel,
                    "dia_vcto": dia_vcto, "dia_pgto": None, "assinatura_contrato": None,
                    "debito_geral": 0, "caucao": 0, "indice_reajuste": "",
                    "vencimento_contrato": None, "observacao": "",
                    "proximo_reajuste": None,
                }).execute())
                invalidar_cache_planilha()
                st.success(f"Loja {novo_id} cadastrada.")
                st.rerun()

    st.divider()
    with st.expander("🧹 Limpeza geral — apagar todos os lançamentos"):
        st.warning(
            "Isso apaga **todos** os lançamentos (entradas, recebidos e histórico do "
            "Extrato) de **todas as lojas**. As lojas cadastradas continuam intactas — "
            "só o histórico de pagamentos é zerado. **Essa ação não pode ser desfeita.**")
        confirmar_txt = st.text_input(
            "Para confirmar, digite LIMPAR (tudo em maiúsculas):",
            key="limpar_pag_confirm")
        if st.button("🗑️ Apagar todos os lançamentos", type="primary",
                     disabled=confirmar_txt != "LIMPAR"):
            limpar_pagamentos()
            st.session_state.pop("limpar_pag_confirm", None)
            st.toast("Todos os lançamentos foram apagados. Lojas mantidas.", icon="🧹")
            st.rerun()


# ----------------------------------------------------------------------------- #
# App
# ----------------------------------------------------------------------------- #
SIDEBAR_CSS = """
<style>
[data-testid="stSidebar"] {
    background-color: #0B2545;
}
[data-testid="stSidebar"] * {
    color: #FFFFFF !important;
}
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p {
    color: #FFFFFF !important;
}
[data-testid="stSidebar"] .stButton>button {
    background-color: #13315C;
    color: #FFFFFF !important;
    border: 1px solid #2C4A7C;
}
[data-testid="stSidebar"] .stButton>button:hover {
    background-color: #1C4278;
    border-color: #E5007D;
}
[data-testid="stSidebar"] hr {
    border-color: rgba(255,255,255,0.25);
}
</style>
"""

# (ícone, rótulo, função da página)
PAGINAS = [
    ("📊", "Painel", pagina_painel),
    ("🧾", "Lançamentos", pagina_lancamentos),
    ("📄", "Extrato", pagina_extrato),
    ("📈", "Reajustes", pagina_reajustes),
    ("🏠", "Imóveis", pagina_imoveis),
]


def main():
    st.set_page_config(page_title="Gestão de Aluguéis", page_icon="🏠", layout="wide")
    st.markdown(SIDEBAR_CSS, unsafe_allow_html=True)
    if not checar_senha():
        return

    st.sidebar.title("🏠 Gestão de Aluguéis")
    rotulos = [f"{icone}  {nome}" for icone, nome, _ in PAGINAS]
    escolha = st.sidebar.radio("Menu", rotulos, label_visibility="collapsed")
    pag = dict(zip(rotulos, [nome for _, nome, _ in PAGINAS]))[escolha]
    funcoes = {nome: fn for _, nome, fn in PAGINAS}

    st.sidebar.divider()
    if st.sidebar.button("🔄 Recarregar dados"):
        st.cache_resource.clear()
        invalidar_cache_planilha()
        st.rerun()
    if st.sidebar.button("🚪 Sair"):
        st.session_state["ok"] = False
        st.rerun()

    try:
        funcoes[pag]()
    except Exception as e:
        st.error(f"Erro ao acessar o banco de dados: {e}")
        st.caption("Verifique se 'supabase_url' e 'supabase_key' (a service_role key) "
                   "estão certos nos secrets do Streamlit, e se as tabelas "
                   "alugueis_lojas / alugueis_pagamentos / alugueis_reajustes existem "
                   "no projeto Supabase.")


if __name__ == "__main__":
    main()
