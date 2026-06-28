"""
Gestão de Aluguéis — controle de recebimentos de imóveis locados.

Stack: Streamlit + SQLAlchemy.
- Roda localmente com SQLite (zero configuração).
- Em produção (Streamlit Cloud), aponte para o Supabase/Postgres via st.secrets["db_url"].

Modelo de saldo:
    saldo do imóvel = saldo_inicial + Σ(cobranças) − Σ(recebimentos)
    saldo > 0  => inquilino DEVE esse valor.
    saldo < 0  => inquilino pagou a MAIOR (crédito a favor dele).
"""

from __future__ import annotations
import datetime as dt
from contextlib import contextmanager

import pandas as pd
import streamlit as st
from sqlalchemy import (create_engine, text, Column, Integer, String, Float,
                        Boolean, Date, DateTime, ForeignKey)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

# ----------------------------------------------------------------------------- #
# Configuração de tipos de lançamento
# ----------------------------------------------------------------------------- #
# Cada tipo tem uma "natureza": débito (cobrança) ou crédito (recebimento).
TIPOS = {
    "Aluguel do mês":          "debito",
    "IPTU / Taxa de Lixo":     "debito",
    "Multa / Juros":           "debito",
    "Outras cobranças":        "debito",
    "Pagamento recebido":      "credito",
    "Pagamento em produtos":   "credito",
    "Desconto / Abatimento":   "credito",
}
TIPOS_DEBITO = [t for t, n in TIPOS.items() if n == "debito"]
TIPOS_CREDITO = [t for t, n in TIPOS.items() if n == "credito"]

# ----------------------------------------------------------------------------- #
# Banco de dados (SQLAlchemy)
# ----------------------------------------------------------------------------- #
Base = declarative_base()


class Imovel(Base):
    __tablename__ = "imoveis"
    id = Column(Integer, primary_key=True)
    nome = Column(String, nullable=False)
    locatario = Column(String, default="")
    telefone = Column(String, default="")
    endereco = Column(String, default="")
    valor_aluguel = Column(Float, default=0.0)
    dia_vencimento = Column(Integer, default=1)
    saldo_inicial = Column(Float, default=0.0)   # pendência herdada do controle antigo
    multa_pct = Column(Float, default=2.0)       # % de multa por atraso (contrato)
    juros_pct_mes = Column(Float, default=1.0)   # % de juros ao mês por atraso
    ativo = Column(Boolean, default=True)
    observacao = Column(String, default="")
    lancamentos = relationship("Lancamento", back_populates="imovel",
                               cascade="all, delete-orphan")


class Lancamento(Base):
    __tablename__ = "lancamentos"
    id = Column(Integer, primary_key=True)
    imovel_id = Column(Integer, ForeignKey("imoveis.id"), nullable=False)
    data = Column(Date, nullable=False)
    competencia = Column(String, default="")   # "AAAA-MM" do aluguel de referência
    tipo = Column(String, nullable=False)
    valor = Column(Float, nullable=False)
    descricao = Column(String, default="")
    criado_em = Column(DateTime, default=dt.datetime.now)
    imovel = relationship("Imovel", back_populates="lancamentos")


@st.cache_resource
def get_engine():
    """SQLite local por padrão; Postgres/Supabase se houver st.secrets['db_url']."""
    url = None
    try:
        url = st.secrets.get("db_url")
    except Exception:
        url = None
    if not url:
        url = "sqlite:///alugueis.db"
    engine = create_engine(url, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    return engine


SessionLocal = None


@contextmanager
def get_session():
    global SessionLocal
    if SessionLocal is None:
        SessionLocal = sessionmaker(bind=get_engine())
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


# ----------------------------------------------------------------------------- #
# Funções de domínio
# ----------------------------------------------------------------------------- #
def brl(v: float) -> str:
    v = v or 0.0
    return ("R$ " + f"{v:,.2f}").replace(",", "X").replace(".", ",").replace("X", ".")


def listar_imoveis(somente_ativos=False) -> pd.DataFrame:
    with get_session() as s:
        q = s.query(Imovel)
        if somente_ativos:
            q = q.filter(Imovel.ativo == True)  # noqa: E712
        rows = [{
            "id": i.id, "nome": i.nome, "locatario": i.locatario,
            "telefone": i.telefone, "endereco": i.endereco,
            "valor_aluguel": i.valor_aluguel, "dia_vencimento": i.dia_vencimento,
            "saldo_inicial": i.saldo_inicial, "ativo": i.ativo,
            "multa_pct": i.multa_pct, "juros_pct_mes": i.juros_pct_mes,
            "observacao": i.observacao,
        } for i in q.order_by(Imovel.nome).all()]
    return pd.DataFrame(rows)


def listar_lancamentos(imovel_id=None) -> pd.DataFrame:
    with get_session() as s:
        q = s.query(Lancamento)
        if imovel_id:
            q = q.filter(Lancamento.imovel_id == imovel_id)
        rows = [{
            "id": l.id, "imovel_id": l.imovel_id, "data": l.data,
            "competencia": l.competencia, "tipo": l.tipo, "valor": l.valor,
            "natureza": TIPOS.get(l.tipo, "debito"), "descricao": l.descricao,
        } for l in q.order_by(Lancamento.data, Lancamento.id).all()]
    df = pd.DataFrame(rows)
    if not df.empty:
        df["data"] = pd.to_datetime(df["data"])
    return df


def saldo_imovel(imovel_id: int) -> float:
    df = listar_lancamentos(imovel_id)
    with get_session() as s:
        imv = s.get(Imovel, imovel_id)
        base = imv.saldo_inicial if imv else 0.0
    if df.empty:
        return base
    deb = df.loc[df["natureza"] == "debito", "valor"].sum()
    cre = df.loc[df["natureza"] == "credito", "valor"].sum()
    return base + deb - cre


def calcular_multa_juros(valor_base, dias_atraso, multa_pct, juros_pct_mes):
    """Multa fixa (%) + juros pró-rata por dia de atraso. Retorna (multa, juros, total)."""
    if dias_atraso <= 0 or valor_base <= 0:
        return 0.0, 0.0, 0.0
    multa = valor_base * (multa_pct / 100.0)
    juros = valor_base * (juros_pct_mes / 100.0) * (dias_atraso / 30.0)
    return round(multa, 2), round(juros, 2), round(multa + juros, 2)


def add_imovel(**kw):
    with get_session() as s:
        s.add(Imovel(**kw))


def update_imovel(imovel_id, **kw):
    with get_session() as s:
        imv = s.get(Imovel, imovel_id)
        for k, v in kw.items():
            setattr(imv, k, v)


def del_imovel(imovel_id):
    with get_session() as s:
        s.delete(s.get(Imovel, imovel_id))


def add_lancamento(**kw):
    with get_session() as s:
        s.add(Lancamento(**kw))


def del_lancamento(lanc_id):
    with get_session() as s:
        s.delete(s.get(Lancamento, lanc_id))


def seed_se_vazio():
    """Cadastra os imóveis com os valores atuais lidos do controle original.

    Os saldos abaixo foram extraídos da planilha em 28/06/2026. Os marcados com
    'CONFIRMAR' vieram de uma leitura de células ambíguas — confira com seu pai
    e ajuste na aba Imóveis (o campo de saldo é editável)."""
    if not listar_imoveis().empty:
        return
    # nome, locatário, aluguel, dia_venc, saldo_inicial, observação
    base = [
        ("Loja 1 — Produtos Naturais", "Célia", 1100.00, 25, 4019.42,
         "CONFIRMAR saldo: planilha mostra 4.019,42 — verificar se é dívida ou crédito."),
        ("Loja 2 — Estética Facial", "Lorena Dias de Andrade", 1100.00, 25, 0.0,
         "Sala relocada em 25/04/2026 (antes: Bruna). Pagou 2 aluguéis de calção."),
        ("Loja 3 — Barbearia", "Douglas Vieira Alves", 980.00, 1, 1232.00,
         "CONFIRMAR saldo: ~R$ 980 (jun/26) + R$ 252 multa/juros. Refazer contrato 01/08/2026."),
        ("Loja 4 — Espetaria / Sorveteria", "Rose Aguiar de Souza", 1270.30, 10, 0.0,
         "Aluguel reajustado (INCC) em 10/05/2026. Pagamentos antecipados."),
        ("Loja 5 — Pizzaria KASS", "Jair Berbert de Souza", 2126.50, 30, 0.0,
         "Pagamentos em dia."),
        ("Loja 6 — Sala projetada (vaga)", "", 0.00, 1, 0.0, ""),
        ("Apto. Florais", "Allan e Amanda", 4000.00, 12, 0.0,
         "CONFIRMAR: sem histórico de pagamentos na planilha. IPTU anual por conta do locatário."),
    ]
    for nome, loc, val, dia, sini, obs in base:
        add_imovel(nome=nome, locatario=loc, valor_aluguel=val,
                   dia_vencimento=dia, ativo=bool(loc), saldo_inicial=sini,
                   observacao=obs)


# ----------------------------------------------------------------------------- #
# Autenticação simples (proteção da URL pública)
# ----------------------------------------------------------------------------- #
def checar_senha() -> bool:
    try:
        senha_correta = st.secrets.get("app_password", "1234")
    except Exception:
        senha_correta = "1234"
    if st.session_state.get("autenticado"):
        return True
    st.markdown("### 🔐 Gestão de Aluguéis")
    pwd = st.text_input("Senha de acesso", type="password")
    if st.button("Entrar", type="primary"):
        if pwd == senha_correta:
            st.session_state["autenticado"] = True
            st.rerun()
        else:
            st.error("Senha incorreta.")
    st.caption("Senha padrão: 1234 — troque em .streamlit/secrets.toml (app_password).")
    return False


# ----------------------------------------------------------------------------- #
# Páginas
# ----------------------------------------------------------------------------- #
def pagina_dashboard():
    st.subheader("📊 Visão geral")
    imoveis = listar_imoveis()
    if imoveis.empty:
        st.info("Cadastre seus imóveis na aba **Imóveis**.")
        return

    hoje = dt.date.today()
    mes = st.selectbox(
        "Mês de referência",
        options=[(hoje.replace(day=1) - pd.DateOffset(months=k)).strftime("%Y-%m")
                 for k in range(0, 12)],
        index=0,
    )
    lanc = listar_lancamentos()

    # Recebido no mês selecionado (créditos)
    recebido_mes = 0.0
    if not lanc.empty:
        m = lanc[(lanc["data"].dt.strftime("%Y-%m") == mes) &
                 (lanc["natureza"] == "credito")]
        recebido_mes = float(m["valor"].sum())

    # Saldos por imóvel
    saldos = {int(r.id): saldo_imovel(int(r.id)) for r in imoveis.itertuples()}
    total_pendente = sum(v for v in saldos.values() if v > 0)
    total_credito = -sum(v for v in saldos.values() if v < 0)
    aluguel_esperado = float(imoveis.loc[imoveis["ativo"], "valor_aluguel"].sum())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Recebido no mês", brl(recebido_mes))
    c2.metric("Aluguel esperado / mês", brl(aluguel_esperado))
    c3.metric("Total pendente", brl(total_pendente))
    c4.metric("Créditos a favor", brl(total_credito))

    st.divider()
    st.markdown("##### Situação por imóvel")
    tabela = []
    for r in imoveis.itertuples():
        s = saldos[int(r.id)]
        if s > 0.005:
            sit = "🔴 Em aberto"
        elif s < -0.005:
            sit = "🔵 Crédito"
        else:
            sit = "🟢 Em dia"
        tabela.append({
            "Imóvel": r.nome, "Inquilino": r.locatario or "—",
            "Aluguel": brl(r.valor_aluguel), "Venc. (dia)": r.dia_vencimento,
            "Saldo": brl(s), "Situação": sit, "Ativo": "Sim" if r.ativo else "Não",
        })
    st.dataframe(pd.DataFrame(tabela), use_container_width=True, hide_index=True)

    # Recebimentos por mês (últimos 12)
    if not lanc.empty:
        cred = lanc[lanc["natureza"] == "credito"].copy()
        if not cred.empty:
            cred["mes"] = cred["data"].dt.strftime("%Y-%m")
            serie = cred.groupby("mes")["valor"].sum().tail(12)
            st.markdown("##### Recebimentos por mês")
            st.bar_chart(serie)


def pagina_lancamentos():
    st.subheader("➕ Lançamentos")
    imoveis = listar_imoveis()
    if imoveis.empty:
        st.info("Cadastre um imóvel primeiro.")
        return

    op = {f"{r.nome}" + (f" — {r.locatario}" if r.locatario else ""): int(r.id)
          for r in imoveis.itertuples()}

    with st.form("novo_lanc", clear_on_submit=True):
        st.markdown("**Novo lançamento**")
        col1, col2 = st.columns(2)
        nome_sel = col1.selectbox("Imóvel", list(op.keys()))
        tipo = col2.selectbox("Tipo", list(TIPOS.keys()))
        col3, col4, col5 = st.columns(3)
        data = col3.date_input("Data", value=dt.date.today(), format="DD/MM/YYYY")
        valor = col4.number_input("Valor (R$)", min_value=0.0, step=50.0, format="%.2f")
        comp = col5.text_input("Competência (mês do aluguel)",
                               value=dt.date.today().strftime("%Y-%m"),
                               help="Mês a que o lançamento se refere, ex: 2026-06")
        desc = st.text_input("Descrição / observação", placeholder="ex.: Recebido via PIX")
        natureza = TIPOS[tipo]
        st.caption(f"Natureza: **{'COBRANÇA (+)' if natureza=='debito' else 'RECEBIMENTO (−)'}**"
                   " — cobranças aumentam a dívida; recebimentos abatem.")
        if st.form_submit_button("Lançar", type="primary"):
            if valor <= 0:
                st.error("Informe um valor maior que zero.")
            else:
                add_lancamento(imovel_id=op[nome_sel], data=data, competencia=comp,
                               tipo=tipo, valor=valor, descricao=desc)
                st.success(f"Lançado: {tipo} de {brl(valor)} em {nome_sel}.")
                st.rerun()

    st.divider()
    with st.expander("🧮 Multa / juros por atraso (opcional)"):
        st.caption("Calcule a multa e os juros — e escolha **cobrar ou perdoar**. "
                   "Os percentuais vêm do contrato de cada imóvel (editáveis na aba Imóveis).")
        nome_mj = st.selectbox("Imóvel", list(op.keys()), key="mj_imovel")
        row_mj = imoveis[imoveis["id"] == op[nome_mj]].iloc[0]
        cobrar = st.checkbox("Cobrar multa/juros (desmarque para perdoar)", value=True)
        m1, m2, m3 = st.columns(3)
        base = m1.number_input("Valor base (R$)", min_value=0.0,
                               value=float(row_mj["valor_aluguel"]), step=50.0,
                               format="%.2f", key="mj_base")
        venc = m2.date_input("Vencimento", value=dt.date.today().replace(day=1),
                             format="DD/MM/YYYY", key="mj_venc")
        pgto = m3.date_input("Data do pagamento", value=dt.date.today(),
                             format="DD/MM/YYYY", key="mj_pgto")
        m4, m5 = st.columns(2)
        multa_pct = m4.number_input("Multa (%)", min_value=0.0,
                                    value=float(row_mj.get("multa_pct") or 2.0),
                                    step=0.5, format="%.2f", key="mj_multa")
        juros_pct = m5.number_input("Juros ao mês (%)", min_value=0.0,
                                    value=float(row_mj.get("juros_pct_mes") or 1.0),
                                    step=0.5, format="%.2f", key="mj_juros")
        dias = max((pgto - venc).days, 0)
        multa_v, juros_v, total_v = calcular_multa_juros(base, dias, multa_pct, juros_pct)

        if dias <= 0:
            st.info("Sem atraso — nenhuma multa/juros a cobrar.")
        else:
            st.write(f"Atraso de **{dias} dia(s)** · Multa: {brl(multa_v)} · "
                     f"Juros: {brl(juros_v)} · **Total: {brl(total_v)}**")

        if not cobrar:
            st.warning("Multa/juros **perdoados** — nada será cobrado.")
            if st.button("Registrar perdão (observação)"):
                add_lancamento(imovel_id=op[nome_mj], data=pgto,
                               competencia=venc.strftime("%Y-%m"),
                               tipo="Desconto / Abatimento", valor=0.0,
                               descricao=f"Multa/juros perdoados (atraso {dias} dias)")
                st.success("Perdão registrado no histórico.")
                st.rerun()
        elif total_v > 0:
            if st.button("Lançar multa/juros", type="primary"):
                add_lancamento(imovel_id=op[nome_mj], data=pgto,
                               competencia=venc.strftime("%Y-%m"),
                               tipo="Multa / Juros", valor=total_v,
                               descricao=f"Atraso {dias} dias — multa {multa_pct}% "
                                         f"+ juros {juros_pct}%/mês")
                st.success(f"Multa/juros de {brl(total_v)} lançada.")
                st.rerun()

    st.divider()
    st.markdown("**Histórico**")
    filtro = st.selectbox("Filtrar por imóvel", ["Todos"] + list(op.keys()))
    imovel_id = None if filtro == "Todos" else op[filtro]
    df = listar_lancamentos(imovel_id)
    if df.empty:
        st.info("Sem lançamentos ainda.")
        return

    mapa_nome = {int(r.id): r.nome for r in imoveis.itertuples()}
    df = df.sort_values("data", ascending=False)
    for r in df.itertuples():
        sinal = "＋" if r.natureza == "debito" else "−"
        c1, c2 = st.columns([6, 1])
        c1.write(
            f"**{r.data.strftime('%d/%m/%Y')}** · {mapa_nome.get(r.imovel_id,'?')} · "
            f"{r.tipo} · {sinal} {brl(r.valor)}"
            + (f" · _{r.descricao}_" if r.descricao else "")
        )
        if c2.button("🗑️", key=f"del{r.id}"):
            del_lancamento(int(r.id))
            st.rerun()


def pagina_extrato():
    st.subheader("📄 Extrato por imóvel")
    imoveis = listar_imoveis()
    if imoveis.empty:
        st.info("Cadastre um imóvel primeiro.")
        return
    op = {f"{r.nome}" + (f" — {r.locatario}" if r.locatario else ""): int(r.id)
          for r in imoveis.itertuples()}
    nome_sel = st.selectbox("Imóvel", list(op.keys()))
    imovel_id = op[nome_sel]

    with get_session() as s:
        imv = s.get(Imovel, imovel_id)
        saldo_ini = imv.saldo_inicial
        info = (imv.nome, imv.locatario, imv.telefone, imv.valor_aluguel)

    df = listar_lancamentos(imovel_id).sort_values(["data", "id"])
    st.write(f"**{info[0]}** · Inquilino: {info[1] or '—'} · "
             f"Aluguel: {brl(info[3])} · Tel.: {info[2] or '—'}")

    linhas = []
    saldo = saldo_ini
    if saldo_ini:
        linhas.append({"Data": "—", "Tipo": "Saldo inicial", "Cobrança": "",
                       "Recebido": "", "Saldo": brl(saldo), "Obs.": ""})
    for r in df.itertuples():
        if r.natureza == "debito":
            saldo += r.valor
            cob, rec = brl(r.valor), ""
        else:
            saldo -= r.valor
            cob, rec = "", brl(r.valor)
        linhas.append({
            "Data": r.data.strftime("%d/%m/%Y"), "Tipo": r.tipo,
            "Cobrança": cob, "Recebido": rec, "Saldo": brl(saldo),
            "Obs.": r.descricao,
        })
    extrato = pd.DataFrame(linhas)
    st.dataframe(extrato, use_container_width=True, hide_index=True)

    s_final = saldo
    if s_final > 0.005:
        st.error(f"Saldo devedor atual: **{brl(s_final)}**")
    elif s_final < -0.005:
        st.info(f"Crédito a favor do inquilino: **{brl(-s_final)}**")
    else:
        st.success("Conta em dia. ✅")

    # Exportar para Excel
    if not extrato.empty:
        import io
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as xw:
            extrato.to_excel(xw, index=False, sheet_name="Extrato")
        st.download_button("⬇️ Baixar extrato (Excel)", buf.getvalue(),
                           file_name=f"extrato_{info[0]}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def pagina_imoveis():
    st.subheader("🏠 Imóveis")
    imoveis = listar_imoveis()

    with st.expander("➕ Cadastrar novo imóvel"):
        with st.form("novo_imovel", clear_on_submit=True):
            c1, c2 = st.columns(2)
            nome = c1.text_input("Nome / identificação *")
            loc = c2.text_input("Inquilino")
            c3, c4, c5 = st.columns(3)
            val = c3.number_input("Valor do aluguel (R$)", min_value=0.0, step=50.0, format="%.2f")
            dia = c4.number_input("Dia do vencimento", min_value=1, max_value=31, value=1)
            sini = c5.number_input("Saldo inicial / pendência herdada (R$)",
                                   step=50.0, format="%.2f",
                                   help="Pendência que já existia no controle antigo.")
            cm1, cm2 = st.columns(2)
            multa_pct = cm1.number_input("Multa por atraso (%)", min_value=0.0,
                                         value=2.0, step=0.5, format="%.2f")
            juros_pct = cm2.number_input("Juros ao mês (%)", min_value=0.0,
                                         value=1.0, step=0.5, format="%.2f")
            tel = st.text_input("Telefone")
            end = st.text_input("Endereço")
            obs = st.text_area("Observações")
            if st.form_submit_button("Salvar", type="primary"):
                if not nome:
                    st.error("Informe o nome do imóvel.")
                else:
                    add_imovel(nome=nome, locatario=loc, valor_aluguel=val,
                               dia_vencimento=int(dia), saldo_inicial=sini,
                               multa_pct=multa_pct, juros_pct_mes=juros_pct,
                               telefone=tel, endereco=end, observacao=obs,
                               ativo=True)
                    st.success("Imóvel cadastrado.")
                    st.rerun()

    if imoveis.empty:
        st.info("Nenhum imóvel cadastrado ainda.")
        return

    st.markdown("##### Editar imóveis")
    for r in imoveis.itertuples():
        with st.expander(f"{r.nome}" + (f" — {r.locatario}" if r.locatario else "")):
            with st.form(f"edit{r.id}"):
                c1, c2 = st.columns(2)
                nome = c1.text_input("Nome", value=r.nome)
                loc = c2.text_input("Inquilino", value=r.locatario or "")
                c3, c4, c5 = st.columns(3)
                val = c3.number_input("Aluguel (R$)", value=float(r.valor_aluguel),
                                      step=50.0, format="%.2f")
                dia = c4.number_input("Vencimento", min_value=1, max_value=31,
                                      value=int(r.dia_vencimento))
                sini = c5.number_input("Saldo inicial (R$)",
                                       value=float(r.saldo_inicial), step=50.0, format="%.2f")
                cm1, cm2 = st.columns(2)
                multa_pct = cm1.number_input("Multa por atraso (%)", min_value=0.0,
                                             value=float(r.multa_pct or 2.0),
                                             step=0.5, format="%.2f")
                juros_pct = cm2.number_input("Juros ao mês (%)", min_value=0.0,
                                             value=float(r.juros_pct_mes or 1.0),
                                             step=0.5, format="%.2f")
                tel = st.text_input("Telefone", value=r.telefone or "")
                end = st.text_input("Endereço", value=r.endereco or "")
                obs = st.text_area("Observações", value=r.observacao or "")
                ativo = st.checkbox("Ativo (locado)", value=bool(r.ativo))
                cc1, cc2 = st.columns(2)
                if cc1.form_submit_button("💾 Salvar", type="primary"):
                    update_imovel(int(r.id), nome=nome, locatario=loc, valor_aluguel=val,
                                  dia_vencimento=int(dia), saldo_inicial=sini,
                                  multa_pct=multa_pct, juros_pct_mes=juros_pct,
                                  telefone=tel, endereco=end, observacao=obs, ativo=ativo)
                    st.success("Atualizado.")
                    st.rerun()
                if cc2.form_submit_button("🗑️ Excluir imóvel"):
                    del_imovel(int(r.id))
                    st.warning("Imóvel excluído (com seus lançamentos).")
                    st.rerun()


# ----------------------------------------------------------------------------- #
# App
# ----------------------------------------------------------------------------- #
def main():
    st.set_page_config(page_title="Gestão de Aluguéis", page_icon="🏠", layout="wide")
    if not checar_senha():
        return
    seed_se_vazio()

    st.sidebar.title("🏠 Gestão de Aluguéis")
    pagina = st.sidebar.radio(
        "Menu", ["Dashboard", "Lançamentos", "Extrato", "Imóveis"]
    )
    st.sidebar.divider()
    if st.sidebar.button("Sair"):
        st.session_state["autenticado"] = False
        st.rerun()

    if pagina == "Dashboard":
        pagina_dashboard()
    elif pagina == "Lançamentos":
        pagina_lancamentos()
    elif pagina == "Extrato":
        pagina_extrato()
    elif pagina == "Imóveis":
        pagina_imoveis()


if __name__ == "__main__":
    main()
