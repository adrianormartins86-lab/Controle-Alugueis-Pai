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
    # Limpa o cache para forçar a leitura de dados novos do Google Sheets
    st.cache_data.clear()
    st.rerun()
st.sidebar.info("Dica: Clique no botão acima sempre que você preencher ou alterar dados diretamente pelo Google Sheets.")

st.title("🏢 Controle de Aluguéis")

# Função auxiliar para formatar valores no padrão de moeda brasileiro
def formatar_brl(valor):
    try:
        return f"R$ {float(valor):,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    except:
        return "R$ 0,00"

# Conectando com o Google Sheets
conn = st.connection("gsheets", type=GSheetsConnection)

# Carregando as abas
df_lojas = conn.read(worksheet="Lojas", ttl=0).dropna(how="all")
df_pagamentos = conn.read(worksheet="Pagamentos", ttl=0).dropna(how="all")

# Garantir que apenas as colunas essenciais de contrato existam no df_lojas
for col in ["Responsável", "Início Contrato", "Aluguel Devido"]:
    if col not in df_lojas.columns:
        df_lojas[col] = ""

# ==========================================
# TRATAMENTO DE TIPOS DE DADOS E CORREÇÃO DE ERROS
# ==========================================
if 'Loja' in df_lojas.columns:
    df_lojas['Loja'] = df_lojas['Loja'].astype(str).str.replace(r'\.0$', '', regex=True)

if 'Loja' in df_pagamentos.columns:
    df_pagamentos['Loja'] = df_pagamentos['Loja'].astype(str).str.replace(r'\.0$', '', regex=True)

df_lojas['Início Contrato'] = df_lojas['Início Contrato'].astype('object')

# Garantir que colunas financeiras (incluindo IPTU e Valor Aluguel) existam no df_pagamentos
for col in ["Valor Aluguel", "IPTU", "Valor Pago", "R$Diferença"]:
    if col not in df_pagamentos.columns:
        df_pagamentos[col] = 0.0

# Criando abas no Streamlit
tab1, tab2, tab3 = st.tabs(["📝 Lançar Pagamento", "📊 Visão Geral", "🔄 Contratos e Reajustes"])

# ==========================================
# ABA 1: Lançar Pagamento
# ==========================================
with tab1:
    st.header("Novo Lançamento")
    
    lista_lojas = df_lojas['Loja'].dropna().tolist()
    loja_selecionada = st.selectbox("Selecione a Loja", lista_lojas, key="loja_pag")
    
    try:
        linha_loja = df_lojas[df_lojas['Loja'] == loja_selecionada]
        aluguel_devido_atual = pd.to_numeric(linha_loja['Aluguel Devido'], errors='coerce').fillna(0).values[0]
    except:
        aluguel_devido_atual = 0.0

    st.info(f"💰 **Valor do Aluguel Cadastrado para a {loja_selecionada}:** {formatar_brl(aluguel_devido_atual)}")
    
    with st.form("form_pagamento", clear_on_submit=True):
        col1, col2 = st.columns(2)
        
        with col1:
            data_pagamento = st.date_input("Data do Pagamento", date.today(), format="DD/MM/YYYY")
            valor_iptu = st.number_input("Taxa de IPTU (R$)", min_value=0.0, value=0.0, step=50.0)
            
        with col2:
            valor_pago = st.number_input("Valor Total Pago (R$)", min_value=0.0, step=50.0)
            st.write("") 
            st.write("") 
            submit = st.form_submit_button("Registrar Pagamento", use_container_width=True)
        
        if submit:
            total_esperado = aluguel_devido_atual + valor_iptu
            diferenca = total_esperado - valor_pago
            
            novo_lancamento = pd.DataFrame([{
                "Data Pagamento": data_pagamento.strftime("%d/%m/%Y"),
                "Loja": loja_selecionada,
                "Valor Aluguel": aluguel_devido_atual,
                "IPTU": valor_iptu,
                "Valor Pago": valor_pago,
                "R$Diferença": diferenca
            }])
            
            # Garante que a ordem das colunas ao salvar seja exatamente a mesma da planilha atual
            novo_lancamento = novo_lancamento.reindex(columns=df_pagamentos.columns)
            
            df_atualizado = pd.concat([df_pagamentos, novo_lancamento], ignore_index=True)
            conn.update(worksheet="Pagamentos", data=df_atualizado)
            
            # Força a limpeza de cache após salvar via sistema
            st.cache_data.clear()
            
            if diferenca > 0:
                st.warning(f"Pagamento parcial registrado! Restou uma diferença de {formatar_brl(diferenca)} neste lançamento.")
            else:
                st.success(f"Pagamento de {formatar_brl(valor_pago)} para a {loja_selecionada} registrado com sucesso!")
            
            st.rerun()

# ==========================================
# ABA 2: Visão Geral (DASHBOARD INTELIGENTE)
# ==========================================
with tab2:
    st.header("Status dos Aluguéis")
    
    if not df_pagamentos.empty:
        df_pagamentos['Data_Temp'] = pd.to_datetime(df_pagamentos['Data Pagamento'], format='%d/%m/%Y', errors='coerce')
        df_pagamentos['Mês/Ano'] = df_pagamentos['Data_Temp'].dt.strftime('%m/%Y')
        
        meses_disponiveis = df_pagamentos['Mês/Ano'].dropna().unique().tolist()
        meses_disponiveis.sort(reverse=True) 
        
        if not meses_disponiveis:
            meses_disponiveis = [date.today().strftime('%m/%Y')]
            
        col_filtro1, col_filtro2 = st.columns(2)
        with col_filtro1:
            mes_analise = st.selectbox("Selecione o Mês do Pagamento", meses_disponiveis)
        with col_filtro2:
            opcoes_loja = ["Todas"] + df_lojas['Loja'].dropna().tolist()
            loja_filtro = st.selectbox("Filtrar por Loja", opcoes_loja)
        
        df_mes = df_pagamentos[df_pagamentos["Mês/Ano"] == mes_analise]
        
        # O SEGREDRO: Agora agrupamos e buscamos o 'Valor Aluguel' do histórico, não apenas os pagamentos
        pagamentos_agrupados = df_mes.groupby("Loja").agg({
            "Valor Pago": "sum",
            "IPTU": "sum",
            "Valor Aluguel": "max" # Pega o valor da época do contrato salvo no Pagamento
        }).reset_index()
        
        df_resumo = pd.merge(df_lojas, pagamentos_agrupados, on="Loja", how="left")
        
        # Tratamento financeiro isolado para não conflitar com nulos
        df_resumo["Valor Pago"] = pd.to_numeric(df_resumo["Valor Pago"], errors='coerce').fillna(0)
        df_resumo["IPTU"] = pd.to_numeric(df_resumo["IPTU"], errors='coerce').fillna(0)
        df_resumo["Aluguel Devido Atual"] = pd.to_numeric(df_resumo["Aluguel Devido"], errors='coerce').fillna(0)
        df_resumo["Valor Aluguel Histórico"] = pd.to_numeric(df_resumo["Valor Aluguel"], errors='coerce')
        
        # BI Lógica: Se o inquilino pagou no mês, usa o valor histórico do aluguel. Se não pagou nada, projeta o aluguel atual.
        df_resumo["Aluguel Considerado"] = df_resumo["Valor Aluguel Histórico"].fillna(df_resumo["Aluguel Devido Atual"])
            
        df_resumo["Total Esperado"] = df_resumo["Aluguel Considerado"] + df_resumo["IPTU"]
        df_resumo["Valor Devedor"] = df_resumo["Total Esperado"] - df_resumo["Valor Pago"]
        
        df_display = df_resumo[["Loja", "Responsável", "Aluguel Considerado", "IPTU", "Total Esperado", "Valor Pago", "Valor Devedor"]].copy()
        
        # Renomeia para exibição limpa
        df_display.rename(columns={"Aluguel Considerado": "Aluguel Devido"}, inplace=True)
        
        if loja_filtro != "Todas":
            df_display = df_display[df_display["Loja"] == loja_filtro]
        
        total_esperado_geral = df_display["Total Esperado"].sum()
        total_recebido_geral = df_display["Valor Pago"].sum()
        total_pendente_geral = df_display["Valor Devedor"].sum()
        
        st.write("")
        
        col1, col2, col3 = st.columns(3)
        col1.metric("Total Esperado", formatar_brl(total_esperado_geral))
        col2.metric("Total Recebido", formatar_brl(total_recebido_geral))
        col3.metric("Pendente (Devedor/Crédito)", formatar_brl(total_pendente_geral))
        
        st.divider()
        
        def destacar_devedores(row):
            if row['Valor Devedor'] > 0:
                return ['background-color: #ffcccc'] * len(row)
            return ['background-color: #ccffcc'] * len(row)
            
        df_estilizado = df_display.style.format({
            "Aluguel Devido": formatar_brl,
            "IPTU": formatar_brl,
            "Total Esperado": formatar_brl,
            "Valor Pago": formatar_brl,
            "Valor Devedor": formatar_brl
        }).apply(destacar_devedores, axis=1)
            
        st.dataframe(df_estilizado, use_container_width=True)
    else:
        st.info("Nenhum pagamento registrado ainda. Realize o primeiro lançamento.")

# ==========================================
# ABA 3: Contratos e Reajustes
# ==========================================
with tab3:
    st.header("Atualizar Contratos e Reajustar Aluguel")
    st.write("Atualize os dados do inquilino ou ajuste o valor base do aluguel.")

    with st.form("form_reajuste", clear_on_submit=True):
        col1, col2 = st.columns(2)
        
        with col1:
            loja_reajuste = st.selectbox("Selecione a Loja", df_lojas['Loja'].dropna().tolist(), key="loja_reajuste")
        
        try:
            dados_loja = df_lojas[df_lojas['Loja'] == loja_reajuste].iloc[0]
            valor_atual = float(pd.to_numeric(dados_loja.get('Aluguel Devido', 0), errors='coerce'))
            responsavel_atual = str(dados_loja.get('Responsável', ''))
            if responsavel_atual.lower() == 'nan': 
                responsavel_atual = ""
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
            
            # Limpa o cache após atualizar via sistema
            st.cache_data.clear()
            
            st.success(f"Cadastro da {loja_reajuste} atualizado com sucesso!")
            st.rerun()
            
    st.subheader("Dados Atuais dos Contratos")
    
    colunas_exibicao = [col for col in ['Loja', 'Responsável', 'Aluguel Devido', 'Início Contrato'] if col in df_lojas.columns]
    df_lojas_display = df_lojas[colunas_exibicao].copy()
    
    if 'Aluguel Devido' in df_lojas_display.columns:
        df_lojas_display['Aluguel Devido'] = pd.to_numeric(df_lojas_display['Aluguel Devido'], errors='coerce').fillna(0)
    
    st.dataframe(df_lojas_display.style.format({
        "Aluguel Devido": formatar_brl
    }), use_container_width=True)
