#!/usr/bin/env python3
"""
Minutar Sentença em Lote — versão com leitura automática por IA (Google Gemini, camada gratuita).

Fluxo:
1) Login simples com senha (definida em .streamlit/secrets.toml / Secrets do Streamlit Cloud).
2) Upload de VÁRIOS PDFs de processos de uma vez.
3) Cada PDF é lido e enviado ao Gemini, que devolve: se o processo já está
   pronto para sentença, qual modelo se aplica, e os campos já preenchidos.
4) Você REVISA os campos sugeridos pela IA (edição livre) de cada processo antes de gerar.
5) Gera o .docx (modelo oficial com destaque amarelo, ou modelo flexível) para cada processo.

IMPORTANTE: a IA pode errar. Sempre revise a minuta antes de assinar.
"""
import io
import json
import sys
import zipfile
from pathlib import Path

import pdfplumber
import streamlit as st
import google.generativeai as genai
from openai import OpenAI

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR / "engine"))
from campos_amarelos import listar_campos, preencher  # noqa: E402
from render import render_text, text_to_docx, load_registry  # noqa: E402

MODELOS_DIR = BASE_DIR / "modelos_usuario"
TEMPLATES_DIR = BASE_DIR / "templates"

st.set_page_config(page_title="Minutar Sentença em Lote", page_icon="⚖️", layout="wide")

# ---------------------------------------------------------------------------
# ESTILO (aparência profissional — paleta jurídica navy/dourado)
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    .stApp { background-color: #ffffff; }
    #MainMenu, footer, header {visibility: hidden;}

    .app-header {
        display: flex;
        align-items: center;
        gap: 1rem;
        padding: 1.4rem 1.6rem;
        margin: -1rem -1rem 1.6rem -1rem;
        background: linear-gradient(135deg, #0f3057 0%, #1a4a7a 100%);
        border-radius: 0 0 12px 12px;
        color: #ffffff;
    }
    .app-header .icon { font-size: 2.4rem; line-height: 1; }
    .app-header h1 {
        font-size: 1.55rem;
        font-weight: 700;
        margin: 0;
        color: #ffffff;
        letter-spacing: .2px;
    }
    .app-header p {
        margin: .2rem 0 0 0;
        color: #d7e3f0;
        font-size: .92rem;
    }

    .step-title {
        font-size: 1.05rem;
        font-weight: 700;
        color: #0f3057;
        border-left: 4px solid #c9a227;
        padding-left: .6rem;
        margin: 1.6rem 0 .8rem 0;
    }

    div[data-testid="stExpander"] {
        border: 1px solid #e2e6ec;
        border-radius: 10px;
        box-shadow: 0 1px 3px rgba(15,48,87,0.06);
        margin-bottom: .6rem;
    }

    .stButton > button[kind="primary"] {
        background-color: #0f3057;
        border: none;
        border-radius: 8px;
        font-weight: 600;
    }
    .stButton > button[kind="primary"]:hover {
        background-color: #163f6d;
    }
    .stDownloadButton > button {
        border-radius: 8px;
        border: 1px solid #0f3057;
        color: #0f3057;
        font-weight: 600;
    }

    .badge {
        display: inline-block;
        padding: .15rem .6rem;
        border-radius: 999px;
        font-size: .78rem;
        font-weight: 600;
        margin-bottom: .4rem;
    }
    .badge-ok { background: #e6f4ea; color: #1e7a34; }
    .badge-warn { background: #fdf1e0; color: #9a5b00; }

    .footer-note {
        margin-top: 2.5rem;
        padding-top: 1rem;
        border-top: 1px solid #e2e6ec;
        color: #8a94a3;
        font-size: .8rem;
        text-align: center;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# LOGIN SIMPLES (acesso restrito)
# ---------------------------------------------------------------------------
def checar_senha():
    def senha_ok():
        if st.session_state.get("senha_input") == st.secrets.get("APP_PASSWORD", ""):
            st.session_state["autenticado"] = True
            del st.session_state["senha_input"]
        else:
            st.session_state["autenticado"] = False

    if st.session_state.get("autenticado"):
        return True

    st.markdown(
        """
        <div class="app-header">
            <div class="icon">⚖️</div>
            <div>
                <h1>Minutar Sentença em Lote</h1>
                <p>Acesso restrito — informe a senha para continuar.</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    _, col_login, _ = st.columns([1, 1.2, 1])
    with col_login:
        st.text_input("Senha de acesso", type="password", on_change=senha_ok, key="senha_input")
        if st.session_state.get("autenticado") is False:
            st.error("Senha incorreta.")
    return False


if not checar_senha():
    st.stop()

# ---------------------------------------------------------------------------
# CONFIGURAÇÃO DA IA (Groq como principal — tier gratuito bem mais generoso —,
# com o Gemini como reserva automática caso a Groq falhe ou não esteja configurada)
# ---------------------------------------------------------------------------
GROQ_API_KEY = st.secrets.get("GROQ_API_KEY", "")
GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", "")

if not GROQ_API_KEY and not GEMINI_API_KEY:
    st.error(
        "Nenhuma chave de IA configurada nos Secrets do Streamlit Cloud. "
        "Configure GROQ_API_KEY (recomendado, console.groq.com) e/ou GEMINI_API_KEY."
    )
    st.stop()

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

groq_client = OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1") if GROQ_API_KEY else None

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "pronto_para_sentenca": {"type": "boolean"},
        "motivo_se_nao_pronto": {"type": "string"},
        "resumo_caso": {"type": "string"},
        "template_id": {"type": "string"},
        "tipo_template": {"type": "string", "enum": ["fixo", "flexivel", "nenhum"]},
        "campos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "campo": {"type": "string"},
                    "valor": {"type": "string"},
                },
                "required": ["campo", "valor"],
            },
        },
    },
    "required": ["pronto_para_sentenca", "template_id", "tipo_template", "campos", "resumo_caso"],
}


def montar_catalogo_modelos():
    with open(MODELOS_DIR / "registry_fixos.json", encoding="utf-8") as f:
        fixos = json.load(f)["templates"]
    flexiveis = load_registry()
    partes = ["MODELOS FIXOS (tipo_template='fixo') — preferência sempre que se encaixarem:"]
    for t in fixos:
        campos = ", ".join(t["campos_obrigatorios"]) if t["campos_obrigatorios"] else "(nenhum campo — modelo pronto)"
        partes.append(f"- id: {t['id']} | desfecho: {t['desfecho']}\n  quando usar: {t['quando_usar']}\n  campos: {campos}")
    partes.append("\nMODELOS FLEXÍVEIS (tipo_template='flexivel') — usar apenas se NENHUM modelo fixo se encaixar:")
    for t in flexiveis:
        campos = ", ".join(t["campos_obrigatorios"])
        partes.append(f"- id: {t['id']} | quando usar: {t['quando_usar']}\n  campos: {campos}")
    return "\n".join(partes)


CATALOGO_MODELOS = montar_catalogo_modelos()

PROMPT_SISTEMA = f"""Você é um assistente de um juiz federal brasileiro especializado em ações
previdenciárias e assistenciais (auxílio-doença, aposentadoria por invalidez, BPC-LOAS e
salário-maternidade de segurada especial rural) nos Juizados Especiais Federais. Sua tarefa é
ler o texto extraído de um processo judicial (PDF) e preparar os dados para minutar a sentença.

Regras:
1. Verifique se o processo já está pronto para sentença: precisa haver perícia judicial
   (médica e/ou social, conforme o caso) já realizada e juntada aos autos, ou fundamento
   equivalente (ex.: modelos que dispensam perícia médica em casos de deficiência intelectual
   evidente, ou casos de salário-maternidade que dependem apenas de prova documental e não de
   perícia). Se NÃO estiver pronto (ex.: só há contestação, sem perícia ou sem oportunidade de
   a parte se manifestar sobre os documentos), marque pronto_para_sentenca=false e explique o
   motivo em motivo_se_nao_pronto.
2. Se estiver pronto, escolha o modelo mais adequado no catálogo abaixo. Dê preferência
   SEMPRE aos modelos fixos (tipo_template='fixo') quando o caso se encaixar EXATAMENTE (preste
   atenção às condições específicas descritas em cada 'quando_usar', como número exato de
   documentos de prova ou ausência de preliminares); só use um modelo flexível
   (tipo_template='flexivel') se nenhum modelo fixo servir bem ao caso.
3. Preencha CADA campo exigido pelo modelo escolhido, com base nos fatos do processo:
   idade (calcule a partir da data de nascimento e da data de hoje), diagnóstico (cite entre
   aspas como no laudo), datas (DIB = normalmente a data do requerimento administrativo -
   DER, salvo se os autos indicarem outro termo), CPF, valores de atrasados (se não houver
   cálculo pronto nos autos, escreva algo como "cujo montante deverá ser apurado em
   liquidação/cálculo judicial (RPV)." em vez de inventar um número). Para salário-maternidade,
   verifique também se há preliminar de prescrição a afastar (data do requerimento
   administrativo - DER, data de nascimento do filho, data da comunicação de indeferimento e
   data da propositura da ação) e siga EXATAMENTE o formato de pontuação indicado no
   'quando_usar' de cada campo quando houver instrução específica.
4. NUNCA invente fatos que não estejam no texto. Se um dado não for encontrado, deixe o campo
   com uma nota clara, por exemplo "[DADO NÃO ENCONTRADO — CONFERIR NOS AUTOS]".
5. Seja extremamente cauteloso: esta é uma minuta de rascunho que será revisada por um humano
   antes de qualquer assinatura. Prefira sinalizar dúvida a arriscar um dado errado.

REGRA GERAL DE AFERIÇÃO DA HIPOSSUFICIÊNCIA (campo 'fundamentacao_vulnerabilidade_texto',
usado nos modelos de BPC-LOAS por deficiência e idoso): NUNCA copie um texto genérico de
vulnerabilidade sem checar os dados reais do estudo social/socioeconômico do processo. Para
escrever esse campo, siga este raciocínio:
a) Calcule a renda familiar per capita relatada no estudo social, EXCLUINDO do somatório: (i)
   valores recebidos a título de Bolsa Família (ou programa de transferência de renda
   equivalente) e (ii) benefício previdenciário ou assistencial no valor de até 1 (um) salário
   mínimo recebido por outro membro da família (art. 20, §14, da Lei nº 8.742/1993).
b) Se, após essas exclusões, a renda per capita resultante for INFERIOR a 1/2 (meio) salário
   mínimo: está preenchido o requisito da hipossuficiência/miserabilidade. Nesse caso, pode
   usar como base o texto padrão já existente no modelo (adaptando aos dados concretos do
   processo — ex.: mencionar os elementos do laudo social que embasam a conclusão), afirmando
   que a vulnerabilidade socioeconômica está comprovada.
c) Se a renda per capita resultante (já com as exclusões) for IGUAL OU SUPERIOR a 1/2 salário
   mínimo: NÃO presuma vulnerabilidade automaticamente. Analise as demais circunstâncias
   fáticas relatadas no estudo social/laudo socioeconômico — condições de moradia, registros
   fotográficos da residência, bens que guarnecem a casa, existência de gastos excepcionais
   (ex.: medicamentos, tratamentos de saúde, fraldas, transporte para tratamento) — e conclua,
   de forma fundamentada e específica ao caso, se essas circunstâncias, ainda assim,
   caracterizam a miserabilidade exigida (flexibilização do critério puramente objetivo de
   renda, admitida pela jurisprudência), OU se a situação não caracteriza vulnerabilidade
   suficiente para a concessão do benefício. Se a prova dos autos for insuficiente para essa
   conclusão em qualquer sentido, sinalize isso explicitamente no texto (não decida no vácuo).
d) Em qualquer caso, cite no texto os elementos concretos do estudo social que embasam a
   conclusão (não apenas o critério de renda) — é isso que torna o campo específico ao processo
   e não um texto genérico.

CATÁLOGO DE MODELOS DISPONÍVEIS:
{CATALOGO_MODELOS}

Responda SOMENTE no formato JSON estruturado solicitado.
"""

# Modelos Groq a tentar, em ordem de preferência (tier gratuito da Groq: ~1.000
# pedidos/dia, bem mais folgado que o do Gemini). A Groq usa modo JSON (json_object),
# não JSON Schema completo, então o formato exigido vai descrito no próprio prompt.
MODELOS_GROQ = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
]

# Modelos Gemini a tentar, em ordem de preferência — usados apenas como reserva,
# caso a Groq não esteja configurada ou falhe. Modelos muito novos costumam vir
# com cota gratuita diária muito baixa (ex.: só 20 pedidos/dia) nas primeiras semanas,
# então priorizamos primeiro os mais estabelecidos (cota maior) e deixamos os mais novos
# como fallback. Se um nome de modelo for descontinuado (404) ou tiver cota esgotada
# (429), o app tenta automaticamente o próximo da lista.
MODELOS_GEMINI = [
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-flash-latest",
    "gemini-2.5-flash",
    "gemini-3.6-flash",
]

INSTRUCAO_FORMATO_JSON = """
Responda SOMENTE com um objeto JSON válido (sem markdown, sem ```), com exatamente
estas chaves:
{
  "pronto_para_sentenca": true ou false,
  "motivo_se_nao_pronto": "string (pode ser vazia se pronto_para_sentenca=true)",
  "resumo_caso": "string",
  "template_id": "string (id exato do catálogo, ou vazio se não pronto)",
  "tipo_template": "fixo" ou "flexivel" ou "nenhum",
  "campos": [{"campo": "nome_do_campo", "valor": "texto do valor"}, ...]
}
"""


def chamar_groq(texto_processo: str) -> dict:
    ultimo_erro = None
    for nome_modelo in MODELOS_GROQ:
        try:
            resposta = groq_client.chat.completions.create(
                model=nome_modelo,
                messages=[
                    {"role": "system", "content": PROMPT_SISTEMA + INSTRUCAO_FORMATO_JSON},
                    {
                        "role": "user",
                        "content": f"Texto extraído do processo (pode conter páginas fora de ordem ou ruído de OCR):\n\n{texto_processo}",
                    },
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
            )
            return json.loads(resposta.choices[0].message.content)
        except Exception as e:
            msg = str(e)
            ultimo_erro = e
            # 404 = modelo não existe/foi descontinuado; 429 = cota da Groq esgotada
            # (pouco provável, mas possível em uso intenso); 413 = texto grande
            # demais para o limite por minuto do modelo — tenta o próximo modelo.
            # Qualquer outro erro interrompe e sobe para quem chamou (chamar_ia),
            # que decide se cai para o Gemini.
            if "404" not in msg and "429" not in msg and "413" not in msg:
                raise
    raise ultimo_erro


def chamar_gemini(texto_processo: str) -> dict:
    ultimo_erro = None
    for nome_modelo in MODELOS_GEMINI:
        try:
            model = genai.GenerativeModel(
                nome_modelo,
                system_instruction=PROMPT_SISTEMA,
                generation_config={
                    "response_mime_type": "application/json",
                    "response_schema": RESPONSE_SCHEMA,
                    "temperature": 0.1,
                },
            )
            resposta = model.generate_content(
                f"Texto extraído do processo (pode conter páginas fora de ordem ou ruído de OCR):\n\n{texto_processo}"
            )
            return json.loads(resposta.text)
        except Exception as e:
            msg = str(e)
            ultimo_erro = e
            # Erro 404 = esse nome de modelo não existe mais.
            # Erro 429 = cota gratuita desse modelo esgotada (por minuto ou por dia).
            # Em ambos os casos, tenta o próximo modelo da lista automaticamente.
            # Qualquer outro erro (ex.: chave inválida, PDF corrompido) não tem
            # relação com o modelo escolhido, então já interrompe e mostra o erro real.
            if "404" not in msg and "429" not in msg:
                raise
    raise ultimo_erro


# A Groq tem limite de 8.000 tokens por minuto (TPM) no plano gratuito para os
# modelos usados aqui. Processos muito extraídos (PDFs longos/escaneados) podem
# facilmente ultrapassar isso. Como regra prática, ~1 token equivale a ~3
# caracteres em português (considerando o prompt do sistema, que já é grande).
# Se o texto do processo + o prompt do sistema ultrapassar essa estimativa,
# pulamos a Groq direto e vamos para o Gemini, que aceita documentos muito
# maiores (evita perder tempo com uma tentativa que já sabemos que vai falhar).
LIMITE_CARACTERES_GROQ = 18000

# O Gemini gratuito também tem um teto de tokens de ENTRADA por minuto (250.000,
# no plano gratuito). Processos muito grandes (PDFs escaneados/longos, 15-20 MB+)
# podem ultrapassar isso mesmo no Gemini. Cortamos o texto num tamanho seguro
# antes de mandar para qualquer uma das IAs, e avisamos o usuário quando isso
# acontece, para que ele saiba que a análise pode estar incompleta e precisa
# revisar manualmente as partes finais do processo.
LIMITE_CARACTERES_TOTAL = 550000


def truncar_texto_processo(texto: str) -> tuple[str, bool]:
    """Corta o texto extraído do PDF num tamanho seguro para as IAs configuradas.
    Retorna (texto_cortado, foi_cortado)."""
    if len(texto) <= LIMITE_CARACTERES_TOTAL:
        return texto, False
    cortado = texto[:LIMITE_CARACTERES_TOTAL]
    cortado += (
        "\n\n[AVISO: o restante do processo foi cortado por ser grande demais "
        "para o limite das IAs disponíveis no momento. Esta análise pode estar "
        "incompleta — confira manualmente as partes finais do processo antes de "
        "confiar nesta sugestão.]"
    )
    return cortado, True


def chamar_ia(texto_processo: str) -> dict:
    """Tenta a Groq primeiro (tier gratuito bem maior); se não estiver configurada,
    se o processo for grande demais para o limite por minuto da Groq, ou se falhar
    por completo, cai automaticamente para o Gemini como reserva (aceita documentos
    bem maiores)."""
    erro_groq = None
    processo_pequeno_o_bastante = len(texto_processo) <= LIMITE_CARACTERES_GROQ
    if groq_client is not None and processo_pequeno_o_bastante:
        try:
            return chamar_groq(texto_processo)
        except Exception as e:
            erro_groq = e
    if GEMINI_API_KEY:
        try:
            return chamar_gemini(texto_processo)
        except Exception as e:
            if erro_groq is not None:
                raise RuntimeError(
                    f"Groq falhou ({erro_groq}); Gemini (reserva) também falhou ({e})"
                ) from e
            raise
    if erro_groq is not None:
        raise erro_groq
    raise RuntimeError("Nenhuma IA configurada (defina GROQ_API_KEY e/ou GEMINI_API_KEY nos Secrets).")


def extrair_texto_pdf(arquivo) -> str:
    partes = []
    with pdfplumber.open(arquivo) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            partes.append(f"--- página {i} ---\n{page.extract_text() or ''}")
    return "\n\n".join(partes)


def carregar_registry_fixos():
    with open(MODELOS_DIR / "registry_fixos.json", encoding="utf-8") as f:
        return {t["id"]: t for t in json.load(f)["templates"]}


def gerar_docx_bytes(analise: dict, valores: dict, id_caso: str) -> bytes:
    tipo = analise.get("tipo_template")
    tpl_id = analise.get("template_id")
    tmp_out = BASE_DIR / f"_tmp_{id_caso}.docx"
    try:
        if tipo == "fixo":
            registry = carregar_registry_fixos()
            tpl = registry[tpl_id]
            template_path = MODELOS_DIR / tpl["arquivo"]
            if tpl["mapeamento"] is None:
                tmp_out.write_bytes(template_path.read_bytes())
            else:
                with open(MODELOS_DIR / tpl["mapeamento"], encoding="utf-8") as f:
                    mapeamento = json.load(f)
                preencher(str(template_path), mapeamento, valores, str(tmp_out), manter_destaque=True)
        else:
            texto_final = render_text(tpl_id, valores)
            text_to_docx(texto_final, tmp_out)
        return tmp_out.read_bytes()
    finally:
        tmp_out.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# ESTADO DA SESSÃO
# ---------------------------------------------------------------------------
if "minutas_geradas" not in st.session_state:
    st.session_state["minutas_geradas"] = {}
if "analises" not in st.session_state:
    # dict: chave = nome do arquivo -> {"resultado": {...} ou None, "erro": str ou None}
    st.session_state["analises"] = {}

st.markdown(
    """
    <div class="app-header">
        <div class="icon">⚖️</div>
        <div>
            <h1>Minutar Sentença em Lote</h1>
            <p>Leitura automática por IA — revise cada campo antes de gerar e sempre confira antes de assinar.</p>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.markdown("### ⚙️ Status")
    if groq_client is not None:
        st.success("IA principal: **Groq** (gpt-oss-120b)")
    if GEMINI_API_KEY:
        st.caption("Reserva automática: Gemini (também usado direto para processos grandes)")
    if groq_client is None and GEMINI_API_KEY:
        st.warning("IA principal: **Gemini** (Groq não configurada)")

    st.markdown("### 📋 Como funciona")
    st.markdown(
        "1. Suba os PDFs dos processos.\n"
        "2. Clique em analisar — a IA lê cada um e sugere o modelo e os campos.\n"
        "3. Revise/edite os campos de cada processo.\n"
        "4. Gere o `.docx` — revise e confira antes de assinar."
    )

    st.markdown("### ⚠️ Aviso")
    st.caption(
        "A IA pode errar. As minutas geradas são rascunhos e precisam de revisão "
        "humana antes de qualquer assinatura."
    )

st.markdown('<div class="step-title">1) Envie os PDFs dos processos</div>', unsafe_allow_html=True)
arquivos = st.file_uploader(
    "PDFs dos processos", type=["pdf"], accept_multiple_files=True,
    help="Selecione vários arquivos de uma vez (segure Ctrl ou Shift ao escolher os PDFs)."
)

col_a, col_b = st.columns([1, 3])
with col_a:
    processar = st.button("🔎 Ler e analisar todos com IA", type="primary", disabled=not arquivos)
with col_b:
    if arquivos:
        st.caption(f"{len(arquivos)} arquivo(s) selecionado(s).")

if processar:
    novos = [a for a in arquivos if a.name not in st.session_state["analises"]]
    if not novos:
        st.info("Todos os arquivos selecionados já foram analisados nesta sessão. "
                 "Role a página para revisar os resultados abaixo.")
    barra = st.progress(0.0, text="Iniciando...")
    for i, arquivo in enumerate(novos, start=1):
        barra.progress((i - 1) / max(len(novos), 1), text=f"Processando {arquivo.name} ({i}/{len(novos)})...")
        try:
            texto = extrair_texto_pdf(arquivo)
            texto, foi_cortado = truncar_texto_processo(texto)
            resultado = chamar_ia(texto)
            st.session_state["analises"][arquivo.name] = {
                "resultado": resultado, "erro": None, "cortado": foi_cortado,
            }
        except Exception as e:
            st.session_state["analises"][arquivo.name] = {"resultado": None, "erro": str(e), "cortado": False}
        barra.progress(i / max(len(novos), 1), text=f"Processando {arquivo.name} ({i}/{len(novos)})...")
    barra.empty()
    st.rerun()

# ---------------------------------------------------------------------------
# 2) RESULTADOS — um bloco expansível por processo
# ---------------------------------------------------------------------------
if st.session_state["analises"]:
    st.markdown('<div class="step-title">2) Resultados da análise — revise cada processo antes de gerar</div>', unsafe_allow_html=True)

    for nome_arquivo, item in st.session_state["analises"].items():
        erro = item.get("erro")
        analise = item.get("resultado")

        with st.expander(f"📄 {nome_arquivo}", expanded=(erro is not None or analise is not None)):
            if item.get("cortado"):
                st.warning(
                    "⚠️ Este processo é muito grande e o texto foi cortado antes de "
                    "ir para a IA (limite técnico das IAs disponíveis). A análise "
                    "abaixo pode estar incompleta — revise manualmente as partes "
                    "finais do processo antes de confiar nesta sugestão."
                )
            if erro:
                st.error(f"Erro ao consultar a IA: {erro}")
                continue

            if analise is None:
                st.info("Ainda não analisado.")
                continue

            st.info(analise.get("resumo_caso", ""))

            if not analise.get("pronto_para_sentenca"):
                st.markdown('<span class="badge badge-warn">⚠️ Não pronto para sentença</span>', unsafe_allow_html=True)
                st.warning(f"Motivo: {analise.get('motivo_se_nao_pronto', '(não informado)')}")
                continue

            tipo = analise.get("tipo_template")
            tpl_id = analise.get("template_id")
            st.markdown('<span class="badge badge-ok">✅ Pronto para sentença</span>', unsafe_allow_html=True)
            st.success(f"Modelo sugerido: **{tpl_id}** ({'oficial/fixo' if tipo == 'fixo' else 'flexível'})")

            st.caption("Edite qualquer campo que a IA tenha errado ou deixado incompleto.")
            chave_base = nome_arquivo.replace(" ", "_")
            valores_editados = {}
            for item_campo in analise.get("campos", []):
                campo_nome = item_campo["campo"]
                valores_editados[campo_nome] = st.text_area(
                    campo_nome,
                    value=item_campo.get("valor", ""),
                    height=70,
                    key=f"campo_{chave_base}_{campo_nome}",
                )

            id_caso_padrao = Path(nome_arquivo).stem
            id_caso = st.text_input(
                "Identificador do caso (nº do processo / nome do autor)",
                value=id_caso_padrao,
                key=f"id_caso_{chave_base}",
            )

            if st.button("✅ Gerar minuta deste processo", type="primary", key=f"gerar_{chave_base}"):
                if not id_caso:
                    st.error("Informe um identificador para o caso.")
                else:
                    try:
                        conteudo = gerar_docx_bytes(analise, valores_editados, id_caso)
                        st.session_state["minutas_geradas"][f"{id_caso}.docx"] = conteudo
                        st.success(f"Minuta gerada: {id_caso}.docx — veja na seção 3 abaixo.")
                    except Exception as e:
                        st.error(f"Erro ao gerar a minuta: {e}")

    prontos = [
        nome for nome, item in st.session_state["analises"].items()
        if item.get("resultado") and item["resultado"].get("pronto_para_sentenca")
        and f"{Path(nome).stem}.docx" not in st.session_state["minutas_geradas"]
    ]
    if len(prontos) > 1:
        st.info(
            f"{len(prontos)} processo(s) prontos ainda não geraram minuta. "
            "Gere um por um acima (o identificador de cada um pode ser ajustado antes de gerar)."
        )

    if st.button("🗑️ Limpar análises desta sessão"):
        st.session_state["analises"] = {}
        st.rerun()

# ---------------------------------------------------------------------------
# 3) MINUTAS GERADAS
# ---------------------------------------------------------------------------
st.markdown('<div class="step-title">3) Minutas geradas nesta sessão</div>', unsafe_allow_html=True)
geradas = st.session_state["minutas_geradas"]
if not geradas:
    st.info("Nenhuma minuta gerada ainda.")
else:
    for nome, conteudo in geradas.items():
        col1, col2 = st.columns([4, 1])
        col1.write(f"📄 {nome}")
        col2.download_button("Baixar", conteudo, file_name=nome,
                              mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                              key=f"dl_{nome}")
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for nome, conteudo in geradas.items():
            zf.writestr(nome, conteudo)
    st.download_button("⬇️ Baixar todas em .zip", zip_buf.getvalue(), file_name="minutas.zip", mime="application/zip")
    if st.button("Limpar minutas geradas"):
        st.session_state["minutas_geradas"] = {}
        st.rerun()

st.markdown(
    '<div class="footer-note">Minutar Sentença em Lote — ferramenta interna de apoio. '
    'Toda minuta gerada por IA é um rascunho e deve ser revisada antes de qualquer assinatura.</div>',
    unsafe_allow_html=True,
)
