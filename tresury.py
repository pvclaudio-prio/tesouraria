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
    "📘 Relatórios Gerenciais",
    "📁 Base de Cláusulas Padrão"
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

    st.markdown("Faça upload de um contrato em `.pdf` ou `.docx` e preencha os dados abaixo.")

    arquivo = st.file_uploader("Selecione o contrato", type=["pdf", "docx"])
    instituicao = st.text_input("Instituição Financeira")
    idioma = st.selectbox("Idioma do Contrato", ["pt", "en"])

    if st.button("📤 Enviar Contrato"):
        if not arquivo or not instituicao:
            st.warning("Por favor, preencha todos os campos e envie um arquivo.")
            return

        drive = conectar_drive()
        pasta_contratos_id = obter_id_pasta("contratos", parent_id=obter_id_pasta("Tesouraria"))

        id_contrato = str(uuid.uuid4())
        nome_final = f"{id_contrato}_{arquivo.name}"

        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{arquivo.name.split('.')[-1]}") as tmp:
            tmp.write(arquivo.read())
            caminho_local = tmp.name

        novo_arquivo = drive.CreateFile({
            'title': nome_final,
            'parents': [{'id': pasta_contratos_id}]
        })
        novo_arquivo.SetContentFile(caminho_local)
        novo_arquivo.Upload()

        df = carregar_base_contratos()
        novo = {
            "id_contrato": id_contrato,
            "nome_arquivo": nome_final,
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
    return pd.read_excel(caminho_temp)

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

def carregar_texto_contrato(titulo_arquivo, arquivo_id):
    drive = conectar_drive()
    caminho_temp = tempfile.NamedTemporaryFile(delete=False).name
    drive.CreateFile({'id': arquivo_id}).GetContentFile(caminho_temp)

    if titulo_arquivo.lower().endswith(".docx"):
        doc = docx.Document(caminho_temp)
        texto = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
    elif titulo_arquivo.lower().endswith(".pdf"):
        texto = executar_document_ai(caminho_temp)
    else:
        st.error("Formato de arquivo não suportado.")
        texto = ""
    return texto

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

def aba_validacao_clausulas():
    st.title("🧾 Validação das Cláusulas")

    contratos = obter_contratos_disponiveis()
    if not contratos:
        st.warning("Nenhum contrato disponível.")
        return

    opcoes = [f"{titulo}" for titulo, _ in contratos]
    contrato_selecionado = st.selectbox("Selecione o contrato:", opcoes)
    titulo_arquivo, id_arquivo = next(x for x in contratos if x[0] == contrato_selecionado)

    texto = carregar_texto_contrato(titulo_arquivo, id_arquivo)
    if not texto:
        st.stop()

    with st.expander("📄 Visualizar texto extraído do contrato"):
        st.text_area("Texto do Contrato", texto, height=400)

    if st.button("🧠 Extrair Cláusulas com IA"):
        df_clausulas = extrair_clausulas_com_agente(texto)
        st.session_state.df_clausulas_extraidas = df_clausulas
        st.success("✅ Cláusulas extraídas com sucesso!")

    if "df_clausulas_extraidas" in st.session_state:
        st.markdown("### ✏️ Revise as cláusulas extraídas:")
        df_editado = st.data_editor(
            st.session_state.df_clausulas_extraidas,
            num_rows="dynamic",
            use_container_width=True,
            key="editor_clausulas"
        )

        instituicao = st.text_input("Instituição Financeira")
        if st.button("✅ Validar cláusulas e iniciar análise"):
            id_contrato = str(uuid.uuid4())
            salvar_clausulas_validadas(df_editado, id_contrato, instituicao, st.session_state.username)
            st.success("✅ Cláusulas salvas com sucesso!")

def extrair_texto_docx(caminho_arquivo):
    doc = docx.Document(caminho_arquivo)
    texto = "\n".join([p.text.strip() for p in doc.paragraphs if p.text.strip()])
    return texto

def extrair_texto_pdf_document_ai(caminho_pdf):
    credentials = service_account.Credentials.from_service_account_info(st.secrets["gcp_docai"])
    project_id = st.secrets["gcp_docai"]["project_id"]
    processor_id = st.secrets["gcp_docai"]["processor_id"]
    location = "us"

    client = documentai.DocumentUnderstandingServiceClient(credentials=credentials)
    name = f"projects/{project_id}/locations/{location}/processors/{processor_id}"

    with open(caminho_pdf, "rb") as f:
        raw_document = {"content": f.read(), "mime_type": "application/pdf"}
    request = {"name": name, "raw_document": raw_document}
    result = client.process_document(request=request)

    return result.document.text

def carregar_texto_contrato(titulo_arquivo, arquivo_id):
    drive = conectar_drive()
    caminho_temp = tempfile.NamedTemporaryFile(delete=False).name
    drive.CreateFile({'id': arquivo_id}).GetContentFile(caminho_temp)

    if titulo_arquivo.lower().endswith(".docx"):
        doc = docx.Document(caminho_temp)
        texto = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
    elif titulo_arquivo.lower().endswith(".pdf"):
        texto = executar_document_ai(caminho_temp)
    else:
        st.error("Formato de arquivo não suportado.")
        texto = ""
    return texto

def dividir_por_secoes_numeradas(texto):
    """
    Divide o texto contratual com base em seções numeradas (ex: 1., 2.1, 3.4.5).
    Retorna uma lista com blocos de texto representando cada seção.
    """
    if not isinstance(texto, str):
        st.error("❌ O texto fornecido não é uma string válida.")
        return []

    padrao = r"(?=\n?\d{1,2}(\.\d{1,2})*\s)"
    secoes = re.split(padrao, texto)

    # Garantir que todos os elementos sejam string e significativos
    secoes_limpos = []
    for s in secoes:
        try:
            s_clean = str(s).strip()
            if len(s_clean) > 30:
                secoes_limpos.append(s_clean)
        except:
            continue

    return secoes_limpos

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

1. Definitions
This Agreement, including its schedules and annexes, shall be interpreted according to the following definitions: “Borrower” refers to PRIO S.A., “Facility” means the total loan commitment...

2. Interest Rate
The applicable interest rate shall be determined as the sum of the Margin (3.5%) and the Base Rate (SOFR), revised quarterly in accordance with market conditions.

3. Repayment Terms
The Borrower agrees to repay the Facility in 8 equal semi-annual installments beginning 6 months after the disbursement date.

4. Events of Default
The following shall constitute an Event of Default: (a) failure to pay any amount due; (b) breach of any covenant...

5. Governing Law
This Agreement shall be governed by and construed in accordance with the laws of England and Wales.
"""

    prompt = f"""
Você é um advogado especializado em contratos de crédito internacional.

Extraia todas as cláusulas do texto a seguir. Cada cláusula deve conter:

- Numeração (1., 2., 3.1, etc.)
- Título da cláusula (se houver)
- Texto completo da cláusula

Não inclua resumos nem comentários. Apresente a lista como nos exemplos abaixo.

{exemplos}

Agora processe o seguinte trecho:

\"\"\"{texto_chunk}\"\"\"
"""
    return prompt.strip()

from openai import OpenAI

def extrair_clausulas_robusto(texto):
    client = OpenAI(api_key=st.secrets["openai"]["api_key"])
    st.info("🔍 Dividindo contrato em blocos para análise...")
    partes = dividir_em_chunks_simples(texto)
    clausulas_total = []

    for i, chunk in enumerate(partes):
        with st.spinner(f"🧠 Analisando trecho {i+1}/{len(partes)}..."):
            prompt = gerar_prompt_com_exemplos(chunk)
            try:
                resposta = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {"role": "system", "content": "Você é um assistente jurídico especializado em cláusulas de contratos de dívida."},
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

    st.markdown("### 📄 Visualização do conteúdo do contrato")
    texto = carregar_texto_contrato(titulo_arquivo, id_arquivo)

    with st.expander("Visualizar texto completo extraído do contrato"):
        st.text_area("Conteúdo extraído", texto, height=400)

    if st.button("🧠 Extrair Cláusulas com IA"):
        with st.spinner("Executando agente jurídico..."):
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

        instituicao = st.text_input("Instituição Financeira")
        if st.button("✅ Validar cláusulas e salvar"):
            id_contrato = str(uuid.uuid4())
            salvar_clausulas_validadas(df_editado, id_contrato, instituicao, st.session_state.username)
            st.success("📦 Cláusulas validadas e salvas com sucesso.")

# =========================
# Salvar cláusulas extraídas
# =========================
def salvar_clausulas_validadas(df_clausulas, id_contrato, instituicao, user_email):
    df = carregar_base_contratos()
    clausulas_txt = "\n".join(df_clausulas["clausula"].tolist())
    nova_linha = {
        "id_contrato": id_contrato,
        "nome_arquivo": "-",
        "data_upload": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "usuario_upload": user_email,
        "clausulas": clausulas_txt,
        "instituicao_financeira": instituicao,
        "tipo": "-",
        "idioma": "pt",
        "user_email": user_email
    }
    df = pd.concat([df, pd.DataFrame([nova_linha])], ignore_index=True)
    salvar_base_contratos(df)

# -----------------------------
# Renderização de conteúdo por página
# -----------------------------
if pagina == "📂 Upload do Contrato":
    aba_upload_contrato(user_email=st.session_state.username)
    
elif pagina == "🧾 Validação das Cláusulas":
    aba_validacao_clausulas()
    
elif pagina == "🔍 Análise Automática":
    st.info("Execução dos agentes financeiros e jurídicos.")
    
elif pagina == "🧑‍⚖️ Revisão Final":
    st.info("Revisão final das cláusulas com input do usuário.")
    
elif pagina == "📊 Índices PRIO":
    aba_indices_prio()
    
elif pagina == "📘 Relatórios Gerenciais":
    st.info("Geração de relatórios estratégicos com IA.")
    
elif pagina == "📁 Base de Cláusulas Padrão":
    st.info("Cláusulas padrão utilizadas pelos agentes.")

