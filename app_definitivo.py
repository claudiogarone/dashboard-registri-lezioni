import streamlit as st
import pandas as pd
import plotly.express as px
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from datetime import datetime
import unicodedata
import re

st.set_page_config(page_title="Registri Lezioni - Dashboard", layout="wide")

FOLDER_ID = "1fsy7Ep3Kbyfhx3NEfnZDryHNfefHmDBM"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

def norm_text(x):
    if pd.isna(x):
        return pd.NA
    s = str(x).strip()
    if s == "" or s.lower() in {"nan", "none", "null"}:
        return pd.NA
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\s+", " ", s)
    return s

def safe_sorted_unique(series):
    if series is None:
        return []
    vals = series.dropna().map(norm_text).dropna().astype(str).unique().tolist()
    return sorted(vals, key=lambda v: v.lower())

def to_hours(duration_str):
    x = norm_text(duration_str)
    if pd.isna(x):
        return 0.0
    try:
        parts = str(x).split(":")
        h = int(parts[0]) if len(parts) > 0 and parts[0] != "" else 0
        m = int(parts[1]) if len(parts) > 1 and parts[1] != "" else 0
        s = int(parts[2]) if len(parts) > 2 and parts[2] != "" else 0
        return round(h + m / 60 + s / 3600, 3)
    except Exception:
        return 0.0

def money_fmt(x):
    try:
        return f"€ {float(x):,.2f}"
    except Exception:
        return "€ 0,00"

@st.cache_resource(show_spinner=False)
def get_credentials():
    return Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=SCOPES
    )

@st.cache_data(ttl=300, show_spinner="Aggiornamento elenco fogli...")
def list_sheet_files(_creds):
    service = build("drive", "v3", credentials=_creds)
    query = (
        f"'{FOLDER_ID}' in parents and "
        "mimeType='application/vnd.google-apps.spreadsheet' and trashed=false"
    )
    results = service.files().list(
        q=query, fields="files(id, name)", pageSize=200
    ).execute()
    return results.get("files", [])

@st.cache_data(ttl=300, show_spinner="Caricamento registri lezioni...")
def load_all_data(_creds, files):
    gc = gspread.authorize(_creds)
    frames = []

    for f in files:
        try:
            sh = gc.open_by_key(f["id"])
            ws = sh.sheet1
            records = ws.get_all_records()
            if not records:
                continue
            df = pd.DataFrame(records)
            df["__file_name"] = f["name"]
            frames.append(df)
        except Exception as e:
            st.warning(f"Impossibile leggere il file '{f['name']}': {e}")

    if not frames:
        return pd.DataFrame()

    data = pd.concat(frames, ignore_index=True, sort=False)

    for col in data.columns:
        if data[col].dtype == object:
            data[col] = data[col].map(norm_text)

    for col in ["Allievo", "Materia", "Mese", "Anno", "Stato_Pagamento",
                "Compito Assegnato (Si/No)", "Compito Superato (Si/No)",
                "Argomento", "Note", "Pagamento Ricevuto"]:
        if col in data.columns:
            data[col] = data[col].map(norm_text)

    if "Allievo" not in data.columns or data["Allievo"].dropna().empty:
        data["Allievo"] = data["__file_name"].map(norm_text)

    if "Data" in data.columns:
        data["Data_dt"] = pd.to_datetime(data["Data"].astype(str).str.strip(), errors="coerce", dayfirst=True)

    for col in ["Totale Ore", "Durata Lezione (Ore)"]:
        if col in data.columns:
            data[col + "_h"] = data[col].apply(to_hours)

    pay_col = None
    for cand in ["Pagamento Ricevuto (EUR)", "Pagamento Ricevuto (€)", "Pagamento Ricevuto"]:
        if cand in data.columns:
            pay_col = cand
            break

    if pay_col:
        data["Pagamento_num"] = pd.to_numeric(
            data[pay_col].astype(str).str.replace(".", "", regex=False).str.replace(",", ".", regex=False),
            errors="coerce",
        ).fillna(0)
    else:
        data["Pagamento_num"] = 0.0

    if "Data Pagamento" in data.columns:
        data["Data_Pagamento_dt"] = pd.to_datetime(
            data["Data Pagamento"].astype(str).str.strip(), errors="coerce", dayfirst=True
        )

    if "Pagamento Ricevuto" in data.columns:
        data["Stato_Pagamento"] = data["Pagamento Ricevuto"].fillna("Non specificato")
        data["Stato_Pagamento"] = data["Stato_Pagamento"].replace("", "Non specificato")
    else:
        data["Stato_Pagamento"] = "Non specificato"

    return data

st.title("Dashboard Registri Lezioni Private")
st.caption("Dati letti in tempo reale dalla cartella Google Drive 'Registri Lezioni'")

creds = get_credentials()
files = list_sheet_files(creds)

if not files:
    st.error("Nessun foglio Google trovato nella cartella. Verifica condivisione e ID cartella.")
    st.stop()

df = load_all_data(creds, files)

if df.empty:
    st.error("Nessun dato disponibile nei fogli trovati.")
    st.stop()

st.sidebar.header("Filtri")

allievi = safe_sorted_unique(df["Allievo"]) if "Allievo" in df.columns else []
sel_allievi = st.sidebar.multiselect("Allievo", allievi, default=allievi)

materie = safe_sorted_unique(df["Materia"]) if "Materia" in df.columns else []
sel_materie = st.sidebar.multiselect("Materia", materie, default=materie) if materie else []

min_date = df["Data_dt"].min() if "Data_dt" in df.columns else pd.NaT
max_date = df["Data_dt"].max() if "Data_dt" in df.columns else pd.NaT
date_range = st.sidebar.date_input(
    "Intervallo date lezione",
    value=(min_date.date() if pd.notna(min_date) else datetime.today(),
           max_date.date() if pd.notna(max_date) else datetime.today()),
)

stati_pagamento = safe_sorted_unique(df["Stato_Pagamento"]) if "Stato_Pagamento" in df.columns else []
sel_stati_pagamento = st.sidebar.multiselect("Stato pagamento", stati_pagamento, default=stati_pagamento) if stati_pagamento else []

compito_assegnato_opts = safe_sorted_unique(df["Compito Assegnato (Si/No)"]) if "Compito Assegnato (Si/No)" in df.columns else []
sel_compito_assegnato = st.sidebar.multiselect("Compito assegnato", compito_assegnato_opts, default=compito_assegnato_opts) if compito_assegnato_opts else []

compito_superato_opts = safe_sorted_unique(df["Compito Superato (Si/No)"]) if "Compito Superato (Si/No)" in df.columns else []
sel_compito_superato = st.sidebar.multiselect("Compito superato", compito_superato_opts, default=compito_superato_opts) if compito_superato_opts else []

argomento_search = st.sidebar.text_input("Cerca in Argomento/Note (testo libero)").strip()

mesi_disponibili = safe_sorted_unique(df["Mese"]) if "Mese" in df.columns else []
sel_mesi = st.sidebar.multiselect("Mese", mesi_disponibili, default=mesi_disponibili) if mesi_disponibili else []

anni_disponibili = safe_sorted_unique(df["Anno"]) if "Anno" in df.columns else []
sel_anni = st.sidebar.multiselect("Anno", anni_disponibili, default=anni_disponibili) if anni_disponibili else []

mask = pd.Series(True, index=df.index)

if "Allievo" in df.columns and sel_allievi:
    mask &= df["Allievo"].map(norm_text).astype(str).isin(sel_allievi)

if "Materia" in df.columns and sel_materie:
    mask &= df["Materia"].map(norm_text).astype(str).isin(sel_materie)

if isinstance(date_range, tuple) and len(date_range) == 2 and "Data_dt" in df.columns:
    start, end = date_range
    mask &= df["Data_dt"].dt.date.between(start, end)

if "Stato_Pagamento" in df.columns and sel_stati_pagamento:
    mask &= df["Stato_Pagamento"].map(norm_text).astype(str).isin(sel_stati_pagamento)

if "Compito Assegnato (Si/No)" in df.columns and sel_compito_assegnato:
    mask &= df["Compito Assegnato (Si/No)"].map(norm_text).astype(str).isin(sel_compito_assegnato)

if "Compito Superato (Si/No)" in df.columns and sel_compito_superato:
    mask &= df["Compito Superato (Si/No)"].map(norm_text).astype(str).isin(sel_compito_superato)

if "Mese" in df.columns and sel_mesi:
    mask &= df["Mese"].map(norm_text).astype(str).isin(sel_mesi)

if "Anno" in df.columns and sel_anni:
    mask &= df["Anno"].map(norm_text).astype(str).isin(sel_anni)

if argomento_search:
    cols_txt = [c for c in ["Argomento", "Note"] if c in df.columns]
    txt_mask = pd.Series(False, index=df.index)
    for c in cols_txt:
        txt_mask |= df[c].fillna("").astype(str).str.contains(argomento_search, case=False, na=False)
    if cols_txt:
        mask &= txt_mask

fdf = df[mask].copy()

st.sidebar.markdown(f"**Righe filtrate:** {len(fdf)} / {len(df)}")
st.sidebar.markdown(f"**Ultimo aggiornamento:** {datetime.now().strftime('%d/%m/%Y %H:%M')}")
if st.sidebar.button("Forza aggiornamento dati"):
    st.cache_data.clear()
    st.rerun()

if fdf.empty:
    st.warning("Nessuna riga corrisponde ai filtri selezionati.")
    st.stop()

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Lezioni", len(fdf))
col2.metric("Ore totali", round(fdf["Totale Ore_h"].sum(), 1) if "Totale Ore_h" in fdf else "-")
col3.metric("Allievi", fdf["Allievo"].nunique() if "Allievo" in fdf.columns else 0)
col4.metric("Incassato", money_fmt(fdf["Pagamento_num"].sum()))
non_pagate = fdf[fdf["Stato_Pagamento"].astype(str).str.lower().str.contains("no", na=False)] if "Stato_Pagamento" in fdf.columns else pd.DataFrame()
col5.metric("Lezioni non pagate", len(non_pagate))

st.divider()

tab_ore, tab_soldi, tab_argomenti, tab_presenze, tab_cluster, tab_dettaglio = st.tabs(
    ["Ore", "Pagamenti", "Argomenti", "Presenze", "Sotto-cluster", "Dettaglio"]
)

with tab_ore:
    st.subheader("Andamento ore di lezione nel tempo")
    granularita = st.radio("Aggrega per", ["Settimana", "Mese"], horizontal=True, key="gran_ore")
    freq = "W" if granularita == "Settimana" else "M"
    if "Data_dt" in fdf.columns and "Totale Ore_h" in fdf.columns:
        trend = (
            fdf.dropna(subset=["Data_dt"])
            .groupby([pd.Grouper(key="Data_dt", freq=freq), "Allievo"])["Totale Ore_h"]
            .sum()
            .reset_index()
        )
        if not trend.empty:
            fig1 = px.line(trend, x="Data_dt", y="Totale Ore_h", color="Allievo", markers=True,
                           labels={"Data_dt": granularita, "Totale Ore_h": "Ore"})
            st.plotly_chart(fig1, use_container_width=True)

    st.subheader("Totale ore per allievo")
    if "Totale Ore_h" in fdf.columns:
        tot_ore = fdf.groupby("Allievo")["Totale Ore_h"].sum().reset_index().sort_values("Totale Ore_h", ascending=False)
        if not tot_ore.empty:
            fig2 = px.bar(tot_ore, x="Allievo", y="Totale Ore_h", text_auto=".1f",
                          labels={"Totale Ore_h": "Ore totali"})
            st.plotly_chart(fig2, use_container_width=True)

    if "Materia" in fdf.columns and "Totale Ore_h" in fdf.columns:
        st.subheader("Ore per materia")
        tot_ore_materia = fdf.groupby("Materia")["Totale Ore_h"].sum().reset_index().sort_values("Totale Ore_h", ascending=False)
        if not tot_ore_materia.empty:
            fig2b = px.bar(tot_ore_materia, x="Materia", y="Totale Ore_h", text_auto=".1f")
            st.plotly_chart(fig2b, use_container_width=True)

with tab_soldi:
    st.subheader("Andamento incassi nel tempo")
    gran_pay = st.radio("Aggrega per", ["Settimana", "Mese"], horizontal=True, key="gran_pay")
    freq_pay = "W" if gran_pay == "Settimana" else "M"
    if "Data_dt" in fdf.columns:
        pay_time = (
            fdf.dropna(subset=["Data_dt"])
            .groupby([pd.Grouper(key="Data_dt", freq=freq_pay)])["Pagamento_num"]
            .sum()
            .reset_index()
        )
        pay_time["Cumulato"] = pay_time["Pagamento_num"].cumsum()
        c1, c2 = st.columns(2)
        with c1:
            if not pay_time.empty:
                fig_pt = px.bar(pay_time, x="Data_dt", y="Pagamento_num",
                                labels={"Data_dt": gran_pay, "Pagamento_num": "Incassato (EUR)"},
                                title="Incassato per periodo")
                st.plotly_chart(fig_pt, use_container_width=True)
        with c2:
            if not pay_time.empty:
                fig_cum = px.area(pay_time, x="Data_dt", y="Cumulato",
                                  labels={"Data_dt": gran_pay, "Cumulato": "Incassato cumulato (EUR)"},
                                  title="Incasso cumulato nel tempo")
                st.plotly_chart(fig_cum, use_container_width=True)

    st.subheader("Incassato per allievo")
    pay_sum = fdf.groupby("Allievo")["Pagamento_num"].sum().reset_index().sort_values("Pagamento_num", ascending=False)
    if not pay_sum.empty:
        fig5b = px.bar(pay_sum, x="Allievo", y="Pagamento_num", text_auto=".2f",
                       labels={"Pagamento_num": "Totale incassato (EUR)"})
        st.plotly_chart(fig5b, use_container_width=True)

    if "Materia" in fdf.columns:
        st.subheader("Incassato per materia")
        pay_materia = fdf.groupby("Materia")["Pagamento_num"].sum().reset_index().sort_values("Pagamento_num", ascending=False)
        if not pay_materia.empty:
            fig_pm = px.bar(pay_materia, x="Materia", y="Pagamento_num", text_auto=".2f")
            st.plotly_chart(fig_pm, use_container_width=True)

    st.subheader("Distribuzione stato pagamenti")
    c3, c4 = st.columns(2)
    with c3:
        pay_status = fdf["Stato_Pagamento"].value_counts().reset_index()
        pay_status.columns = ["Stato", "Numero lezioni"]
        if not pay_status.empty:
            fig5a = px.pie(pay_status, names="Stato", values="Numero lezioni", title="Per numero di lezioni")
            st.plotly_chart(fig5a, use_container_width=True)
    with c4:
        pay_status_amt = fdf.groupby("Stato_Pagamento")["Pagamento_num"].sum().reset_index()
        if not pay_status_amt.empty:
            fig5c = px.pie(pay_status_amt, names="Stato_Pagamento", values="Pagamento_num", title="Per importo (EUR)")
            st.plotly_chart(fig5c, use_container_width=True)

    st.subheader("Lezioni non pagate / in sospeso")
    cols_show = [c for c in ["Data", "Allievo", "Materia", "Numero Lezione", "Stato_Pagamento", "Pagamento_num"] if c in fdf.columns]
    st.dataframe(non_pagate[cols_show] if not non_pagate.empty else pd.DataFrame(columns=cols_show),
                 use_container_width=True, hide_index=True)

with tab_argomenti:
    st.subheader("Argomenti trattati")
    if "Argomento" in fdf.columns:
        arg_sel = st.selectbox("Filtra ulteriormente per allievo", ["Tutti"] + allievi, key="arg_sel")
        arg_df = fdf if arg_sel == "Tutti" else fdf[fdf["Allievo"] == arg_sel]
        cols_arg = [c for c in ["Data", "Allievo", "Materia", "Numero Lezione", "Argomento"] if c in arg_df.columns]
        if cols_arg:
            sort_col = "Data_dt" if "Data_dt" in arg_df.columns else (cols_arg[0] if cols_arg else None)
            if sort_col:
                st.dataframe(
                    arg_df[cols_arg].sort_values(sort_col, na_position="last"),
                    use_container_width=True, hide_index=True,
                )

with tab_presenze:
    st.subheader("Lezioni svolte per mese")
    st.caption("Proxy: ogni riga registrata equivale a una lezione svolta.")
    if "Data_dt" in fdf.columns:
        pres = fdf.dropna(subset=["Data_dt"]).copy()
        pres["Mese_anno"] = pres["Data_dt"].dt.to_period("M").astype(str)
        pres_count = pres.groupby(["Mese_anno", "Allievo"]).size().reset_index(name="Lezioni")
        if not pres_count.empty:
            fig4 = px.bar(pres_count, x="Mese_anno", y="Lezioni", color="Allievo", barmode="group")
            st.plotly_chart(fig4, use_container_width=True)

    if "Voto" in fdf.columns:
        st.subheader("Andamento voti/valutazioni")
        voto_df = fdf.copy()
        voto_df["Voto_num"] = pd.to_numeric(voto_df["Voto"], errors="coerce")
        voto_df = voto_df.dropna(subset=["Voto_num"])
        if not voto_df.empty:
            fig_voto = px.line(voto_df.sort_values("Data_dt"), x="Data_dt", y="Voto_num", color="Allievo", markers=True)
            st.plotly_chart(fig_voto, use_container_width=True)

with tab_cluster:
    st.subheader("Esplora sotto-cluster (drill-down)")
    dims_disponibili = [c for c in ["Allievo", "Materia", "Mese", "Anno", "Stato_Pagamento"] if c in fdf.columns]
    if dims_disponibili:
        c1, c2 = st.columns(2)
        with c1:
            dim1 = st.selectbox("Raggruppa per (livello 1)", dims_disponibili, index=0)
        with c2:
            dim2_opts = ["(nessuno)"] + [d for d in dims_disponibili if d != dim1]
            dim2 = st.selectbox("Suddividi per (livello 2)", dim2_opts, index=0)

        metrica = st.selectbox("Metrica da visualizzare", ["Ore totali", "Incassato (EUR)", "Numero lezioni"])
        metrica_col = {"Ore totali": "Totale Ore_h", "Incassato (EUR)": "Pagamento_num", "Numero lezioni": None}[metrica]

        if dim2 == "(nessuno)":
            if metrica_col and metrica_col in fdf.columns:
                agg = fdf.groupby(dim1)[metrica_col].sum().reset_index()
            else:
                agg = fdf.groupby(dim1).size().reset_index(name="Numero lezioni")
                metrica_col = "Numero lezioni"
            if not agg.empty:
                fig_c = px.bar(agg.sort_values(metrica_col, ascending=False), x=dim1, y=metrica_col, text_auto=".1f")
                st.plotly_chart(fig_c, use_container_width=True)
        else:
            if metrica_col and metrica_col in fdf.columns:
                agg = fdf.groupby([dim1, dim2])[metrica_col].sum().reset_index()
            else:
                agg = fdf.groupby([dim1, dim2]).size().reset_index(name="Numero lezioni")
                metrica_col = "Numero lezioni"
            if not agg.empty:
                fig_c = px.bar(agg, x=dim1, y=metrica_col, color=dim2, barmode="group", text_auto=".1f")
                st.plotly_chart(fig_c, use_container_width=True)

        st.subheader("Vista Sunburst (multi-livello)")
        sun_dims = st.multiselect("Livelli gerarchia (in ordine)", dims_disponibili, default=dims_disponibili[:2])
        if sun_dims:
            sun_metric = st.selectbox("Metrica sunburst", ["Ore totali", "Incassato (EUR)"], key="sun_metric")
            sun_col = "Totale Ore_h" if sun_metric == "Ore totali" else "Pagamento_num"
            if sun_col in fdf.columns:
                fig_sun = px.sunburst(fdf, path=sun_dims, values=sun_col)
                st.plotly_chart(fig_sun, use_container_width=True)

with tab_dettaglio:
    st.subheader("Dettaglio registro (righe filtrate)")
    df_out = fdf.drop(columns=["Data_dt", "Data_Pagamento_dt"], errors="ignore")
    st.dataframe(df_out, use_container_width=True, hide_index=True)
    csv = df_out.to_csv(index=False).encode("utf-8")
    st.download_button("Scarica CSV filtrato", csv, "registro_filtrato.csv", "text/csv")