"""
Gestão de Aluguéis — versão com Google Sheets como banco de dados.

Backend: uma planilha Google com 3 abas (Lojas, Pagamentos, Reajustes).
O app lê/escreve via gspread (conta de serviço). Os cálculos são feitos em
Python, então a planilha guarda apenas os campos que o usuário digita.

Aba Pagamentos (colunas A..H):
    Loja | Dt Lcto | Referente | Valor Lcto | Valor Pago | Multa | Juros | CM

    Total Pago    = Valor Pago + Multa + Juros + CM   (calculado)
    Saldo Devedor = Valor Lcto - Total Pago           (calculado)

Configuração (Streamlit secrets):
    app_password = "sua_senha"
    sheet_key    = "ID_DA_PLANILHA"
    [gcp_service_account]
    type = "service_account"
    ...
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials

# ----------------------------------------------------------------------------- #
# Estrutura das abas
# ----------------------------------------------------------------------------- #
H_LOJAS = ["Loja", "Responsável", "Aluguel Atual", "Dia Vcto", "Início Contrato",
           "Saldo Inicial", "Multa %", "Juros % a.m.", "Próximo Reajuste", "Observação"]

# Aba Pagamentos — colunas A..H (sem Competência)
H_PAG = ["Loja", "Dt Lcto", "Referente", "Valor Lcto",
         "Valor Pago", "Multa", "Juros", "CM"]

H_REAJ = ["Loja", "Data", "Índice", "%", "Valor Anterior", "Valor Novo"]

LOJAS_SEED = [
    [1, "Produtos Naturais — Célia", 1100.00, 25, "", 4019.42, 2, 1, "25/06/2026",
     "CONFIRMAR saldo (4.019,42): verificar se é dívida ou crédito."],
    [2, "Estética Facial — Lorena Dias de Andrade", 1100.00, 25, "", 0, 2, 1, "25/05/2027",
     "Sala relocada em 25/04/2026 (antes: Bruna)."],
    [3, "Barbearia — Douglas Vieira Alves", 980.00, 1, "", 1232.00, 2, 1, "01/08/2026",
     "CONFIRMAR saldo (~980 + 252 multa). Refazer contrato 01/08/2026."],
    [4, "Espetaria/Sorveteria — Rose Aguiar de Souza", 1270.30, 10, "", 0, 2, 1, "10/05/2027",
     "Reajuste INCC 5,86% em 10/05/2026."],
    [5, "Pizzaria KASS — Jair Berbert de Souza", 2126.50, 30, "", 0, 2, 1, "",
     "Pagamentos em dia."],
    [6, "Sala projetada (vaga)", 0, 1, "", 0, 2, 1, "", ""],
    [7, "Apto. Florais — Allan e Amanda", 4000.00, 12, "", 0, 2, 1, "",
     "CONFIRMAR: sem histórico na planilha."],
]

SCOPES = ["https://www.googleapis.com/auth/spreadsheets",
          "https://www.googleapis.com/auth/drive"]


# ----------------------------------------------------------------------------- #
# Conexão com o Google Sheets
# ----------------------------------------------------------------------------- #
@st.cache_resource
def get_spreadsheet():
    creds = Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]), scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(st.secrets["sheet_key"])
    garantir_estrutura(sh)
    return sh


def garantir_estrutura(sh):
    """Cria as abas e cabeçalhos que faltarem; cadastra os 7 imóveis se vazio."""
    titulos = {ws.title: ws for ws in sh.worksheets()}

    def garante(nome, header, ncols):
        if nome in titulos:
            ws = titulos[nome]
        else:
            ws = sh.add_worksheet(title=nome, rows=500, cols=ncols)
        atual = ws.row_values(1)
        if atual[:len(header)] != header:
            ws.update("A1", [header])
        return ws

    ws_lojas = garante("Lojas", H_LOJAS, len(H_LOJAS))
    garante("Pagamentos", H_PAG, len(H_PAG))
    garante("Reajustes", H_REAJ, len(H_REAJ))

    if len(ws_lojas.get_all_values()) <= 1:
        ws_lojas.update("A2", LOJAS_SEED, value_input_option="USER_ENTERED")


def ws(nome):
    return get_spreadsheet().worksheet(nome)


def ler(nome, headers, com_linha: bool = False) -> pd.DataFrame:
    """Lê a aba pegando só as colunas conhecidas (pela 1ª ocorrência do nome).

    Se com_linha=True, devolve também '__linha' com o número REAL da linha na
    planilha (usada para excluir registros com segurança).
    """
    vals = ws(nome).get_all_values()
    if not vals:
        cols = list(headers) + (["__linha"] if com_linha else [])
        return pd.DataFrame(columns=cols)

    head = vals[0]
    width = len(head)

    linhas, numeros = [], []
    for i, r in enumerate(vals[1:], start=2):   # linha 1 = cabeçalho
        linhas.append(list(r)[:width] + [""] * (width - len(r)))
        numeros.append(i)

    base = pd.DataFrame(linhas, columns=[f"__c{i}" for i in range(width)])

    out = {}
    for h in headers:
        out[h] = base[f"__c{head.index(h)}"] if h in head else ""
    df = pd.DataFrame(out, index=base.index)
    df["__linha"] = numeros

    if headers and headers[0] in df.columns and not df.empty:
        df = df[df[headers[0]].astype(str).str.strip() != ""]

    df = df.reset_index(drop=True)
    if not com_linha:
        df = df.drop(columns=["__linha"])
    return df


def excluir_linha(aba: str, linha: int):
    ws(aba).delete_rows(int(linha))


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


def to_date(v):
    if not v:
        return None
    return pd.to_datetime(v, dayfirst=True, errors="coerce")


def total_pago(r) -> float:
    """Total Pago = Valor Pago + Multa + Juros + CM."""
    return num(r["Valor Pago"]) + num(r["Multa"]) + num(r["Juros"]) + num(r["CM"])


# ----------------------------------------------------------------------------- #
# Cálculo de saldo
# ----------------------------------------------------------------------------- #
def saldo_loja(loja_id, lojas: pd.DataFrame, pags: pd.DataFrame) -> float:
    """Saldo Inicial + Σ Valor Lcto − Σ Total Pago. Positivo = devedor."""
    base = 0.0
    linha = lojas[lojas["Loja"].astype(str) == str(loja_id)]
    if not linha.empty:
        base = num(linha.iloc[0]["Saldo Inicial"])
    if pags.empty:
        return base
    p = pags[pags["Loja"].astype(str) == str(loja_id)]
    lancado = sum(num(r["Valor Lcto"]) for _, r in p.iterrows())
    pago = sum(total_pago(r) for _, r in p.iterrows())
    return base + lancado - pago


# ----------------------------------------------------------------------------- #
# Login
# ----------------------------------------------------------------------------- #
def checar_senha() -> bool:
    senha = st.secrets.get("app_password", "1234")
    if st.session_state.get("ok"):
        return True
    st.markdown("### 🔐 Gestão de Aluguéis")
    pwd = st.text_input("Senha", type="password")
    if st.button("Entrar", type="primary"):
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
        d = to_date(r.get("Próximo Reajuste"))
        if d is not None and not pd.isna(d):
            dias = (d.date() - hoje).days
            if dias <= 45:
                avisos.append((dias, r["Responsável"], d.date()))
    if avisos:
        st.markdown("##### 📈 Reajustes a vencer")
        for dias, resp, d in sorted(avisos):
            txt = f"**{resp}** — {d.strftime('%d/%m/%Y')}"
            (st.warning if dias < 0 else st.info)(
                txt + (f" (há {-dias} dias)" if dias < 0 else f" (em {dias} dias)"))


def pagina_lancamentos():
    st.subheader("➕ Lançamentos")
    lojas = ler("Lojas", H_LOJAS)
    if lojas.empty:
        st.info("Cadastre um imóvel primeiro.")
        return

    op = {f'{r["Loja"]} — {r["Responsável"]}': r["Loja"] for _, r in lojas.iterrows()}

    with st.form("novo", clear_on_submit=True):
        st.markdown("**Novo lançamento**")
        sel = st.selectbox("Imóvel", list(op.keys()))
        loja_row = lojas[lojas["Loja"] == op[sel]].iloc[0]

        c1, c2 = st.columns(2)
        data = c1.date_input("Dt Lcto", value=dt.date.today(), format="DD/MM/YYYY")
        referente = c2.text_input("Referente", value="Aluguel",
                                  help="Digite livremente: Aluguel, IPTU, Multa, Acordo...")

        c3, c4 = st.columns(2)
        valor_lcto = c3.number_input("R$ Valor Lcto", min_value=0.0,
                                     value=num(loja_row["Aluguel Atual"]),
                                     step=50.0, format="%.2f",
                                     help="Quanto foi cobrado/lançado.")
        valor_pago = c4.number_input("R$ Valor Pago", min_value=0.0, step=50.0, format="%.2f",
                                     help="Quanto foi pago referente a este lançamento.")

        c5, c6, c7 = st.columns(3)
        multa = c5.number_input("R$ Multa", min_value=0.0, step=10.0, format="%.2f")
        juros = c6.number_input("R$ Juros", min_value=0.0, step=10.0, format="%.2f")
        cm = c7.number_input("R$ CM (correção monetária)", min_value=0.0,
                             step=10.0, format="%.2f")

        st.caption("R$ Total Pago = Valor Pago + Multa + Juros + CM  ·  "
                   "Saldo Devedor = Valor Lcto − Total Pago")

        if st.form_submit_button("Lançar", type="primary"):
            ws("Pagamentos").append_row(
                [op[sel], data.strftime("%d/%m/%Y"), referente,
                 valor_lcto, valor_pago, multa, juros, cm],
                value_input_option="USER_ENTERED")
            st.success("Lançamento salvo na planilha.")
            st.rerun()

    st.divider()
    with st.expander("🧮 Calculadora de multa / juros por atraso (opcional)"):
        st.caption("Calcule o valor e depois digite no lançamento. "
                   "Os % vêm do cadastro do imóvel.")
        selm = st.selectbox("Imóvel", list(op.keys()), key="mj")
        lr = lojas[lojas["Loja"] == op[selm]].iloc[0]
        m1, m2, m3 = st.columns(3)
        base = m1.number_input("Valor base", min_value=0.0, value=num(lr["Aluguel Atual"]),
                               step=50.0, format="%.2f", key="mjb")
        venc = m2.date_input("Vencimento", value=dt.date.today().replace(day=1),
                             format="DD/MM/YYYY", key="mjv")
        pgto = m3.date_input("Pagamento", value=dt.date.today(),
                             format="DD/MM/YYYY", key="mjp")
        m4, m5 = st.columns(2)
        mpct = m4.number_input("Multa %", min_value=0.0, value=num(lr["Multa %"]) or 2.0,
                               step=0.5, format="%.2f", key="mjm")
        jpct = m5.number_input("Juros % a.m.", min_value=0.0,
                               value=num(lr["Juros % a.m."]) or 1.0,
                               step=0.5, format="%.2f", key="mjj")
        dias = max((pgto - venc).days, 0)
        mv = round(base * mpct / 100, 2)
        jv = round(base * jpct / 100 * dias / 30, 2)
        if dias <= 0:
            st.info("Sem atraso.")
        else:
            st.write(f"Atraso **{dias} dia(s)** · Multa {brl(mv)} · Juros {brl(jv)} · "
                     f"**Total {brl(mv + jv)}**")

    st.divider()
    st.markdown("**Últimos lançamentos**")
    pags = ler("Pagamentos", H_PAG, com_linha=True)
    if pags.empty:
        st.info("Sem lançamentos ainda.")
        return

    nomes = {r["Loja"]: r["Responsável"] for _, r in lojas.iterrows()}
    for _, r in pags.tail(15).iloc[::-1].iterrows():
        linha_planilha = int(r["__linha"])
        c1, c2 = st.columns([6, 1])
        c1.write(f'**{r.get("Dt Lcto","")}** · {nomes.get(r["Loja"], r["Loja"])} · '
                 f'{r.get("Referente","") or "—"} · lançado {brl(num(r["Valor Lcto"]))} · '
                 f'pago {brl(total_pago(r))}')
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

    saldo_inicial = num(lr["Saldo Inicial"])
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
                "R$ Valor Lcto": lcto,
                "R$ Valor Pago": num(r["Valor Pago"]),
                "Multa": num(r["Multa"]),
                "Juros": num(r["Juros"]),
                "CM": num(r["CM"]),
                "R$ Total Pago": tot,
                "Saldo Devedor": lcto - tot,
                "Saldo Acum.": acum,
                "__linha": int(r["__linha"]),
            })

    if saldo_inicial:
        st.caption(f"Saldo inicial (cadastro do imóvel): **{brl(saldo_inicial)}**")

    modo_edicao = st.toggle("🗑️ Habilitar exclusão de lançamentos", value=False,
                            key="ext_edit",
                            help="Ative para mostrar o botão de excluir em cada linha. "
                                 "A exclusão remove o registro da planilha.")

    COLS = ["Dt Lcto", "Referente", "R$ Valor Lcto", "R$ Valor Pago", "Multa",
            "Juros", "CM", "R$ Total Pago", "Saldo Devedor", "Saldo Acum."]
    NUMERICAS = COLS[2:]

    if not linhas:
        st.info("Nenhum lançamento para este imóvel.")
    elif not modo_edicao:
        df = pd.DataFrame(linhas)[COLS].copy()
        for c in NUMERICAS:
            df[c] = df[c].apply(brl)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        larguras = [1.0, 1.5, 1.1, 1.1, 0.9, 0.9, 0.9, 1.1, 1.1, 1.1, 0.9]
        h = st.columns(larguras)
        for col, titulo in zip(h, COLS + ["Ação"]):
            col.markdown(f"<small><b>{titulo}</b></small>", unsafe_allow_html=True)
        st.markdown("<hr style='margin:2px 0 8px 0'>", unsafe_allow_html=True)

        alvo = st.session_state.get("ext_confirm")

        for item in linhas:
            c = st.columns(larguras)
            c[0].write(item["Dt Lcto"])
            c[1].write(item["Referente"])
            for i, campo in enumerate(NUMERICAS, start=2):
                v = item[campo]
                c[i].write(brl(v) if v else "—")

            linha_planilha = item["__linha"]
            if alvo == linha_planilha:
                b1, b2 = c[10].columns(2)
                if b1.button("✅", key=f"ok{linha_planilha}", help="Confirmar exclusão"):
                    excluir_linha("Pagamentos", linha_planilha)
                    st.session_state.pop("ext_confirm", None)
                    st.toast("Lançamento excluído.", icon="🗑️")
                    st.rerun()
                if b2.button("↩️", key=f"no{linha_planilha}", help="Cancelar"):
                    st.session_state.pop("ext_confirm", None)
                    st.rerun()
            else:
                if c[10].button("🗑️", key=f"ex{linha_planilha}", help="Excluir lançamento"):
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
        indice = c2.selectbox("Índice", ["IGP-M", "IPCA", "INCC", "IGP-DI", "Outro"])
        perc = c3.number_input("Reajuste (%)", value=0.0, step=0.5, format="%.2f")

        atual = num(lr["Aluguel Atual"])
        novo = round(atual * (1 + perc / 100), 2)
        st.write(f"Atual: **{brl(atual)}** → Novo: **{brl(novo)}**")

        if st.form_submit_button("Aplicar reajuste", type="primary"):
            if perc == 0:
                st.warning("Informe um percentual.")
            else:
                ws("Reajustes").append_row(
                    [op[sel], data.strftime("%d/%m/%Y"), indice, perc, atual, novo],
                    value_input_option="USER_ENTERED")
                wl = ws("Lojas")
                cell = wl.find(str(op[sel]), in_column=1)
                wl.update_cell(cell.row, 3, novo)
                wl.update_cell(cell.row, 9,
                               (data + dt.timedelta(days=365)).strftime("%d/%m/%Y"))
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
                c3, c4, c5 = st.columns(3)
                dia = c3.number_input("Dia vcto", min_value=1, max_value=31,
                                      value=int(num(r["Dia Vcto"]) or 1))
                sini = c4.number_input("Saldo inicial", value=num(r["Saldo Inicial"]),
                                       step=50.0, format="%.2f")
                prox = c5.text_input("Próximo reajuste (dd/mm/aaaa)",
                                     value=str(r.get("Próximo Reajuste", "")))
                c6, c7 = st.columns(2)
                mpct = c6.number_input("Multa %", value=num(r["Multa %"]) or 2.0, step=0.5)
                jpct = c7.number_input("Juros % a.m.", value=num(r["Juros % a.m."]) or 1.0,
                                       step=0.5)
                obs = st.text_area("Observação", value=str(r.get("Observação", "")))

                if st.form_submit_button("💾 Salvar", type="primary"):
                    wl = ws("Lojas")
                    cell = wl.find(str(r["Loja"]), in_column=1)
                    wl.update(f"B{cell.row}:J{cell.row}",
                              [[resp, aluguel, dia, str(r.get("Início Contrato", "")),
                                sini, mpct, jpct, prox, obs]],
                              value_input_option="USER_ENTERED")
                    st.success("Atualizado.")
                    st.rerun()


# ----------------------------------------------------------------------------- #
# App
# ----------------------------------------------------------------------------- #
def main():
    st.set_page_config(page_title="Gestão de Aluguéis", page_icon="🏠", layout="wide")
    if not checar_senha():
        return

    st.sidebar.title("🏠 Gestão de Aluguéis")
    pag = st.sidebar.radio("Menu", ["Painel", "Lançamentos", "Extrato",
                                    "Reajustes", "Imóveis"])
    st.sidebar.divider()
    if st.sidebar.button("🔄 Recarregar dados"):
        st.cache_resource.clear()
        st.rerun()
    if st.sidebar.button("Sair"):
        st.session_state["ok"] = False
        st.rerun()

    try:
        {"Painel": pagina_painel, "Lançamentos": pagina_lancamentos,
         "Extrato": pagina_extrato, "Reajustes": pagina_reajustes,
         "Imóveis": pagina_imoveis}[pag]()
    except Exception as e:
        st.error(f"Erro ao acessar a planilha: {e}")
        st.caption("Verifique se a planilha foi compartilhada com o e-mail da conta "
                   "de serviço (client_email) e se o sheet_key está correto.")


if __name__ == "__main__":
    main()
