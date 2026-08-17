#!/usr/bin/env python3
"""
Minutar Sentença em Lote — versão com leitura automática por IA (Google Gemini, camada gratuita).

Fluxo:
1) Login simples com senha (definida em .streamlit/secrets.toml).
2) Upload de PDF(s) do processo.
3) O texto é extraído e enviado ao Gemini, que devolve: se o processo já está
   pronto para sentença, qual modelo se aplica, e os campos já preenchidos.
4) Você REVISA os campos sugeridos pela IA (edição livre) antes de gerar.
5) Gera o .docx (modelo oficial com destaque amarelo, ou modelo flexível).

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
previdenciárias e assistenciais (auxílio-doença, aposentadoria por invalidez, BPC-LOAS) nos
Juizados Especiais Federais. Sua tarefa é ler o texto extraído de um processo judicial (PDF)
e preparar os dados para minutar a sentença.

Regras:
1. Verifique se o processo já está pronto para sentença: precisa haver perícia judicial
   (médica e/ou social, conforme o caso) já realizada e juntada aos autos, ou fundamento
   equivalente (ex.: modelos que dispensam perícia médica em casos de deficiência intelectual
   evidente). Se NÃO estiver pronto (ex.: só há contestação, sem perícia), marque
   pronto_para_sentenca=false e explique o motivo em motivo_se_nao_pronto.
2. Se estiver pronto, escolha o modelo mais adequado no catálogo abaixo. Day preferência
   SEMPRE aos modelos fixos (tipo_template='fixo') quando o caso se encaixar; só use um
   modelo flexível (tipo_template='flexivel') se nenhum modelo fixo servir bem ao caso.
3. Preencha CADA campo exigido pelo modelo escolhido, com base nos fatos do processo:
   idade (calcule a partir da data de nascimento e da data de hoje), diagnóstico (cite entre
   aspas como no laudo), datas (DIB = normalmente a data do requerimento administrativo -
   DER, salvo se os autos indicarem outro termo), CPF, valores de atrasados (se não houver
   cálculo pronto nos autos, escreva algo como "cujo montante deverá ser apurado em
   liquidação/cálculo judicial (RPV)." em vez de inventar um número).
4. NUNCA invente fatos que não estejam no texto. Se um dado não for encontrado, deixe o campo
   com uma nota clara, por exemplo "[DADO NÃO ENCONTRADO — CONFERIR NOS AUTOS]".
5. Seja extremamente cauteloso: esta é uma minuta de rascunho que será revisada por um humano
   antes de qualquer assinatura. Prefira sinalizar dúvida a arriscar um dado errado.

CATÁLOGO DE MODELOS DISPONÍVEIS:
{CATALOGO_MODELOS}

Responda SOMENTE no formato JSON estruturado solicitado.
"""

MODEL_NAME = "gemini-1.5-flash"


def chamar_gemini(texto_processo: str) -> dict:
    model = genai.GenerativeModel(
        MODEL_NAME,
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


def extrair_texto_pdf(arquivo) -> str:
    partes = []
    with pdfplumber.open(arquivo) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            partes.append(f"--- página {i} ---\n{page.extract_text() or ''}")
    return "\n\n".join(partes)


def carregar_registry_fixos():
    with open(MODELOS_DIR / "registry_fixos.json", encoding="utf-8") as f:
        return {t["id"]: t for t in json.load(f)["templates"]}


# ---------------------------------------------------------------------------
# ESTADO DA SESSÃO
# ---------------------------------------------------------------------------
if "minutas_geradas" not in st.session_state:
    st.session_state["minutas_geradas"] = {}
if "analise_atual" not in st.session_state:
    st.session_state["analise_atual"] = None

st.title("⚖️ Minutar Sentença em Lote — leitura automática por IA")
st.caption("A IA (Google Gemini) lê o PDF, decide o modelo e sugere os campos. "
           "Você revisa e confirma antes de gerar o .docx. Sempre confira antes de assinar.")

st.header("1) Envie o PDF do processo")
arquivo = st.file_uploader("PDF do processo", type=["pdf"])

if arquivo and st.button("🔎 Ler e analisar com IA", type="primary"):
    with st.spinner("Extraindo texto do PDF..."):
        texto = extrair_texto_pdf(arquivo)
    with st.spinner("Analisando com a IA (pode levar até 1 minuto para PDFs grandes)..."):
        try:
            resultado = chamar_gemini(texto)
            st.session_state["analise_atual"] = resultado
        except Exception as e:
            st.error(f"Erro ao consultar a IA: {e}")
            st.session_state["analise_atual"] = None

analise = st.session_state["analise_atual"]

if analise:
    st.header("2) Resultado da análise")
    st.info(analise.get("resumo_caso", ""))

    if not analise.get("pronto_para_sentenca"):
        st.warning(f"⚠️ A IA avaliou que este processo NÃO está pronto para sentença.\n\n"
                   f"Motivo: {analise.get('motivo_se_nao_pronto', '(não informado)')}")
    else:
        tipo = analise.get("tipo_template")
        tpl_id = analise.get("template_id")
        st.success(f"Modelo sugerido: **{tpl_id}** ({'oficial/fixo' if tipo == 'fixo' else 'flexível'})")

        st.header("3) Revise os campos antes de gerar")
        st.caption("Edite qualquer campo que a IA tenha errado ou deixado incompleto.")

        valores_editados = {}
        for item in analise.get("campos", []):
            valores_editados[item["campo"]] = st.text_area(
                item["campo"], value=item.get("valor", ""), height=70
            )

        id_caso = st.text_input("Identificador do caso (nº do processo / nome do autor)", "")

        if st.button("✅ Gerar minuta", type="primary"):
            if not id_caso:
                st.error("Informe um identificador para o caso.")
            else:
                try:
                    if tipo == "fixo":
                        registry = carregar_registry_fixos()
                        tpl = registry[tpl_id]
                        template_path = MODELOS_DIR / tpl["arquivo"]
                        tmp_out = BASE_DIR / f"_tmp_{id_caso}.docx"
                        if tpl["mapeamento"] is None:
                            tmp_out.write_bytes(template_path.read_bytes())
                        else:
                            with open(MODELOS_DIR / tpl["mapeamento"], encoding="utf-8") as f:
                                mapeamento = json.load(f)
                            preencher(str(template_path), mapeamento, valores_editados, str(tmp_out),
                                      manter_destaque=True)
                    else:
                        texto_final = render_text(tpl_id, valores_editados)
                        tmp_out = BASE_DIR / f"_tmp_{id_caso}.docx"
                        text_to_docx(texto_final, tmp_out)

                    st.session_state["minutas_geradas"][f"{id_caso}.docx"] = tmp_out.read_bytes()
                    tmp_out.unlink(missing_ok=True)
                    st.success(f"Minuta gerada: {id_caso}.docx — veja na seção 4 abaixo.")
                except Exception as e:
                    st.error(f"Erro ao gerar a minuta: {e}")

st.header("4) Minutas geradas nesta sessão")
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
