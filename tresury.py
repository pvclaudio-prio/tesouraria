import streamlit as st
import pandas as pd
from datetime import datetime, date
from io import BytesIO
from pathlib import Path
import plotly.express as px
import os
from pydrive.auth import GoogleAuth
from pydrive.drive import GoogleDrive
import zipfile
import tempfile
import json
from oauth2client.client import OAuth2Credentials
import httplib2
import traceback
import httpx
from sentence_transformers import SentenceTransformer, util
from openai import OpenAI
import json
import requests
from difflib import get_close_matches
import re
from datetime import timedelta
import matplotlib.pyplot as plt
from docx import Document
from docx.shared import Pt
from pandas import Timestamp
from google.cloud import documentai_v1 as documentai
from google.oauth2 import service_account
import openai
import docx
import uuid
import openpyxl
import time
from docx2pdf import convert
from google.cloud import documentai_v1beta3 as documentai
from PyPDF2 import PdfReader, PdfWriter
import io

st.set_page_config(layout = 'wide')
client = OpenAI(api_key=st.secrets["openai"]["api_key"])

# -----------------------------
# Validação Usuários com st.secrets
# -----------------------------
@st.cache_data
def carregar_usuarios():
    usuarios_config = st.secrets.get("users", {})
    usuarios = {}
    for user, dados in usuarios_config.items():
        try:
            nome, senha = dados.split("|", 1)
            usuarios[user] = {"name": nome, "password": senha}
        except:
            st.warning(f"Erro ao carregar usuário '{user}' nos secrets.")
    return usuarios

users = carregar_usuarios()

# Inicializa variáveis de sessão
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.username = ""

# -----------------------------
# Tela de Login
# -----------------------------
if not st.session_state.logged_in:
    st.title("🔐 Login")
    username = st.text_input("Usuário")
    password = st.text_input("Senha", type="password")

    if st.button("Entrar"):
        user = users.get(username)
        if user and user["password"] == password:
            st.session_state.logged_in = True
            st.session_state.username = username
            st.success(f"Bem-vindo, {user['name']}!")
            st.rerun()
        else:
            st.error("Usuário ou senha incorretos.")
    st.stop()

# -----------------------------
# Menu lateral após login
# -----------------------------
st.sidebar.image("PRIO_SEM_POLVO_PRIO_PANTONE_LOGOTIPO_Azul.png")

nome_usuario = users[st.session_state.username]["name"]
st.sidebar.success(f"Logado como: {nome_usuario}")

if st.sidebar.button("Logout"):
    st.session_state.logged_in = False
    st.session_state.username = ""
    st.rerun()

# -----------------------------
# Menu de Navegação
# -----------------------------
st.sidebar.title("Navegação")
pagina = st.sidebar.radio("Ir para:", [
    "📂 Upload do Contrato",
    "🧾 Validação das Cláusulas",
    "🔍 Análise Automática",
    "🧑‍⚖️ Revisão Final",
    "📊 Índices PRIO",
    "📘 Relatórios Gerenciais"
])

# =========================
# Funções de conexão com o Google Drive
# =========================
def conectar_drive():
    cred_dict = st.secrets["credentials"]

    credentials = OAuth2Credentials(
        access_token=cred_dict["access_token"],
        client_id=cred_dict["client_id"],
        client_secret=cred_dict["client_secret"],
        refresh_token=cred_dict["refresh_token"],
        token_expiry=datetime.strptime(cred_dict["token_expiry"], "%Y-%m-%dT%H:%M:%SZ"),
        token_uri=cred_dict["token_uri"],
        user_agent="streamlit-app/1.0",
        revoke_uri=cred_dict["revoke_uri"]
    )

    if credentials.access_token_expired:
        credentials.refresh(httplib2.Http())

    gauth = GoogleAuth()
    gauth.credentials = credentials
    return GoogleDrive(gauth)

def obter_id_pasta(nome_pasta, parent_id=None):
    drive = conectar_drive()
    query = f"title = '{nome_pasta}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    resultado = drive.ListFile({'q': query}).GetList()
    if resultado:
        return resultado[0]['id']
    return None

# =========================
# Base de contratos
# =========================

def aba_upload_contrato(user_email):
    st.title("📂 Upload do Contrato")

    st.markdown("Faça upload de um contrato em `.pdf` e preencha os dados abaixo.")

    arquivo = st.file_uploader("Selecione o contrato", type=["pdf"])
    nome_amigavel = st.text_input("Nome do contrato para exibição futura (ex: FAB PRIO - Empréstimo 2025)")
    instituicao = st.text_input("Instituição Financeira")
    idioma = st.selectbox("Idioma do Contrato", ["pt", "en"])

    if st.button("📤 Enviar Contrato"):
        if not arquivo or not nome_amigavel or not instituicao:
            st.warning("Por favor, preencha todos os campos e envie um arquivo.")
            return

        drive = conectar_drive()
        pasta_contratos_id = obter_id_pasta("contratos", parent_id=obter_id_pasta("Tesouraria"))

        id_contrato = str(uuid.uuid4())
        nome_arquivo_drive = f"{id_contrato}_{arquivo.name}"

        # Salvar temporariamente
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{arquivo.name.split('.')[-1]}") as tmp:
            tmp.write(arquivo.read())
            caminho_local = tmp.name

        # Upload para o Drive
        novo_arquivo = drive.CreateFile({
            'title': nome_arquivo_drive,
            'parents': [{'id': pasta_contratos_id}]
        })
        novo_arquivo.SetContentFile(caminho_local)
        novo_arquivo.Upload()

        # Atualizar base
        df = carregar_base_contratos()
        novo = {
            "id_contrato": id_contrato,
            "nome_arquivo": nome_amigavel,
            "tipo": arquivo.name.split(".")[-1],
            "idioma": idioma,
            "instituicao_financeira": instituicao,
            "data_upload": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "usuario_upload": user_email,
            "clausulas": "",
            "user_email": user_email
        }
        df = pd.concat([df, pd.DataFrame([novo])], ignore_index=True)
        salvar_base_contratos(df)

        st.success("✅ Contrato enviado e registrado com sucesso.")

def carregar_base_contratos():
    drive = conectar_drive()
    pasta_bases_id = obter_id_pasta("bases", parent_id=obter_id_pasta("Tesouraria"))
    if not pasta_bases_id:
        st.error("Pasta 'bases' não encontrada.")
        return pd.DataFrame()

    arquivos = drive.ListFile({
        'q': f"'{pasta_bases_id}' in parents and title = 'base_contratos.xlsx' and trashed = false"
    }).GetList()

    if not arquivos:
        return pd.DataFrame(columns=[
            "id_contrato", "nome_arquivo", "data_upload", "usuario_upload",
            "clausulas", "instituicao_financeira", "tipo", "idioma", "user_email"
        ])

    caminho_temp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx").name
    arquivos[0].GetContentFile(caminho_temp)
    df = pd.read_excel(caminho_temp)

    # Garantir que a coluna de cláusulas seja tratada como string
    if "clausulas" in df.columns:
        df["clausulas"] = df["clausulas"].astype(str)

    return df

def salvar_base_contratos(df):
    drive = conectar_drive()
    pasta_bases_id = obter_id_pasta("bases", parent_id=obter_id_pasta("Tesouraria"))
    pasta_backups_id = obter_id_pasta("backups", parent_id=obter_id_pasta("Tesouraria"))

    caminho_temp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx").name
    df.to_excel(caminho_temp, index=False)

    arquivos = drive.ListFile({
        'q': f"'{pasta_bases_id}' in parents and title = 'base_contratos.xlsx' and trashed = false"
    }).GetList()

    if arquivos:
        arquivo = arquivos[0]
    else:
        arquivo = drive.CreateFile({
            'title': 'base_contratos.xlsx',
            'parents': [{'id': pasta_bases_id}]
        })

    arquivo.SetContentFile(caminho_temp)
    arquivo.Upload()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = drive.CreateFile({
        'title': f'base_contratos__{timestamp}.xlsx',
        'parents': [{'id': pasta_backups_id}]
    })
    backup.SetContentFile(caminho_temp)
    backup.Upload()

# =========================
# Manipulação de contratos
# =========================
def obter_contratos_disponiveis():
    drive = conectar_drive()
    pasta_id = obter_id_pasta("contratos", parent_id=obter_id_pasta("Tesouraria"))
    arquivos = drive.ListFile({'q': f"'{pasta_id}' in parents and trashed = false"}).GetList()
    return [(arq['title'], arq['id']) for arq in arquivos]

def docx_para_pdf_temporario(caminho_docx):
    caminho_temp_dir = tempfile.mkdtemp()
    caminho_pdf = os.path.join(caminho_temp_dir, "convertido.pdf")
    convert(caminho_docx, caminho_pdf)
    return caminho_pdf

def extrair_com_document_ai_paginas(caminho_pdf, max_paginas=15):
    from google.cloud import documentai_v1 as documentai
    credentials = service_account.Credentials.from_service_account_info(st.secrets["gcp_docai"])
    project_id = st.secrets["gcp_docai"]["project_id"]
    processor_id = st.secrets["gcp_docai"]["processor_id"]
    location = "us"

    client = documentai.DocumentProcessorServiceClient(credentials=credentials)
    name = f"projects/{project_id}/locations/{location}/processors/{processor_id}"

    leitor = PdfReader(caminho_pdf)
    total_paginas = len(leitor.pages)
    texto_total = ""

    for i in range(0, total_paginas, max_paginas):
        escritor = PdfWriter()
        for j in range(i, min(i + max_paginas, total_paginas)):
            escritor.add_page(leitor.pages[j])

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_pdf:
            escritor.write(temp_pdf)
            temp_pdf.flush()
            with open(temp_pdf.name, "rb") as f:
                document = {"content": f.read(), "mime_type": "application/pdf"}

        request = {"name": name, "raw_document": document}
        result = client.process_document(request=request)
        texto_total += result.document.text + "\n"

    return texto_total.strip()

def executar_document_ai(caminho_pdf):
    credentials = service_account.Credentials.from_service_account_info(st.secrets["gcp_docai"])
    project_id = st.secrets["gcp_docai"]["project_id"]
    location = "us"
    processor_id = st.secrets["gcp_docai"]["processor_id"]

    client = documentai.DocumentUnderstandingServiceClient(credentials=credentials)
    name = f"projects/{project_id}/locations/{location}/processors/{processor_id}"

    with open(caminho_pdf, "rb") as f:
        document = {"content": f.read(), "mime_type": "application/pdf"}

    request = {"name": name, "raw_document": document}
    result = client.process_document(request=request)
    return result.document.text

# =========================
# Extração de cláusulas via IA
# =========================

def carregar_texto_contrato_drive(titulo_arquivo, arquivo_id):
    """
    Lê o arquivo armazenado no Google Drive (PDF) e extrai o texto completo via Document AI.
    """
    drive = conectar_drive()
    caminho_temp = tempfile.NamedTemporaryFile(delete=False).name
    drive.CreateFile({'id': arquivo_id}).GetContentFile(caminho_temp)

    try:
        if titulo_arquivo.lower().endswith(".docx"):
            caminho_pdf = docx_para_pdf_temporario(caminho_temp)
            texto = extrair_com_document_ai_paginas(caminho_pdf)
            os.remove(caminho_pdf)  # limpa PDF temporário
        elif titulo_arquivo.lower().endswith(".pdf"):
            texto = extrair_com_document_ai_paginas(caminho_temp)
        else:
            st.error("❌ Formato de arquivo não suportado. Use .docx ou .pdf.")
            return ""
    except Exception as e:
        st.error(f"❌ Erro ao extrair o contrato: {e}")
        return ""

    return texto

def aba_validacao_clausulas():
    st.title("🧾 Validação das Cláusulas Contratuais")

    contratos = obter_contratos_disponiveis()
    if not contratos:
        st.warning("Nenhum contrato disponível.")
        return

    nomes_arquivos = [titulo for titulo, _ in contratos]
    contrato_selecionado = st.selectbox("Selecione o contrato para análise:", nomes_arquivos)

    if not contrato_selecionado:
        st.stop()

    titulo_arquivo, id_arquivo = next(item for item in contratos if item[0] == contrato_selecionado)

    # Extrai id_contrato do nome do arquivo (antes do primeiro "_")
    id_contrato = titulo_arquivo.split("_")[0]

    st.markdown("### 📄 Visualização do conteúdo do contrato")
    texto = carregar_texto_contrato_drive(titulo_arquivo, id_arquivo)

    with st.expander("Visualizar texto completo extraído do contrato"):
        st.text_area("Conteúdo extraído", texto, height=400)

    if st.button("🧠 Extrair Cláusulas com IA"):
        df_clausulas = extrair_clausulas_robusto(texto)
        st.session_state["df_clausulas_extraidas"] = df_clausulas
        st.success("✅ Cláusulas extraídas com sucesso!")

    if "df_clausulas_extraidas" in st.session_state:
        st.markdown("### ✍️ Revisar Cláusulas Extraídas")
        df_editado = st.data_editor(
            st.session_state["df_clausulas_extraidas"],
            num_rows="dynamic",
            use_container_width=True,
            key="editor_clausulas"
        )

        if st.button("✅ Validar cláusulas e salvar"):
            sucesso = salvar_clausulas_validadas(df_editado, id_contrato)
            if sucesso:
                st.success("📦 Cláusulas validadas e salvas com sucesso.")
            else:
                st.error("❌ Contrato não encontrado na base para atualização.")

def dividir_em_chunks_simples(texto, max_chars=7000):
    paragrafos = texto.split("\n\n")
    chunks = []
    atual = ""

    for p in paragrafos:
        if len(atual) + len(p) + 2 <= max_chars:
            atual += p + "\n\n"
        else:
            chunks.append(atual.strip())
            atual = p + "\n\n"
    if atual:
        chunks.append(atual.strip())

    return chunks

def gerar_prompt_com_exemplos(texto_chunk):
    exemplos = """
Exemplos de cláusulas extraídas corretamente:

This Agreement, including its schedules and annexes, shall be interpreted according to the following definitions: “Borrower” refers to PRIO S.A., “Facility” means the total loan commitment...

The applicable interest rate shall be determined as the sum of the Margin (3.5%) and the Base Rate (SOFR), revised quarterly in accordance with market conditions.

The Borrower agrees to repay the Facility in 8 equal semi-annual installments beginning 6 months after the disbursement date.

The following shall constitute an Event of Default: (a) failure to pay any amount due; (b) breach of any covenant...

This Agreement shall be governed by and construed in accordance with the laws of England and Wales.
"""

    prompt = f"""
Você é um advogado especializado em contratos de crédito internacional.

Extraia todas as cláusulas do texto a seguir. Cada cláusula deve conter apenas:

- Texto completo da cláusula

Não inclua o seguinte:

- Numeração (1., 2., 3.1, etc.)
- Título da cláusula (se houver)

Não inclua resumos nem comentários. Apresente a lista como nos exemplos abaixo.

{exemplos}

Agora processe o seguinte trecho:

\"\"\"{texto_chunk}\"\"\"
"""
    return prompt.strip()

def extrair_clausulas_robusto(texto):
    client = OpenAI(api_key=st.secrets["openai"]["api_key"])
    st.info("🔍 Analisando o contrato...")
    partes = dividir_em_chunks_simples(texto)
    clausulas_total = []

    for i, chunk in enumerate(partes):
        with st.spinner(f"🧠 Analisando trecho {i+1}/{len(partes)}..."):
            prompt = gerar_prompt_com_exemplos(chunk)
            try:
                resposta = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {"role": "system", "content": "Você é um especialista jurídico com muita experiência e domínios em cláusulas de contratos de dívida."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0,
                    max_tokens=4096
                )
                saida = resposta.choices[0].message.content.strip()
                linhas = [l.strip() for l in saida.split("\n") if l.strip()]
                clausulas_total.extend(linhas)
            except Exception as e:
                clausulas_total.append(f"[Erro no chunk {i+1}]: {e}")

    df = pd.DataFrame(clausulas_total, columns=["clausula"])
    return df

# =========================
# Salvar cláusulas extraídas
# =========================
def salvar_clausulas_validadas(df_clausulas, id_contrato):
    df = carregar_base_contratos()
    if df.empty:
        return False

    # Garante que cláusulas estejam como string
    df_clausulas["clausula"] = df_clausulas["clausula"].astype(str)
    clausulas_txt = "\n".join(df_clausulas["clausula"].tolist())

    # Verifica se o contrato existe
    idx = df[df["id_contrato"] == id_contrato].index
    if len(idx) == 0:
        return False

    # Atualiza a cláusula na linha existente
    df.loc[idx[0], "clausulas"] = clausulas_txt
    salvar_base_contratos(df)
    return True
# =========================
# 📌 Aba: Análise Automática das Cláusulas
# =========================
def carregar_clausulas_validadas():
    df = carregar_base_contratos()
    if df.empty:
        return pd.DataFrame(columns=["nome_arquivo", "clausula"])

    clausulas_expandidas = []

    for _, row in df.iterrows():
        texto = row.get("clausulas", "")
        if not isinstance(texto, str) or not texto.strip():
            continue
        clausulas = [c.strip() for c in texto.split("\n") if c.strip()]
        for c in clausulas:
            clausulas_expandidas.append({
                "nome_arquivo": row["nome_arquivo"],
                "clausula": c
            })

    return pd.DataFrame(clausulas_expandidas)

def aba_analise_automatica():
    st.title("🧠 Análise Automática das Cláusulas")
    df = carregar_base_contratos()
    df_contrato = carregar_clausulas_analisadas()
    
    contratos_disponiveis = df["nome_arquivo"].dropna().unique().tolist()
    contrato_escolhido = st.selectbox("Selecione o contrato:", contratos_disponiveis)

    if (df_contrato is None or df_contrato.empty) or (not contratos_disponiveis):
        st.warning("Não há cláusulas validadas disponíveis.")
    else:
        df_contrato = df_contrato[df_contrato['nome_arquivo']==contrato_escolhido]
        st.dataframe(df_contrato, use_container_width=True)
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df_contrato.to_excel(writer, index=False)
        st.download_button("📥 Baixar Análises", data=buffer.getvalue(), file_name="clausulas_analisadas.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    if st.button("✅ Iniciar Análise Automática"):
        # Carregar cláusulas validadas
        if df.empty or "clausulas" not in df.columns:
            st.warning("Não há cláusulas validadas disponíveis.")
            return
    
        if not contrato_escolhido:
            st.stop()
    
        df_clausulas = df[df["nome_arquivo"] == contrato_escolhido].copy()
        clausulas = df_clausulas.iloc[0]["clausulas"].split("\n")
        clausulas = [c.strip() for c in clausulas if c.strip()]
    
        # Carregar índices financeiros
        drive = conectar_drive()
        pasta_bases_id = obter_id_pasta("bases", parent_id=obter_id_pasta("Tesouraria"))
        arquivos = drive.ListFile({'q': f"'{pasta_bases_id}' in parents and title = 'empresa_referencia_PRIO.xlsx' and trashed = false"}).GetList()
        if not arquivos:
            st.error("Base de índices financeiros 'empresa_referencia_PRIO.xlsx' não encontrada.")
            return
    
        caminho_indices = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx").name
        arquivos[0].GetContentFile(caminho_indices)
        df_indices = pd.read_excel(caminho_indices)
    
        # Criar cliente da OpenAI
        client = OpenAI(api_key=st.secrets["openai"]["api_key"])
    
        resultados = []
        st.info("🔍 Iniciando análise com os especialistas jurídico e financeiro...")
    
        for i, clausula in enumerate(clausulas):
            with st.spinner(f"Processando cláusula {i+1}/{len(clausulas)}..."):
    
                # 🔹 Agente Jurídico
                prompt_juridico = f"""
    Você é um advogado especialista em contratos de dívida.
    Analise a cláusula abaixo e diga se está Conforme ou se Necessita Revisão. Você somente pode escolher uma alternativa.
    Sempre inicie sua resposta com exatamente as palavras Conforme ou Necessita Revisão.
    Justifique de forma objetiva com base jurídica.
    
    Cláusula:
    \"\"\"{clausula}\"\"\"
    """
                resposta_juridico = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{"role": "user", "content": prompt_juridico}],
                    temperature=0,
                    max_tokens=1000
                ).choices[0].message.content.strip()
    
                # 🔹 Agente Financeiro
                texto_indices = df_indices.to_string(index=False)
                prompt_financeiro = f"""
    Você é um especialista financeiro com foco em contratos de captação de dívida. Abaixo estão os índices financeiros da empresa PRIO:
    
    {texto_indices}
    
    Analise a cláusula a seguir e diga se ela está financeiramente Conforme ou se Necessita Revisão. Você somente pode escolher uma alternativa.
    Sempre inicie sua resposta com exatamente as palavras Conforme ou Necessita Revisão.
    Caso a cláusula não aborde nenhuma condicionante financeira, diga que está Conforme e no motivo informe objetivamente que não foram identificados
    índices financeiros para análise.
    Justifique com base nos dados da empresa e benchmarking de mercado para casos semelhantes.
    
    Cláusula:
    \"\"\"{clausula}\"\"\"
    """
                resposta_financeiro = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{"role": "user", "content": prompt_financeiro}],
                    temperature=0,
                    max_tokens=1000
                ).choices[0].message.content.strip()
    
                # 🔹 Agente Supervisor
                prompt_supervisor = f"""
    Você é o supervisor responsável pela revisão final. 
    Abaixo está a cláusula, a análise do agente jurídico e a análise do agente financeiro. 
    Revise cada uma delas e diga se Concorda ou Não Concorda, e explique brevemente o motivo.
    Sempre inicie sua resposta com exatamente as palavras Concorda ou Não Concorda.
    
    Cláusula:
    \"\"\"{clausula}\"\"\"
    
    Análise Jurídica:
    {resposta_juridico}
    
    Análise Financeira:
    {resposta_financeiro}
    """
                resposta_supervisor = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{"role": "user", "content": prompt_supervisor}],
                    temperature=0,
                    max_tokens=1000
                ).choices[0].message.content.strip()
    
                # Coletar resultados
                resultados.append({
                    "nome_arquivo": contrato_escolhido,
                    "clausula": clausula,
                    "revisao_juridico": "Conforme" if "Conforme" in resposta_juridico else "Necessita Revisão",
                    "motivo_juridico": resposta_juridico,
                    "revisao_financeiro": "Conforme" if "Conforme" in resposta_financeiro else "Necessita Revisão",
                    "motivo_financeiro": resposta_financeiro,
                    "revisao_sup": "Concorda" if "Concorda" in resposta_supervisor else "Não Concorda",
                    "motivo_sup": resposta_supervisor,
                })
    
        # Montar DataFrame final
        df_resultado = pd.DataFrame(resultados)
        st.success("✅ Análise automática concluída.")
        st.dataframe(df_resultado, use_container_width=True)

        #Salvar Clausulas
        if st.button("Desejar Salvar ?"):
            salvar_clausulas_validadas_usuario(df_resultado)
    
        # Exportar
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df_resultado.to_excel(writer, index=False)
        st.download_button("📥 Baixar Análises", data=buffer.getvalue(), file_name="clausulas_analisadas.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

def carregar_clausulas_analisadas():
    drive = conectar_drive()
    pasta_bases_id = obter_id_pasta("bases", parent_id=obter_id_pasta("Tesouraria"))

    arquivos = drive.ListFile({
        'q': f"'{pasta_bases_id}' in parents and title = 'clausulas_analisadas.xlsx' and trashed = false"
    }).GetList()

    if not arquivos:
        st.warning("❌ Base de cláusulas analisadas não encontrada.")
        return pd.DataFrame(columns=[
            "nome_arquivo", "clausula",
            "analise_juridico_status", "analise_juridico_motivo",
            "analise_financeiro_status", "analise_financeiro_motivo",
            "revisao_juridico_status", "revisao_juridico_motivo",
            "revisao_financeiro_status", "revisao_financeiro_motivo"
        ])

    caminho_temp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx").name
    arquivos[0].GetContentFile(caminho_temp)
    return pd.read_excel(caminho_temp)
    
# =========================
# 📌 Aba: Revisão Final
# =========================

def aba_revisao_final():
    st.title("🧑‍⚖️ Revisão Final do Usuário - Cláusulas Contratuais")
    
    df = carregar_clausulas_validadas()
    with st.spinner("Carregando cláusulas analisadas..."):
        df = carregar_clausulas_analisadas()
    if df.empty:
        st.warning("Nenhuma cláusula analisada disponível.")

    contratos_disponiveis = df["nome_arquivo"].unique().tolist()
    contrato = st.selectbox("Selecione o contrato:", contratos_disponiveis)

    df_filtrado = df[df["nome_arquivo"] == contrato].copy()

    st.markdown("### 📝 Revisão Final do Usuário")

    # Inicializar colunas editáveis, se necessário
    for col in ["user_revisao", "motivo_user"]:
        if col not in df_filtrado.columns:
            df_filtrado[col] = ""

    df_editado = st.data_editor(
        df_filtrado,
        use_container_width=True,
        num_rows="dynamic",
        column_order=[
            "clausula",
            "revisao_juridico", "motivo_juridico",
            "revisao_financeiro", "motivo_financeiro",
            "revisao_sup", "motivo_sup",
            "user_revisao", "motivo_user"
        ],
        disabled=[
            "nome_arquivo", "clausula",
            "revisao_juridico", "motivo_juridico",
            "revisao_financeiro", "motivo_financeiro",
            "revisao_sup", "motivo_sup"
        ],
        key="revisao_final_editor"
    )

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df_editado.to_excel(writer, index=False)
    st.download_button("📥 Baixar Análises", data=buffer.getvalue(), file_name="clausulas_validadas.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
   
    if st.button("✅ Salvar revisão final do usuário"):
        salvar_clausulas_validadas_usuario(df_editado)
        st.success("✅ Revisão final do usuário salva com sucesso!")
        
def salvar_clausulas_validadas_usuario(df):
    drive = conectar_drive()
    pasta_bases_id = obter_id_pasta("bases", parent_id=obter_id_pasta("Tesouraria"))
    pasta_backups_id = obter_id_pasta("backups", parent_id=obter_id_pasta("Tesouraria"))

    nome_arquivo = "clausulas_validadas.xlsx"
    caminho_temp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx").name
    df.to_excel(caminho_temp, index=False)

    arquivos = drive.ListFile({
        'q': f"'{pasta_bases_id}' in parents and title = '{nome_arquivo}' and trashed = false"
    }).GetList()

    if arquivos:
        arquivo = arquivos[0]
    else:
        arquivo = drive.CreateFile({
            'title': nome_arquivo,
            'parents': [{'id': pasta_bases_id}]
        })

    arquivo.SetContentFile(caminho_temp)
    arquivo.Upload()

    # Backup
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = drive.CreateFile({
        'title': f'clausulas_validadas__{timestamp}.xlsx',
        'parents': [{'id': pasta_backups_id}]
    })
    backup.SetContentFile(caminho_temp)
    backup.Upload()

def carregar_clausulas_validadas():
    drive = conectar_drive()
    pasta_bases_id = obter_id_pasta("bases", parent_id=obter_id_pasta("Tesouraria"))

    arquivos = drive.ListFile({
        'q': f"'{pasta_bases_id}' in parents and title = 'clausulas_validadas.xlsx' and trashed = false"
    }).GetList()

    if not arquivos:
        st.warning("❌ Base de cláusulas validadas não encontrada.")
        return pd.DataFrame(columns=[
            "nome_arquivo", "clausula",
            "analise_juridico_status", "analise_juridico_motivo",
            "analise_financeiro_status", "analise_financeiro_motivo",
            "revisao_juridico_status", "revisao_juridico_motivo",
            "revisao_financeiro_status", "revisao_financeiro_motivo",
            "user_revisao", "motivo_user"
        ])

    caminho_temp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx").name
    arquivos[0].GetContentFile(caminho_temp)
    return pd.read_excel(caminho_temp)

# =========================
# 📌 Aba: Índices PRIO
# =========================

def aba_indices_prio():
    st.title("📊 Índices Financeiros da PRIO")

    drive = conectar_drive()
    pasta_bases_id = obter_id_pasta("bases", parent_id=obter_id_pasta("Tesouraria"))
    pasta_backups_id = obter_id_pasta("backups", parent_id=obter_id_pasta("Tesouraria"))

    nome_arquivo = "empresa_referencia_PRIO.xlsx"

    # Verificar se o arquivo existe no Drive
    arquivos = drive.ListFile({
        'q': f"'{pasta_bases_id}' in parents and title = '{nome_arquivo}' and trashed = false"
    }).GetList()

    if not arquivos:
        st.warning("Base 'empresa_referencia_PRIO.xlsx' não encontrada. Será criada uma nova base.")
        df_indices = pd.DataFrame(columns=["EBITDA", "Mrg EBITDA", "Res Fin", "Dívida", "Lucro Líq", "Caixa"])
    else:
        caminho_temp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx").name
        arquivos[0].GetContentFile(caminho_temp)
        df_indices = pd.read_excel(caminho_temp)

    st.markdown("### ✍️ Editar Índices")
    df_editado = st.data_editor(
        df_indices,
        num_rows="dynamic",
        use_container_width=True,
        key="editor_indices_prio"
    )

    if st.button("💾 Salvar Índices"):
        caminho_temp_salvar = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx").name
        df_editado.to_excel(caminho_temp_salvar, index=False)

        # Atualizar ou criar o arquivo no Drive
        if arquivos:
            arquivo = arquivos[0]
        else:
            arquivo = drive.CreateFile({
                'title': nome_arquivo,
                'parents': [{'id': pasta_bases_id}]
            })

        arquivo.SetContentFile(caminho_temp_salvar)
        arquivo.Upload()

        # Criar backup
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = drive.CreateFile({
            'title': f"empresa_referencia_PRIO__{timestamp}.xlsx",
            'parents': [{'id': pasta_backups_id}]
        })
        backup.SetContentFile(caminho_temp_salvar)
        backup.Upload()

        st.success("✅ Índices salvos e backup criado com sucesso!")

# =========================
# 📌 Aba: Relatório Gerencial
# =========================

def aba_relatorios_gerenciais():
    st.title("📘 Relatório Gerencial - Ações Prioritárias por Contrato")

    df = carregar_clausulas_validadas()
    if df.empty:
        st.warning("Base de cláusulas validadas está vazia.")
        return

    contratos = df["nome_arquivo"].unique().tolist()
    contrato_selecionado = st.selectbox("Selecione o contrato para análise:", contratos)

    if not contrato_selecionado:
        return

    if st.button("🔍 Executar análise"):
        clausulas_contrato = df[df["nome_arquivo"] == contrato_selecionado]["clausula"].tolist()

        texto_clausulas = "\n\n".join(clausulas_contrato)
        prompt = f"""
Você é um especialista jurídico em gestão contratual e compliance.

Com base nas cláusulas abaixo, elenque de forma objetiva e por ordem de significância as principais ações que o usuário deve realizar para garantir a segurança jurídica do contrato.

Sua resposta deve conter no máximo 1 página e apresentar as ações com títulos curtos, seguidos de explicações objetivas (1 parágrafo por ação). Seja direto, técnico e evite repetições.

Mantenha sempre uma breve referência à cláusula que precisa ser revisada para assegurar a conformidade.

Cláusulas do contrato:
\"\"\"{texto_clausulas}\"\"\"
"""

        client = OpenAI(api_key=st.secrets["openai"]["api_key"])
        with st.spinner("Gerando análise..."):
            resposta = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "Você é um consultor jurídico especialista em contratos de captação de dívida internacionais."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0,
                max_tokens=2048
            )

        analise_final = resposta.choices[0].message.content.strip()
        st.markdown("### ✅ Análise Gerada:")
        st.markdown(analise_final)

        # Exportação em Word
        buffer = BytesIO()
        doc = Document()
        doc.add_heading(f"Relatório Gerencial - {contrato_selecionado}", level=1)
        for par in analise_final.split("\n"):
            if par.strip():
                doc.add_paragraph(par.strip())
        doc.save(buffer)
        st.download_button(
            label="📥 Baixar Análise em Word",
            data=buffer.getvalue(),
            file_name=f"relatorio_gerencial_{contrato_selecionado}.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )

# -----------------------------
# Renderização de conteúdo por página
# -----------------------------
if pagina == "📂 Upload do Contrato":
    aba_upload_contrato(user_email=st.session_state.username)
    
elif pagina == "🧾 Validação das Cláusulas":
    aba_validacao_clausulas()
    
elif pagina == "🔍 Análise Automática":
    aba_analise_automatica()
    
elif pagina == "🧑‍⚖️ Revisão Final":
    aba_revisao_final()
    
elif pagina == "📊 Índices PRIO":
    aba_indices_prio()
    
elif pagina == "📘 Relatórios Gerenciais":
    aba_relatorios_gerenciais()
