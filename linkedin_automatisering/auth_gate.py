"""Enkel lösenordsinloggning innan huvudappen visas."""
from __future__ import annotations

import os

import streamlit as st


def app_password() -> str:
    """Lösenord från Streamlit Secrets (moln) eller .env (lokalt): APP_PASSWORD."""
    try:
        return str(st.secrets["APP_PASSWORD"]).strip()
    except Exception:
        return (os.getenv("APP_PASSWORD") or "").strip()


def require_login() -> None:
    if st.session_state.get("authenticated"):
        return

    _, col, _ = st.columns([1, 1.2, 1])
    with col:
        st.title("Inloggning")
        st.caption("LinkedIn Talare Automatisering")
        pwd = st.text_input("Lösenord", type="password", key="app_login_password")
        if st.button("Logga in", type="primary", use_container_width=True):
            expected = app_password()
            if expected and pwd == expected:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Fel lösenord.")
        if not app_password():
            st.warning(
                "Saknar `APP_PASSWORD`. Lägg i `.env` lokalt eller under "
                "**Secrets** i Streamlit Community Cloud."
            )

    st.stop()
