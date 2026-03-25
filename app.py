import io
import json
import os
import uuid
from datetime import datetime

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
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

Regras:
- Se o texto mencionar mais de uma prova/treino, retorne múltiplas linhas.
- Colunas:
  - Prova: nome da prova/treino (ou descrição curta)
  - Distância: valor + unidade (ex: "42 km", "21k", "70.3", "10 km")
  - Altimetria: se for prova e você conseguir identificar no texto, preencha; senão, deixe "".
  - Tempo: tempo associado (ex: "1:35:20", "5h12", "DNF"). Se não houver, "".
- NÃO invente dados. Se não estiver no texto, deixe vazio.

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


def main():
    load_dotenv()
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
    st.caption("Você pode digitar ou enviar um áudio (nós transcrevemos).")

    audio_recorded = None
    if hasattr(st, "audio_input"):
        audio_recorded = st.audio_input("Opcional: grave um áudio (como no WhatsApp)")
    elif st_audiorec is not None:
        st.caption("Gravação por microfone (modo WhatsApp)")
        audio_recorded = st_audiorec()
        # st_audiorec retorna bytes WAV (ou None)
        if audio_recorded is not None:
            st.audio(audio_recorded, format="audio/wav")

    audio_uploaded = st.file_uploader(
        "Ou envie um áudio (mp3, wav, m4a)",
        type=["mp3", "wav", "m4a", "mpeg", "mp4"],
        accept_multiple_files=False,
    )

    audio = audio_recorded or audio_uploaded
    if audio is not None:
        if audio_uploaded is not None:
            st.audio(audio_uploaded)
        if st.button("Transcrever áudio", type="secondary"):
            if OpenAI is None:
                st.error("Instale `openai` para habilitar transcrição.")
            elif not os.getenv("OPENAI_API_KEY"):
                st.error("Configure `OPENAI_API_KEY` no `.env` para transcrever.")
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

    auto_altimetry = st.checkbox("Tentar buscar altimetria automaticamente (pode demorar)", value=True)

    if OpenAI is None:
        st.info("Instale `openai` para habilitar a extração automática pela OpenAI.")
    elif not os.getenv("OPENAI_API_KEY"):
        st.warning("Configure `OPENAI_API_KEY` no `.env` para habilitar a extração automática.")

    if st.button("Gerar tabela", type="primary", disabled=not bool(text.strip())):
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
                }
            ],
            columns=COLUMNS_BOI_PRETO,
        )
        with st.spinner("Transformando seu texto em tabela..."):
            df_new = llm_extract_rows(text=text.strip())
            if auto_altimetry:
                df_new = llm_fill_altimetry(df_new)

        atividades = df_new.copy()
        atividades.insert(0, "Submission ID", submission_id)
        atividades = _ensure_columns(atividades, COLUMNS_ATIVIDADES)

        st.session_state["boi_row"] = boi_row
        st.session_state["df_edited"] = atividades

    if "df_edited" in st.session_state:
        st.subheader("Prévia (edite antes de salvar)")
        df_edited = st.data_editor(
            st.session_state["df_edited"],
            num_rows="dynamic",
            use_container_width=True,
        )
        st.session_state["df_edited"] = df_edited

        colA, colB = st.columns([1, 2])
        with colA:
            validate_ok = st.checkbox("Excel está OK (validar e salvar)", value=False)
        with colB:
            correction_prompt = st.text_input("Se não estiver OK, descreva a correção (prompt)")

        c1, c2 = st.columns(2)
        with c1:
            if st.button("Aplicar correção pelo prompt", disabled=not bool(correction_prompt.strip())):
                with st.spinner("Aplicando correções..."):
                    fixed = llm_apply_correction_prompt(
                        st.session_state["df_edited"][["Prova", "Distância", "Altimetria", "Tempo"]],
                        correction_prompt.strip(),
                    )
                    fixed.insert(0, "Submission ID", st.session_state["df_edited"]["Submission ID"].iloc[0])
                    st.session_state["df_edited"] = _ensure_columns(fixed, COLUMNS_ATIVIDADES)
                st.rerun()

        with c2:
            if st.button("Salvar no Excel (adicionar)", disabled=not validate_ok):
                boi_row = st.session_state.get("boi_row")
                atividades_rows = st.session_state["df_edited"].copy()
                with st.spinner("Salvando..."):
                    append_rows(boi_row=boi_row, atividades_rows=atividades_rows)
                st.success("Salvo com sucesso (dados adicionados ao Excel).")
                st.session_state.pop("df_edited", None)
                st.session_state.pop("boi_row", None)
                st.session_state.pop("transcription", None)

    st.divider()
    st.info("Para acessar o banco completo (visualizar/baixar), use a aba **Admin**.")


if __name__ == "__main__":
    main()
