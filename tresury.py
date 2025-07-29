import streamlit as st

st.set_page_config(layout = 'wide')

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
st.sidebar.image("PRIO_SEM_POLVO_PRIO_PANTONE_LOGOTIPO_Azul.png", use_container_width=True)

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

st.markdown(f"### 👤 Usuário: `{nome_usuario}`")

# -----------------------------
# Renderização de conteúdo por página
# -----------------------------
if pagina == "📂 Upload do Contrato":
    st.info("Área de upload dos contratos.")
elif pagina == "🧾 Validação das Cláusulas":
    st.info("Área de validação das cláusulas extraídas.")
elif pagina == "🔍 Análise Automática":
    st.info("Execução dos agentes financeiros e jurídicos.")
elif pagina == "🧑‍⚖️ Revisão Final":
    st.info("Revisão final das cláusulas com input do usuário.")
elif pagina == "📊 Índices PRIO":
    st.info("Edição dos indicadores financeiros da PRIO.")
elif pagina == "📘 Relatórios Gerenciais":
    st.info("Geração de relatórios estratégicos com IA.")
elif pagina == "📁 Base de Cláusulas Padrão":
    st.info("Cláusulas padrão utilizadas pelos agentes.")

