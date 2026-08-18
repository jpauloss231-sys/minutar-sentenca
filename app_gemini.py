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
 
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR / "engine"))
from campos_amarelos import listar_campos, preencher  # noqa: E402
from render import render_text, text_to_docx, load_registry  # noqa: E402
 
MODELOS_DIR = BASE_DIR / "modelos_usuario"
TEMPLATES_DIR = BASE_DIR / "templates"
 
st.set_page_config(page_title="Minutar Sentença em Lote — IA", layout="wide")
 
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
 
    st.text_input("Senha de acesso", type="password", on_change=senha_ok, key="senha_input")
    if st.session_state.get("autenticado") is False:
        st.error("Senha incorreta.")
    return False
 
 
if not checar_senha():
    st.stop()
 
# ---------------------------------------------------------------------------
# CONFIGURAÇÃO DO GEMINI
# ---------------------------------------------------------------------------
GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", "")
if not GEMINI_API_KEY:
    st.error("GEMINI_API_KEY não configurada nos Secrets do Streamlit Cloud.")
    st.stop()
 
genai.configure(api_key=GEMINI_API_KEY)
 
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
 
CATÁLOGO DE MODELOS DISPONÍVEIS:
{CATALOGO_MODELOS}
 
Responda SOMENTE no formato JSON estruturado solicitado.
"""
 
# Lista de modelos a tentar, em ordem de preferência. Modelos muito novos costumam vir
# com cota gratuita diária muito baixa (ex.: só 20 pedidos/dia) nas primeiras semanas,
# então priorizamos primeiro os mais estabelecidos (cota maior) e deixamos os mais novos
# como fallback. Se um nome de modelo for descontinuado (404) ou tiver cota esgotada
# (429), o app tenta automaticamente o próximo da lista.
MODELOS_CANDIDATOS = [
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-flash-latest",
    "gemini-2.5-flash",
    "gemini-3.6-flash",
]
 
 
def chamar_gemini(texto_processo: str) -> dict:
    ultimo_erro = None
    for nome_modelo in MODELOS_CANDIDATOS:
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
 
st.title("⚖️ Minutar Sentença em Lote — leitura automática por IA")
st.caption("A IA (Google Gemini) lê cada PDF, decide o modelo e sugere os campos. "
           "Você revisa e confirma antes de gerar cada .docx. Sempre confira antes de assinar.")
 
st.header("1) Envie os PDFs dos processos (pode subir vários de uma vez)")
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
            resultado = chamar_gemini(texto)
            st.session_state["analises"][arquivo.name] = {"resultado": resultado, "erro": None}
        except Exception as e:
            st.session_state["analises"][arquivo.name] = {"resultado": None, "erro": str(e)}
        barra.progress(i / max(len(novos), 1), text=f"Processando {arquivo.name} ({i}/{len(novos)})...")
    barra.empty()
    st.rerun()
 
# ---------------------------------------------------------------------------
# 2) RESULTADOS — um bloco expansível por processo
# ---------------------------------------------------------------------------
if st.session_state["analises"]:
    st.header("2) Resultados da análise — revise cada processo antes de gerar")
 
    for nome_arquivo, item in st.session_state["analises"].items():
        erro = item.get("erro")
        analise = item.get("resultado")
 
        with st.expander(f"📄 {nome_arquivo}", expanded=(erro is not None or analise is not None)):
            if erro:
                st.error(f"Erro ao consultar a IA: {erro}")
                continue
 
            if analise is None:
                st.info("Ainda não analisado.")
                continue
 
            st.info(analise.get("resumo_caso", ""))
 
            if not analise.get("pronto_para_sentenca"):
                st.warning(
                    f"⚠️ A IA avaliou que este processo NÃO está pronto para sentença.\n\n"
                    f"Motivo: {analise.get('motivo_se_nao_pronto', '(não informado)')}"
                )
                continue
 
            tipo = analise.get("tipo_template")
            tpl_id = analise.get("template_id")
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
