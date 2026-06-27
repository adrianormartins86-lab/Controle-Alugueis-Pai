import streamlit as st
import pandas as pd
from streamlit_gsheets import GSheetsConnection
from datetime import date

st.set_page_config(page_title="Controle de Inquilinos", layout="wide")
st.title("🏢 Controle de Aluguéis")

# Conectando com o Google Sheets
# Lembre-se de configurar a URL da planilha no .streamlit/secrets.toml
conn = st.connection("gsheets", type=GSheetsConnection)

# Carregando as abas
# ttl=0 garante que os dados estejam sempre atualizados ao carregar
df_lojas = conn.read(worksheet="Lojas", ttl=0).dropna(how="all")
df_pagamentos = conn.read(worksheet="Pagamentos", ttl=0).dropna(how="all")

# Criando abas no Streamlit para organizar a interface
tab1, tab2 = st.tabs(["📝 Lançar Pagamento", "📊 Visão Geral e Inadimplência"])

with tab1:
    st.header("Novo Lançamento")
    
    with st.form("form_pagamento", clear_on_submit=True):
        col1, col2 = st.columns(2)
        
        with col1:
            loja_selecionada = st.selectbox("Selecione a Loja", df_lojas['Loja'].tolist())
            data_pagamento = st.date_input("Data do Pagamento", date.today())
            
        with col2:
            # Opções de meses (pode ser gerado dinamicamente no futuro)
            mes_referencia = st.selectbox("Mês de Referência", 
                                          ["01/2026", "02/2026", "03/2026", "04/2026", 
                                           "05/2026", "06/2026", "07/2026"])
            valor_pago = st.number_input("Valor Pago (R$)", min_value=0.0, step=50.0)
            
        submit = st.form_submit_button("Registrar Pagamento")
        
        if submit:
            # Criando um novo DataFrame com a linha do novo pagamento
            novo_lancamento = pd.DataFrame([{
                "Data Pagamento": data_pagamento.strftime("%d/%m/%Y"),
                "Loja": loja_selecionada,
                "Mês Referência": mes_referencia,
                "Valor Pago": valor_pago
            }])
            
            # Adicionando ao dataframe existente e atualizando o Sheets
            df_atualizado = pd.concat([df_pagamentos, novo_lancamento], ignore_index=True)
            conn.update(worksheet="Pagamentos", data=df_atualizado)
            
            st.success(f"Pagamento de R$ {valor_pago} para a {loja_selecionada} registrado com sucesso!")
            st.rerun()

with tab2:
    st.header("Status dos Aluguéis")
    
    # Filtro de mês para análise
    mes_analise = st.selectbox("Selecione o Mês para Análise", 
                               ["01/2026", "02/2026", "03/2026", "04/2026", 
                                "05/2026", "06/2026", "07/2026"], index=5)
    
    if not df_pagamentos.empty:
        # Filtrar pagamentos do mês selecionado
        df_mes = df_pagamentos[df_pagamentos["Mês Referência"] == mes_analise]
        
        # Agrupar valores pagos por loja usando pandas (bem estilo análise de dados)
        pagamentos_agrupados = df_mes.groupby("Loja")["Valor Pago"].sum().reset_index()
        
        # Fazer um JOIN com a tabela de lojas para trazer o Valor do Aluguel
        df_resumo = pd.merge(df_lojas, pagamentos_agrupados, on="Loja", how="left")
        
        # Preencher NaN com 0 para quem não pagou nada ainda
        df_resumo["Valor Pago"] = df_resumo["Valor Pago"].fillna(0)
        
        # Calcular o Valor Devedor
        df_resumo["Valor Devedor"] = df_resumo["Valor Aluguel"] - df_resumo["Valor Pago"]
        
        # Formatando para exibição
        df_display = df_resumo[["Loja", "Responsável", "Valor Aluguel", "Valor Pago", "Valor Devedor"]].copy()
        
        # Criando métricas visuais no topo
        total_esperado = df_display["Valor Aluguel"].sum()
        total_recebido = df_display["Valor Pago"].sum()
        total_pendente = df_display["Valor Devedor"].sum()
        
        col1, col2, col3 = st.columns(3)
        col1.metric("Total Esperado", f"R$ {total_esperado:.2f}")
        col2.metric("Total Recebido", f"R$ {total_recebido:.2f}")
        col3.metric("Pendente (Devedor)", f"R$ {total_pendente:.2f}")
        
        st.divider()
        
        # Aplicando um estilo condicional simples para destacar devedores (DAX na veia, mas no Pandas!)
        def destacar_devedores(row):
            if row['Valor Devedor'] > 0:
                return ['background-color: #ffcccc'] * len(row)
            return ['background-color: #ccffcc'] * len(row)
            
        st.dataframe(df_display.style.apply(destacar_devedores, axis=1), use_container_width=True)
    else:
        st.info("Nenhum pagamento registrado ainda.")
