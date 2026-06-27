import streamlit as st
import pandas as pd
from streamlit_gsheets import GSheetsConnection
from datetime import date

st.set_page_config(page_title="Controle de Inquilinos", layout="wide")
st.title("🏢 Controle de Aluguéis")

# Função auxiliar para formatar valores no padrão de moeda brasileiro (R$ 1.234,56)
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

# Garantir que colunas de contrato existam no df_lojas
for col in ["Início Contrato", "Prazo Anos", "Mês Reajuste", "Aluguel Devido"]:
    if col not in df_lojas.columns:
        df_lojas[col] = ""

# ==========================================
# TRATAMENTO DE TIPOS DE DADOS E CORREÇÃO DE ERROS
# ==========================================
# 1. Força a coluna 'Loja' a ser texto puro e limpa o '.0' do Pandas (ex: '1.0' vira '1')
if 'Loja' in df_lojas.columns:
    df_lojas['Loja'] = df_lojas['Loja'].astype(str).str.replace(r'\.0$', '', regex=True)

# 2. Força colunas de texto/data vazias a aceitarem Strings para evitar o TypeError no salvamento
df_lojas['Início Contrato'] = df_lojas['Início Contrato'].astype('object')
df_lojas['Mês Reajuste'] = df_lojas['Mês Reajuste'].astype('object')

# Garantir que colunas financeiras existam no df_pagamentos
for col in ["Valor Aluguel", "Valor Pago", "R$Diferença"]:
    if col not in df_pagamentos.columns:
        df_pagamentos[col] = 0.0

# Criando abas no Streamlit
tab1, tab2, tab3 = st.tabs(["📝 Lançar Pagamento", "📊 Visão Geral", "🔄 Contratos e Reajustes"])

# ==========================================
# ABA 1: Lançar Pagamento
# ==========================================
with tab1:
    st.header("Novo Lançamento")
    
    # Seleção da Loja (fora do formulário para permitir a atualização reativa na tela)
    lista_lojas = df_lojas['Loja'].dropna().tolist()
    loja_selecionada = st.selectbox("Selecione a Loja", lista_lojas, key="loja_pag")
    
    # Buscando o "Aluguel Devido" atual na base cadastral
    try:
        linha_loja = df_lojas[df_lojas['Loja'] == loja_selecionada]
        aluguel_devido_atual = pd.to_numeric(linha_loja['Aluguel Devido'], errors='coerce').fillna(0).values[0]
    except:
        aluguel_devido_atual = 0.0

    # Exibe na interface o valor esperado formatado com vírgula
    st.info(f"💰 **Valor do Aluguel Cadastrado para a {loja_selecionada}:** {formatar_brl(aluguel_devido_atual)}")
    
    with st.form("form_pagamento", clear_on_submit=True):
        col1, col2 = st.columns(2)
        
        with col1:
            # Data de entrada configurada no formato brasileiro
            data_pagamento = st.date_input("Data do Pagamento", date.today(), format="DD/MM/YYYY")
            mes_referencia = st.selectbox("Mês de Referência", 
                                          ["01/2026", "02/2026", "03/2026", "04/2026", 
                                           "05/2026", "06/2026", "07/2026"])
            
        with col2:
            valor_pago = st.number_input("Valor Pago (R$)", min_value=0.0, step=50.0)
            
        submit = st.form_submit_button("Registrar Pagamento")
        
        if submit:
            diferenca = aluguel_devido_atual - valor_pago
            
            novo_lancamento = pd.DataFrame([{
                "Data Pagamento": data_pagamento.strftime("%d/%m/%Y"),
                "Loja": loja_selecionada,
                "Mês Referência": mes_referencia,
                "Valor Aluguel": aluguel_devido_atual,
                "Valor Pago": valor_pago,
                "R$Diferença": diferenca
            }])
            
            df_atualizado = pd.concat([df_pagamentos, novo_lancamento], ignore_index=True)
            conn.update(worksheet="Pagamentos", data=df_atualizado)
            
            if diferenca > 0:
                st.warning(f"Pagamento parcial de {formatar_brl(valor_pago)} registrado! Restou uma diferença de {formatar_brl(diferenca)} neste lançamento.")
            else:
                st.success(f"Pagamento integral de {formatar_brl(valor_pago)} para a {loja_selecionada} registrado com sucesso!")
            
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
        df_resumo["Valor Pago"] = pd.to_numeric(df_resumo["Valor Pago"], errors='coerce').fillna(0)
        df_resumo["Aluguel Devido"] = pd.to_numeric(df_resumo["Aluguel Devido"], errors='coerce').fillna(0)
        df_resumo["Valor Devedor"] = df_resumo["Aluguel Devido"] - df_resumo["Valor Pago"]
        
        df_display = df_resumo[["Loja", "Responsável", "Aluguel Devido", "Valor Pago", "Valor Devedor"]].copy()
        
        total_esperado = df_display["Aluguel Devido"].sum()
        total_recebido = df_display["Valor Pago"].sum()
        total_pendente = df_display["Valor Devedor"].sum()
        
        # Métricas exibidas no formato de moeda local
        col1, col2, col3 = st.columns(3)
        col1.metric("Total Esperado no Mês", formatar_brl(total_esperado))
        col2.metric("Total Recebido no Mês", formatar_brl(total_recebido))
        col3.metric("Pendente Geral (Devedor)", formatar_brl(total_pendente))
        
        st.divider()
        
        def destacar_devedores(row):
            if row['Valor Devedor'] > 0:
                return ['background-color: #ffcccc'] * len(row)
            return ['background-color: #ccffcc'] * len(row)
            
        # Aplicação de estilo na tabela para formatar as moedas com vírgula sem quebrar a lógica matemática
        df_estilizado = df_display.style.format({
            "Aluguel Devido": formatar_brl,
            "Valor Pago": formatar_brl,
            "Valor Devedor": formatar_brl
        }).apply(destacar_devedores, axis=1)
            
        st.dataframe(df_estilizado, use_container_width=True)
    else:
        st.info("Nenhum pagamento registrado ainda.")

# ==========================================
# ABA 3: Contratos e Reajustes
# ==========================================
with tab3:
    st.header("Atualizar Contratos e Reajustar Aluguel")
    st.write("Ajuste manualmente o valor base do aluguel sempre que houver necessidade.")

    with st.form("form_reajuste", clear_on_submit=True):
        loja_reajuste = st.selectbox("Selecione a Loja", df_lojas['Loja'].dropna().tolist(), key="loja_reajuste")
        
        try:
            dados_loja = df_lojas[df_lojas['Loja'] == loja_reajuste].iloc[0]
            valor_atual = float(pd.to_numeric(dados_loja.get('Aluguel Devido', 0), errors='coerce'))
        except:
            valor_atual = 0.0
        
        col1, col2 = st.columns(2)
        with col1:
            novo_valor = st.number_input("Novo Valor do Aluguel (R$)", value=valor_atual, step=50.0)
            mes_reajuste = st.selectbox("Mês Base de Reajuste", 
                                        ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", 
                                         "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"])
        with col2:
            # Entrada de data configurada no formato nacional brasileiro
            inicio_contrato = st.date_input("Data de Início do Contrato", format="DD/MM/YYYY")
            prazo_anos = st.number_input("Duração do Contrato (Anos)", min_value=1, value=1)
            
        submit_reajuste = st.form_submit_button("Salvar Atualização")
        
        if submit_reajuste:
            idx = df_lojas.index[df_lojas['Loja'] == loja_reajuste].tolist()[0]
            
            df_lojas.at[idx, 'Aluguel Devido'] = novo_valor
            df_lojas.at[idx, 'Início Contrato'] = inicio_contrato.strftime("%d/%m/%Y")
            df_lojas.at[idx, 'Prazo Anos'] = prazo_anos
            df_lojas.at[idx, 'Mês Reajuste'] = mes_reajuste
            
            conn.update(worksheet="Lojas", data=df_lojas)
            
            st.success(f"Contrato da {loja_reajuste} atualizado com sucesso! Novo aluguel: {formatar_brl(novo_valor)}")
            st.rerun()
            
    st.subheader("Dados Atuais dos Contratos")
    
    df_lojas_display = df_lojas.copy()
    if 'Aluguel Devido' in df_lojas_display.columns:
        df_lojas_display['Aluguel Devido'] = pd.to_numeric(df_lojas_display['Aluguel Devido'], errors='coerce').fillna(0)
    
    st.dataframe(df_lojas_display.style.format({
        "Aluguel Devido": formatar_brl
    }), use_container_width=True)
