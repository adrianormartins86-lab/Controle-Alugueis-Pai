"""
Gestão de Aluguéis — versão com Google Sheets como banco de dados.

Backend: uma planilha Google com 3 abas (Lojas, Pagamentos, Reajustes).
O app lê/escreve via gspread (conta de serviço). Os cálculos são feitos em
Python, então a planilha guarda apenas os campos que o usuário digita.

Aba Pagamentos (colunas A..J):
    Loja | Dt Lcto | Referente | Dt Vcto | Valor Lcto | Valor Pago | Multa | Juros | CM
    | Observação

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
# Aba Lojas — colunas A..L
H_LOJAS = ["Loja", "Responsável", "Aluguel Atual", "Dia Vcto", "Dia Pgto",
           "Assinatura Contrato", "Débito Geral", "Caução", "Índice Reajuste",
           "Vencimento Contrato", "Observação", "Próximo Reajuste"]
# "Próximo Reajuste" foi acrescentada no FIM de propósito: assim as colunas já
# existentes na planilha do cliente (A..K) não mudam de posição/sentido.

INDICES = ["IGP-M", "IGP-DI", "IPCA", "INCC-DI"]

MESES_PT = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho",
            "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]

# Aba Pagamentos — colunas A..J
H_PAG = ["Loja", "Dt Lcto", "Referente", "Dt Vcto", "Valor Lcto",
         "Valor Pago", "Multa", "Juros", "CM", "Observação"]

H_REAJ = ["Loja", "Data", "Índice", "%", "Valor Anterior", "Valor Novo"]

LOJAS_SEED = [
    [1, "Produtos Naturais -- Wilson Oliveira", 1100.00, 25, "", "", -4019.42, 0, "IGP-M",
     "25/06/2026", "Saldo devedor: (-4.019,42) em 30/06/2026.", ""],
    [2, "Estética Facial — Lorena Dias de Andrade", 1100.00, 25, "", "", 0, 0, "IGP-M",
     "25/05/2027", "Sala relocada em 25/04/2026 (antes: Bruna).", ""],
    [3, "Barbearia — Douglas Vieira Alves", 980.00, 1, "", "", 1232.00, 0, "IGP-M",
     "01/08/2026", "Refazer contrato 01/08/2026.", ""],
    [4, "D2 - Espetaria - Everton Argos Leão", 1270.30, 10, "", "", 0, 0, "INCC-DI",
     "10/05/2027", "Reajuste INCC 5,86% em 10/05/2026.", ""],
    [5, "Pizzaria KASS — Jair Berbert de Souza", 2126.50, 30, "", "", 0, 0, "IGP-M",
     "", "Pagamentos em dia.", ""],
    [6, "Sala projetada (vaga)", 0, 1, "", "", 0, 0, "", "", "", ""],
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
    """Cria as abas e cabeçalhos que faltarem; cadastra os 6 imóveis se vazio."""
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
    """Total Pago = Valor Pago + Multa + Juros + CM."""
    return num(r["Valor Pago"]) + num(r["Multa"]) + num(r["Juros"]) + num(r["CM"])


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


def pagina_lancamentos():
    st.subheader("🧾 Lançamentos — Demonstrativo Mensal")
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
               "multa, acordo...) e o valor. Adicione ou apague linhas como numa planilha.")
    entradas_ini = pd.DataFrame([{"Descrição": "Aluguel", "Valor": num(loja_row["Aluguel Atual"])}])
    entradas_df = st.data_editor(
        entradas_ini, num_rows="dynamic", use_container_width=True, hide_index=True,
        key=f"ent_{loja_id}_{ano_ref}_{mes_ref}_{reset_key}",
        column_config={
            "Descrição": st.column_config.TextColumn("Descrição", width="large"),
            "Valor": st.column_config.NumberColumn("R$ Valor", format="%.2f",
                                                    min_value=0.0, step=50.0),
        })
    obs_entradas = st.text_area("OBS (entradas)", height=60,
                                key=f"obsent_{loja_id}_{ano_ref}_{mes_ref}_{reset_key}")
    tot_entradas = float(pd.to_numeric(entradas_df.get("Valor"), errors="coerce")
                         .fillna(0).sum()) if not entradas_df.empty else 0.0
    st.markdown(f"**Total das Entradas: {brl(tot_entradas)}**")

    st.divider()
    st.markdown("**Recebido no mês** · valor total ou parcial")
    recebido_ini = pd.DataFrame(columns=["Descrição", "Valor"]).astype({"Valor": "float64"})
    recebido_df = st.data_editor(
        recebido_ini, num_rows="dynamic", use_container_width=True, hide_index=True,
        key=f"rec_{loja_id}_{ano_ref}_{mes_ref}_{reset_key}",
        column_config={
            "Descrição": st.column_config.TextColumn("Descrição", width="large"),
            "Valor": st.column_config.NumberColumn("R$ Valor", format="%.2f",
                                                    min_value=0.0, step=50.0),
        })
    obs_recebido = st.text_area("OBS (recebido)", height=60,
                                key=f"obsrec_{loja_id}_{ano_ref}_{mes_ref}_{reset_key}")
    tot_recebido = float(pd.to_numeric(recebido_df.get("Valor"), errors="coerce")
                         .fillna(0).sum()) if not recebido_df.empty else 0.0
    st.markdown(f"**Total recebido: {brl(tot_recebido)}**")

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

    if st.button("💾 Salvar Demonstrativo do Mês", type="primary"):
        dt_lcto = hoje if (ano_ref, mes_ref) == (hoje.year, hoje.month) else ini_mes
        linhas = []
        primeira = True
        for _, row in entradas_df.iterrows():
            desc = str(row.get("Descrição") or "").strip()
            valor = num(row.get("Valor"))
            if not desc or valor == 0:
                continue
            vcto = vcto_aluguel.strftime("%d/%m/%Y") if desc.strip().lower() == "aluguel" else ""
            linhas.append([loja_id, dt_lcto.strftime("%d/%m/%Y"), desc, vcto,
                           valor, 0, 0, 0, 0, obs_entradas if primeira else ""])
            primeira = False
        primeira = True
        for _, row in recebido_df.iterrows():
            desc = str(row.get("Descrição") or "").strip()
            valor = num(row.get("Valor"))
            if not desc or valor == 0:
                continue
            linhas.append([loja_id, dt_lcto.strftime("%d/%m/%Y"), desc, "",
                           0, valor, 0, 0, 0, obs_recebido if primeira else ""])
            primeira = False
        if obs_finais.strip():
            linhas.append([loja_id, dt_lcto.strftime("%d/%m/%Y"), f"OBS {escolha}",
                           "", 0, 0, 0, 0, 0, obs_finais.strip()])

        if not linhas:
            st.warning("Nada para salvar — preencha ao menos uma linha com descrição e valor.")
        else:
            ws("Pagamentos").append_rows(linhas, value_input_option="USER_ENTERED")
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
                "Multa": num(r["Multa"]),
                "Juros": num(r["Juros"]),
                "CM": num(r["CM"]),
                "R$ Total Pago": tot,
                "Saldo Devedor": lcto - tot,
                "Saldo Acum.": acum,
                "Observação": r.get("Observação", "") or "",
                "__linha": int(r["__linha"]),
            })

    if saldo_inicial:
        st.caption(f"Débito geral (cadastro do imóvel): **{brl(saldo_inicial)}**")

    modo_edicao = st.toggle("🗑️ Habilitar exclusão de lançamentos", value=False,
                            key="ext_edit",
                            help="Ative para mostrar o botão de excluir em cada linha. "
                                 "A exclusão remove o registro da planilha.")

    COLS = ["Dt Lcto", "Referente", "Dt Vcto", "R$ Valor Lcto", "R$ Valor Pago",
            "Multa", "Juros", "CM", "R$ Total Pago", "Saldo Devedor", "Saldo Acum.",
            "Observação"]
    TEXTO = COLS[:3]          # Dt Lcto, Referente, Dt Vcto
    NUMERICAS = COLS[3:11]    # todas em R$
    FINAL_TEXTO = ["Observação"]

    if not linhas:
        st.info("Nenhum lançamento para este imóvel.")
    elif not modo_edicao:
        df = pd.DataFrame(linhas)[COLS].copy()
        for c in NUMERICAS:
            df[c] = df[c].apply(brl)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        larguras = [1.0, 1.3, 1.0, 1.0, 1.0, 0.8, 0.8, 0.8, 1.0, 1.0, 1.0, 1.3, 0.9]
        h = st.columns(larguras)
        for col, titulo in zip(h, COLS + ["Ação"]):
            col.markdown(f"<small><b>{titulo}</b></small>", unsafe_allow_html=True)
        st.markdown("<hr style='margin:2px 0 8px 0'>", unsafe_allow_html=True)

        alvo = st.session_state.get("ext_confirm")

        for item in linhas:
            c = st.columns(larguras)
            for i, campo in enumerate(TEXTO):
                c[i].write(item[campo])
            for i, campo in enumerate(NUMERICAS, start=len(TEXTO)):
                v = item[campo]
                c[i].write(brl(v) if v else "—")
            c[len(TEXTO) + len(NUMERICAS)].write(item["Observação"] or "—")

            linha_planilha = item["__linha"]
            acao = c[len(COLS)]
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
                ws("Reajustes").append_row(
                    [op[sel], data.strftime("%d/%m/%Y"), indice, perc, atual, novo],
                    value_input_option="USER_ENTERED")
                wl = ws("Lojas")
                cell = wl.find(str(op[sel]), in_column=1)
                wl.update_cell(cell.row, 3, novo)   # coluna C = Aluguel Atual
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
                    wl = ws("Lojas")
                    cell = wl.find(str(r["Loja"]), in_column=1)
                    # B..L = Responsável, Aluguel Atual, Dia Vcto, Dia Pgto,
                    #        Assinatura Contrato, Débito Geral, Caução, Índice Reajuste,
                    #        Vencimento Contrato, Observação, Próximo Reajuste
                    wl.update(f"B{cell.row}:L{cell.row}",
                              [[resp, aluguel, dia_vcto, dia_pgto, assinatura,
                                debito, calcao, ", ".join(indices_sel),
                                venc_contr, obs, prox_reaj]],
                              value_input_option="USER_ENTERED")
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
                    wl = ws("Lojas")
                    cell = wl.find(str(r["Loja"]), in_column=1)
                    excluir_linha("Lojas", cell.row)
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
                ws("Lojas").append_row(
                    [novo_id, resp, aluguel, dia_vcto, "", "", 0, 0, "", "", "", ""],
                    value_input_option="USER_ENTERED")
                st.success(f"Loja {novo_id} cadastrada.")
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
        st.rerun()
    if st.sidebar.button("🚪 Sair"):
        st.session_state["ok"] = False
        st.rerun()

    try:
        funcoes[pag]()
    except Exception as e:
        st.error(f"Erro ao acessar a planilha: {e}")
        st.caption("Verifique se a planilha foi compartilhada com o e-mail da conta "
                   "de serviço (client_email) e se o sheet_key está correto.")


if __name__ == "__main__":
    main()
