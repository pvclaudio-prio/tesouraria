# -*- coding: utf-8 -*-
import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from io import BytesIO
import os, io, re, uuid, tempfile, traceback

# Visual / extras (mantidos se você usa em outras abas)
import plotly.express as px
import matplotlib.pyplot as plt

# Google Drive (PyDrive) e OAuth
from pydrive.auth import GoogleAuth
from pydrive.drive import GoogleDrive
from oauth2client.client import OAuth2Credentials
import httplib2

# HTTP/ML utilitários
import httpx, requests
from sentence_transformers import SentenceTransformer, util  # se usar embeddings locais

# OpenAI SDK v1
from openai import OpenAI

# Documentos/arquivos
from docx import Document
from PyPDF2 import PdfReader, PdfWriter
from docx2pdf import convert
import openpyxl  # engine Excel

# Google Cloud Document AI (sem sobrescrever nomes)
from google.cloud import documentai_v1 as docai_v1
from google.cloud import documentai_v1beta3 as docai_v1beta3
from google.oauth2 import service_account

# -----------------------------------------------------------------------------
# Config Streamlit
# -----------------------------------------------------------------------------
st.set_page_config(layout="wide")

# -----------------------------------------------------------------------------
# OpenAI Client + Wrapper Resiliente
# -----------------------------------------------------------------------------
# Observação: NÃO importe "import openai" (módulo) aqui para evitar confundir com o client.

client = OpenAI(api_key=st.secrets["openai"]["api_key"])

def _call_responses(messages, *, temperature=0, max_output_tokens=1200, reasoning_effort="minimal", model="gpt-5"):
    """Tenta chamar a Responses API (GPT‑5)."""
    resp = client.responses.create(
        model=model,
        input=messages,  # aceita messages no v1
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        reasoning={"effort": reasoning_effort},
    )
    return (getattr(resp, "output_text", "") or "").strip()

def _call_chat(messages, *, temperature=0, max_tokens=1200, model="gpt-5"):
    """Fallback: Chat Completions. Tenta gpt‑5; se não existir, troca para gpt‑4o."""
    try:
        out = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return (out.choices[0].message.content or "").strip()
    except Exception as e1:
        # Se modelo não suportado nesse endpoint, troca para gpt-4o
        try:
            out = client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            st.warning("ℹ️ Fallback automático: usando gpt-4o (Chat Completions).")
            return (out.choices[0].message.content or "").strip()
        except Exception as e2:
            raise RuntimeError(f"Falha no fallback Chat Completions: {e1} | {e2}")

def gpt5(messages, *, temperature=0, max_output_tokens=1200, reasoning_effort="minimal"):
    """
    Chama GPT‑5 pela Responses API; se indisponível no seu runtime (AttributeError),
    faz fallback para Chat Completions automaticamente (gpt‑5 → gpt‑4o).
    """
    try:
        return _call_responses(
            messages,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            reasoning_effort=reasoning_effort,
            model="gpt-5",
        )
    except AttributeError as e:
        # Caso clássico do seu print: 'OpenAI' object has no attribute 'responses'
        st.warning("ℹ️ Seu runtime do OpenAI não expõe .responses; usando fallback Chat Completions.")
        return _call_chat(
            messages,
            temperature=temperature,
            max_tokens=max_output_tokens,
            model="gpt-5",
        )
    except Exception as e:
        # Qualquer outra falha da Responses -> tenta Chat Completions
        return _call_chat(
            messages,
            temperature=temperature,
            max_tokens=max_output_tokens,
            model="gpt-5",
        )

# -----------------------------------------------------------------------------
# Login simples com st.secrets
# -----------------------------------------------------------------------------
@st.cache_data
def carregar_usuarios():
    usuarios_config = st.secrets.get("users", {})
    usuarios = {}
    for user, dados in usuarios_config.items():
        try:
            nome, senha = dados.split("|", 1)
            usuarios[user] = {"name": nome, "password": senha}
        except Exception:
            st.warning(f"Erro ao carregar usuário '{user}' nos secrets.")
    return usuarios

users = carregar_usuarios()
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.username = ""

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

# -----------------------------------------------------------------------------
# Sidebar/Menu
# -----------------------------------------------------------------------------
st.sidebar.image("PRIO_SEM_POLVO_PRIO_PANTONE_LOGOTIPO_Azul.png")
nome_usuario = users[st.session_state.username]["name"]
st.sidebar.success(f"Logado como: {nome_usuario}")

if st.sidebar.button("Logout"):
    st.session_state.logged_in = False
    st.session_state.username = ""
    st.rerun()

st.sidebar.title("Navegação")
pagina = st.sidebar.radio(
    "Ir para:",
    [
        "📂 Upload do Contrato",
        "🧾 Validação das Cláusulas",
        "🔍 Análise Automática",
        "🧑‍⚖️ Revisão Final",
        "📊 Índices PRIO",
        "📘 Relatórios Gerenciais",
    ],
)

# -----------------------------------------------------------------------------
# Google Drive Helpers
# -----------------------------------------------------------------------------
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
        revoke_uri=cred_dict["revoke_uri"],
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
    return resultado[0]['id'] if resultado else None

# -----------------------------------------------------------------------------
# Base de contratos
# -----------------------------------------------------------------------------
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
            "id_contrato","nome_arquivo","data_upload","usuario_upload",
            "clausulas","instituicao_financeira","tipo","idioma","user_email"
        ])
    caminho = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx").name
    arquivos[0].GetContentFile(caminho)
    df = pd.read_excel(caminho)
    if "clausulas" in df.columns:
        df["clausulas"] = df["clausulas"].astype(str)
    return df

def salvar_base_contratos(df):
    drive = conectar_drive()
    pasta_bases_id = obter_id_pasta("bases", parent_id=obter_id_pasta("Tesouraria"))
    pasta_backups_id = obter_id_pasta("backups", parent_id=obter_id_pasta("Tesouraria"))
    caminho = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx").name
    df.to_excel(caminho, index=False)
    arquivos = drive.ListFile({
        'q': f"'{pasta_bases_id}' in parents and title = 'base_contratos.xlsx' and trashed = false"
    }).GetList()
    arq = arquivos[0] if arquivos else drive.CreateFile({'title':'base_contratos.xlsx','parents':[{'id':pasta_bases_id}]})
    arq.SetContentFile(caminho); arq.Upload()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bkp = drive.CreateFile({'title':f'base_contratos__{ts}.xlsx','parents':[{'id':pasta_backups_id}]})
    bkp.SetContentFile(caminho); bkp.Upload()

# -----------------------------------------------------------------------------
# Upload de contrato
# -----------------------------------------------------------------------------
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
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{arquivo.name.split('.')[-1]}") as tmp:
            tmp.write(arquivo.read()); caminho_local = tmp.name
        novo_arquivo = drive.CreateFile({'title':nome_arquivo_drive,'parents':[{'id':pasta_contratos_id}]})
        novo_arquivo.SetContentFile(caminho_local); novo_arquivo.Upload()
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

# -----------------------------------------------------------------------------
# Leitura/extração (Document AI)
# -----------------------------------------------------------------------------
def docx_para_pdf_temporario(caminho_docx):
    tmpdir = tempfile.mkdtemp()
    caminho_pdf = os.path.join(tmpdir, "convertido.pdf")
    convert(caminho_docx, caminho_pdf)
    return caminho_pdf

def extrair_com_document_ai_paginas(caminho_pdf, max_paginas=15):
    credentials = service_account.Credentials.from_service_account_info(st.secrets["gcp_docai"])
    project_id = st.secrets["gcp_docai"]["project_id"]
    processor_id = st.secrets["gcp_docai"]["processor_id"]
    location = "us"
    client_docai = docai_v1.DocumentProcessorServiceClient(credentials=credentials)
    name = f"projects/{project_id}/locations/{location}/processors/{processor_id}"
    leitor = PdfReader(caminho_pdf)
    total_paginas = len(leitor.pages)
    texto_total = ""
    for i in range(0, total_paginas, max_paginas):
        escritor = PdfWriter()
        for j in range(i, min(i+max_paginas, total_paginas)):
            escritor.add_page(leitor.pages[j])
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_pdf:
            escritor.write(temp_pdf); temp_pdf.flush()
            with open(temp_pdf.name, "rb") as f:
                document = {"content": f.read(), "mime_type": "application/pdf"}
        result = client_docai.process_document(request={"name":name, "raw_document":document})
        texto_total += (getattr(result.document, "text", "") or "") + "\n"
    return (texto_total or "").trim() if hasattr(str, "trim") else (texto_total or "").strip()

def carregar_texto_contrato_drive(titulo_arquivo, arquivo_id):
    drive = conectar_drive()
    caminho_temp = tempfile.NamedTemporaryFile(delete=False).name
    drive.CreateFile({'id': arquivo_id}).GetContentFile(caminho_temp)
    try:
        if titulo_arquivo.lower().endswith(".docx"):
            caminho_pdf = docx_para_pdf_temporario(caminho_temp)
            texto = extrair_com_document_ai_paginas(caminho_pdf)
            if os.path.exists(caminho_pdf): os.remove(caminho_pdf)
        elif titulo_arquivo.lower().endswith(".pdf"):
            texto = extrair_com_document_ai_paginas(caminho_temp)
        else:
            st.error("❌ Formato de arquivo não suportado. Use .docx ou .pdf.")
            return ""
    except Exception as e:
        st.error(f"❌ Erro ao extrair o contrato: {e}")
        return ""
    if not isinstance(texto, str):
        texto = "" if texto is None else str(texto)
    return texto

# -----------------------------------------------------------------------------
# Chunking robusto + prompts
# -----------------------------------------------------------------------------
def dividir_em_chunks_simples(texto, max_chars=7000):
    if not isinstance(texto, str):
        texto = "" if texto is None else str(texto)
    texto = texto.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not texto:
        return []
    paragrafos = re.split(r"\n\s*\n+", texto)  # nunca split("")
    chunks, atual = [], ""
    for p in paragrafos:
        if len(atual) + len(p) + 2 <= max_chars:
            atual += p + "\n\n"
        else:
            if atual.strip():
                chunks.append(atual.strip())
            if len(p) > max_chars:
                for i in range(0, len(p), max_chars):
                    pedaço = p[i:i+max_chars].strip()
                    if pedaço: chunks.append(pedaço)
                atual = ""
            else:
                atual = p + "\n\n"
    if atual.strip():
        chunks.append(atual.strip())
    if not chunks:
        for i in range(0, len(texto), max_chars):
            fatia = texto[i:i+max_chars].strip()
            if fatia: chunks.append(fatia)
    return chunks

def gerar_prompt_com_exemplos(texto_chunk):
    exemplos = """
Exemplos de cláusulas extraídas corretamente:

The Lender agrees, subject to the terms and conditions hereof, to make available to the Borrower the Loan, in one disbursement during the Availability Period upon receipt of a Drawdown Request from the Borrower not later than the Specified Time.

The Borrower shall treat the proceeds of the Loan as a recebimento antecipado de exportação in accordance with the regulations issued by the Central Bank of Brazil. Promptly upon the receipt of the Loan, the Borrower shall enter into an appropriate foreign exchange transaction in order to convert the amount of the Loan proceeds from U.S. Dollars into Brazilian currency (reais) in accordance with the regulations of the Central Bank of Brazil.

The Borrower agrees to contract, execute and perform all of the foreign exchange transactions entered into in connection with this Agreement exclusively with the Lender.

The Borrower shall keep all copies of the shipping documents with respect to the respective export transaction, including documents conveying title to the Goods; the bill(s) of lading; the commercial invoice(s); and any other document which the Lender may reasonably request to attest the shipment of the Goods in a manner consistent with commercial export transactions.

Any Loan amounts which, at that time, are unutilized shall be immediately cancelled at the end of the Availability Period.
"""
    prompt = f"""
Você é um advogado especializado em contratos de crédito internacional.

Extraia todas as cláusulas do texto a seguir. Cada cláusula deve conter apenas:
- Texto completo da cláusula

Não inclua:
- Numeração (1., 2., 3.1, etc.)
- Título da cláusula
- Resumos ou comentários

Apresente a lista no mesmo formato dos exemplos acima.

Agora processe o seguinte trecho:

\"\"\"{texto_chunk}\"\"\"
"""
    return prompt.strip()

# -----------------------------------------------------------------------------
# Extração de cláusulas por IA (com progress bar)
# -----------------------------------------------------------------------------
def extrair_clausulas_robusto(texto):
    st.info("🔍 Analisando o contrato...")
    if not isinstance(texto, str) or not texto.strip():
        st.error("❌ Não há texto extraído do contrato. Verifique o arquivo e tente novamente.")
        return pd.DataFrame(columns=["clausula"])
    partes = dividir_em_chunks_simples(texto)
    if not partes:
        st.error("❌ Não foi possível segmentar o texto do contrato para extração.")
        return pd.DataFrame(columns=["clausula"])

    clausulas_total = []
    progress_bar = st.progress(0)
    status_text = st.empty()
    total = max(len(partes), 1)

    for i, chunk in enumerate(partes):
        status_text.text(f"Analisando trecho {i+1}/{total}...")
        with st.spinner(f"Processando IA no trecho {i+1}/{total}..."):
            prompt = gerar_prompt_com_exemplos(chunk)
            try:
                saida = gpt5(
                    [
                        {"role": "system", "content": "Você é um especialista jurídico com muita experiência e domínio em cláusulas de contratos de dívida."},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0,
                    max_output_tokens=1800,
                    reasoning_effort="minimal",
                )
                linhas = [l.strip() for l in (saida or "").split("\n") if l.strip()]
                clausulas_total.extend(linhas)
            except Exception as e:
                clausulas_total.append(f"[Erro no trecho {i+1}]: {e}")
        progress_bar.progress((i + 1) / total)
    status_text.text("")
    return pd.DataFrame(clausulas_total, columns=["clausula"])

# -----------------------------------------------------------------------------
# Persistência de análises
# -----------------------------------------------------------------------------
def salvar_base_generica(df, nome_arquivo, pasta="bases"):
    drive = conectar_drive()
    pasta_id = obter_id_pasta(pasta, parent_id=obter_id_pasta("Tesouraria"))
    caminho = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx").name
    df.to_excel(caminho, index=False)
    arquivos = drive.ListFile({'q': f"'{pasta_id}' in parents and title = '{nome_arquivo}' and trashed = false"}).GetList()
    arq = arquivos[0] if arquivos else drive.CreateFile({'title':nome_arquivo,'parents':[{'id':pasta_id}]})
    arq.SetContentFile(caminho); arq.Upload()

def carregar_clausulas_analisadas():
    drive = conectar_drive()
    pasta_bases_id = obter_id_pasta("bases", parent_id=obter_id_pasta("Tesouraria"))
    arquivos = drive.ListFile({
        'q': f"'{pasta_bases_id}' in parents and title = 'clausulas_analisadas.xlsx' and trashed = false"
    }).GetList()
    if not arquivos:
        st.warning("❌ Base de cláusulas analisadas não encontrada.")
        return pd.DataFrame(columns=[
            "nome_arquivo","clausula",
            "analise_juridico_status","analise_juridico_motivo",
            "analise_financeiro_status","analise_financeiro_motivo",
            "revisao_juridico_status","revisao_juridico_motivo",
            "revisao_financeiro_status","revisao_financeiro_motivo"
        ])
    caminho = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx").name
    arquivos[0].GetContentFile(caminho)
    return pd.read_excel(caminho)

def salvar_clausulas_validadas_usuario(df_novo):
    drive = conectar_drive()
    pasta_bases_id = obter_id_pasta("bases", parent_id=obter_id_pasta("Tesouraria"))
    pasta_backups_id = obter_id_pasta("backups", parent_id=obter_id_pasta("Tesouraria"))
    nome_arquivo = "clausulas_analisadas.xlsx"
    caminho_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx").name
    arquivos = drive.ListFile({'q': f"'{pasta_bases_id}' in parents and title = '{nome_arquivo}' and trashed = false"}).GetList()
    if arquivos:
        caminho_antigo = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx").name
        arquivos[0].GetContentFile(caminho_antigo)
        df_existente = pd.read_excel(caminho_antigo)
        contrato_atual = df_novo["nome_arquivo"].iloc[0]
        df_existente = df_existente[df_existente["nome_arquivo"] != contrato_atual]
        df_final = pd.concat([df_existente, df_novo], ignore_index=True)
    else:
        df_final = df_novo
    df_final.to_excel(caminho_tmp, index=False)
    arq = arquivos[0] if arquivos else drive.CreateFile({'title':nome_arquivo,'parents':[{'id':pasta_bases_id}]})
    arq.SetContentFile(caminho_tmp); arq.Upload()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bkp = drive.CreateFile({'title':f'clausulas_analisadas__{ts}.xlsx','parents':[{'id':pasta_backups_id}]})
    bkp.SetContentFile(caminho_tmp); bkp.Upload()

def carregar_base_contratos_para_expansao():
    df = carregar_base_contratos()
    if df.empty: return pd.DataFrame(columns=["nome_arquivo","clausula"])
    linhas = []
    for _, row in df.iterrows():
        texto = row.get("clausulas","")
        if not isinstance(texto, str) or not texto.strip():
            continue
        for c in [x.strip() for x in texto.split("\n") if x.strip()]:
            linhas.append({"nome_arquivo": row["nome_arquivo"], "clausula": c})
    return pd.DataFrame(linhas)

def salvar_clausulas_validadas(df_clausulas, id_contrato):
    df = carregar_base_contratos()
    if df.empty or "clausula" not in df_clausulas.columns:
        return False
    df_clausulas["clausula"] = df_clausulas["clausula"].astype(str)
    clausulas_txt = "\n".join(df_clausulas["clausula"].tolist())
    idx = df[df["id_contrato"] == id_contrato].index
    if len(idx) == 0: return False
    df.loc[idx[0], "clausulas"] = clausulas_txt
    salvar_base_contratos(df)
    return True

# -----------------------------------------------------------------------------
# Abas
# -----------------------------------------------------------------------------
def aba_validacao_clausulas():
    st.title("🧾 Validação das Cláusulas Contratuais")
    contratos = obter_contratos_disponiveis()
    if not contratos:
        st.warning("Nenhum contrato disponível.")
        return
    nomes_arquivos = [t for t, _ in contratos]
    contrato_sel = st.selectbox("Selecione o contrato para análise:", nomes_arquivos)
    if not contrato_sel: st.stop()
    titulo_arquivo, id_arquivo = next(item for item in contratos if item[0] == contrato_sel)
    id_contrato = titulo_arquivo.split("_")[0]  # prefixo UUID salvo no upload

    st.markdown("### 📄 Visualização do conteúdo do contrato")
    texto = carregar_texto_contrato_drive(titulo_arquivo, id_arquivo)
    with st.expander("Visualizar texto completo extraído do contrato"):
        st.text_area("Conteúdo extraído", texto or "", height=400)

    if st.button("✅ Extrair Cláusulas com IA"):
        if not texto or not str(texto).strip():
            st.error("❌ Não há texto extraído do contrato. Verifique o arquivo e tente novamente.")
        else:
            df_clausulas = extrair_clausulas_robusto(texto)
            st.session_state["df_clausulas_extraidas"] = df_clausulas
            st.success("✅ Extração concluída.")

    if "df_clausulas_extraidas" in st.session_state:
        st.markdown("### ✍️ Revisar Cláusulas Extraídas")
        df_editado = st.data_editor(
            st.session_state["df_clausulas_extraidas"],
            num_rows="dynamic",
            use_container_width=True,
            key="editor_clausulas"
        )
        if st.button("✅ Validar cláusulas e salvar"):
            if salvar_clausulas_validadas(df_editado, id_contrato):
                st.success("📦 Cláusulas validadas e salvas com sucesso.")
            else:
                st.error("❌ Contrato não encontrado na base para atualização.")

def aba_analise_automatica():
    st.title("🧠 Análise Automática das Cláusulas")
    df = carregar_base_contratos_para_expansao()
    df_contrato = carregar_clausulas_analisadas()
    contratos_disponiveis = df["nome_arquivo"].dropna().unique().tolist()
    contrato_escolhido = st.selectbox("Selecione o contrato:", contratos_disponiveis)
    df_clausulas = df[df["nome_arquivo"] == contrato_escolhido].copy() if contrato_escolhido else pd.DataFrame()
    clausulas = [c.strip() for c in df_clausulas["clausula"].tolist() if c.strip()] if not df_clausulas.empty else []

    if clausulas and st.button("✅ Iniciar Análise Automática"):
        drive = conectar_drive()
        pasta_bases_id = obter_id_pasta("bases", parent_id=obter_id_pasta("Tesouraria"))
        arquivos = drive.ListFile({
            'q': f"'{pasta_bases_id}' in parents and title = 'empresa_referencia_PRIO.xlsx' and trashed = false"
        }).GetList()
        if not arquivos:
            st.error("Base de índices financeiros 'empresa_referencia_PRIO.xlsx' não encontrada.")
            return
        caminho_idx = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx").name
        arquivos[0].GetContentFile(caminho_idx)
        df_indices = pd.read_excel(caminho_idx)

        resultados = []
        st.info("🔍 Iniciando análise com os especialistas jurídico e financeiro...")
        progress_bar, status_text = st.progress(0), st.empty()

        for i, clausula in enumerate(clausulas):
            status_text.text(f"Processando cláusula {i+1}/{len(clausulas)}...")
            with st.spinner():
                pj = f"""
Você é um advogado especialista em contratos de dívida.
Analise a cláusula abaixo e diga se está Conforme ou se Necessita Revisão. Você somente pode escolher uma alternativa.
Sempre inicie sua resposta com exatamente as palavras Conforme ou Necessita Revisão.
Justifique de forma objetiva com base jurídica.

Cláusula:
\"\"\"{clausula}\"\"\"
"""
                rj = gpt5([{"role":"user","content":pj}], temperature=0, max_output_tokens=800)

                texto_indices = df_indices.to_string(index=False)
                pf = f"""
Você é um especialista financeiro com foco em contratos de captação de dívida. Abaixo estão os índices financeiros da empresa PRIO:

{texto_indices}

Analise a cláusula a seguir e diga se ela está financeiramente Conforme ou se Necessita Revisão. Você somente pode escolher uma alternativa.
Sempre inicie sua resposta com exatamente as palavras Conforme ou Necessita Revisão.
Caso a cláusula não aborde nenhuma condicionante financeira, diga que está Conforme e no motivo informe objetivamente que não foram identificados índices financeiros para análise.
Justifique com base nos dados da empresa e benchmarking de mercado para casos semelhantes.

Cláusula:
\"\"\"{clausula}\"\"\"
"""
                rf = gpt5([{"role":"user","content":pf}], temperature=0, max_output_tokens=800)

                ps = f"""
Você é o supervisor responsável pela revisão final.
Abaixo está a cláusula, a análise do agente jurídico e a análise do agente financeiro.
Revise cada uma delas e diga se Concorda ou Não Concorda, e explique brevemente o motivo.
Sempre inicie sua resposta com exatamente as palavras Concorda ou Não Concorda.

Cláusula:
\"\"\"{clausula}\"\"\"

Análise Jurídica:
{rj}

Análise Financeira:
{rf}
"""
                rs = gpt5([{"role":"user","content":ps}], temperature=0, max_output_tokens=800)

                resultados.append({
                    "nome_arquivo": contrato_escolhido,
                    "clausula": clausula,
                    "revisao_juridico": "Conforme" if "Conforme" in rj else "Necessita Revisão",
                    "motivo_juridico": rj,
                    "revisao_financeiro": "Conforme" if "Conforme" in rf else "Necessita Revisão",
                    "motivo_financeiro": rf,
                    "revisao_sup": "Concorda" if "Concorda" in rs else "Não Concorda",
                    "motivo_sup": rs,
                })
            progress_bar.progress((i+1)/len(clausulas))

        df_resultado = pd.DataFrame(resultados)
        st.session_state["analise_automatica_resultado"] = df_resultado
        st.success("✅ Análise automática concluída.")
    elif not clausulas:
        st.warning("Não há cláusulas validadas disponíveis.")

    if "analise_automatica_resultado" in st.session_state:
        df_resultado = st.session_state["analise_automatica_resultado"]
        st.dataframe(df_resultado, use_container_width=True)
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            df_resultado.to_excel(w, index=False)
        st.download_button("📥 Baixar Análises", data=buf.getvalue(),
            file_name="clausulas_analisadas.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="download_novo")
        if st.button("Desejar Salvar ?", key="salvar_novo"):
            salvar_clausulas_validadas_usuario(df_resultado)
            st.success("✅ Revisão final do usuário salva com sucesso!")
            del st.session_state["analise_automatica_resultado"]

def aba_revisao_final():
    st.title("🧑‍⚖️ Revisão Final do Usuário - Cláusulas Contratuais")
    # Carrega a base já analisada (última)
    df = carregar_clausulas_analisadas()
    if df.empty:
        st.warning("Nenhuma cláusula analisada disponível.")
        return
    contratos = df["nome_arquivo"].unique().tolist()
    contrato = st.selectbox("Selecione o contrato:", contratos)
    df_filtrado = df[df["nome_arquivo"] == contrato].copy()
    for col in ["user_revisao","motivo_user"]:
        if col not in df_filtrado.columns:
            df_filtrado[col] = ""
    st.markdown("### 📝 Revisão Final do Usuário")
    df_editado = st.data_editor(
        df_filtrado,
        use_container_width=True,
        num_rows="dynamic",
        column_order=[
            "clausula",
            "revisao_juridico","motivo_juridico",
            "revisao_financeiro","motivo_financeiro",
            "revisao_sup","motivo_sup",
            "user_revisao","motivo_user",
        ],
        disabled=[
            "nome_arquivo","clausula",
            "revisao_juridico","motivo_juridico",
            "revisao_financeiro","motivo_financeiro",
            "revisao_sup","motivo_sup",
        ],
        key="revisao_final_editor",
    )
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df_editado.to_excel(w, index=False)
    st.download_button("📥 Baixar Análises", data=buf.getvalue(),
        file_name="clausulas_validadas.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if st.button("✅ Salvar revisão final do usuário"):
        # Reaproveita rotina de salvar "validadas" para o arquivo clausulas_validadas.xlsx
        # (mantida por compatibilidade com seu fluxo anterior)
        drive = conectar_drive()
        pasta_bases_id = obter_id_pasta("bases", parent_id=obter_id_pasta("Tesouraria"))
        pasta_backups_id = obter_id_pasta("backups", parent_id=obter_id_pasta("Tesouraria"))
        nome_arquivo = "clausulas_validadas.xlsx"
        caminho_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx").name
        # Merge com existente (por contrato)
        arquivos = drive.ListFile({'q': f"'{pasta_bases_id}' in parents and title = '{nome_arquivo}' and trashed = false"}).GetList()
        if arquivos:
            caminho_antigo = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx").name
            arquivos[0].GetContentFile(caminho_antigo)
            df_existente = pd.read_excel(caminho_antigo)
            contrato_atual = df_editado["nome_arquivo"].iloc[0]
            df_existente = df_existente[df_existente["nome_arquivo"] != contrato_atual]
            df_final = pd.concat([df_existente, df_editado], ignore_index=True)
        else:
            df_final = df_editado
        df_final.to_excel(caminho_tmp, index=False)
        arq = arquivos[0] if arquivos else drive.CreateFile({'title':nome_arquivo,'parents':[{'id':pasta_bases_id}]})
        arq.SetContentFile(caminho_tmp); arq.Upload()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        bkp = drive.CreateFile({'title':f'clausulas_validadas__{ts}.xlsx','parents':[{'id':pasta_backups_id}]})
        bkp.SetContentFile(caminho_tmp); bkp.Upload()
        st.success("✅ Revisão final do usuário salva com sucesso!")

def aba_indices_prio():
    st.title("📊 Índices Financeiros da PRIO")
    drive = conectar_drive()
    pasta_bases_id = obter_id_pasta("bases", parent_id=obter_id_pasta("Tesouraria"))
    pasta_backups_id = obter_id_pasta("backups", parent_id=obter_id_pasta("Tesouraria"))
    nome_arquivo = "empresa_referencia_PRIO.xlsx"
    arquivos = drive.ListFile({'q': f"\"{pasta_bases_id}\" in parents and title = '{nome_arquivo}' and trashed = false"}).GetList()
    if not arquivos:
        st.warning("Base 'empresa_referencia_PRIO.xlsx' não encontrada. Será criada uma nova base.")
        df_indices = pd.DataFrame(columns=["EBITDA","Mrg EBITDA","Res Fin","Dívida","Lucro Líq","Caixa"])
    else:
        caminho = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx").name
        arquivos[0].GetContentFile(caminho)
        df_indices = pd.read_excel(caminho)
    st.markdown("### ✍️ Editar Índices")
    df_editado = st.data_editor(df_indices, num_rows="dynamic", use_container_width=True, key="editor_indices_prio")
    if st.button("💾 Salvar Índices"):
        caminho_salvar = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx").name
        df_editado.to_excel(caminho_salvar, index=False)
        arq = arquivos[0] if arquivos else drive.CreateFile({'title':nome_arquivo,'parents':[{'id':pasta_bases_id}]})
        arq.SetContentFile(caminho_salvar); arq.Upload()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        bkp = drive.CreateFile({'title':f"empresa_referencia_PRIO__{ts}.xlsx",'parents':[{'id':pasta_backups_id}]})
        bkp.SetContentFile(caminho_salvar); bkp.Upload()
        st.success("✅ Índices salvos e backup criado com sucesso!")

def aba_relatorios_gerenciais():
    st.title("📘 Relatório Gerencial - Ações Prioritárias por Contrato")
    # carrega base "validadas" (que inclui colunas de revisão do usuário)
    drive = conectar_drive()
    pasta_bases_id = obter_id_pasta("bases", parent_id=obter_id_pasta("Tesouraria"))
    arquivos = drive.ListFile({'q': f"'{pasta_bases_id}' in parents and title = 'clausulas_validadas.xlsx' and trashed = false"}).GetList()
    if not arquivos:
        st.warning("Base de cláusulas validadas está vazia.")
        return
    caminho = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx").name
    arquivos[0].GetContentFile(caminho)
    df = pd.read_excel(caminho)
    if df.empty:
        st.warning("Base de cláusulas validadas está vazia.")
        return

    contratos = df["nome_arquivo"].unique().tolist()
    contrato_sel = st.selectbox("Selecione o contrato para análise:", contratos)
    if not contrato_sel:
        return
    if st.button("✅ Executar análise"):
        clausulas = df[df["nome_arquivo"] == contrato_sel]["clausula"].tolist()
        texto_clausulas = "\n\n".join([c for c in clausulas if isinstance(c, str) and c.strip()])
        if not texto_clausulas.strip():
            st.error("❌ Não há cláusulas para analisar nesse contrato.")
            return
        prompt = f"""
Você é um especialista jurídico em gestão contratual e compliance.

Com base nas cláusulas abaixo, elenque de forma objetiva e por ordem de significância as principais ações que o usuário deve realizar para garantir a segurança jurídica do contrato.

As ações precisam ser específicas para as cláusulas do contrato marcadas como Necessita Revisão. Não traga ações generalistas, seja crítico, objetivo e especialista.

Sua resposta deve conter no máximo 1 página e apresentar as ações com títulos curtos, seguidos de explicações objetivas (1 parágrafo por ação). Seja direto, técnico e evite repetições.

Mantenha sempre uma breve referência à cláusula que precisa ser revisada para assegurar a conformidade.

Cláusulas do contrato:
\"\"\"{texto_clausulas}\"\"\"
"""
        with st.spinner("Gerando análise..."):
            analise = gpt5(
                [
                    {"role":"system","content":"Você é um consultor jurídico especialista em contratos de captação de dívida internacionais."},
                    {"role":"user","content":prompt},
                ],
                temperature=0,
                max_output_tokens=1800,
                reasoning_effort="minimal",
            )
        st.markdown("### ✅ Análise Gerada:")
        st.markdown(analise or "_(Sem conteúdo retornado)_")
        buf = BytesIO()
        doc = Document()
        doc.add_heading(f"Relatório Gerencial - {contrato_sel}", level=1)
        for par in (analise or "").split("\n"):
            if par.strip(): doc.add_paragraph(par.strip())
        doc.save(buf)
        st.download_button(
            "📥 Baixar Análise",
            data=buf.getvalue(),
            file_name=f"relatorio_gerencial_{contrato_sel}.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )

# -----------------------------------------------------------------------------
# Renderização
# -----------------------------------------------------------------------------
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
