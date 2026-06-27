import streamlit as st
import pandas as pd
from streamlit_gsheets import GSheetsConnection
from datetime import date

st.set_page_config(page_title="Controle de Inquilinos", layout="wide")
st.title("🏢 Controle de Aluguéis")

# Conectando com o Google Sheets
conn = st.connection("gsheets", type=GSheetsConnection)

# Carregando as abas
df_lojas = conn.read(worksheet="Lojas", ttl=0).dropna(how="all")
df_pagamentos = conn.read(worksheet="Pagamentos", ttl=0).dropna(how="all")

# Garantir que colunas de contrato existam no df_lojas para não quebrar caso a planilha ainda não tenha
for col in ["Início Contrato", "Prazo Anos", "Mês Reajuste"]:
    if col not in df_lojas.columns:
        df_lojas[col] = ""

# Criando abas no Streamlit
tab1, tab2, tab3 = st.tabs(["📝 Lançar Pagamento", "📊 Visão Geral", "🔄 Contratos e Reajustes"])

# ==========================================
# ABA 1: Lançar Pagamento
# ==========================================
with tab1:
    st.header("Novo Lançamento")
    
    with st.form("form_pagamento", clear_on_submit=True):
        col1, col2 = st.columns(2)
        
        with col1:
            loja_selecionada = st.selectbox("Selecione a Loja", df_lojas['Loja'].tolist(), key="loja_pag")
            data_pagamento = st.date_input("Data do Pagamento", date.today())
            
        with col2:
            mes_referencia = st.selectbox("Mês de Referência", 
                                          ["01/2026", "02/2026", "03/2026", "04/2026", 
                                           "05/2026", "06/2026", "07/2026"])
            valor_pago = st.number_input("Valor Pago (R$)", min_value=0.0, step=50.0)
            
        submit = st.form_submit_button("Registrar Pagamento")
        
        if submit:
            novo_lancamento = pd.DataFrame([{
                "Data Pagamento": data_pagamento.strftime("%d/%m/%Y"),
                "Loja": loja_selecionada,
                "Mês Referência": mes_referencia,
                "Valor Pago": valor_pago
            }])
            
            df_atualizado = pd.concat([df_pagamentos, novo_lancamento], ignore_index=True)
            conn.update(worksheet="Pagamentos", data=df_atualizado)
            
            st.success(f"Pagamento de R$ {valor_pago} para a {loja_selecionada} registrado!")
            st.rerun()

# ==========================================
# ABA 2: Visão Geral
# ==========================================
with tab2:
    st.header("Status dos Aluguéis")
    
    mes_analise = st.selectbox("Selecione o Mês para Análise", 
                               ["01/2026", "02/2026", "03/2026", "04/2026", 
                                "05/2026", "06/2026", "07/2026"], index=5)
    
    if not df_pagamentos.empty:
        df_mes = df_pagamentos[df_pagamentos["Mês Referência"] == mes_analise]
        pagamentos_agrupados = df_mes.groupby("Loja")["Valor Pago"].sum().reset_index()
        
        df_resumo = pd.merge(df_lojas, pagamentos_agrupados, on="Loja", how="left")
        df_resumo["Valor Pago"] = df_resumo["Valor Pago"].fillna(0)
        
        # Ajustado para "Aluguel Devido" conforme a imagem da sua planilha
        df_resumo["Aluguel Devido"] = pd.to_numeric(df_resumo["Aluguel Devido"], errors='coerce').fillna(0)
        df_resumo["Valor Devedor"] = df_resumo["Aluguel Devido"] - df_resumo["Valor Pago"]
        
        df_display = df_resumo[["Loja", "Responsável", "Aluguel Devido", "Valor Pago", "Valor Devedor"]].copy()
        
        total_esperado = df_display["Aluguel Devido"].sum()
        total_recebido = df_display["Valor Pago"].sum()
        total_pendente = df_display["Valor Devedor"].sum()
        
        col1, col2, col3 = st.columns(3)
        col1.metric("Total Esperado", f"R$ {total_esperado:.2f}")
        col2.metric("Total Recebido", f"R$ {total_recebido:.2f}")
        col3.metric("Pendente (Devedor)", f"R$ {total_pendente:.2f}")
        
        st.divider()
        
        def destacar_devedores(row):
            if row['Valor Devedor'] > 0:
                return ['background-color: #ffcccc'] * len(row)
            return ['background-color: #ccffcc'] * len(row)
            
        st.dataframe(df_display.style.apply(destacar_devedores, axis=1), use_container_width=True)
    else:
        st.info("Nenhum pagamento registrado ainda.")

# ==========================================
# ABA 3: Contratos e Reajustes
# ==========================================
with tab3:
    st.header("Atualizar Contratos e Reajustar Aluguel")
    st.write("Atualize os prazos de contrato ou reajuste o valor base do aluguel (Aluguel Devido).")

    with st.form("form_reajuste", clear_on_submit=True):
        loja_reajuste = st.selectbox("Selecione a Loja", df_lojas['Loja'].tolist(), key="loja_reajuste")
        
        # Puxar dados atuais para exibir no formulário
        dados_loja = df_lojas[df_lojas['Loja'] == loja_reajuste].iloc[0]
        valor_atual = float(dados_loja.get('Aluguel Devido', 0))
        
        col1, col2 = st.columns(2)
        with col1:
            novo_valor = st.number_input("Novo Valor do Aluguel (R$)", value=valor_atual, step=50.0)
            mes_reajuste = st.selectbox("Mês Base de Reajuste", 
                                        ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", 
                                         "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"])
        with col2:
            inicio_contrato = st.date_input("Data de Início do Contrato")
            prazo_anos = st.number_input("Duração do Contrato (Anos)", min_value=1, value=1)
            
        submit_reajuste = st.form_submit_button("Salvar Atualização")
        
        if submit_reajuste:
            # Encontrar o índice da loja no DataFrame
            idx = df_lojas.index[df_lojas['Loja'] == loja_reajuste].tolist()[0]
            
            # Atualizar os valores no Pandas
            df_lojas.at[idx, 'Aluguel Devido'] = novo_valor
            df_lojas.at[idx, 'Início Contrato'] = inicio_contrato.strftime("%d/%m/%Y")
            df_lojas.at[idx, 'Prazo Anos'] = prazo_anos
            df_lojas.at[idx, 'Mês Reajuste'] = mes_reajuste
            
            # Subir o DataFrame atualizado de volta para a aba "Lojas"
            conn.update(worksheet="Lojas", data=df_lojas)
            
            st.success(f"Contrato da {loja_reajuste} atualizado com sucesso! Novo valor: R$ {novo_valor:.2f}")
            st.rerun()
            
    # Exibir tabela de lojas atual
    st.subheader("Dados Atuais dos Contratos")
    st.dataframe(df_lojas, use_container_width=True)
