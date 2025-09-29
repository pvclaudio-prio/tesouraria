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
from difflib import get_close_matches, SequenceMatcher
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
import math

st.set_page_config(
    page_title="Revisão de Contratos - PRIO",
    page_icon="📝",
    layout="wide",
    initial_sidebar_state="expanded",
)
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

# =========================
# Página: Validação de Cláusulas
# =========================
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

    # Reseta estados quando o contrato muda
    if st.session_state.get("contrato_validacao") != id_contrato:
        st.session_state["contrato_validacao"] = id_contrato
        st.session_state.pop("texto_contrato", None)
        st.session_state.pop("df_clausulas_extraidas", None)

    # Botão para iniciar a leitura do contrato
    if st.button("▶️ Iniciar leitura do contrato"):
        with st.spinner("Lendo e extraindo texto do contrato..."):
            texto = carregar_texto_contrato_drive(titulo_arquivo, id_arquivo)
            st.session_state["texto_contrato"] = texto or ""
        if st.session_state.get("texto_contrato"):
            st.success("✅ Texto do contrato carregado com sucesso.")
        else:
            st.error("❌ Não foi possível carregar o texto do contrato.")

    # Exibe o texto apenas se já tiver sido carregado
    if "texto_contrato" in st.session_state and st.session_state["texto_contrato"]:
        st.markdown("### 📄 Visualização do conteúdo do contrato")
        with st.expander("Visualizar texto completo extraído do contrato"):
            st.text_area("Conteúdo extraído", st.session_state["texto_contrato"], height=400)

        st.markdown("### 🧠 Passo 2 — Extrair cláusulas com IA")
        if st.button("✅ Extrair Cláusulas com IA"):
            df_clausulas = extrair_clausulas_robusto(st.session_state["texto_contrato"])
            st.session_state["df_clausulas_extraidas"] = df_clausulas
            
            if not df_clausulas.empty:
                st.success("✅ Cláusulas extraídas com sucesso!")
            else:
                st.warning("⚠️ Nenhuma cláusula foi extraída. Revise o texto do contrato.")
    else:
        st.info("Clique em **‘▶️ Iniciar leitura do contrato’** para carregar o texto antes de extrair as cláusulas.")

    # Edição e validação só aparecem após a extração das cláusulas
    if "df_clausulas_extraidas" in st.session_state and st.session_state["df_clausulas_extraidas"] is not None:
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

# =========================
# Chunking
# =========================
def dividir_em_chunks_simples(texto, max_chars=7000):
    """
    MELHORADO:
    - Preferência por dividir em quebras de seção/cabeçalhos
    - Garante overlap para não partir cláusulas longas entre chunks
    - Evita cortar no meio de frases/pontos finais quando possível
    """
    if not texto:
        return []

    # Normaliza quebras
    t = re.sub(r'\r\n?', '\n', texto)

    # Heurísticas de possíveis cabeçalhos/seções
    # (e.g., linhas MAIÚSCULAS, '1.1', 'Section', 'DEFINITIONS', etc.)
    # Usaremos como pontos candidatos de corte.
    section_break = re.compile(
        r'(?:\n(?=[A-Z][A-Z \-\d\.\(\)]{3,}\n)|\n(?=\d+(?:\.\d+){0,3}\s)|\n(?=SECTION\s+\d+)|\n(?=DEFINITIONS)|\n(?=BACKGROUND:))',
        flags=re.IGNORECASE
    )

    parts = re.split(section_break, t)
    parts = [p.strip() for p in parts if p and p.strip()]

    chunks = []
    atual = ""
    overlap = 800  # ~overlap para cobrir cauda/cabeça de cláusula

    def safe_append(acc, nxt):
        if acc:
            return acc + "\n\n" + nxt
        return nxt

    for p in parts:
        if len(atual) + len(p) + 2 <= max_chars:
            atual = safe_append(atual, p)
        else:
            # antes de cortar, tenta encontrar um ponto final próximo ao limite
            if len(atual) > 0:
                chunks.append(atual.strip())
            # inicia novo chunk com overlap do final do anterior (sem ultrapassar)
            if chunks:
                cauda = chunks[-1][-overlap:]
                atual = (cauda + "\n\n" + p).strip()
                # Se ficar muito grande, reduz o overlap dinamicamente
                if len(atual) > max_chars:
                    reduzir = len(atual) - max_chars + 200
                    atual = (chunks[-1][-max(0, overlap - reduzir):] + "\n\n" + p).strip()
            else:
                atual = p

            # Se ainda exceder, força cortes internos por parágrafos completos
            while len(atual) > max_chars:
                corte = _find_last_safe_boundary(atual, max_chars)
                chunks.append(atual[:corte].strip())
                atual = atual[corte:].strip()

    if atual:
        chunks.append(atual.strip())

    # Dedup/compress chunks vazios ou idênticos
    uniq = []
    seen = set()
    for c in chunks:
        key = re.sub(r'\s+', ' ', c).strip().lower()
        if key and key not in seen:
            seen.add(key)
            uniq.append(c)
    return uniq


def _find_last_safe_boundary(texto, limit):
    """
    Procura o último ponto seguro para corte antes de 'limit':
    prioridade: duplas quebras > ponto final > ponto e vírgula > quebra simples.
    """
    candidates = [
        texto.rfind("\n\n", 0, limit),
        texto.rfind(". ", 0, limit),
        texto.rfind("; ", 0, limit),
        texto.rfind("\n", 0, limit),
    ]
    pos = max([c for c in candidates if c != -1] or [limit])
    return max(1, pos)

# =========================
# Prompt robusto (sem exemplos fixos; retorna JSON)
# =========================
def gerar_prompt_com_exemplos(texto_chunk):
    """
    ALTERADO:
    - Remove exemplos fixos que o modelo poderia repetir.
    - Exige saída em JSON: {"clauses": ["...","..."]}
    - Regras claras: não copiar nada do prompt, não inventar, não sumarizar.
    """
    prompt = f"""
Você é um advogado especialista em contratos de captação de dívida (export prepayment, ECA, trade finance).
Tarefa: IDENTIFICAR e CATALOGAR **cláusulas completas** no trecho abaixo.

Regras de ouro (siga à risca):
1) **NÃO** copie exemplos, títulos do documento, marcadores, headers/footers, números de página, placeholders ([•]) ou trechos deste próprio prompt.
2) **NÃO** inclua numeração/títulos. Extraia **apenas o texto integral da cláusula**.
3) **NÃO** resuma. **NÃO** reescreva. **NÃO** traduza. Retorne o texto **exato** da cláusula conforme o contrato.
4) Considere como "cláusula" todo enunciado normativo/operacional **completo** (obrigações, definições, prazos, taxas, eventos de default, lei aplicável, etc.) que possa ser referenciado isoladamente.
   - Em definições, capture o enunciado inteiro até o fechamento da ideia (geralmente até o ponto final ou quebra clara).
   - Em listas (a), (b), (c) que formam uma cláusula única, una os itens da mesma cláusula em **uma única string**.
5) **NÃO** quebre cláusulas em várias saídas; **cada item do array deve conter uma cláusula completa**.
6) Se o trecho contém apenas parte de uma cláusula (indícios de que foi cortada), **ignore** essa cláusula neste chunk para evitar fragmentos.
7) Saída **apenas** em JSON válido, no formato:
{{
  "clauses": [
    "cláusula 1 (texto completo sem título/numeração)",
    "cláusula 2",
    ...
  ]
}}

Agora processe o trecho a seguir:

\"\"\"{texto_chunk}\"\"\"    
"""
    return prompt.strip()

# =========================
# Extração com IA + deduplicação
# =========================
def extrair_clausulas_robusto(texto):
    """
    ALTERADO:
    - Usa JSON como protocolo de saída para garantir 1 cláusula = 1 item.
    - Parser robusto (tenta JSON; fallback para heurística).
    - Deduplicação forte entre chunks (normalização + similaridade).
    """
    client = OpenAI(api_key=st.secrets["openai"]["api_key"])
    st.info("🔍 Analisando o contrato...")
    partes = dividir_em_chunks_simples(texto)
    clausulas_total = []

    for i, chunk in enumerate(partes):
        with st.spinner(f"Extraindo cláusulas do contrato: {i+1}/{len(partes)}..."):
            prompt = gerar_prompt_com_exemplos(chunk)
            try:
                resposta = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {"role": "system", "content": (
                            "Você é um especialista jurídico com ampla experiência em contratos "
                            "de dívida e operações de pré-pagamento de exportação. Siga estritamente as instruções do usuário."
                        )},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0,
                    max_tokens=4096
                )
                saida = (resposta.choices[0].message.content or "").strip()
                clausulas = _parse_clauses_from_output(saida)
                clausulas_total.extend(clausulas)
            except Exception as e:
                # Mantém robustez, mas sem poluir com erro como cláusula
                st.error(f"Erro no chunk {i+1}: {e}")

    clausulas_total = _dedupe_clauses(clausulas_total)

    # DataFrame final (mesmo formato/coluna)
    df = pd.DataFrame(clausulas_total, columns=["clausula"])
    return df


def _parse_clauses_from_output(saida: str):
    """
    Tenta interpretar a saída como JSON {"clauses": [...]}.
    Fallback: extrai blocos entre aspas ou linhas longas.
    """
    # Primeira tentativa: JSON
    try:
        data = json.loads(saida)
        if isinstance(data, dict) and "clauses" in data and isinstance(data["clauses"], list):
            # Garante strings limpas
            return [_clean_clause_text(c) for c in data["clauses"] if isinstance(c, str) and _clean_clause_text(c)]
    except Exception:
        pass

    # Segunda tentativa: procurar um bloco JSON dentro do texto
    try:
        match = re.search(r'\{[\s\S]*\}', saida)
        if match:
            data = json.loads(match.group(0))
            if isinstance(data, dict) and "clauses" in data and isinstance(data["clauses"], list):
                return [_clean_clause_text(c) for c in data["clauses"] if isinstance(c, str) and _clean_clause_text(c)]
    except Exception:
        pass

    # Fallback heurístico: linhas separadas por \n\n (apenas linhas "longas")
    linhas = [l.strip() for l in re.split(r'\n{2,}', saida) if l.strip()]
    linhas = [l for l in linhas if len(l) > 30]  # ignora ruído curto
    return [_clean_clause_text(l) for l in linhas if _clean_clause_text(l)]


def _clean_clause_text(txt: str) -> str:
    # Remove títulos claros no início (heurística) e numerações
    t = txt.strip()
    t = re.sub(r'^[0-9]+(\.[0-9]+)*\s*[-–—]*\s*', '', t)  # 1., 1.1.1 -
    t = re.sub(r'^(SECTION|SEÇÃO|ARTIGO|CLAUSE)\s+[0-9A-Za-z\.\-–—]+\s*[:\-–—]\s*', '', t, flags=re.I)
    # Remove headers/footers comuns e placeholders pontuais
    t = re.sub(r'\b\d{5,}v\d+\b', '', t)  # ex: 13348400v3
    t = re.sub(r'\[[^\]]*\]', '', t)      # remove [•], [__], etc.
    # Compacta espaços
    t = re.sub(r'[ \t]+', ' ', t)
    t = re.sub(r'\s+\n', '\n', t)
    t = re.sub(r'\n{3,}', '\n\n', t)
    return t.strip()


def _norm_for_hash(s: str) -> str:
    s2 = s.lower()
    s2 = re.sub(r'\s+', ' ', s2)
    s2 = re.sub(r'["“”\'`´]', '', s2)
    s2 = re.sub(r'[\(\)\[\]\{\}]', '', s2)
    s2 = re.sub(r'\bsection\b|\bclause\b|\bartigo\b', '', s2)
    s2 = re.sub(r'\d{5,}v\d+', '', s2)
    return s2.strip()


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _dedupe_clauses(clausulas, sim_threshold=0.9):
    """
    Remove duplicatas exatas e quase-duplicatas entre chunks com overlap.
    Mantém a versão mais longa (mais informativa).
    """
    uniq = []
    seen = []

    for c in clausulas:
        c_clean = _clean_clause_text(c)
        if not c_clean:
            continue
        n = _norm_for_hash(c_clean)

        # Duplicata exata?
        if any(n == _norm_for_hash(u) for u in uniq):
            continue

        # Quase duplicata?
        is_dup = False
        for u in uniq:
            if _similar(n, _norm_for_hash(u)) >= sim_threshold:
                # Se a nova é mais longa, substitui
                if len(c_clean) > len(u):
                    idx = uniq.index(u)
                    uniq[idx] = c_clean
                is_dup = True
                break

        if not is_dup:
            uniq.append(c_clean)

    # Ordena por primeira ocorrência/estabilidade
    return uniq

# =========================
# Salvar cláusulas extraídas (inalterado)
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
def carregar_clausulas_contratos():
    df = carregar_base_contratos()
    if df.empty:
        return pd.DataFrame(columns=["nome_arquivo", "clausulas"])

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

    df = carregar_clausulas_contratos()
    df_contrato = carregar_clausulas_analisadas()

    contratos_disponiveis = df["nome_arquivo"].dropna().unique().tolist()
    contrato_escolhido = st.selectbox("Selecione o contrato:", contratos_disponiveis)

    # Verifica se há cláusulas validadas no contrato escolhido
    df_clausulas = df[df["nome_arquivo"] == contrato_escolhido].copy() if contrato_escolhido else pd.DataFrame()
    clausulas = [c.strip() for c in df_clausulas["clausula"].tolist() if c.strip()] if not df_clausulas.empty else []

    # Botão para iniciar análise automática
    if clausulas:
        if st.button("✅ Iniciar Análise Automática"):
            drive = conectar_drive()
            pasta_bases_id = obter_id_pasta("bases", parent_id=obter_id_pasta("Tesouraria"))
            arquivos = drive.ListFile({
                'q': f"'{pasta_bases_id}' in parents and title = 'empresa_referencia_PRIO.xlsx' and trashed = false"
            }).GetList()
            if not arquivos:
                st.error("Base de índices financeiros 'empresa_referencia_PRIO.xlsx' não encontrada.")
                return

            caminho_indices = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx").name
            arquivos[0].GetContentFile(caminho_indices)
            df_indices = pd.read_excel(caminho_indices)

            client = OpenAI(api_key=st.secrets["openai"]["api_key"])
            resultados = []
            st.info("🔍 Iniciando análise com os especialistas jurídico e financeiro...")

            progress_bar = st.progress(0)
            status_text = st.empty()

            for i, clausula in enumerate(clausulas):
                status_text.text(f"Processando cláusula {i+1}/{len(clausulas)}...")
                with st.spinner():

                    # Agente Jurídico
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

                    # Agente Financeiro
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

                    # Agente Supervisor
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

                progress_bar.progress((i + 1) / len(clausulas))

            df_resultado = pd.DataFrame(resultados)
            st.session_state["analise_automatica_resultado"] = df_resultado
            st.success("✅ Análise automática concluída.")

    else:
        st.warning("Não há cláusulas validadas disponíveis.")

    # 🔁 Exibir resultado atual e botões (prioridade: resultado novo)
    if "analise_automatica_resultado" in st.session_state:
        df_resultado = st.session_state["analise_automatica_resultado"]
        st.dataframe(df_resultado, use_container_width=True)

        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df_resultado.to_excel(writer, index=False)

        st.download_button("📥 Baixar Análises", data=buffer.getvalue(),
                           file_name="clausulas_analisadas.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           key="download_novo")

        if st.button("Desejar Salvar ?", key="salvar_novo"):
            salvar_clausulas_validadas_usuario(df_resultado)
            st.success("✅ Revisão final do usuário salva com sucesso!")
            del st.session_state["analise_automatica_resultado"]

    # 🔁 Exibir análise antiga apenas se não houver análise nova
    elif df_contrato is not None and not df_contrato.empty:
        df_contrato = df_contrato[df_contrato["nome_arquivo"] == contrato_escolhido]
        if not df_contrato.empty:
            st.dataframe(df_contrato, use_container_width=True)
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                df_contrato.to_excel(writer, index=False)
            st.download_button("📥 Baixar Análises", data=buffer.getvalue(),
                               file_name="clausulas_analisadas.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               key="download_anterior")

    
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

def salvar_clausulas_validadas_usuario(df_novo):
    drive = conectar_drive()
    pasta_bases_id = obter_id_pasta("bases", parent_id=obter_id_pasta("Tesouraria"))
    pasta_backups_id = obter_id_pasta("backups", parent_id=obter_id_pasta("Tesouraria"))

    nome_arquivo = "clausulas_analisadas.xlsx"
    caminho_temp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx").name

    # Carregar base existente, se houver
    arquivos = drive.ListFile({
        'q': f"'{pasta_bases_id}' in parents and title = '{nome_arquivo}' and trashed = false"
    }).GetList()

    if arquivos:
        caminho_antigo = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx").name
        arquivos[0].GetContentFile(caminho_antigo)
        df_existente = pd.read_excel(caminho_antigo)

        # Remove as cláusulas do contrato atual
        contrato_atual = df_novo["nome_arquivo"].iloc[0]
        df_existente = df_existente[df_existente["nome_arquivo"] != contrato_atual]

        # Concatena com as novas
        df_final = pd.concat([df_existente, df_novo], ignore_index=True)
    else:
        df_final = df_novo

    df_final.to_excel(caminho_temp, index=False)

    # Salvar no Drive
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
        'title': f'clausulas_analisadas__{timestamp}.xlsx',
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
# 📌 Aba: Revisão Final
# =========================
# ---------------------------------------------
# Opções do usuário
USER_REVISAO_OPCOES = ["Concordo", "Discordo", "Melhoria"]
# ---------------------------------------------

def aba_revisao_final():
    st.title("🧑‍⚖️ Revisão Final do Usuário - Cláusulas Contratuais")

    # CSS leve para quebrar linhas e permitir alturas maiores
    st.markdown("""
        <style>
        /* permite quebra de linha dentro das células de texto */
        .stDataFrame td, .stDataFrame div, .stDataEditor td, .stDataEditor div {
            white-space: normal !important;
        }
        /* evita cortes nas células */
        .stDataEditor [data-testid="stVerticalBlock"] { overflow: visible !important; }
        </style>
    """, unsafe_allow_html=True)

    with st.spinner("Carregando cláusulas analisadas..."):
        df = carregar_clausulas_analisadas()
    if df is None or df.empty:
        st.warning("Nenhuma cláusula analisada disponível.")
        return

    contratos_disponiveis = df["nome_arquivo"].dropna().unique().tolist()
    contrato = st.selectbox("Selecione o contrato:", contratos_disponiveis)
    if not contrato:
        return

    df_filtrado = df[df["nome_arquivo"] == contrato].copy()

    st.markdown("### 📝 Revisão Final do Usuário")

    # Garante colunas editáveis
    for col in ["user_revisao", "motivo_user"]:
        if col not in df_filtrado.columns:
            df_filtrado[col] = ""

    # Ordenação/visibilidade das colunas
    colunas_ordem = [
        "clausula",
        "revisao_juridico", "motivo_juridico",
        "revisao_financeiro", "motivo_financeiro",
        "revisao_sup", "motivo_sup",
        "user_revisao", "motivo_user",
        "nome_arquivo",  # mantida para salvar, mas escondida
    ]
    colunas_ordem = [c for c in colunas_ordem if c in df_filtrado.columns]

    # Configuração de colunas (tipos, selects, larguras)
    col_cfg = {
        "clausula": st.column_config.TextColumn("Cláusula", width="large"),
        "motivo_juridico": st.column_config.TextColumn("Motivo Jurídico", width="large"),
        "motivo_financeiro": st.column_config.TextColumn("Motivo Financeiro", width="large"),
        "motivo_sup": st.column_config.TextColumn("Motivo Supervisor", width="large"),
        "user_revisao": st.column_config.SelectboxColumn(
            "Revisão do Usuário",
            options=USER_REVISAO_OPCOES,
            help="Selecione sua revisão para a cláusula"
        ),
        "motivo_user": st.column_config.TextColumn(
            "Motivo (usuário)",
            help="Explique de forma objetiva sua concordância/discordância ou sugestão de melhoria",
            width="large"
        ),
        "nome_arquivo": st.column_config.TextColumn("Contrato (interno)"),
        "revisao_juridico": st.column_config.TextColumn("Revisão Jurídica"),
        "revisao_financeiro": st.column_config.TextColumn("Revisão Financeira"),
        "revisao_sup": st.column_config.TextColumn("Revisão Supervisor"),
    }

    # Desabilita colunas não-editáveis
    desabilitadas = [c for c in df_filtrado.columns if c not in ["user_revisao", "motivo_user"]]

    # Altura “inteligente” da grade (até 15 linhas sem scroll)
    linhas = len(df_filtrado)
    altura = min(700, 56 + 40 * min(15, linhas))

    with st.form("form_revisao_final", clear_on_submit=False):
        df_editado = st.data_editor(
            df_filtrado,
            column_config=col_cfg,
            column_order=colunas_ordem,
            disabled=desabilitadas,
            hide_index=True,
            num_rows="fixed",              # não deixa adicionar/remover linhas
            use_container_width=True,
            height=altura,
            key="revisao_final_editor"
        )

        col_a, col_b = st.columns([1, 2])
        salvar_click = col_a.form_submit_button("✅ Salvar revisão final do usuário", use_container_width=True)
        baixar_click = col_b.form_submit_button("⬇️ Baixar análises (.xlsx)", use_container_width=True)

    # Pós-submit: DOWNLOAD
    if baixar_click:
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df_editado.to_excel(writer, index=False)
        st.download_button(
            "📥 Clique para baixar",
            data=buffer.getvalue(),
            file_name="clausulas_validadas.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )

    # Pós-submit: SALVAR
    if salvar_click:
        try:
            salvar_clausulas_revisadas_usuario(df_editado)
            st.success("✅ Revisão final do usuário salva com sucesso!")
        except Exception as e:
            st.error(f"Falha ao salvar no Drive: {e}")

    st.caption(f"A base possui **{linhas}** cláusulas para o contrato selecionado.")

# --------------------------------------------------------------------
# Mantém sua lógica de persistência no Drive (com pequenos reforços)
# --------------------------------------------------------------------
def salvar_clausulas_revisadas_usuario(df_novo: pd.DataFrame):
    drive = conectar_drive()
    pasta_tesouraria_id = obter_id_pasta("Tesouraria")
    pasta_bases_id = obter_id_pasta("bases", parent_id=pasta_tesouraria_id)
    pasta_backups_id = obter_id_pasta("backups", parent_id=pasta_tesouraria_id)

    nome_arquivo = "clausulas_validadas.xlsx"
    caminho_temp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx").name

    # Carrega base existente (se houver)
    arquivos = drive.ListFile({
        "q": f"'{pasta_bases_id}' in parents and title = '{nome_arquivo}' and trashed = false"
    }).GetList()

    if arquivos:
        caminho_antigo = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx").name
        arquivos[0].GetContentFile(caminho_antigo)
        df_existente = pd.read_excel(caminho_antigo)

        # Remove as linhas do contrato atual para substituí-las
        contrato_atual = df_novo["nome_arquivo"].iloc[0]
        df_existente = df_existente[df_existente["nome_arquivo"] != contrato_atual]

        df_final = pd.concat([df_existente, df_novo], ignore_index=True)
    else:
        df_final = df_novo

    # Salva base final
    df_final.to_excel(caminho_temp, index=False)

    # Sobe/atualiza arquivo principal
    if arquivos:
        arquivo = arquivos[0]
    else:
        arquivo = drive.CreateFile({"title": nome_arquivo, "parents": [{"id": pasta_bases_id}]})
    arquivo.SetContentFile(caminho_temp)
    arquivo.Upload()

    # Backup com timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = drive.CreateFile({
        "title": f"clausulas_validadas__{timestamp}.xlsx",
        "parents": [{"id": pasta_backups_id}]
    })
    backup.SetContentFile(caminho_temp)
    backup.Upload()

# --------------------------------------------------------------------
# Caso você ainda use esta função em outro ponto
# --------------------------------------------------------------------
def carregar_clausulas_validadas():
    drive = conectar_drive()
    pasta_bases_id = obter_id_pasta("bases", parent_id=obter_id_pasta("Tesouraria"))

    arquivos = drive.ListFile({
        "q": f"'{pasta_bases_id}' in parents and title = 'clausulas_validadas.xlsx' and trashed = false"
    }).GetList()

    if not arquivos:
        st.warning("❌ Base de cláusulas validadas não encontrada.")
        return pd.DataFrame(columns=[
            "nome_arquivo", "clausula",
            "revisao_juridico", "motivo_juridico",
            "revisao_financeiro", "motivo_financeiro",
            "revisao_sup", "motivo_sup",
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

    if st.button("✅ Executar análise"):
        clausulas_contrato = df[df["nome_arquivo"] == contrato_selecionado]["clausula"].tolist()

        texto_clausulas = "\n\n".join(clausulas_contrato)
        prompt = f"""
Você é um especialista jurídico em gestão contratual e compliance.

Com base nas cláusulas abaixo, elenque de forma objetiva e por ordem de significância as principais ações que o usuário deve realizar para garantir a segurança jurídica do contrato.

As ações precisam ser específicas para as cláusulas do contrato marcadas como Necessita Revisão. Não traga ações generalistas, seja crítico, objetivo e especialista.

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
            label="📥 Baixar Análise",
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
