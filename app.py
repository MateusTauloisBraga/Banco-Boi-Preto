import io
import json
import os
import uuid
from datetime import datetime

import pandas as pd
import streamlit as st
from filelock import FileLock
from PIL import Image

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None

try:
    from st_audiorec import st_audiorec
except Exception:  # pragma: no cover
    st_audiorec = None


APP_TITLE = "Banco Boi Preto"
DATA_DIR = "data"
EXCEL_PATH = os.path.join(DATA_DIR, "banco_boi_preto.xlsx")
LOCK_PATH = EXCEL_PATH + ".lock"

SHEET_BOI_PRETO = "BoiPreto"
SHEET_ATIVIDADES = "Atividades"

COLUMNS_BOI_PRETO = [
    "Submission ID",
    "Criado em",
    "Sexo",
    "Finisher",
    "Tempo Finisher Boi Preto",
    "Transcrição",
]

COLUMNS_ATIVIDADES = [
    "Submission ID",
    "Prova",
    "Distância",
    "Altimetria",
    "Tempo",
]


def ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def load_db() -> pd.DataFrame:
    """
    Retorna uma visão "achatada" (join) de BoiPreto + Atividades para visualização/download.
    """
    ensure_data_dir()
    boi, atv = load_workbook()
    if atv.empty:
        # garante colunas da visão final mesmo sem atividades
        view_cols = ["Sexo", "Finisher", "Tempo Finisher Boi Preto", "Prova", "Distância", "Altimetria", "Tempo"]
        return pd.DataFrame(columns=view_cols)
    joined = atv.merge(boi, on="Submission ID", how="left")
    view = joined[
        [
            "Sexo",
            "Finisher",
            "Tempo Finisher Boi Preto",
            "Transcrição",
            "Prova",
            "Distância",
            "Altimetria",
            "Tempo",
        ]
    ].copy()
    return view


def _ensure_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    out = df.copy()
    for c in columns:
        if c not in out.columns:
            out[c] = ""
    return out[columns]


def load_workbook() -> tuple[pd.DataFrame, pd.DataFrame]:
    ensure_data_dir()
    if not os.path.exists(EXCEL_PATH):
        boi = pd.DataFrame(columns=COLUMNS_BOI_PRETO)
        atv = pd.DataFrame(columns=COLUMNS_ATIVIDADES)
        return boi, atv

    try:
        boi = pd.read_excel(EXCEL_PATH, sheet_name=SHEET_BOI_PRETO)
    except Exception:
        boi = pd.DataFrame(columns=COLUMNS_BOI_PRETO)
    try:
        atv = pd.read_excel(EXCEL_PATH, sheet_name=SHEET_ATIVIDADES)
    except Exception:
        atv = pd.DataFrame(columns=COLUMNS_ATIVIDADES)

    boi = _ensure_columns(boi, COLUMNS_BOI_PRETO)
    atv = _ensure_columns(atv, COLUMNS_ATIVIDADES)
    return boi, atv


def append_rows(boi_row: pd.DataFrame, atividades_rows: pd.DataFrame) -> None:
    ensure_data_dir()
    with FileLock(LOCK_PATH):
        boi, atv = load_workbook()
        boi_row = _ensure_columns(boi_row, COLUMNS_BOI_PRETO)
        atividades_rows = _ensure_columns(atividades_rows, COLUMNS_ATIVIDADES)

        boi_out = pd.concat([boi, boi_row], ignore_index=True)
        atv_out = pd.concat([atv, atividades_rows], ignore_index=True)

        # Reescreve o arquivo inteiro com duas abas (mais robusto que append em xlsx)
        with pd.ExcelWriter(EXCEL_PATH, engine="openpyxl") as writer:
            boi_out.to_excel(writer, sheet_name=SHEET_BOI_PRETO, index=False)
            atv_out.to_excel(writer, sheet_name=SHEET_ATIVIDADES, index=False)


def fmt_hhmmss(hours: int, minutes: int, seconds: int) -> str:
    hours = int(hours)
    minutes = int(minutes)
    seconds = int(seconds)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def get_openai_client():
    if OpenAI is None:
        return None
    api_key = ""
    try:
        api_key = str(st.secrets.get("OPENAI_API_KEY", "")).strip()
    except Exception:
        api_key = ""
    if not api_key:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    return OpenAI(api_key=api_key)


def llm_transcribe_audio(uploaded_file) -> str:
    """
    Transcreve áudio para texto usando OpenAI (se configurado).
    """
    client = get_openai_client()
    if client is None:
        return ""

    if uploaded_file is None:
        return ""

    # Streamlit UploadedFile / audio_input geralmente expõe .getvalue() e .name
    if hasattr(uploaded_file, "getvalue"):
        audio_bytes = uploaded_file.getvalue()
    else:
        # pode ser bytes (ex.: streamlit-audiorec)
        audio_bytes = uploaded_file
    if not audio_bytes:
        return ""

    import io as _io

    f = _io.BytesIO(audio_bytes)
    filename = getattr(uploaded_file, "name", None) or "audio.wav"
    f.name = filename  # alguns clientes usam extensão via name

    # Preferência: whisper-1 (amplamente suportado). Pode ser sobrescrito via env.
    model = os.getenv("OPENAI_TRANSCRIBE_MODEL", "whisper-1")
    resp = client.audio.transcriptions.create(
        model=model,
        file=f,
    )
    # resp.text no client atual
    return (getattr(resp, "text", None) or "").strip()


def get_logo_image():
    logo_path = os.path.join(os.getcwd(), "logo.png")
    if not os.path.exists(logo_path):
        return None
    try:
        return Image.open(logo_path)
    except Exception:
        return None


def llm_fill_altimetry(df: pd.DataFrame) -> pd.DataFrame:
    """
    Tenta preencher Altimetria vazia usando busca na web via OpenAI tools (quando disponível).
    Se o ambiente/modelo não suportar ferramenta de busca, mantém como está.
    """
    client = get_openai_client()
    if client is None:
        return df

    out = df.copy()
    for i in range(len(out)):
        prova = str(out.loc[i, "Prova"] or "").strip()
        distancia = str(out.loc[i, "Distância"] or "").strip()
        alt = str(out.loc[i, "Altimetria"] or "").strip()
        if alt or not prova:
            continue

        schema_name = "boi_preto_altimetry"
        schema = {
            "type": "object",
            "properties": {"altimetria": {"type": "string"}},
            "required": ["altimetria"],
            "additionalProperties": False,
        }

        prompt = f"""
Encontre na web a altimetria (ganho de elevação / D+) da prova abaixo.
Retorne SOMENTE um texto curto com o valor e unidade, exemplo: "+850 m" ou "850 m".
Se não encontrar com confiança, retorne "".

Prova: {prova}
Distância: {distancia}
""".strip()

        try:
            resp = client.responses.create(
                model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
                input=prompt,
                tools=[{"type": "web_search_preview"}],
                text={"format": {"type": "json_schema", "name": schema_name, "schema": schema}},
            )
            data = json.loads(resp.output_text)
            out.loc[i, "Altimetria"] = (data.get("altimetria") or "").strip()
        except Exception:
            continue

    return out


def llm_extract_rows(text: str) -> pd.DataFrame:
    """
    Converte texto livre (provas/treinos) em linhas estruturadas.
    Retorna 1..N linhas, sem inventar dados (campos ausentes ficam vazios).
    """
    client = get_openai_client()
    if client is None:
        return pd.DataFrame([{"Prova": "", "Distância": "", "Altimetria": "", "Tempo": ""}])

    schema_name = "boi_preto_rows"
    schema = {
        "type": "object",
        "properties": {
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "Prova": {"type": "string"},
                        "Distância": {"type": "string"},
                        "Altimetria": {"type": "string"},
                        "Tempo": {"type": "string"},
                    },
                    "required": ["Prova", "Distância", "Altimetria", "Tempo"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["rows"],
        "additionalProperties": False,
    }

    prompt = f"""
Você vai receber um texto em português descrevendo provas e/ou treinos (texto não estruturado).
Transforme em linhas de tabela.

Prioridade de extração (quase sempre presentes):
- **Nome da prova**
- **Distância**
- **Tempo**
E, ocasionalmente, **Altimetria** quando houver indícios no texto.

Regras:
- Se o texto mencionar mais de uma prova/treino, retorne múltiplas linhas.
- Colunas:
  - Prova: nome da prova/treino (ou descrição curta)
  - Distância: valor + unidade (ex: "42 km", "21k", "27 km", "70.3", "10 km")
  - Altimetria: ganho de elevação / elevação / D+. Se não houver, deixe "".
  - Tempo: tempo associado (ex: "1:35:20", "5h12", "DNF"). Se não houver, "".
- NÃO invente dados. Se não estiver no texto, deixe vazio.

Interpretação de termos comuns de altimetria (exemplos):
- "ganho", "elevação", "D+", "altimetria" significam **Altimetria**.
- Expressões como **"com mil"**, **"com 1000"**, **"com 1.000"** indicam altimetria de ~**1000 m**.
  Ex.: "Prova WTR de 27k com mil" → Prova: "WTR", Distância: "27 km", Altimetria: "1000 m".

Texto:
\"\"\"{text}\"\"\"
""".strip()

    resp = client.responses.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        input=prompt,
        text={"format": {"type": "json_schema", "name": schema_name, "schema": schema}},
    )
    data = json.loads(resp.output_text)

    rows = []
    for r in data.get("rows", []):
        rows.append(
            {
                "Prova": (r.get("Prova") or "").strip(),
                "Distância": (r.get("Distância") or "").strip(),
                "Altimetria": (r.get("Altimetria") or "").strip(),
                "Tempo": (r.get("Tempo") or "").strip(),
            }
        )

    if not rows:
        rows = [
            {
                "Prova": "",
                "Distância": "",
                "Altimetria": "",
                "Tempo": "",
            }
        ]

    return pd.DataFrame(rows, columns=["Prova", "Distância", "Altimetria", "Tempo"])


def llm_apply_correction_prompt(df: pd.DataFrame, correction_prompt: str) -> pd.DataFrame:
    client = get_openai_client()
    if client is None:
        return df

    schema_name = "boi_preto_rows_corrected"
    schema = {
        "type": "object",
        "properties": {
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {c: {"type": "string"} for c in ["Prova", "Distância", "Altimetria", "Tempo"]},
                    "required": ["Prova", "Distância", "Altimetria", "Tempo"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["rows"],
        "additionalProperties": False,
    }

    prompt = f"""
Você receberá uma tabela (linhas) e um pedido de correção do usuário.
Aplique SOMENTE as correções solicitadas e devolva a tabela completa (todas as colunas, todas as linhas).

Tabela (JSON):
{df.to_json(orient="records", force_ascii=False)}

Pedido do usuário:
\"\"\"{correction_prompt}\"\"\"
""".strip()

    resp = client.responses.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        input=prompt,
        text={"format": {"type": "json_schema", "name": schema_name, "schema": schema}},
    )
    data = json.loads(resp.output_text)
    rows = data.get("rows", [])
    out = pd.DataFrame(rows, columns=["Prova", "Distância", "Altimetria", "Tempo"])
    for c in ["Prova", "Distância", "Altimetria", "Tempo"]:
        if c not in out.columns:
            out[c] = ""
    return out[["Prova", "Distância", "Altimetria", "Tempo"]]


def render_header():
    logo_img = get_logo_image()
    st.set_page_config(page_title=APP_TITLE, page_icon=logo_img or "📄", layout="centered")
    if logo_img is not None:
        st.image(logo_img, width=220)
    st.title(APP_TITLE)
    st.markdown(
        "Obrigado por participar! A intenção deste banco é **prever o tempo de prova** das pessoas "
        "para conseguirmos **organizar a prova de maneira mais eficiente**."
    )
    st.markdown(
        "**Como preencher (passo a passo):**\n"
        "- **1)** Grave um áudio contando suas provas/treinos (ou digite o texto).\n"
        "- **2)** Ao **parar de gravar**, a transcrição aparece automaticamente.\n"
        "- **3)** Confira/edite a transcrição.\n"
        "- **4)** Clique em **Gerar Tabela** — o app gera e **salva automaticamente** no banco.\n"
    )


def main():
    render_header()

    sexo = st.selectbox(
        "Sexo",
        options=["Prefiro não informar", "M", "F"],
        index=0,
    )

    finisher = st.selectbox(
        "Você já fez a Boi Preto?",
        options=["Não", "Sim"],
        index=0,
    )

    tempo_finisher = ""
    if finisher == "Sim":
        c1, c2, c3 = st.columns(3)
        with c1:
            hh = st.number_input("Horas", min_value=0, max_value=99, value=0)
        with c2:
            mm = st.number_input("Minutos", min_value=0, max_value=59, value=0)
        with c3:
            ss = st.number_input("Segundos", min_value=0, max_value=59, value=0)
        tempo_finisher = fmt_hhmmss(hh, mm, ss)

    st.divider()

    st.subheader("Conte sua experiência")
    st.caption("Grave um áudio (estilo WhatsApp) ou digite. Ao parar de gravar, transcrevemos automaticamente.")

    audio_recorded = None
    if hasattr(st, "audio_input"):
        audio_recorded = st.audio_input("Opcional: grave um áudio (como no WhatsApp)")
    elif st_audiorec is not None:
        st.caption("Gravação por microfone (modo WhatsApp)")
        audio_recorded = st_audiorec()
        # st_audiorec retorna bytes WAV (ou None)
        if audio_recorded is not None:
            st.audio(audio_recorded, format="audio/wav")
    else:
        st.warning("Gravação não disponível neste ambiente. Atualize o Streamlit ou habilite o componente de áudio.")

    audio = audio_recorded
    if audio is not None:
        # Auto-transcrição quando o áudio muda (parou de gravar / novo upload)
        try:
            audio_bytes = audio.getvalue() if hasattr(audio, "getvalue") else audio
        except Exception:
            audio_bytes = None

        if audio_bytes:
            audio_key = f"{len(audio_bytes)}:{hash(audio_bytes[:2048])}"
            if st.session_state.get("last_audio_key") != audio_key:
                st.session_state["last_audio_key"] = audio_key
                if OpenAI is None:
                    st.warning("Transcrição automática: instale `openai` para habilitar.")
                else:
                    has_key = False
                    try:
                        has_key = bool(str(st.secrets.get("OPENAI_API_KEY", "")).strip())
                    except Exception:
                        has_key = bool(os.getenv("OPENAI_API_KEY", "").strip())

                    if not has_key:
                        st.warning("Transcrição automática: configure `OPENAI_API_KEY` nos Secrets do Streamlit Cloud.")
                    else:
                        with st.spinner("Transcrevendo áudio..."):
                            try:
                                st.session_state["transcription"] = llm_transcribe_audio(audio)
                            except Exception as e:
                                st.error(f"Falha ao transcrever: {e}")
                        st.rerun()

    default_text = st.session_state.get("transcription", "")
    text = st.text_area(
        "Descreva o que você já fez de prova ou treino",
        height=180,
        placeholder="Ex: Fiz a Prova X (21 km) em 1:38:20 e a Prova Y (42 km) em 3:45...",
        value=default_text,
    )

    st.caption("A altimetria será buscada automaticamente quando possível (pode demorar).")

    if st.button("Salvar Resposta", type="primary", disabled=not bool(text.strip())):
        submission_id = str(uuid.uuid4())
        created_at = datetime.now().isoformat(timespec="seconds")

        boi_row = pd.DataFrame(
            [
                {
                    "Submission ID": submission_id,
                    "Criado em": created_at,
                    "Sexo": sexo,
                    "Finisher": finisher,
                    "Tempo Finisher Boi Preto": tempo_finisher if finisher == "Sim" else "",
                    "Transcrição": text.strip(),
                }
            ],
            columns=COLUMNS_BOI_PRETO,
        )
        with st.spinner("Transformando seu texto em tabela..."):
            df_new = llm_extract_rows(text=text.strip())
            df_new = llm_fill_altimetry(df_new)

        atividades = df_new.copy()
        atividades.insert(0, "Submission ID", submission_id)
        atividades = _ensure_columns(atividades, COLUMNS_ATIVIDADES)

        with st.spinner("Salvando no banco..."):
            append_rows(boi_row=boi_row, atividades_rows=atividades)

        st.success("Dados salvos com sucesso.")
        st.session_state["last_saved_preview_boi"] = boi_row
        st.session_state["last_saved_preview_atv"] = atividades
        try:
            st.session_state["last_saved_preview_joined"] = atividades.merge(
                boi_row[["Submission ID", "Sexo", "Finisher", "Tempo Finisher Boi Preto", "Transcrição"]],
                on="Submission ID",
                how="left",
            )[
                [
                    "Sexo",
                    "Finisher",
                    "Tempo Finisher Boi Preto",
                    "Transcrição",
                    "Prova",
                    "Distância",
                    "Altimetria",
                    "Tempo",
                ]
            ]
        except Exception:
            st.session_state["last_saved_preview_joined"] = atividades
        st.session_state.pop("transcription", None)

    if "last_saved_preview_boi" in st.session_state and "last_saved_preview_atv" in st.session_state:
        st.subheader("Resposta salva")
        st.dataframe(st.session_state.get("last_saved_preview_joined"), use_container_width=True)

    st.divider()
    st.info("Para acessar o banco completo (visualizar/baixar), use a aba **Admin**.")


if __name__ == "__main__":
    main()
