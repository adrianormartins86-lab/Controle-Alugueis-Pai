import streamlit as st
import pandas as pd
from streamlit_gsheets import GSheetsConnection
from datetime import date

st.set_page_config(page_title="Controle de Inquilinos", layout="wide")

# ==========================================
# BARRA LATERAL (SINCRONIZAÇÃO MANUAL)
# ==========================================
st.sidebar.title("⚙️ Sistema")
if st.sidebar.button("🔄 Sincronizar com Sheets", use_container_width=True):
    st.cache_data.clear()
    st.rerun()
st.sidebar.info("Dica: Clique no botão acima sempre que você preencher ou alterar dados diretamente pelo Google Sheets.")

st.title("🏢 Controle de Aluguéis")

def formatar_brl(valor):
    try:
        return f"R$ {float(valor):,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    except:
        return "R$ 0,00"

# Conexão e Carregamento
conn = st.connection("gsheets", type=GSheetsConnection)
df_lojas = conn.read(worksheet="Lojas", ttl=0).dropna(how="all")
df_pagamentos = conn.read(worksheet="Pagamentos", ttl=0).dropna(how="all")

# Garantir Colunas Lojas
for col in ["Responsável", "Início Contrato", "Aluguel Devido"]:
    if col not in df_lojas.columns:
        df_lojas[col] = ""

# Tratamento de Tipos
if 'Loja' in df_lojas.columns:
    df_lojas['Loja'] = df_lojas['Loja'].astype(str).str.replace(r'\.0$', '', regex=True)

if 'Loja' in df_pagamentos.columns:
    df_pagamentos['Loja'] = df_pagamentos['Loja'].astype(str).str.replace(r'\.0$', '', regex=True)

df_lojas['Início Contrato'] = df_lojas['Início Contrato'].astype('object')

# Garantir Colunas Pagamentos (Mês Referência voltou!)
for col in ["Mês Referência", "Valor Aluguel", "IPTU", "Valor Pago", "R$Diferença"]:
    if col not in df_pagamentos.columns:
        df_pagamentos[col] = "" if col == "Mês Referência" else 0.0

tab1, tab2, tab3 = st.tabs(["📝 Lançar Pagamento", "📊 Visão Geral (Extrato)", "🔄 Contratos e Reajustes"])

# ==========================================
# ABA 1: Lançar Pagamento
# ==========================================
with tab1:
    st.header("Novo Lançamento")
    
    # --- LINHA 1: Loja | Responsável | Mês Referência ---
    col1, col2, col3 = st.columns(3)
    
    with col1:
        lista_lojas = df_lojas['Loja'].dropna().tolist()
        loja_selecionada = st.selectbox("Selecione a Loja", lista_lojas, key="loja_pag")
    
    # Busca automática do Responsável e do Aluguel
    try:
        linha_loja = df_lojas[df_lojas['Loja'] == loja_selecionada].iloc[0]
        aluguel_devido_atual = float(pd.to_numeric(linha_loja['Aluguel Devido'], errors='coerce'))
        responsavel_atual = str(linha_loja['Responsável'])
        if responsavel_atual.lower() == 'nan': responsavel_atual = "Não cadastrado"
    except:
        aluguel_devido_atual = 0.0
        responsavel_atual = "Não encontrado"
        
    with col2:
        st.text_input("Responsável (Inquilino)", value=responsavel_atual, disabled=True)
        
    with col3:
        # Gera uma lista de meses (anos 2024 a 2027)
        meses = [f"{str(m).zfill(2)}/{y}" for y in range(2024, 2028) for m in range(1, 13)]
        mes_atual_str = date.today().strftime("%m/%Y")
        idx_mes = meses.index(mes_atual_str) if mes_atual_str in meses else len(meses)//2
        mes_referencia = st.selectbox("Mês de Referência", meses, index=idx_mes)

    st.info(f"💰 **Valor do Aluguel Base Cadastrado:** {formatar_brl(aluguel_devido_atual)}")
    
    # --- LINHA 2: Data | Valor Pago | IPTU ---
    with st.form("form_pagamento", clear_on_submit=True):
        col_f1, col_f2, col_f3 = st.columns(3)
        
        with col_f1:
            data_pagamento = st.date_input("Data do Pagamento", date.today(), format="DD/MM/YYYY")
        with col_f2:
            valor_pago = st.number_input("Valor Pago (R$)", min_value=0.0, step=50.0)
        with col_f3:
            valor_iptu = st.number_input("Taxa de IPTU (R$)", min_value=0.0, value=0.0, step=50.0)
            
        st.write("") 
        submit = st.form_submit_button("Registrar Pagamento", use_container_width=True)
        
        if submit:
            total_esperado = aluguel_devido_atual + valor_iptu
            diferenca = total_esperado - valor_pago
            
            novo_lancamento = pd.DataFrame([{
                "Data Pagamento": data_pagamento.strftime("%d/%m/%Y"),
                "Loja": loja_selecionada,
                "Mês Referência": mes_referencia,
                "Valor Aluguel": aluguel_devido_atual,
                "IPTU": valor_iptu,
                "Valor Pago": valor_pago,
                "R$Diferença": diferenca
            }])
            
            novo_lancamento = novo_lancamento.reindex(columns=df_pagamentos.columns)
            df_atualizado = pd.concat([df_pagamentos, novo_lancamento], ignore_index=True)
            conn.update(worksheet="Pagamentos", data=df_atualizado)
            
            st.cache_data.clear()
            
            if diferenca > 0:
                st.warning(f"Pagamento parcial registrado! Restou uma diferença de {formatar_brl(diferenca)}.")
            else:
                st.success(f"Pagamento de {formatar_brl(valor_pago)} para a {loja_selecionada} registrado com sucesso!")
            
            st.rerun()

# ==========================================
# ABA 2: Visão Geral (ESTILO EXCEL / EXTRATO)
# ==========================================
with tab2:
    st.header("Extrato de Pagamentos")
    st.write("Visualize todo o histórico de lançamentos como em uma planilha.")
    
    if not df_pagamentos.empty:
        opcoes_loja = ["Todas"] + df_lojas['Loja'].dropna().tolist()
        loja_filtro = st.selectbox("Filtrar por Loja", opcoes_loja)
        
        df_extrato = df_pagamentos.copy()
        
        if loja_filtro != "Todas":
            df_extrato = df_extrato[df_extrato["Loja"] == loja_filtro]
            
        # Converte para números para cálculos seguros
        for col in ["Valor Aluguel", "IPTU", "Valor Pago", "R$Diferença"]:
            df_extrato[col] = pd.to_numeric(df_extrato[col], errors='coerce').fillna(0)
            
        # Ordena por data (do mais recente para o mais antigo)
        df_extrato['Data_Temp'] = pd.to_datetime(df_extrato['Data Pagamento'], format='%d/%m/%Y', errors='coerce')
        df_extrato = df_extrato.sort_values(by='Data_Temp', ascending=False).drop(columns=['Data_Temp'])
        
        # Métricas Globais do Filtro Atual
        tot_pago = df_extrato["Valor Pago"].sum()
        tot_iptu = df_extrato["IPTU"].sum()
        tot_diferenca = df_extrato["R$Diferença"].sum()
        
        col1, col2, col3 = st.columns(3)
        col1.metric("Total Pago (Período)", formatar_brl(tot_pago))
        col2.metric("Total IPTU Arrecadado", formatar_brl(tot_iptu))
        col3.metric("Saldo Pendente Acumulado", formatar_brl(tot_diferenca))
        
        st.divider()
        
        # Traz o nome do responsável para a tabela se for "Todas" as lojas
        if loja_filtro == "Todas":
            df_extrato = pd.merge(df_extrato, df_lojas[["Loja", "Responsável"]], on="Loja", how="left")
            cols = ["Data Pagamento", "Mês Referência", "Loja", "Responsável", "Valor Aluguel", "IPTU", "Valor Pago", "R$Diferença"]
        else:
            cols = ["Data Pagamento", "Mês Referência", "Valor Aluguel", "IPTU", "Valor Pago", "R$Diferença"]
            
        # Exibe apenas as colunas que existem
        cols = [c for c in cols if c in df_extrato.columns]
        df_display = df_extrato[cols]
        
        def destacar_extrato(row):
            if row.get('R$Diferença', 0) > 0:
                return ['background-color: #ffcccc'] * len(row) # Vermelho (Devendo)
            elif row.get('R$Diferença', 0) < 0:
                return ['background-color: #cce5ff'] * len(row) # Azul (Crédito/Pagou a mais)
            return [''] * len(row) # Branco/Padrão (Pago exato)
            
        df_estilizado = df_display.style.format({
            "Valor Aluguel": formatar_brl,
            "IPTU": formatar_brl,
            "Valor Pago": formatar_brl,
            "R$Diferença": formatar_brl
        }).apply(destacar_extrato, axis=1)
            
        st.dataframe(df_estilizado, use_container_width=True)
    else:
        st.info("Nenhum pagamento registrado ainda.")

# ==========================================
# ABA 3: Contratos e Reajustes
# ==========================================
with tab3:
    st.header("Atualizar Contratos e Reajustar Aluguel")

    with st.form("form_reajuste", clear_on_submit=True):
        col1, col2 = st.columns(2)
        
        with col1:
            loja_reajuste = st.selectbox("Selecione a Loja", df_lojas['Loja'].dropna().tolist(), key="loja_reajuste")
        
        try:
            dados_loja = df_lojas[df_lojas['Loja'] == loja_reajuste].iloc[0]
            valor_atual = float(pd.to_numeric(dados_loja.get('Aluguel Devido', 0), errors='coerce'))
            responsavel_atual = str(dados_loja.get('Responsável', ''))
            if responsavel_atual.lower() == 'nan': responsavel_atual = ""
        except:
            valor_atual = 0.0
            responsavel_atual = ""
            
        with col1:
            novo_responsavel = st.text_input("Responsável (Inquilino)", value=responsavel_atual)
            
        with col2:
            data_ajuste = st.date_input("Data do Contrato / Ajuste", format="DD/MM/YYYY")
            novo_valor = st.number_input("Novo Valor (R$)", value=valor_atual, step=50.0)
            
        submit_reajuste = st.form_submit_button("Salvar Atualizações", use_container_width=True)
        
        if submit_reajuste:
            idx = df_lojas.index[df_lojas['Loja'] == loja_reajuste].tolist()[0]
            
            df_lojas.at[idx, 'Responsável'] = novo_responsavel
            df_lojas.at[idx, 'Aluguel Devido'] = novo_valor
            df_lojas.at[idx, 'Início Contrato'] = data_ajuste.strftime("%d/%m/%Y")
            
            conn.update(worksheet="Lojas", data=df_lojas)
            st.cache_data.clear()
            
            st.success(f"Cadastro da {loja_reajuste} atualizado com sucesso!")
            st.rerun()
            
    st.subheader("Dados Atuais dos Contratos")
    colunas_exibicao = [col for col in ['Loja', 'Responsável', 'Aluguel Devido', 'Início Contrato'] if col in df_lojas.columns]
    df_lojas_display = df_lojas[colunas_exibicao].copy()
    
    if 'Aluguel Devido' in df_lojas_display.columns:
        df_lojas_display['Aluguel Devido'] = pd.to_numeric(df_lojas_display['Aluguel Devido'], errors='coerce').fillna(0)
    
    st.dataframe(df_lojas_display.style.format({"Aluguel Devido": formatar_brl}), use_container_width=True)
