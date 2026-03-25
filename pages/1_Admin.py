import os

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from PIL import Image
import io


APP_TITLE = "Admin — Banco Boi Preto"
EXCEL_PATH = os.path.join("data", "banco_boi_preto.xlsx")
LOCK_PATH = EXCEL_PATH + ".lock"
SHEET_BOI_PRETO = "BoiPreto"
SHEET_ATIVIDADES = "Atividades"

def get_logo_image():
    logo_path = os.path.join(os.getcwd(), "logo.png")
    if not os.path.exists(logo_path):
        return None
    try:
        return Image.open(logo_path)
    except Exception:
        return None


def main():
    load_dotenv()

    logo_img = get_logo_image()
    st.set_page_config(page_title=APP_TITLE, page_icon=logo_img or "🔒", layout="wide")
    if logo_img is not None:
        st.image(logo_img, width=180)
    st.title(APP_TITLE)

    admin_password = os.getenv("ADMIN_PASSWORD", "")
    pwd = st.text_input("Senha admin", type="password")
    if not admin_password:
        st.warning("Defina `ADMIN_PASSWORD` no `.env` para habilitar o acesso admin.")
        st.stop()
    if pwd != admin_password:
        st.warning("Acesso restrito.")
        st.stop()

    st.subheader("Reset do banco")
    st.warning("Essa ação apaga o arquivo Excel do banco de dados. Não tem como desfazer.")
    confirm_reset = st.checkbox("Eu entendo e quero resetar o banco", value=False)
    confirm_text = st.text_input('Digite "RESETAR" para confirmar')
    if st.button("Resetar banco (apagar tudo)", type="primary", disabled=not (confirm_reset and confirm_text == "RESETAR")):
        try:
            if os.path.exists(EXCEL_PATH):
                os.remove(EXCEL_PATH)
            if os.path.exists(LOCK_PATH):
                os.remove(LOCK_PATH)
            st.success("Banco resetado. O próximo salvamento recriará o arquivo.")
            st.rerun()
        except Exception as e:
            st.error(f"Falha ao resetar banco: {e}")

    st.divider()

    if not os.path.exists(EXCEL_PATH):
        st.info("Ainda não existe banco salvo.")
        st.stop()

    st.subheader("Download do banco")
    with open(EXCEL_PATH, "rb") as f:
        st.download_button(
            "Baixar banco_boi_preto.xlsx",
            data=f.read(),
            file_name="banco_boi_preto.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    try:
        boi = pd.read_excel(EXCEL_PATH, sheet_name=SHEET_BOI_PRETO)
    except Exception:
        boi = pd.DataFrame(
            columns=["Submission ID", "Criado em", "Sexo", "Finisher", "Tempo Finisher Boi Preto", "Transcrição"]
        )
    try:
        atv = pd.read_excel(EXCEL_PATH, sheet_name=SHEET_ATIVIDADES)
    except Exception:
        atv = pd.DataFrame(columns=["Submission ID", "Prova", "Distância", "Altimetria", "Tempo"])

    for col in ["Submission ID", "Criado em", "Sexo", "Finisher", "Tempo Finisher Boi Preto", "Transcrição"]:
        if col not in boi.columns:
            boi[col] = ""
    for col in ["Submission ID", "Prova", "Distância", "Altimetria", "Tempo"]:
        if col not in atv.columns:
            atv[col] = ""

    joined = atv.merge(boi, on="Submission ID", how="left")

    st.divider()
    st.subheader("Visualização")

    col1, col2, col3 = st.columns(3)
    with col1:
        finisher = st.selectbox("Finisher", options=["(Todos)", "Sim", "Não"], index=0)
    with col2:
        sexo = st.selectbox("Sexo", options=["(Todos)", "M", "F", "Prefiro não informar"], index=0)
    with col3:
        q = st.text_input("Buscar (Prova)")

    only_missing_alt = st.checkbox("Somente sem altimetria", value=False)

    view = joined.copy()
    if finisher != "(Todos)":
        view = view[view["Finisher"] == finisher]
    if sexo != "(Todos)":
        view = view[view["Sexo"] == sexo]
    if q.strip():
        ql = q.strip().lower()
        view = view[view["Prova"].fillna("").str.lower().str.contains(ql)]
    if only_missing_alt:
        view = view[view["Altimetria"].fillna("").astype(str).str.strip() == ""]

    st.dataframe(view, use_container_width=True)

    st.caption("Aba BoiPreto")
    st.dataframe(boi, use_container_width=True)
    st.caption("Aba Atividades")
    st.dataframe(atv, use_container_width=True)


if __name__ == "__main__":
    main()

