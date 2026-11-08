"""Expense Tracker Dashboard.  Run: streamlit run app.py"""
import os
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import analytics as an
import captions as cap
import db

st.set_page_config(page_title="Expense Tracker", page_icon=":material/account_balance_wallet:", layout="wide")

# Palette (fixed colour per entity)
C_IN, C_OUT, C_FIX, C_VAR = "#2a78d6", "#eb6834", "#6250d6", "#1baf7a"
YEAR_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
MUTED = "#898781"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def style(fig, title, height=320):
    fig.update_layout(
        title=dict(text=title, font=dict(size=14)), height=height, margin=dict(l=0, r=0, t=44, b=0),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", hovermode="x unified",
        legend=dict(orientation="h", y=-0.18, x=0), font=dict(color=MUTED))
    fig.update_xaxes(showgrid=False, linecolor=MUTED)
    fig.update_yaxes(gridcolor="rgba(137,135,129,0.18)", tickprefix="₹", zeroline=False)
    return fig


def show(fig, alt):
    st.plotly_chart(fig, width="stretch", alt=alt)


def inr(v):
    return f"₹{v:,.0f}"


TYPE_LABEL = {"inflow": "Income", "fixed": "Fixed", "variable": "Variable"}
DEFAULT_CATEGORIES = {
    "inflow": ["Salary", "Bonus", "Freelance", "Interest", "Dividends", "Gift", "Refund", "Rental"],
    "fixed": ["Subscriptions", "EMI", "SIP", "Gym", "Fuel", "Protein", "Rent", "Insurance"],
    "variable": ["Dining", "Groceries", "Transport", "Shopping", "Entertainment", "Travel", "Medical"],
}
DEFAULT_TAGS = ["Habit", "Unplanned", "Impromptu", "Medical", "Trip"]


def merge_options(*lists):
    """Union of option lists, Letter Case, no case-duplicates (first spelling wins), sorted."""
    seen = {}
    for lst in lists:
        for v in lst:
            seen.setdefault(db.fmt(v).casefold(), db.fmt(v))
    return sorted(seen.values(), key=str.casefold)


def categories_for(types):
    used = entries.loc[entries["type"].isin(types), "category"].unique().tolist() if not entries.empty else []
    return merge_options(used, *[DEFAULT_CATEGORIES[t] for t in types])


def joke(text):
    st.caption(f":material/sentiment_satisfied: *{text}*")


# =============================================================== users / lock
users = db.list_users()
st.session_state.setdefault("unlocked", set())

with st.sidebar:
    st.markdown("### :material/account_balance_wallet: Expense Tracker")
    # A widget's key can't be assigned after the widget exists, so other code leaves a request here
    # and it is applied before the selectbox below is created.
    if "_pick_user" in st.session_state:
        wanted = st.session_state.pop("_pick_user")
        if wanted:
            st.session_state["who"] = wanted
        else:
            st.session_state.pop("who", None)
    if users:
        names = [u["name"] for u in users]
        who = st.selectbox("User", names, key="who", label_visibility="collapsed")
        current = users[names.index(who)]
    else:
        current = None
    with st.expander(":material/person_add: Add user", expanded=not users):
        with st.form("new_user", clear_on_submit=True):
            nu = st.text_input("Name")
            pin = st.text_input("PIN (optional)", type="password", help="Stored only as a salted SHA-256 hash.")
            if st.form_submit_button("Create", width="stretch"):
                try:
                    db.create_user(nu, pin)
                    st.session_state["_pick_user"] = nu.strip()
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))

if current is None:
    st.title("Welcome")
    st.info("Create your first user in the sidebar to begin. Each user gets a separate private database.")
    st.stop()

if current["locked"] and current["path"] not in st.session_state["unlocked"]:
    st.title(f"Hi {current['name']}")
    with st.form("unlock"):
        p = st.text_input("PIN", type="password")
        if st.form_submit_button("Unlock"):
            if db.verify_pin(current["path"], p):
                st.session_state["unlocked"].add(current["path"])
                st.rerun()
            else:
                st.error("Wrong PIN.")
    st.stop()

db.set_active(current["path"])
db.init_db()
if st.session_state.get("backed_up") != current["path"]:  # one automatic snapshot per session
    db.backup_if_stale(current["path"])
    st.session_state["backed_up"] = current["path"]

posted = db.post_due_fixed()
if posted:
    st.toast(f"Auto-posted {posted} due fixed expense(s)", icon=":material/event_repeat:")

entries, fixed_items, budgets = db.get_entries(), db.get_fixed_items(), db.get_budgets()
today = date.today()
seed = f"{current['name']}-{today:%Y-%m}"

PAGES = {"Dashboard": ":material/dashboard:", "Add entry": ":material/add_circle:",
         "Fixed items": ":material/event_repeat:", "Budgets": ":material/savings:",
         "Insights": ":material/insights:", "History": ":material/history:",
         "Backup": ":material/cloud_upload:"}
with st.sidebar:
    page = st.radio("Go to", list(PAGES), format_func=lambda p: f"{p}", label_visibility="collapsed",
                    key="page")
    st.caption(f"Signed in as **{current['name']}**")


# =============================================================== helpers
def entry_editor(df: pd.DataFrame, key: str, height="auto"):
    """Table with a Delete checkbox column; deletes selected rows (a backup is taken first)."""
    if df.empty:
        st.caption("No entries yet.")
        return
    view = df[["id", "date", "type", "category", "amount", "tag", "note"]].copy()
    view["date"] = view["date"].dt.date
    view["type"] = view["type"].map(TYPE_LABEL)
    view.insert(0, "delete", False)
    edited = st.data_editor(
        view, hide_index=True, key=key, height=height, width="stretch",
        disabled=[c for c in view.columns if c != "delete"],
        column_config={"delete": st.column_config.CheckboxColumn("Delete", width="small"),
                       "id": None, "category": "Category / source", "tag": "Tags",
                       "amount": st.column_config.NumberColumn("Amount", format="₹%.0f")})
    ids = edited.loc[edited["delete"], "id"].tolist()
    if st.button(f"Delete {len(ids)} selected" if ids else "Delete selected", icon=":material/delete:",
                 disabled=not ids, key=f"{key}_btn"):
        db.backup()  # undo safety net: restore from the Backup page
        db.delete_entries(ids)
        st.toast(f"Deleted {len(ids)} entr{'y' if len(ids) == 1 else 'ies'}", icon=":material/delete:")
        st.rerun()


# =============================================================== Dashboard
if page == "Dashboard":
    st.title("Overview")
    if entries.empty:
        st.info("No data yet. Add an entry from the sidebar.")
        joke(cap.pick("empty", seed))
        st.stop()
    ms_all = an.monthly_summary(entries)
    span = st.segmented_control("Range", ["6M", "12M", "All"], default="12M", label_visibility="collapsed")
    ms = ms_all if span == "All" else ms_all.tail(6 if span == "6M" else 12)

    cur = pd.Timestamp(today).to_period("M").to_timestamp()
    row = ms_all.loc[cur] if cur in ms_all.index else ms_all.iloc[-1]
    before = ms_all[ms_all.index < row.name]
    prev = before.iloc[-1] if len(before) else None

    def delta(col):
        return None if prev is None else f"{row[col] - prev[col]:+,.0f} vs last month"

    rate = None if pd.isna(row["savings_rate"]) else row["savings_rate"]
    ratio = None if pd.isna(row["fixed_ratio"]) else row["fixed_ratio"]
    st.caption(f"{row.name:%B %Y}")
    with st.container(horizontal=True):
        st.metric("Income", inr(row["inflow"]), delta("inflow"), border=True)
        st.metric("Outflow", inr(row["outflow"]), delta("outflow"), delta_color="inverse", border=True)
        st.metric("Net", inr(row["net"]), delta("net"), border=True)
        st.metric("Savings rate", "–" if rate is None else f"{rate * 100:.0f}%", border=True)
        st.metric("Fixed ÷ income", "–" if ratio is None else f"{ratio * 100:.0f}%", border=True)

    with st.container(border=True):
        st.markdown("**Reading of the month**")
        bits = []
        if rate is not None:
            bits.append(f"You kept **{rate * 100:.0f}%** of income ({inr(row['net'])} net).")
        if row["outflow"]:
            bits.append(f"Variable spend is **{row['variable'] / row['outflow'] * 100:.0f}%** of outflow; "
                        f"fixed commitments are **{inr(row['fixed'])}**.")
        st.write(" ".join(bits) or "Not enough data yet.")
        joke(cap.savings(rate, seed))

    for a in an.detect_alerts(entries):
        {"danger": st.error, "warning": st.warning, "info": st.info}[a.level](
            f"{a.detail}  \n*{cap.alert(a.title, seed + a.title)}*", title=a.title)

    up = an.upcoming_fixed(fixed_items, days=30)
    c_left, c_right = st.columns([3, 2], gap="medium")
    with c_left:
        fig = go.Figure()
        fig.add_bar(x=ms.index, y=ms["inflow"], name="Income", marker_color=C_IN)
        fig.add_bar(x=ms.index, y=ms["outflow"], name="Outflow", marker_color=C_OUT)
        fig.add_scatter(x=ms.index, y=ms["net"], name="Net", mode="lines+markers",
                        line=dict(color=MUTED, width=2), marker=dict(size=8))
        fig.update_layout(barmode="group", bargap=0.35)
        show(style(fig, "Income vs outflow"), "Monthly income and outflow bars with net line")
    with c_right:
        with st.container(border=True, height=320):
            st.markdown("**Coming up (30 days)**")
            if up.empty:
                st.caption("Nothing due.")
                joke(cap.pick("upcoming_none", seed))
            else:
                st.metric("Total due", inr(up["amount"].sum()))
                st.dataframe(
                    up.assign(when=up["in_days"].map(lambda d: "today" if d == 0 else f"in {d}d"))
                      [["name", "when", "amount"]],
                    hide_index=True, width="stretch", height=150,
                    column_config={"amount": st.column_config.NumberColumn("₹", format="%.0f")})
                joke(cap.pick("upcoming", seed))

    c1, c2 = st.columns(2, gap="medium")
    with c1:
        yoy = an.yearly_comparison(ms_all, "outflow")
        fig = go.Figure()
        for i, y in enumerate(yoy.columns):
            fig.add_scatter(x=MONTHS, y=yoy[y], name=str(y), mode="lines+markers",
                            line=dict(color=YEAR_COLORS[i % 4], width=2), marker=dict(size=8))
        show(style(fig, "Outflow, year on year"), "Outflow by month, one line per year")
    with c2:
        fig = go.Figure()
        fig.add_bar(x=ms.index, y=ms["fixed"], name="Fixed", marker_color=C_FIX)
        fig.add_bar(x=ms.index, y=ms["variable"], name="Variable", marker_color=C_VAR)
        fig.update_layout(barmode="stack", bargap=0.35)
        show(style(fig, "Fixed vs variable"), "Stacked bars of fixed and variable outflow per month")

# =============================================================== Add entry
elif page == "Add entry":
    st.title("Add entry")
    # These two sit outside the form so the fields below can change as soon as you switch.
    money = st.segmented_control("Money", ["Income", "Expense"], default="Expense") or "Expense"
    if money == "Expense":
        kind = st.segmented_control("Expense type", ["Fixed", "Variable"], default="Variable") or "Variable"
        t = kind.lower()
    else:
        t = "inflow"
    with st.form(f"entry_{t}", clear_on_submit=True):
        c = st.columns(3)
        d = c[0].date_input("Date", today)
        amt = c[1].number_input("Amount (₹)", min_value=0.0, step=100.0)
        cat = c[2].selectbox(
            "Source of income" if t == "inflow" else "Category", categories_for([t]), index=None,
            accept_new_options=True, placeholder="Pick one, or type a new one and press Enter")
        tags = []
        if t != "inflow":
            tags = st.multiselect("Tags", merge_options(DEFAULT_TAGS, db.all_tags()), accept_new_options=True,
                                  placeholder="Optional. Pick, or type a new tag and press Enter")
        note = st.text_input("Note")
        if st.form_submit_button("Save", icon=":material/check:"):
            if not cat or not cat.strip() or amt <= 0:
                st.error("A category/source and a positive amount are required.")
            else:
                db.add_entry(d, t, cat, amt, note, tags)
                st.toast(f"Saved {TYPE_LABEL[t].lower()}: {db.fmt(cat)} {inr(amt)}", icon=":material/check_circle:")
                st.rerun()
    st.subheader("Recent entries")
    st.caption("Tick rows and delete — mistakes are one click away from gone (and recoverable from Backup).")
    entry_editor(entries.head(8), "recent")

# =============================================================== Fixed items
elif page == "Fixed items":
    st.title("Fixed expenses")
    st.caption("Recurring items are posted to your entries automatically when they fall due.")
    with st.form("fx", clear_on_submit=True):
        c = st.columns(5, vertical_alignment="bottom")
        n = c[0].text_input("Name")
        cat = c[1].selectbox("Category", categories_for(["fixed"]), index=None, accept_new_options=True,
                             placeholder="Pick or type")
        a = c[2].number_input("Amount (₹)", min_value=0.0, step=100.0)
        f = c[3].selectbox("Frequency", list(db.FREQUENCIES), index=2)
        nd = c[4].date_input("Next due", today)
        if st.form_submit_button("Add", icon=":material/add:") and n.strip() and cat and cat.strip() and a > 0:
            db.add_fixed_item(n, cat, a, f, nd)
            st.rerun()
    if not fixed_items.empty:
        act = fixed_items[fixed_items["active"] == 1]
        mult = {"weekly": 52 / 12, "biweekly": 26 / 12, "monthly": 1, "quarterly": 1 / 3, "yearly": 1 / 12}
        monthly = (act["amount"] * act["frequency"].map(mult)).sum()
        with st.container(horizontal=True):
            st.metric("Monthly commitment", inr(monthly), border=True)
            st.metric("Per year", inr(monthly * 12), border=True)
        for _, it in fixed_items.iterrows():
            with st.container(border=True, horizontal=True, vertical_alignment="center"):
                st.markdown(f"**{it['name']}** · {it['category']}  \n"
                            f"{inr(it['amount'])} · {it['frequency']} · next {it['next_due']}"
                            + ("" if it["active"] else "  \n*paused*"))
                if st.button("Resume" if not it["active"] else "Pause", key=f"t{it['id']}",
                             icon=":material/play_arrow:" if not it["active"] else ":material/pause:"):
                    db.set_fixed_active(int(it["id"]), not it["active"])
                    st.rerun()
                if st.button("Remove", key=f"d{it['id']}", icon=":material/delete:"):
                    db.delete_fixed_item(int(it["id"]))
                    st.rerun()

# =============================================================== Budgets
elif page == "Budgets":
    st.title("Budgets")
    with st.form("bg", clear_on_submit=True):
        c = st.columns(3, vertical_alignment="bottom")
        cat = c[0].selectbox("Category", categories_for(["fixed", "variable"]), index=None,
                             accept_new_options=True, placeholder="Pick or type")
        a = c[1].number_input("Monthly budget (₹)", min_value=0.0, step=500.0)
        if c[2].form_submit_button("Set", icon=":material/check:") and cat and cat.strip():
            db.set_budget(cat, a)
            st.rerun()
    for _, b in an.budget_status(entries, budgets).iterrows():
        with st.container(border=True):
            top = st.columns([4, 1], vertical_alignment="center")
            top[0].markdown(f"**{b['category']}** — {inr(b['spent'])} of {inr(b['amount'])} "
                            f"({b['pct'] * 100:.0f}%)")
            if top[1].button("Remove", key=f"rb{b['category']}", icon=":material/delete:"):
                db.delete_budget(b["category"])
                st.rerun()
            st.progress(min(b["pct"], 1.0))
            joke(cap.budget(b["pct"], seed + b["category"]))

# =============================================================== Insights
elif page == "Insights":
    st.title("Insights")
    if entries.empty:
        st.info("Add some data first.")
        joke(cap.pick("empty", seed))
        st.stop()
    fc = an.forecast(entries, fixed_items)
    if not fc.empty:
        fig = go.Figure()
        fig.add_bar(x=fc.index, y=fc["inflow"], name="Income", marker_color=C_IN)
        fig.add_bar(x=fc.index, y=fc["outflow"], name="Outflow", marker_color=C_OUT)
        fig.update_layout(barmode="group", bargap=0.35)
        show(style(fig, "3-month forecast"), "Projected income and outflow for the next three months")
        avg_net = fc["net"].mean()
        st.write(f"Projected average net: **{inr(avg_net)}/month** "
                 "(trailing 3-month averages + scheduled fixed items).")
        joke(cap.pick("forecast_pos" if avg_net >= 0 else "forecast_neg", seed))
    cm = an.category_monthly(entries[entries["type"] == "variable"])
    if not cm.empty:
        tot = cm.sum().sort_values()
        fig = go.Figure(go.Bar(x=tot.values, y=tot.index, orientation="h", marker_color=C_VAR))
        fig.update_layout(hovermode="closest")
        show(style(fig, "Variable spend by category (all time)"), "Horizontal bars of variable spend per category")
        st.write(f"Biggest variable category: **{tot.index[-1]}** ({inr(tot.iloc[-1])}).")
    with st.container(border=True):
        st.markdown("**Emergency fund**")
        bal = st.number_input("Current savings balance (₹)", min_value=0.0, step=10000.0)
        m = an.emergency_fund_months(bal, entries)
        if m is not None and bal > 0:
            st.metric("Months of expenses covered", f"{m:.1f}")
            joke(cap.fund(m, seed))
    inc = entries[entries["type"] == "inflow"].groupby("category")["amount"].sum().sort_values()
    if not inc.empty:
        fig = go.Figure(go.Bar(x=inc.values, y=inc.index, orientation="h", marker_color=C_IN))
        fig.update_layout(hovermode="closest")
        show(style(fig, "Income by source (all time)"), "Horizontal bars of income per source")
        st.write(f"Biggest source: **{inc.index[-1]}** ({inr(inc.iloc[-1])}, "
                 f"{inc.iloc[-1] / inc.sum() * 100:.0f}% of income).")
    tagged = entries[(entries["type"] != "inflow") & (entries["tag"] != "")].assign(
        tag=lambda d: d["tag"].map(db.split_tags)).explode("tag")
    if not tagged.empty:
        st.markdown("**Spend by tag**")
        st.dataframe(tagged.groupby("tag")["amount"].agg(entries="count", total="sum")
                     .sort_values("total", ascending=False), width="stretch")
        joke(cap.pick("tags", seed))

# =============================================================== History
elif page == "History":
    st.title("History")
    if entries.empty:
        st.info("No entries.")
    else:
        f = st.columns([2, 2, 3], vertical_alignment="bottom")
        ft = f[0].segmented_control("Type", list(TYPE_LABEL), format_func=TYPE_LABEL.get, selection_mode="multi")
        cats = f[1].multiselect("Category / source", sorted(entries["category"].unique()))
        q = f[2].text_input("Search note")
        v = entries
        if ft:
            v = v[v["type"].isin(ft)]
        if cats:
            v = v[v["category"].isin(cats)]
        if q:
            v = v[v["note"].str.contains(q, case=False, na=False)]
        entry_editor(v, "hist", height=420)
        st.download_button("Export CSV", v.drop(columns=["fixed_item_id"]).to_csv(index=False),
                           f"{current['name']}-expenses.csv", "text/csv", icon=":material/download:")

# =============================================================== Backup
else:
    st.title("Backup & restore")
    st.write("Your data lives in a private SQLite file that git ignores. Keep copies somewhere safe.")
    c = st.columns(2)
    if c[0].button("Back up now", icon=":material/backup:", width="stretch"):
        st.toast(f"Saved {os.path.basename(db.backup())}", icon=":material/check_circle:")
    with open(current["path"], "rb") as fh:
        c[1].download_button("Download database", fh.read(), f"{current['name']}-expenses.db",
                             icon=":material/download:", width="stretch")
    bdir = os.path.join(db.BACKUP_DIR, os.path.splitext(os.path.basename(current["path"]))[0])
    snaps = sorted(os.listdir(bdir), reverse=True) if os.path.isdir(bdir) else []
    st.caption(f"Snapshots are stored in `{bdir}` (set EXPENSE_BACKUP_DIR to a synced folder). "
               "A snapshot is taken automatically once a day and before every delete.")
    if snaps:
        pick = st.selectbox("Snapshots", snaps)
        if st.button("Restore this snapshot", icon=":material/restore:"):
            db.restore(os.path.join(bdir, pick), current["path"])
            st.toast("Restored (previous state was snapshotted first)", icon=":material/restore:")
            st.rerun()
    with st.expander("Delete this user", icon=":material/warning:"):
        st.caption("Removes the user's live database. Snapshots stay in the backup folder.")
        if st.button("Delete user permanently", type="primary"):
            db.backup()
            db.delete_user(current["path"])
            st.session_state["_pick_user"] = None
            st.rerun()
