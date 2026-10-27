"""Marketing portal: manage offers and rules, see results, and preview any customer.

Run:  streamlit run portal/app.py
Saved changes go to config/*.yaml and apply to the bot's very next offer.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from portal import views  # noqa: E402

st.set_page_config(page_title="Offer Engine Portal", page_icon="🎁", layout="wide")
views.require_password()
with st.sidebar:
    st.markdown("### 🎁 Offer Engine")
    st.caption("Marketing portal for the Telegram offer bot. Saved changes apply to the next offer.")
nav = st.navigation([
    st.Page(views.overview, title="Overview", icon="📊", default=True),
    st.Page(views.offers_page, title="Offers", icon="🎁"),
    st.Page(views.rules_page, title="Rules", icon="⚙️"),
    st.Page(views.customer_page, title="Customer preview", icon="🔍"),
])
nav.run()
