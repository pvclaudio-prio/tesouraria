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

def extrair_clausulas_robusto(texto):
    st.info("🔍 Iniciando extração das cláusulas com análise por seção numerada...")
    openai.api_key = st.secrets["openai"]["api_key"]

    secoes = dividir_por_secoes_numeradas(texto)
    clausulas_extraidas = []

    prompt_base = """
Você é um advogado especialista em contratos de dívida internacionais.

Sua tarefa é identificar cláusulas contratuais completas — aquelas que representam obrigações, condições, definições, garantias ou penalidades contratuais.

Leia o texto abaixo e extraia todas as cláusulas jurídicas encontradas. Cada cláusula deve começar com sua numeração (ex: 1., 2.1, 3.1.4), seguida do título (se existir) e o texto completo da cláusula.

TEXTO DO CONTRATO:
\"\"\"{secao}\"\"\"

Responda apenas com a lista de cláusulas. Não resuma nem acrescente comentários.
"""

    for i, secao in enumerate(secoes):
        with st.spinner(f"🔎 Processando seção {i+1} de {len(secoes)}..."):
            prompt = prompt_base.format(secao=secao)
            try:
                resposta = openai.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {"role": "system", "content": "Você é um advogado especialista em leitura contratual."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.1,
                    max_tokens=2048
                )
                resultado = resposta.choices[0].message.content.strip()
                linhas = [linha.strip() for linha in resultado.split("\n") if linha.strip()]
                clausulas_extraidas.extend(linhas)
                time.sleep(1)  # rate limit protection
            except Exception as e:
                clausulas_extraidas.append(f"[Erro na seção {i+1}]: {e}")

    # Reorganização e limpeza
    clausulas_final = []
    for idx, linha in enumerate(clausulas_extraidas, start=1):
        texto_limpo = re.sub(r"^\d+(\.\d+)*\s*", "", linha)
        clausulas_final.append(f"{idx}. {texto_limpo}")

    df = pd.DataFrame(clausulas_final, columns=["clausula"])
    return df

def aba_validacao_clausulas():
    st.title("🧾 Validação das Cláusulas")

    df_base = carregar_base_contratos()
    if df_base.empty:
        st.warning("Nenhum contrato disponível.")
        return

    # Seleciona o contrato
    contrato_selecionado = st.selectbox("Selecione o contrato:", df_base["nome_arquivo"].unique())
    linha = df_base[df_base["nome_arquivo"] == contrato_selecionado].iloc[0]
    titulo_arquivo = linha["nome_arquivo"]
    id_contrato = linha["id_contrato"]

    # Carrega texto bruto
    arquivos_disponiveis = obter_contratos_disponiveis()
    _, id_arquivo = next((t, i) for t, i in arquivos_disponiveis if t == titulo_arquivo)
    texto = carregar_texto_contrato(titulo_arquivo, id_arquivo)

    with st.expander("📄 Texto Extraído do Contrato"):
        st.text_area("Texto do Contrato", texto, height=400)

    if st.button("💬 Extrair Cláusulas com IA"):
        df_clausulas = extrair_clausulas_robusto(texto)
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

        if st.button("✅ Validar cláusulas e salvar"):
            # Atualiza o registro na base de contratos
            clausulas_txt = "\n".join(df_editado["clausula"].tolist())
            df_base.loc[df_base["id_contrato"] == id_contrato, "clausulas"] = clausulas_txt
            salvar_base_contratos(df_base)
            st.success("📁 Cláusulas salvas com sucesso na base de contratos.")

    
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

