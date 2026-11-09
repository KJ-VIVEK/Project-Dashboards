"""Asset Tracker Dashboard.  Run: streamlit run app.py"""
from datetime import date, datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import analytics as an
import db
import market

st.set_page_config(page_title="Asset Tracker", page_icon=":material/account_balance:", layout="wide")

MUTED = "#898781"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
SOURCE_COLORS = {"Salary": "#2a78d6", "Dividend/FD": "#1baf7a", "PL Booking": "#eda100", "Miscellaneous": "#6250d6",
                 "Taxes Paid": "#d6403a", "Insurance Premium": "#c2185b"}
LOAN_COLORS = {"Good": "#1baf7a", "Bad": "#eda100", "Worst": "#d6403a"}
BUCKET_COLORS = ["#2a78d6", "#6aa7ea", "#1baf7a", "#74d1ac", "#eda100", "#f3c85c", "#6250d6", "#a396ea",
                 "#eb6834", "#d6403a", "#c9a227", "#9aa0a6", "#8c5a3c"]
IDEAL, REAL = "#898781", "#2a78d6"


# ------------------------------------------------------------------ helpers
DATE_MIN, DATE_MAX = date(1950, 1, 1), date(2100, 12, 31)   # Streamlit's default window is only +/-10 years


def dinput(container, label, *args, **kw):
    """Date picker that allows long horizons (a 40-50 year policy or loan) and old start dates."""
    kw.setdefault("min_value", DATE_MIN)
    kw.setdefault("max_value", DATE_MAX)
    return container.date_input(label, *args, **kw)


def dcol(label, **kw):
    kw.setdefault("min_value", DATE_MIN)
    kw.setdefault("max_value", DATE_MAX)
    return st.column_config.DateColumn(label, **kw)


def style(fig, title, height=340, money=True):
    legend_rows = 1 if len(fig.data) > 1 else 0   # the legend sits between the title and the plot, never on the axis
    fig.update_layout(title=dict(text=title, font=dict(size=14)), height=height,
                      margin=dict(l=0, r=0, t=44 + 34 * legend_rows, b=0),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      legend=(dict(orientation="h", yanchor="bottom", y=1.0, x=0) if legend_rows
                              else dict(orientation="h", y=-0.18, x=0)), font=dict(color=MUTED))
    fig.update_xaxes(showgrid=False, linecolor=MUTED)
    fig.update_yaxes(gridcolor="rgba(137,135,129,0.18)", zeroline=False)
    return fig


def show(fig):
    st.plotly_chart(fig, width="stretch")


def flash(msg):
    st.session_state["_flash"] = msg


def version(key):
    return st.session_state.setdefault("_ver", {}).get(key, 0)


def bump(key):
    v = st.session_state.setdefault("_ver", {})
    v[key] = v.get(key, 0) + 1


def saved_msg(stats):
    bits = [f"{n} {k}" for k, n in stats.items() if n and k != "skipped"]
    msg = "Saved: " + ", ".join(bits) if bits else "No changes to save."
    if stats.get("skipped"):
        msg += f" ({stats['skipped']} incomplete row(s) not saved — fill the required columns.)"
    return msg


def edit_table(table, shown, orig, config, required, key, to_db=None, hint=None):
    """Editable sheet: edit cells or delete rows (new rows are added through the forms above). Save writes the diff."""
    cfg = {"id": None, **config}
    ed = st.data_editor(shown, key=f"ed_{key}_{version(key)}", num_rows="delete", hide_index=True,
                        column_config=cfg, width="stretch")
    c1, c2 = st.columns([1, 5], vertical_alignment="center")
    if c1.button("Save changes", key=f"save_{key}", type="primary"):
        edited = to_db(ed) if to_db else ed
        removed = set(orig["id"].astype(int)) - set(pd.to_numeric(edited["id"]).dropna().astype(int))
        if removed:
            db.backup()  # safety copy before anything is deleted
        flash(saved_msg(db.sync_table(table, orig, edited, required)))
        bump(key)
        st.rerun()
    c2.caption(hint or "Edit cells directly. To delete, tick the row's left edge and press Delete; then Save changes.")


def money_cfg(label, **kw):
    return st.column_config.NumberColumn(label, format="%.2f", min_value=0, **kw)


def kpi(col, label, value, delta=None, help=None):
    col.metric(label, value if isinstance(value, str) else an.inr(value), delta, help=help)


def fmt_df(df, money_cols=(), pct_cols=()):
    out = df.copy()
    for c in money_cols:
        out[c] = out[c].map(an.inr)
    for c in pct_cols:
        out[c] = out[c].map(lambda v: f"{v:.1f}%")
    return out


# ------------------------------------------------------------------ startup
db.init_db()
if "_backed_up" not in st.session_state:
    db.backup_if_stale()
    st.session_state["_backed_up"] = True
if "_flash" in st.session_state:
    st.toast(st.session_state.pop("_flash"))

# ---- live prices: controls run BEFORE the data is loaded so refreshed prices are used straight away ----
LIVE_OPTIONS = {"Off": None, "1 min": 60, "5 min": 300, "15 min": 900}
with st.sidebar:
    st.markdown("### :material/account_balance: Asset Tracker")
    interval = LIVE_OPTIONS[st.selectbox("Live prices", list(LIVE_OPTIONS), index=2, key="live_every",
                                         help="How often prices, USD→INR and gold/silver are fetched while this "
                                              "page is open. Holdings need a Symbol (see Stocks tab).")]
    refresh_now = st.button("Refresh now", icon=":material/refresh:", width="stretch")
    with st.expander("USD → INR rate"):
        use_manual = st.checkbox("Use my own rate", value=db.meta_get("fx_use_manual", "0") == "1", key="fx_use_manual_ui")
        manual = st.number_input("₹ per $1", min_value=0.0, value=float(db.meta_get("fx_manual", 0) or 0),
                                 format="%.4f", key="fx_manual_ui")
        if st.button("Save rate", width="stretch"):
            db.meta_set("fx_use_manual", "1" if use_manual else "0")
            db.meta_set("fx_manual", manual)
            flash("Rate saved.")
            st.rerun()
if refresh_now:
    with st.spinner("Fetching live prices…"):
        st.session_state["_live_res"] = market.refresh(force=True)
elif interval and "_live_boot" not in st.session_state:
    with st.spinner("Fetching live prices…"):
        st.session_state["_live_res"] = market.refresh(max_age=interval)
st.session_state["_live_boot"] = True

if interval:
    @st.fragment(run_every=interval)
    def _live_ticker():
        r = market.refresh(max_age=max(interval - 5, 30))
        if r.get("changed"):
            st.session_state["_live_res"] = r
            st.rerun()  # redraw the whole app with the new prices
    with st.sidebar:
        _live_ticker()

income = db.get_table("income")
securities = db.get_table("securities")
trades = db.get_table("trades")
dividends = db.get_table("dividends")
savings = db.get_table("savings")
insurance = db.get_table("insurance")
ins_rev = db.get_table("insurance_revisions")
prem_paid = an.insurance_payments(insurance, ins_rev)
loans = db.get_table("loans")
metals = db.get_table("metals")
real_estate = db.get_table("real_estate")
notes = db.get_table("notes")
prices = db.metal_prices()
fx = db.usd_inr()

positions, realised, pos_warnings = an.build_positions(securities, trades, dividends, fx)
metals_f = an.metals_frame(metals, prices)
re_f = an.real_estate_frame(real_estate)
loans_f = an.loans_frame(loans)
savings_total = an.num(savings["amount"]).sum()
loans_total = loans_f["outstanding"].sum()
buckets = an.bucket_values(positions, metals_f, re_f, savings_total)
inv_b = buckets[buckets["bucket"] != db.SAVINGS_BUCKET]
invest_value, invest_cost = inv_b["value"].sum(), inv_b["invested"].sum()

with st.sidebar:
    st.divider()
    if fx:
        when = db.meta_get("fx_time")
        manual_on = db.meta_get("fx_use_manual", "0") == "1"
        st.caption(f"**USD/INR {fx:,.4f}** · {'your own rate' if manual_on else 'live' + (' · ' + when[11:16] if when else '')}")
    elif (securities["currency"] == "USD").any() or market.offline():
        st.warning("No USD→INR rate yet: USD holdings are valued 1:1. Go online or set your own rate above.")
    res = st.session_state.get("_live_res", {})
    if res.get("skipped") == "offline":
        st.caption("Offline mode (ASSET_OFFLINE=1): live prices are off.")
    elif res.get("at"):
        st.caption(f"Last refresh {res['at']}")
        if res.get("failed"):
            st.caption(":material/warning: No live quote for " + ", ".join(res["failed"]) + " (check Symbol)")
    if st.button("Back up now", icon=":material/backup:", width="stretch"):
        st.toast(f"Backup saved: {db.backup()}")
    with open(db.DB_PATH, "rb") as f:
        st.download_button("Download database", f.read(), file_name=f"assets-{date.today()}.db",
                           icon=":material/download:", width="stretch")
    st.caption("All amounts are in ₹. USD holdings are converted at the live rate.")

st.title("Asset Tracker")
tabs = st.tabs(["Summary", "Source", "Cash & Savings", "Insurance", "Loans", "Stocks",
                "Gold & Silver", "Real Estate", "Notes"])

# ================================================================== 1. SUMMARY
with tabs[0]:
    nw = an.net_worth(invest_value, savings_total, loans_total)
    c = st.columns(5)
    kpi(c[0], "Net worth", nw, help="Investments + cash & savings − loans outstanding")
    kpi(c[1], "Investments (current)", invest_value,
        f"{an.inr(invest_value - invest_cost)} vs invested" if invest_cost else None)
    kpi(c[2], "Invested (cost)", invest_cost)
    kpi(c[3], "Cash & savings", savings_total)
    kpi(c[4], "Loans outstanding", loans_total)

    st.subheader("Income")
    auto = st.toggle("Include dividends and realised profit/loss recorded on the Stocks tab", value=True,
                     help="Dividends count as Dividend/FD; sells count as PL Booking. Turn off if you also type "
                          "them on the Source sheet, to avoid double counting.", key="sum_auto")
    prem_on = st.toggle("Subtract insurance premiums paid (from the Insurance tab)", value=True, key="sum_prem",
                        help="Premiums are spread evenly over the months each policy has been in force, from its start "
                             "date to today, and subtracted from income like Taxes Paid.")
    inc = an.income_frame(income, dividends, realised, auto, prem_paid if prem_on else None)
    years = sorted(inc["year"].unique(), reverse=True)
    period = st.selectbox("Period", ["All time"] + years, key="sum_year",
                          help="Drives the income chart, 'where the income went' and the ideal values below.")
    yr = None if period == "All time" else period
    if yr:
        pm, xs = an.income_pivot(inc, yr), MONTHS
    else:
        pm = an.income_pivot(inc, None, by="year")
        xs = [str(c_) for c_ in pm.columns]
    income_total = float(pm.to_numpy().sum())
    left, right = st.columns([3, 2])
    with left:
        fig = go.Figure()
        for src, row in pm.iterrows():
            if row.abs().sum():
                fig.add_bar(x=xs, y=row.values, name=src, marker_color=SOURCE_COLORS.get(src, MUTED))
        fig.update_layout(barmode="relative")
        fig.update_yaxes(tickprefix="₹", tickformat="~s")
        show(style(fig, f"Income {'by month, ' + str(yr) if yr else 'by year'}: {an.inr(income_total)}"))
    with right:
        by_src = pm.sum(axis=1)
        by_src = by_src[by_src > 0]
        if by_src.empty:
            st.info("No income yet. Add some on the Source tab.")
        else:
            fig = go.Figure(go.Pie(labels=by_src.index, values=by_src.values, hole=0.55, sort=False,
                                   marker_colors=[SOURCE_COLORS.get(s, MUTED) for s in by_src.index],
                                   textinfo="label+percent"))
            show(style(fig, "Where income came from").update_layout(showlegend=False))

    # ---- where the income went:  Expense = Income - Investments - Savings
    st.subheader("Where the income went")
    flow = an.money_flow(income_total, an.stock_net_invested(trades, securities, realised, fx),
                         metals_f, re_f, savings, yr)
    c = st.columns(5)
    kpi(c[0], "Income", flow["income"])
    kpi(c[1], "Invested", flow["invested"], help="New money put into stocks/funds, gold & silver and property")
    kpi(c[2], "Saved", flow["saved"], help="Cash, FD, EPF, NPS and goal balances" + (
        "; for a single year, only entries with a start date in it" if yr else ""))
    kpi(c[3], "Expenses (left over)", flow["expense"], help="Income − Invested − Saved")
    kpi(c[4], "Invested + saved", f"{flow['savings_rate_pct']:.0f}% of income")
    st.caption("**Expenses = Income − Investments − Savings.** Whatever you earned and did not invest or save is "
               "treated as spent. Investments are net of units sold (recycled capital is not counted twice).")
    if flow["expense"] < 0:
        st.warning("Invested + saved is more than the income recorded for this period. Add the missing income on "
                   "the Source tab, or pick 'All time'.")
    if flow["income"] > 0:
        labels = ["Income", "Investments", "Saved", "Expenses", "Stocks & funds", "Gold & silver", "Real estate"]
        links = [(0, 1, flow["invested"]), (0, 2, flow["saved"]), (0, 3, max(flow["expense"], 0)),
                 (1, 4, flow["stocks"]), (1, 5, flow["metals"]), (1, 6, flow["real_estate"])]
        links = [l for l in links if l[2] > 0]
        fig = go.Figure(go.Sankey(
            node=dict(label=labels, pad=18, thickness=14, color=[REAL, "#6250d6", "#1baf7a", "#eb6834",
                                                                  "#a396ea", "#eda100", "#8c5a3c"]),
            link=dict(source=[l[0] for l in links], target=[l[1] for l in links], value=[l[2] for l in links],
                      color="rgba(137,135,129,0.25)")))
        show(style(fig, "Income → investments, savings and expenses", height=300))

    # ---- ideal vs reality:  Ideal = Income x %
    st.subheader("Investments: ideal vs reality")
    pcts = db.get_target_pcts()
    alloc = an.allocation(buckets, pcts, income_total)
    planned = sum(pcts.values())
    c = st.columns(3)
    kpi(c[0], f"Income ({period})", income_total)
    kpi(c[1], "Planned to invest + save", f"{planned:.1f}% of income")
    kpi(c[2], "Left for expenses", f"{100 - planned:.1f}% of income")
    st.caption("**Ideal = income × the % you set for each bucket.** Reality = current value of what you hold.")
    if income_total == 0:
        st.info("Ideal values are income × %, so they show once income is recorded on the Source tab.")
    a = alloc[(alloc["value"] > 0) | (alloc["target"] > 0)]
    left, right = st.columns([3, 2])
    with left:
        fig = go.Figure()
        fig.add_bar(y=a["bucket"], x=a["target"], name="Ideal (income × %)", orientation="h", marker_color=IDEAL)
        fig.add_bar(y=a["bucket"], x=a["value"], name="Reality (current value)", orientation="h", marker_color=REAL)
        fig.update_layout(barmode="group", yaxis=dict(autorange="reversed"))
        fig.update_xaxes(tickprefix="₹", tickformat="~s")
        show(style(fig, "Ideal vs reality by bucket", height=max(320, 70 * len(a))))
    with right:
        held = alloc[alloc["value"] > 0]
        if not held.empty:
            fig = go.Figure(go.Pie(labels=held["bucket"], values=held["value"], hole=0.55, sort=False,
                                   marker_colors=BUCKET_COLORS[:len(held)], textinfo="percent"))
            show(style(fig, "Current allocation"))
    table = alloc[["bucket", "invested", "value", "actual_pct", "target_pct", "target", "gap"]].rename(columns={
        "bucket": "Bucket", "invested": "Invested", "value": "Current value", "actual_pct": "Actual % of income",
        "target_pct": "Ideal %", "target": "Ideal ₹", "gap": "Left to invest (−: over)"})
    st.dataframe(fmt_df(table, ["Invested", "Current value", "Ideal ₹", "Left to invest (−: over)"],
                        ["Actual % of income", "Ideal %"]), hide_index=True, width="stretch")

    with st.expander("Set ideal values (% of income per bucket)"):
        tdf = pd.DataFrame({"bucket": db.INVESTMENT_BUCKETS,
                            "pct": [pcts.get(b, 0.0) for b in db.INVESTMENT_BUCKETS]})
        ted = st.data_editor(tdf, hide_index=True, width="stretch", key=f"targets_{version('targets')}",
                             disabled=["bucket"], column_config={
                                 "bucket": "Bucket", "pct": st.column_config.NumberColumn(
                                     "% of income", min_value=0, max_value=100, format="%.1f")})
        st.caption(f"Total {ted['pct'].fillna(0).sum():.1f}%. What is not allocated is your expense budget. "
                   "0 means no target.")
        if st.button("Save ideal values", type="primary", key="save_targets"):
            if ted["pct"].fillna(0).sum() > 100.0001:
                st.error("The percentages add up to more than 100%.")
            else:
                db.set_target_pcts(dict(zip(ted["bucket"], ted["pct"])))
                flash("Ideal values saved.")
                bump("targets")
                st.rerun()

# ================================================================== 2. SOURCE (income)
with tabs[1]:
    st.caption("Everything you earn, by month. Enter **Taxes Paid** as a positive amount: it is subtracted from "
               "the totals, so income shown is after tax. Dividends and realised profit/loss from the Stocks tab "
               "appear in the tables below automatically (switch this off on the Summary tab).")
    with st.form("add_income", clear_on_submit=True):
        c = st.columns([1, 1.2, 2, 1.2, 0.8], vertical_alignment="bottom")
        d_ = dinput(c[0], "Date", date.today())
        s_ = c[1].selectbox("Section", db.INCOME_SOURCES)
        desc = c[2].text_input("Description", placeholder="e.g. Employer name, FD interest, Swing trade")
        amt = c[3].number_input("Amount (₹)", min_value=0.0, step=1000.0, format="%.2f")
        if c[4].form_submit_button("Add", width="stretch", type="primary"):
            if amt > 0:
                db.insert("income", {"date": d_, "source": s_, "description": desc, "amount": amt})
                flash("Income added.")
                st.rerun()
            else:
                st.error("Enter an amount above zero.")

    inc = an.income_frame(income, dividends, realised, st.session_state.get("sum_auto", True),
                          prem_paid if st.session_state.get("sum_prem", True) else None)
    view = st.radio("View", ["Per month", "Per year"], horizontal=True, key="src_view")
    if view == "Per month":
        ys = sorted(inc["year"].unique(), reverse=True) or [date.today().year]
        yr = st.selectbox("Year", ys, key="src_year")
        pv = an.income_pivot(inc, yr)
        pv.columns = MONTHS
    else:
        pv = an.income_pivot(inc, None, by="year")
        pv.columns = [str(c_) for c_ in pv.columns]
    pv["Total"] = pv.sum(axis=1)
    pv.loc["Total"] = pv.sum()
    st.dataframe(pv.map(lambda v: an.inr(v) if v else "·"), width="stretch")

    st.markdown("**Edit entries**")
    f1, f2, f3 = st.columns(3)
    all_years = sorted({d.year for d in income["date"].dropna()}, reverse=True)
    fy = f1.selectbox("Year", ["All"] + all_years, key="src_f_year")
    fm = f2.selectbox("Month", ["All"] + MONTHS, key="src_f_month")
    fs = f3.multiselect("Section", db.INCOME_SOURCES, key="src_f_src")
    mask = pd.Series(True, index=income.index)
    dts = pd.to_datetime(income["date"], errors="coerce")
    if fy != "All":
        mask &= dts.dt.year == fy
    if fm != "All":
        mask &= dts.dt.month == MONTHS.index(fm) + 1
    if fs:
        mask &= income["source"].isin(fs)
    sub = income[mask]
    edit_table("income", sub, sub, {
        "date": dcol("Date", required=True),
        "source": st.column_config.SelectboxColumn("Section", options=db.INCOME_SOURCES, required=True),
        "description": "Description", "amount": money_cfg("Amount (₹)", required=True)},
        ("date", "source", "amount"), "income")

# ================================================================== 3. CASH & SAVINGS
with tabs[2]:
    sv = savings.copy()
    sv["amount"] = an.num(sv["amount"])
    tot = an.savings_totals(sv)
    c = st.columns(4)
    kpi(c[0], "Total cash & savings", savings_total)
    for col, k in zip(c[1:], ["Savings Account", "FD", "EPF"]):
        kpi(col, k, sv.loc[sv["kind"] == k, "amount"].sum())
    c = st.columns(4)
    kpi(c[0], "NPS", sv.loc[sv["kind"] == "NPS", "amount"].sum())
    kpi(c[1], "Short-term goals", sv.loc[sv["kind"] == "Short-term Goal", "amount"].sum())
    kpi(c[2], "PPF / RD", sv.loc[sv["kind"].isin(["PPF", "RD"]), "amount"].sum())
    kpi(c[3], "Cash in hand", sv.loc[sv["kind"] == "Cash in Hand", "amount"].sum())

    with st.form("add_savings", clear_on_submit=True):
        c = st.columns([1.2, 1.6, 1.2, 2, 1], vertical_alignment="bottom")
        kind = c[0].selectbox("Type", db.SAVINGS_KINDS)
        name = c[1].text_input("Name", placeholder="e.g. HDFC FD #2, Honeymoon fund")
        amt = c[2].number_input("Amount now (₹)", min_value=0.0, step=1000.0, format="%.2f")
        purpose = c[3].text_input("What is it meant for?", placeholder="e.g. House down payment, Emergency")
        c2 = st.columns([1, 1, 1, 1, 1], vertical_alignment="bottom")
        rate = c2[0].number_input("Interest % (optional)", min_value=0.0, step=0.1, format="%.2f")
        mat = dinput(c2[1], "Maturity / target date", value=None)
        tgt = c2[2].number_input("Goal target ₹ (goals)", min_value=0.0, step=1000.0, format="%.2f")
        if c2[4].form_submit_button("Add", width="stretch", type="primary"):
            if name.strip():
                db.insert("savings", {"kind": kind, "name": name, "amount": amt, "purpose": purpose,
                                      "interest_rate": rate or None, "maturity_date": mat, "target": tgt or None})
                flash("Added.")
                st.rerun()
            else:
                st.error("Give it a name.")

    goals = an.goal_progress(sv)
    if not goals.empty:
        st.markdown("**Short-term goals**")
        for _, g in goals.iterrows():
            st.progress(g["pct"] / 100, text=f"{g['name']}: {an.inr(g['amount'])} of {an.inr(g['target'])} "
                        f"({g['pct']:.0f}%){' — by ' + str(g['maturity_date']) if g['maturity_date'] else ''}")
    if not tot.empty:
        fig = go.Figure(go.Bar(x=tot["kind"], y=tot["amount"], marker_color=REAL))
        fig.update_yaxes(tickprefix="₹", tickformat="~s")
        show(style(fig, "Cash & savings by type", height=280))
    st.markdown("**All accounts**")
    edit_table("savings", savings, savings, {
        "kind": st.column_config.SelectboxColumn("Type", options=db.SAVINGS_KINDS, required=True),
        "name": st.column_config.TextColumn("Name", required=True),
        "amount": money_cfg("Amount (₹)", required=True), "purpose": "Meant for",
        "interest_rate": st.column_config.NumberColumn("Interest %", min_value=0, format="%.2f"),
        "start_date": dcol("Start"),
        "maturity_date": dcol("Maturity / target"),
        "target": money_cfg("Goal target (₹)"), "note": "Note"}, ("kind", "name", "amount"), "savings")

# ================================================================== 4. INSURANCE
with tabs[3]:
    start_years = [d.year for d in insurance["start_date"] if hasattr(d, "year") and pd.notna(d)]
    years = list(range(date.today().year, min(start_years + [date.today().year]) - 1, -1))
    ins_year = st.selectbox("Year", years, key="ins_year",
                            help="Premiums are shown per year, spread over the months each policy is in force: "
                                 "premium × payments per year × months in force ÷ 12.")
    ins_y = an.insurance_frame(insurance, ins_rev, year=ins_year)
    act = ins_y[ins_y["status"] == "Active"]
    c = st.columns(3)
    kpi(c[0], "Policies", f"{len(act)} active · {int((ins_y['status'] == 'Inactive').sum())} inactive")
    kpi(c[1], "Premium per month (now)", ins_y["per_month"].sum(), help="The rate being paid today, after any premium change.")
    kpi(c[2], "Premium per year (now)", ins_y["per_year"].sum(), help="Today's rate for a full year.")
    st.write("")
    c = st.columns(3)
    kpi(c[0], f"Premium in {ins_year}", ins_y["year_cost"].sum(),
        help="Pro-rated by the months each policy was in force that year, with premium changes applied from their month.")
    kpi(c[1], "Paid to date", ins_y["paid_to_date"].sum(skipna=True),
        help="From each policy's start date until today (or its end date). Needs a start date.")
    kpi(c[2], "Total cover", an.num(act["sum_assured"]).sum())
    st.write("")
    due = act.copy()
    due["_d"] = pd.to_datetime(due["next_due"], errors="coerce")
    soon = due[(due["_d"] >= pd.Timestamp(date.today())) & (due["_d"] <= pd.Timestamp(date.today()) + pd.Timedelta(days=30))]
    for _, r in soon.iterrows():
        st.warning(f"{r['name']}: premium {an.inr(r['premium_now'])} due {r['next_due']}")
    undated = ins_y[~ins_y["active"].astype(bool) & ins_y["end_date"].isna()]["name"].tolist()
    if undated:
        st.info("Lapsed policies without an end date count for nothing in any year: " + ", ".join(undated) +
                ". Add an end date to count the premiums you paid.")

    with st.expander("Add a policy", icon=":material/add:", expanded=ins_y.empty):
        with st.form("add_ins", clear_on_submit=True, border=False):
            st.markdown("##### The policy")
            c = st.columns(3)
            name = c[0].text_input("Policy name")
            typ = c[1].selectbox("Type", db.INSURANCE_TYPES)
            prov = c[2].text_input("Provider")
            c = st.columns(3)
            pno = c[0].text_input("Policy number")
            cover = c[1].number_input("Sum assured (₹)", min_value=0.0, step=100000.0, format="%.0f")
            link = c[2].text_input("Link to policy", placeholder="https://… or Drive link")
            st.markdown("##### Premium")
            c = st.columns(3)
            prem = c[0].number_input("Premium each time (₹)", min_value=0.0, step=500.0, format="%.2f",
                                     help="The amount you pay per payment, e.g. per month for a monthly policy.")
            freq = c[1].selectbox("Paid", list(db.FREQUENCIES), index=3)
            nxt = dinput(c[2], "Next due", value=None)
            st.markdown("##### Period")
            c = st.columns(3)
            p_start = dinput(c[0], "Start date", value=None)
            p_end = dinput(c[1], "End date", value=None, help="Leave empty while the policy is running.")
            st.write("")
            if st.form_submit_button("Add policy", type="primary"):
                if not name.strip() or prem <= 0:
                    st.error("A name and a premium above zero are needed.")
                elif p_start and p_end and p_end < p_start:
                    st.error("The end date is before the start date.")
                else:
                    db.insert("insurance", {"name": name, "type": typ, "provider": prov, "policy_no": pno,
                                            "sum_assured": cover or None, "premium": prem, "frequency": freq,
                                            "next_due": nxt, "link": link, "active": True,
                                            "start_date": p_start, "end_date": p_end})
                    flash("Policy added.")
                    st.rerun()

    with st.expander("Premium changed? Record the new amount", icon=":material/price_change:"):
        st.caption("Keep the same policy and say from when the amount to pay changed (a tax change, a repricing…). "
                   "Earlier months keep the old amount, later months use the new one.")
        if insurance.empty:
            st.write("Add a policy first.")
        else:
            with st.form("ins_revision", clear_on_submit=True, border=False):
                names = {int(r["id"]): r["name"] for _, r in insurance.iterrows()}
                c = st.columns(4, vertical_alignment="bottom")
                pid = c[0].selectbox("Policy", list(names), format_func=names.get)
                new_prem = c[1].number_input("New premium each time (₹)", min_value=0.0, step=500.0, format="%.2f")
                eff = dinput(c[2], "Effective from", value=None)
                why = c[3].text_input("Reason", placeholder="e.g. GST change")
                if st.form_submit_button("Record premium change", type="primary"):
                    pol = ins_y[ins_y["id"] == pid].iloc[0]
                    if new_prem <= 0 or eff is None:
                        st.error("A new premium above zero and an effective date are needed.")
                    elif hasattr(pol["start_date"], "year") and pd.notna(pol["start_date"]) and eff < pol["start_date"]:
                        st.error("The change cannot start before the policy does.")
                    else:
                        db.insert("insurance_revisions", {"insurance_id": pid, "effective_from": eff,
                                                          "premium": new_prem, "note": why})
                        flash(f"{names[pid]}: premium {an.inr(new_prem)} from {eff}.")
                        st.rerun()
        if not ins_rev.empty:
            st.markdown("**Premium history**")
            hist = []
            for _, p in insurance.iterrows():
                hist.append({"Policy": p["name"], "From": p["start_date"] if hasattr(p["start_date"], "year") and pd.notna(p["start_date"]) else None,
                             "Premium": p["premium"], "Note": "starting premium"})
                for _, r in ins_rev[ins_rev["insurance_id"] == p["id"]].sort_values("effective_from").iterrows():
                    hist.append({"Policy": p["name"], "From": r["effective_from"], "Premium": r["premium"],
                                 "Note": r["note"] if isinstance(r["note"], str) else ""})
            st.dataframe(fmt_df(pd.DataFrame(hist), ["Premium"]), hide_index=True, width="stretch")
            st.markdown("**Edit or delete a premium change**")
            names_all = dict(zip(insurance["id"], insurance["name"]))
            edit_table("insurance_revisions", ins_rev.assign(policy=ins_rev["insurance_id"].map(names_all)), ins_rev, {
                "insurance_id": None, "policy": st.column_config.TextColumn("Policy", disabled=True),
                "effective_from": dcol("Effective from", required=True),
                "premium": money_cfg("Premium (₹)", required=True), "note": "Reason"},
                ("effective_from", "premium"), "ins_rev", to_db=lambda e: e.drop(columns="policy"),
                hint="Edit cells, or tick a row's left edge and press Delete; then Save changes.")

    if not ins_y.empty:
        dots = {"Active": "🟢 Active", "Inactive": "⚪ Inactive", "Not started": "🟡 Not started"}
        order = {"Active": 0, "Not started": 1, "Inactive": 2}
        g = ins_y.assign(_o=ins_y["status"].map(order)).sort_values(["_o", "name"])
        t = pd.DataFrame({"Policy": g["name"], "Status": g["status"].map(dots),
                          "Premium paid": g["paid_to_date"].map(lambda v: "–" if pd.isna(v) else an.inr(v)),
                          "Insurance amount": an.num(g["sum_assured"]).map(an.inr)})
        st.markdown("##### Policies")
        st.dataframe(t, hide_index=True, width="stretch")
        st.caption("Premium paid is the total since the policy started, with premium changes applied. "
                   "Active means the policy is running today.")
        nodate = ins_y[ins_y["paid_to_date"].isna()]["name"].tolist()
        if nodate:
            st.info("No start date, so no premium history: " + ", ".join(nodate) +
                    ". Add a start date to count what you paid (it is also subtracted from income on the Summary).")

    with st.expander("Edit or delete policies", icon=":material/edit:"):
        edit_table("insurance", insurance, insurance, {
            "name": st.column_config.TextColumn("Policy", required=True),
            "type": st.column_config.SelectboxColumn("Type", options=db.INSURANCE_TYPES),
            "provider": "Provider", "policy_no": "Policy no.", "sum_assured": money_cfg("Sum assured (₹)"),
            "premium": money_cfg("Starting premium (₹)", required=True),
            "frequency": st.column_config.SelectboxColumn("Paid", options=list(db.FREQUENCIES), required=True),
            "next_due": dcol("Next due"),
            "start_date": dcol("Start"), "end_date": dcol("End"),
            "link": st.column_config.LinkColumn("Policy link", display_text="Open"), "notes": "Notes",
            "active": st.column_config.CheckboxColumn("Active")}, ("name", "premium", "frequency"), "insurance",
            hint="Untick Active when a policy lapses and add an end date. To change what you pay, use "
                 "“Premium changed?” above rather than editing the starting premium.")

# ================================================================== 5. LOANS
def _m(v):
    """Rupees, or a dash when a figure was not recorded."""
    return "–" if v is None or pd.isna(v) else an.inr(v)


with tabs[4]:
    n_closed = int((loans_f["status"] == "Closed").sum())
    c = st.columns(3)
    kpi(c[0], "Outstanding now", loans_total)
    kpi(c[1], "EMI per month", loans_f["emi"].sum())
    kpi(c[2], "Loans", f"{len(loans_f) - n_closed} active · {n_closed} closed")
    st.write("")
    c = st.columns(3)
    kpi(c[0], "Interest paid so far", loans_f["interest_paid"].sum(),
        help="Actual interest where you recorded it, otherwise the calculator's estimate. Closed loans only.")
    kpi(c[1], "Other charges paid", loans_f["other_charges"].sum(), help="Processing fees and similar.")
    kpi(c[2], "Total cost of borrowing", loans_f["cost_of_borrowing"].sum(), help="Interest + other charges.")
    st.write("")
    c = st.columns(3)
    kpi(c[0], "Interest saved by closing early", loans_f["interest_saved"].sum(),
        help="Full-tenure interest (standard EMI calculator) − interest you actually paid.")
    kpi(c[1], "…after other charges", loans_f["net_saved"].sum(skipna=True) if loans_f["interest_saved"].notna().any() else 0,
        help="Interest saved − other charges you paid to close or set up the loan.")
    kpi(c[2], "Bad + Worst debt outstanding",
        loans_f.loc[loans_f["category"].isin(["Bad", "Worst"]), "outstanding"].sum())
    st.write("")
    st.caption("**Good**: builds an asset or income (home, education). **Bad**: buys something that loses value "
               "(car, gadgets). **Worst**: funds consumption at high interest (credit card, personal loan).")

    with st.expander("Add a loan", icon=":material/add:", expanded=loans_f.empty):
        with st.form("add_loan", clear_on_submit=True, border=False):
            st.markdown("##### The loan")
            c = st.columns(3)
            name = c[0].text_input("Loan name", placeholder="e.g. Bike loan")
            lender = c[1].text_input("Lender")
            cat = c[2].selectbox("Category", db.LOAN_CATEGORIES)
            c = st.columns(3)
            status = c[0].selectbox("Status", db.LOAN_STATUSES)
            rate = c[1].number_input("Interest % p.a.", min_value=0.0, step=0.1, format="%.2f")
            emi = c[2].number_input("EMI (₹)", min_value=0.0, step=500.0, format="%.0f")
            st.markdown("##### Amounts")
            c = st.columns(4)
            down = c[0].number_input("Down payment (₹)", min_value=0.0, step=10000.0, format="%.0f",
                                     help="Paid from your pocket on day one, outside the loan.")
            prin = c[1].number_input("Loan amount (₹)", min_value=0.0, step=10000.0, format="%.0f")
            charges = c[2].number_input("Other charges (₹)", min_value=0.0, step=500.0, format="%.0f",
                                        help="Processing fee, foreclosure charge, documentation, insurance bundled in…")
            outs = c[3].number_input("Outstanding (₹)", min_value=0.0, step=10000.0, format="%.0f",
                                     help="Leave 0 for a closed loan.")
            st.markdown("##### Dates")
            c = st.columns(3)
            sd = dinput(c[0], "Started", value=None)
            ed_ = dinput(c[1], "Planned end", value=None, help="Original end of the tenure, used for the full-tenure interest.")
            closed_on = dinput(c[2], "Closed on", value=None, help="Only for closed loans.")
            st.markdown("##### What you paid")
            st.caption("Optional, but it replaces the estimates with your real figures.")
            c = st.columns(4)
            n_paid = c[0].number_input("Installments paid", min_value=0, step=1)
            emi_paid = c[1].number_input("Paid through installments (₹)", min_value=0.0, step=1000.0, format="%.0f",
                                         help="Total of all the installments you paid.")
            emi_int = c[2].number_input("…of which interest (₹)", min_value=0.0, step=500.0, format="%.0f")
            closing = c[3].number_input("Paid to close the loan (₹)", min_value=0.0, step=1000.0, format="%.0f",
                                        help="The foreclosure amount, including any charges paid that day.")
            st.markdown("##### Notes")
            link = st.text_input("Link to documents")
            details = st.text_area("Details others should know", placeholder="Co-borrower, collateral, prepayment rules, "
                                   "where the papers are…", height=80)
            st.write("")
            if st.form_submit_button("Add loan", type="primary"):
                closed = status == "Closed"
                if not name.strip():
                    st.error("A loan name is needed.")
                elif not closed and outs <= 0:
                    st.error("An active loan needs an outstanding amount above zero (choose Closed if it is repaid).")
                elif closed and sd and closed_on and closed_on < sd:
                    st.error("The closing date is before the start date.")
                elif emi_int > emi_paid:
                    st.error("Interest within the installments cannot be more than the installments paid.")
                else:
                    db.insert("loans", {
                        "name": name, "lender": lender, "category": cat, "principal": prin or None,
                        "status": status, "closed_date": closed_on if closed else None,
                        "outstanding": 0.0 if closed else outs, "interest_rate": rate or None, "emi": emi or None,
                        "start_date": sd, "end_date": ed_, "link": link, "details": details,
                        "down_payment": down or None, "other_charges": charges or None,
                        "installments_paid": int(n_paid) or None, "emi_paid": emi_paid or None,
                        "emi_interest": emi_int if emi_paid else None, "closing_payment": closing or None})
                    flash("Loan added.")
                    st.rerun()

    # ---- one card per loan: where the money went and what the loan really cost ----
    for _, r in loans_f.sort_values(["status", "name"]).iterrows():
        closed = r["status"] == "Closed"
        with st.container(border=True):
            h = st.columns([4, 1.2])
            h[0].markdown(f"#### {r['name']}")
            h[0].caption(" · ".join(x for x in [r["lender"] if isinstance(r["lender"], str) and r["lender"] else None,
                                                 f"{r['category']} debt",
                                                 f"{r['interest_rate']:g}% p.a." if pd.notna(r["interest_rate"]) else None] if x))
            h[1].markdown(f":green-badge[Closed]" if closed else ":orange-badge[Active]")
            if closed:
                summ = [f"Cost of borrowing {_m(r['cost_of_borrowing'])}"]
                if pd.notna(r["interest_saved"]):
                    summ.append(f"interest saved {_m(r['interest_saved'])}")
            else:
                summ = [f"Outstanding {_m(r['outstanding'])}", f"EMI {_m(r['emi'])}/month"]
            st.markdown(" · ".join(summ))
            with st.expander("Details", icon=":material/expand_more:"):
                if closed:
                    st.write("")
                    c = st.columns(3)
                    kpi(c[0], "Item cost", r["asset_cost"], help="Down payment + loan amount")
                    kpi(c[1], "Total you paid", r["total_paid"], help="Down payment + installments + closing payment")
                    kpi(c[2], "Cost of borrowing", r["cost_of_borrowing"], help="Interest + other charges")
                    st.write("")
                    c = st.columns(3)
                    kpi(c[0], "Interest paid", r["interest_paid"], help="Your actual figure when recorded, else the estimate")
                    kpi(c[1], "Interest saved", r["interest_saved"],
                        help="Full tenure interest (standard calculator) − interest paid")
                    kpi(c[2], "Saved after other charges", r["net_saved"])
                    st.write("")
                    tl = []
                    if pd.notna(r["tenure_months"]):
                        unit = "installments" if pd.notna(r["installments_paid"]) else "months"
                        ran = f"closed after {int(r['tenure_months'])} {unit}"
                        if pd.notna(r["planned_months"]):
                            ran += f" of {int(r['planned_months'])} planned"
                        tl.append(ran)
                    if pd.notna(r["elapsed_months"]):
                        tl.append(f"{int(r['elapsed_months'])} months from start to closing")
                    tl.append(f"{r['start_date'] or '–'} → {r['closed_date'] or '–'}")
                    st.caption(" · ".join(tl))
                    rows = [("Down payment", r["down_payment"], "your pocket, day one"),
                            ("Installments: principal", r["emi_principal"], "repaid through EMIs"),
                            ("Installments: interest", r["emi_interest"] if pd.notna(r["emi_interest"]) else r["interest_est"],
                             "paid through EMIs" if pd.notna(r["emi_interest"]) else "estimated"),
                            ("Closing: principal cleared", r["balance_cleared"], "the balance paid off early"),
                            ("Closing: other charges", r["other_charges"], "processing / foreclosure")]
                    parts = pd.DataFrame(rows, columns=["Part", "Amount", "What"]).dropna(subset=["Amount"])
                    parts = parts[parts["Amount"] > 0]
                    if not parts.empty:
                        colors = {"Down payment": "#898781", "Installments: principal": "#1baf7a",
                                  "Installments: interest": "#eda100", "Closing: principal cleared": "#4c8bf5",
                                  "Closing: other charges": "#d6403a"}
                        fig = go.Figure()
                        for _, p in parts.iterrows():
                            fig.add_bar(y=[r["name"]], x=[p["Amount"]], name=f"{p['Part']}  {an.inr(p['Amount'])}",
                                        orientation="h", marker_color=colors[p["Part"]],
                                        hovertemplate=f"{p['Part']}: {an.inr(p['Amount'])} ({p['What']})<extra></extra>")
                        fig.update_layout(barmode="stack")
                        fig.update_yaxes(visible=False)
                        fig.update_xaxes(tickprefix="₹", tickformat="~s")
                        show(style(fig, "Where your money went", height=240).update_layout(
                        legend=dict(orientation="v", x=1.02, y=0.5, yanchor="middle"), margin=dict(l=0, r=0, t=44, b=0)))
                    st.divider()
                    st.markdown("##### How it adds up")
                    if pd.notna(r["interest_full"]):
                        st.markdown(f"- **Interest:** {_m(r['interest_paid'])} paid vs {_m(r['interest_full'])} "
                                    f"over the full tenure, so {_m(r['interest_saved'])} saved")
                    if pd.notna(r["lender_paid"]):
                        st.markdown(f"- **Paid to the lender:** {an.inr(r['lender_paid'])} "
                                    f"(installments {an.inr(r['emi_paid'])} + closing {an.inr(r['closing_payment'])}) "
                                    f"− loan {an.inr(r['principal'])} = **{an.inr(r['lender_extra'])}** "
                                    f"(interest + other charges)")
                    st.markdown(f"- **Closing payment:** {_m(r['closing_payment'])} = "
                                f"{_m(r['balance_cleared'])} balance + {_m(r['other_charges'])} charges")
                    if pd.notna(r["closing_gap"]) and abs(r["closing_gap"]) > 1:
                        st.warning(f"The closing payment is {an.inr(abs(r['closing_gap']))} "
                                   f"{'more' if r['closing_gap'] > 0 else 'less'} than balance + charges. "
                                   "Check the installment total, interest or charges.")
                    if pd.isna(r["emi_interest"]):
                        st.info("Interest is an estimate. Add installments paid, the interest in them and the closing "
                                "payment (edit below) to use your real figures.")
                    st.caption("Interest saved uses the standard reducing-balance EMI formula on the loan amount, rate "
                               "and planned tenure. It needs the planned end date.")
                else:
                    st.write("")
                    c = st.columns(3)
                    kpi(c[0], "Loan amount", r["principal"])
                    kpi(c[1], "Outstanding", r["outstanding"])
                    kpi(c[2], "EMI per month", r["emi"])
                    st.write("")
                    c = st.columns(3)
                    kpi(c[0], "Other charges", r["other_charges"])
                    kpi(c[1], "Full-tenure interest", r["interest_full"],
                        help="What the whole tenure would cost at the standard EMI (needs rate and planned end).")
                    kpi(c[2], "Interest rate", f"{r['interest_rate']:g}%" if pd.notna(r["interest_rate"]) else "–")
                    st.write("")
                    st.progress(min(max(r["repaid_pct"] / 100, 0), 1), text=f"{r['repaid_pct']:.0f}% of principal repaid")
                    st.caption(f"{r['start_date'] or '–'} → {r['end_date'] or '–'}")
        st.write("")

    if loans_total > 0:
        g = loans_f.groupby("category")["outstanding"].sum().reindex(db.LOAN_CATEGORIES).dropna()
        fig = go.Figure(go.Bar(x=g.values, y=g.index, orientation="h", marker_color=[LOAN_COLORS[k] for k in g.index]))
        fig.update_xaxes(tickprefix="₹", tickformat="~s")
        show(style(fig, "Outstanding by debt quality", height=220))
    cost = loans_f[loans_f["cost_of_borrowing"].notna()]
    if len(cost) > 1:
        fig = go.Figure()
        fig.add_bar(x=cost["name"], y=cost["interest_paid"], name="Interest", marker_color="#eda100")
        fig.add_bar(x=cost["name"], y=cost["other_charges"], name="Other charges", marker_color="#d6403a")
        fig.update_layout(barmode="stack")
        fig.update_yaxes(tickprefix="₹", tickformat="~s")
        show(style(fig, "What each loan cost you", height=260))

    with st.expander("Edit or delete loans", icon=":material/edit:"):
        edit_table("loans", loans.assign(tenure_months=loans_f["tenure_months"]), loans, {
            "name": st.column_config.TextColumn("Loan", required=True), "lender": "Lender",
            "category": st.column_config.SelectboxColumn("Category", options=db.LOAN_CATEGORIES, required=True),
            "status": st.column_config.SelectboxColumn("Status", options=db.LOAN_STATUSES, required=True),
            "down_payment": money_cfg("Down payment (₹)"), "principal": money_cfg("Loan amount (₹)"),
            "other_charges": money_cfg("Other charges (₹)"), "outstanding": money_cfg("Outstanding (₹)"),
            "interest_rate": st.column_config.NumberColumn("Interest %", min_value=0, format="%.2f"),
            "emi": money_cfg("EMI (₹)"), "start_date": dcol("Started"),
            "end_date": dcol("Planned end"),
            "closed_date": dcol("Closed on"),
            "installments_paid": st.column_config.NumberColumn("Installments paid", min_value=0, step=1, format="%d"),
            "emi_paid": money_cfg("Paid via installments (₹)"), "emi_interest": money_cfg("…interest in them (₹)"),
            "closing_payment": money_cfg("Paid to close (₹)"),
            "tenure_months": st.column_config.NumberColumn("Tenure (months)", disabled=True, format="%d",
                                                           help="Installments paid when recorded, otherwise whole months from start to closing (or planned end)."),
            "link": st.column_config.LinkColumn("Documents", display_text="Open"),
            "details": st.column_config.TextColumn("Details", width="large")}, ("name", "category"), "loans",
            to_db=lambda e: e.drop(columns="tenure_months").assign(
                outstanding=lambda d: d["outstanding"].where(d["status"] != "Closed", 0).fillna(0)))

    if not loans.empty:
        st.markdown("**Share loan details**")
        pick = st.selectbox("Loan", ["All loans"] + loans["name"].tolist(), key="loan_pick")
        part = loans if pick == "All loans" else loans[loans["name"] == pick]
        md = an.loans_markdown(part, "Loan summary" if pick == "All loans" else pick)
        with st.expander("Preview"):
            st.markdown(md)
        st.download_button("Download as Markdown", md, file_name="loans.md", icon=":material/download:")
        st.caption("A read-only write-up with links and notes that you can send to family or an advisor.")

# ================================================================== 6. STOCKS
with tabs[5]:
    for w in pos_warnings:
        st.warning(w)
    open_pos = positions[positions["qty"] > 0]
    c = st.columns(5)
    kpi(c[0], "Invested", open_pos["invested"].sum())
    kpi(c[1], "Current value", open_pos["value"].sum(),
        f"{an.inr(open_pos['value'].sum() - open_pos['invested'].sum())} unrealised")
    kpi(c[2], "Realised P/L", positions["realised"].sum())
    kpi(c[3], "Dividends received", positions["dividends"].sum())
    kpi(c[4], "Holdings", str(len(open_pos)))

    names = securities["name"].tolist()
    a1, a2, a3 = st.columns(3)
    with a1.popover("Add holding", icon=":material/add:", width="stretch"):
        with st.form("add_sec", clear_on_submit=True):
            nm = st.text_input("Name")
            sym = st.text_input("Symbol for live price (optional)", placeholder="RELIANCE.NS · AAPL · BTC-INR · 119551",
                                help="Yahoo Finance ticker (.NS / .BO for Indian stocks), or an AMFI scheme code "
                                     "for mutual funds. Leave blank for manual pricing (e.g. unlisted shares).")
            cat = st.selectbox("Category", db.SECURITY_CATEGORIES)
            ccy = st.radio("Price currency", db.CURRENCIES, horizontal=True,
                           help="USD holdings are converted to ₹ at the live USD/INR rate.")
            why = st.text_area("Why am I investing in this?", height=80)
            cp = st.number_input("Current price (in that currency)", min_value=0.0, format="%.2f",
                                 help="Optional when a symbol is set: it is fetched live.")
            st.caption("First purchase (leave quantity at 0 to skip)")
            q = st.number_input("Quantity", min_value=0.0, format="%.4f")
            p = st.number_input("Buy price (in that currency)", min_value=0.0, format="%.2f")
            fe = st.number_input("Fees / brokerage (in that currency)", min_value=0.0, format="%.2f")
            fxr = st.number_input("USD→INR rate at purchase (USD only)", min_value=0.0, value=float(fx), format="%.4f")
            dt_ = dinput(st, "Bought on", date.today())
            if st.form_submit_button("Add holding", type="primary"):
                if not nm.strip():
                    st.error("Name required.")
                elif nm.strip().casefold() in {n.casefold() for n in names}:
                    st.error("A holding with this name exists. Use Add trade instead.")
                else:
                    sid = db.insert("securities", {"name": nm.strip(), "symbol": sym, "category": cat, "reason": why,
                                                   "current_price": cp or None, "currency": ccy,
                                                   "price_date": date.today() if cp else None})
                    if q > 0:
                        db.insert("trades", {"security_id": sid, "date": dt_, "side": "Buy", "qty": q, "price": p,
                                             "fees": fe or None, "fx_rate": (fxr or None) if ccy == "USD" else None})
                    flash(f"{nm.strip()} added." + (" Live price will load on the next refresh." if sym.strip() else ""))
                    st.rerun()
    with a2.popover("Add trade (buy / sell)", icon=":material/swap_horiz:", width="stretch"):
        if not names:
            st.info("Add a holding first.")
        else:
            with st.form("add_trade", clear_on_submit=True):
                who = st.selectbox("Holding", names)
                side = st.radio("Side", ["Buy", "Sell"], horizontal=True)
                dt_ = dinput(st, "Date", date.today())
                q = st.number_input("Quantity", min_value=0.0, format="%.4f")
                p = st.number_input("Price per unit (in the holding's currency)", min_value=0.0, format="%.2f")
                fe = st.number_input("Fees / brokerage / taxes (same currency)", min_value=0.0, format="%.2f")
                fxr = st.number_input("USD→INR rate on that day (USD holdings only)", min_value=0.0,
                                      value=float(fx), format="%.4f",
                                      help="Locks in the rupee cost of this trade. Defaults to today's rate.")
                nt = st.text_input("Note")
                if st.form_submit_button("Save trade", type="primary"):
                    if q > 0 and p > 0:
                        sid = int(securities.loc[securities["name"] == who, "id"].iloc[0])
                        is_usd = securities.loc[securities["id"] == sid, "currency"].iloc[0] == "USD"
                        db.insert("trades", {"security_id": sid, "date": dt_, "side": side, "qty": q, "price": p,
                                             "fees": fe or None, "note": nt, "fx_rate": (fxr or None) if is_usd else None})
                        if pd.isna(pd.to_numeric(securities.loc[securities["id"] == sid, "current_price"]).iloc[0]):
                            db.update_row("securities", sid, {"current_price": p, "price_date": dt_})
                        flash(f"{side} saved.")
                        st.rerun()
                    else:
                        st.error("Quantity and price must be above zero.")
    with a3.popover("Record dividend", icon=":material/payments:", width="stretch"):
        if not names:
            st.info("Add a holding first.")
        else:
            with st.form("add_div", clear_on_submit=True):
                who = st.selectbox("Holding", names, key="div_who")
                dt_ = dinput(st, "Received on", date.today(), key="div_date")
                amt = st.number_input("Amount received (₹)", min_value=0.0, format="%.2f",
                                      help="In rupees, as credited to your bank.")
                nt = st.text_input("Note", key="div_note")
                if st.form_submit_button("Save dividend", type="primary"):
                    if amt > 0:
                        sid = int(securities.loc[securities["name"] == who, "id"].iloc[0])
                        db.insert("dividends", {"security_id": sid, "date": dt_, "amount": amt, "note": nt})
                        flash("Dividend saved.")
                        st.rerun()
                    else:
                        st.error("Amount must be above zero.")

    cats = st.multiselect("Category", db.SECURITY_CATEGORIES, key="stk_cats", placeholder="All categories")
    show_closed = st.toggle("Show fully sold holdings", key="stk_closed")
    view = positions if show_closed else open_pos
    if cats:
        view = view[view["category"].isin(cats)]
    if view.empty:
        st.info("No holdings to show yet. Use Add holding above.")
    else:
        t = view[["name", "category", "currency", "qty", "avg_price", "first_buy", "last_buy", "invested",
                  "current_price", "price_date", "value", "pnl", "pnl_pct", "weight_pct", "dividends", "last_dividend", "realised", "reason"]]
        st.dataframe(t, hide_index=True, width="stretch", column_config={
            "name": "Holding", "category": "Category", "currency": "Ccy",
            "price_date": dcol("Price as of"), "qty": st.column_config.NumberColumn("Units", format="%.4g"),
            "avg_price": st.column_config.NumberColumn("Avg buy price", format="%.2f", help="In the holding's own currency"),
            "first_buy": dcol("First bought"),
            "last_buy": dcol("Last bought"),
            "invested": st.column_config.NumberColumn("Invested (₹)", format="₹%.0f"),
            "current_price": st.column_config.NumberColumn("Price now", format="%.2f", help="In the holding's own currency"),
            "value": st.column_config.NumberColumn("Current value (₹)", format="₹%.0f"),
            "pnl": st.column_config.NumberColumn("Unrealised P/L (₹)", format="₹%.0f"),
            "pnl_pct": st.column_config.NumberColumn("P/L %", format="%.1f%%"),
            "weight_pct": st.column_config.ProgressColumn("% of portfolio", format="%.1f%%", min_value=0, max_value=100),
            "dividends": st.column_config.NumberColumn("Dividends", format="₹%.0f"),
            "last_dividend": dcol("Last dividend"),
            "realised": st.column_config.NumberColumn("Realised P/L", format="₹%.0f"),
            "reason": st.column_config.TextColumn("Why I hold it", width="large")})

    left, right = st.columns(2)
    with left:
        g = open_pos.groupby("category")["value"].sum().reindex(db.SECURITY_CATEGORIES).dropna()
        if not g.empty:
            fig = go.Figure(go.Pie(labels=g.index, values=g.values, hole=0.55, sort=False,
                                   marker_colors=BUCKET_COLORS[:len(g)], textinfo="percent"))
            show(style(fig, "Portfolio by category"))
    with right:
        if not dividends.empty:
            dd = dividends.copy()
            dd["ym"] = pd.to_datetime(dd["date"]).dt.to_period("M").astype(str)
            m = dd.groupby("ym")["amount"].sum()
            fig = go.Figure(go.Bar(x=m.index, y=m.values, marker_color=SOURCE_COLORS["Dividend/FD"]))
            fig.update_yaxes(tickprefix="₹", tickformat="~s")
            show(style(fig, "Dividends received by month"))

    if names:
        with st.expander("History of one holding"):
            who = st.selectbox("Holding", names, key="hist_who")
            sid = int(securities.loc[securities["name"] == who, "id"].iloc[0])
            st.markdown("Trades")
            st.dataframe(trades[trades["security_id"] == sid].drop(columns=["id", "security_id"]),
                         hide_index=True, width="stretch")
            st.markdown("Dividends")
            st.dataframe(dividends[dividends["security_id"] == sid].drop(columns=["id", "security_id"]),
                         hide_index=True, width="stretch")

    with st.expander("Edit holdings, trades and dividends"):
        e1, e2, e3 = st.tabs(["Holdings & prices", "Trades", "Dividends"])
        id2name = dict(zip(securities["id"], securities["name"]))
        name2id = {v: k for k, v in id2name.items()}
        with e1:
            st.caption("Holdings with a **Symbol** are priced live (a typed price is replaced on the next refresh); "
                       "leave Symbol blank to price a holding yourself. "
                       "Deleting a holding also deletes its trades and dividends (a backup is taken first).")

            def stamp_price(df):
                df = df.copy()
                old = securities.set_index("id")["current_price"]
                for i, r in df.iterrows():
                    if pd.notna(r["id"]) and r["current_price"] != old.get(r["id"]) and pd.notna(r["current_price"]):
                        df.at[i, "price_date"] = date.today()
                return df
            edit_table("securities", securities, securities, {
                "name": st.column_config.TextColumn("Name", required=True), "symbol": st.column_config.TextColumn("Symbol (live price)", help="RELIANCE.NS, AAPL, BTC-INR or an AMFI code"),
                "currency": st.column_config.SelectboxColumn("Ccy", options=db.CURRENCIES, required=True),
                "category": st.column_config.SelectboxColumn("Category", options=db.SECURITY_CATEGORIES, required=True),
                "reason": st.column_config.TextColumn("Why I hold it", width="large"),
                "current_price": st.column_config.NumberColumn("Price now (own currency)", min_value=0, format="%.2f"),
                "price_date": dcol("Price as of")}, ("name", "category", "currency"), "securities",
                to_db=stamp_price)
        with e2:
            tshow = trades.assign(security=trades["security_id"].map(id2name)).drop(columns=["security_id"])
            tshow = tshow[["id", "security", "date", "side", "qty", "price", "fees", "fx_rate", "note"]]

            def to_db_t(df):
                df = df.copy()
                df["security_id"] = df["security"].map(name2id)
                return df.drop(columns=["security"])
            edit_table("trades", tshow, trades, {
                "security": st.column_config.SelectboxColumn("Holding", options=names, required=True),
                "date": dcol("Date", required=True),
                "side": st.column_config.SelectboxColumn("Side", options=["Buy", "Sell"], required=True),
                "qty": st.column_config.NumberColumn("Units", min_value=0, format="%.4f", required=True),
                "price": money_cfg("Price (own currency)", required=True), "fees": money_cfg("Fees (own currency)"),
                "fx_rate": st.column_config.NumberColumn("USD→INR", min_value=0, format="%.4f",
                                                          help="USD holdings only; blank = today's rate"),
                "note": "Note"},
                ("security_id", "date", "side", "qty", "price"), "trades", to_db=to_db_t)
        with e3:
            dshow = dividends.assign(security=dividends["security_id"].map(id2name)).drop(columns=["security_id"])
            dshow = dshow[["id", "security", "date", "amount", "note"]]

            def to_db_d(df):
                df = df.copy()
                df["security_id"] = df["security"].map(name2id)
                return df.drop(columns=["security"])
            edit_table("dividends", dshow, dividends, {
                "security": st.column_config.SelectboxColumn("Holding", options=names, required=True),
                "date": dcol("Received on", required=True),
                "amount": money_cfg("Amount (₹)", required=True), "note": "Note"},
                ("security_id", "date", "amount"), "dividends", to_db=to_db_d)

# ================================================================== 7. GOLD & SILVER
FORMS = ["Coin", "Bar", "Jewellery", "Other"]
with tabs[6]:
    st.caption("Physical gold and silver (coins, bars, jewellery). Prices below are per gram of pure metal "
               "(24K gold, 999 silver); each purchase is valued at its own karat/purity.")
    live_m = st.checkbox("Keep prices live", value=db.meta_get("metal_auto", "1") == "1", key="metal_live",
                         help="International spot price × USD/INR ÷ 31.1035 g/oz, then your local premium on top.")
    manual_price, premium_in, local_in = {}, {}, {}
    cols = st.columns(2)
    for col, metal in zip(cols, db.METALS):
        spot = float(db.meta_get(f"spot_{metal.lower()}", 0) or 0)
        unit = "24K" if metal == "Gold" else "999"
        with col.container(border=True):
            st.markdown(f"##### {metal} · {unit} · per gram")
            if live_m:
                c = st.columns(3)
                c[0].metric("International", an.inr(spot) if spot else "–",
                            help="Spot price converted to ₹/g: no import duty, GST or dealer margin")
                c[1].metric("Price used", an.inr(prices[metal]) if prices[metal] else "–",
                            help="International price + local premium")
                if spot and fx:
                    st.caption(f"International = ${spot * market.OZ_GRAMS / fx:,.0f}/oz (COMEX futures) × "
                               f"₹{fx:,.2f}/$ ÷ {market.OZ_GRAMS} g/oz = ₹{spot:,.0f}/g")
                premium_in[metal] = c[2].number_input(
                    "Local premium %", value=db.metal_premium(metal), step=0.5, format="%.2f",
                    key=f"prem_{metal}_{db.metal_premium(metal)}",
                    help="Starts at India's 6% import duty. Indian shop rates also include dealer margin: "
                         "calibrate below.")
                local_in[metal] = st.number_input(
                    f"Calibrate: today's {unit} {metal.lower()} rate from your jeweller (₹/g)", min_value=0.0,
                    value=0.0, format="%.2f", key=f"cal_{metal}_{version('metal_cal')}",
                    help="Optional. Type the per-gram rate you see locally (before GST) and the premium is worked "
                         "out for you. Leave 0 to use the premium above.")
            else:
                manual_price[metal] = st.number_input(
                    f"{metal} price (₹ / g, {unit})", min_value=0.0, value=prices[metal], format="%.2f",
                    key=f"man_{metal}_{prices[metal]}")
    if st.button("Save price settings", type="primary"):
        db.meta_set("metal_auto", "1" if live_m else "0")
        problems = []
        for metal in db.METALS:
            low = metal.lower()
            if live_m:
                spot = float(db.meta_get(f"spot_{low}", 0) or 0)
                prem = premium_in[metal]
                if local_in[metal] > 0:
                    if spot > 0:
                        prem = (local_in[metal] / spot - 1) * 100
                    else:
                        problems.append(f"No live {metal.lower()} price yet to calibrate against; try Refresh now.")
                db.meta_set(f"premium_{low}", round(prem, 2))
                if spot > 0:
                    db.meta_set(f"price_{low}", round(spot * (1 + prem / 100), 2))
            else:
                db.meta_set(f"price_{low}", manual_price[metal])
        if live_m and not all(float(db.meta_get(f"spot_{m.lower()}", 0) or 0) for m in db.METALS):
            market.refresh(force=True)
        bump("metal_cal")
        flash(" ".join(problems) or "Price settings saved.")
        st.rerun()
    if live_m and not (prices["Gold"] or prices["Silver"]):
        st.info("No live price yet. Press Refresh now in the sidebar, or turn off 'Keep prices live' to type them.")

    for metal in db.METALS:
        m = metals_f[metals_f["metal"] == metal]
        c = st.columns(5)
        kpi(c[0], f"{metal} held", f"{m['grams'].sum():,.2f} g")
        kpi(c[1], "Invested (all-in)", m["invested"].sum())
        kpi(c[2], "Charges + GST paid", m["charges"].sum(),
            f"{m['charges'].sum() / m['metal_cost'].sum() * 100:.1f}% of metal cost" if m["metal_cost"].sum() else None,
            help="Making charges + GST + other charges")
        kpi(c[3], "Current value", m["value"].sum())
        kpi(c[4], "Gain / loss", m["pnl"].sum())

    st.markdown("#### Add a purchase")
    metal = st.radio("Metal", db.METALS, horizontal=True, key="metal_pick")
    with st.form("add_metal", clear_on_submit=True):
        c = st.columns(3)
        form = c[0].selectbox("Form", FORMS)
        pur = c[1].selectbox("Karat" if metal == "Gold" else "Purity (fineness)", db.PURITIES[metal],
                             index=db.PURITIES[metal].index(db.DEFAULT_PURITY[metal]),
                             help="Gold in karats (22K = 91.6% pure); silver as fineness (925 = sterling).")
        dt_ = dinput(c[2], "Bought on", date.today())
        desc = st.text_input("Description", placeholder="e.g. 10 g Tanishq coin")
        c = st.columns(5)
        gr = c[0].number_input("Grams", min_value=0.0, format="%.3f")
        rate = c[1].number_input("Rate paid (₹/g)", min_value=0.0, format="%.2f",
                                 help="Per-gram rate you were charged for this karat/purity, before making charges and GST")
        mk = c[2].number_input("Making charges (₹)", min_value=0.0, format="%.2f")
        gst = c[3].number_input("GST (₹)", min_value=0.0, format="%.2f")
        oth = c[4].number_input("Other charges (₹)", min_value=0.0, format="%.2f")
        if st.form_submit_button("Add purchase", type="primary"):
            if gr > 0 and rate > 0:
                db.insert("metals", {"metal": metal, "form": form, "description": desc, "date": dt_, "grams": gr,
                                     "rate_per_g": rate, "making_charges": mk or None, "gst": gst or None,
                                     "other_charges": oth or None, "purity": pur})
                flash("Purchase added.")
                st.rerun()
            else:
                st.error("Grams and rate must be above zero.")

    if not metals_f.empty:
        st.markdown("#### Per purchase")
        t = metals_f[["metal", "purity", "description", "date", "grams", "metal_cost", "charges", "charges_pct",
                      "invested", "current_price", "value", "pnl", "pnl_pct"]]
        st.dataframe(t, hide_index=True, width="stretch", column_config={
            "metal": "Metal", "purity": "Karat / purity", "description": "Description",
            "date": dcol("Bought on"),
            "grams": st.column_config.NumberColumn("Grams", format="%.3f"),
            "metal_cost": st.column_config.NumberColumn("Metal cost", format="₹%.0f"),
            "charges": st.column_config.NumberColumn("Charges + GST", format="₹%.0f"),
            "charges_pct": st.column_config.NumberColumn("Charges %", format="%.1f%%"),
            "invested": st.column_config.NumberColumn("Invested", format="₹%.0f"),
            "current_price": st.column_config.NumberColumn("Price now /g", format="₹%.2f",
                                                           help="For this purchase's karat/purity"),
            "value": st.column_config.NumberColumn("Value now", format="₹%.0f"),
            "pnl": st.column_config.NumberColumn("Gain / loss", format="₹%.0f"),
            "pnl_pct": st.column_config.NumberColumn("Gain %", format="%.1f%%")})

    with st.expander("Edit or delete purchases", icon=":material/edit:"):
        purity_opts = db.PURITIES["Gold"] + db.PURITIES["Silver"]
        purity_opts += sorted({p for p in metals["purity"].dropna() if p not in purity_opts})
        edit_table("metals", metals, metals, {
            "metal": st.column_config.SelectboxColumn("Metal", options=db.METALS, required=True),
            "form": st.column_config.SelectboxColumn("Form", options=FORMS),
            "purity": st.column_config.SelectboxColumn("Karat / purity", options=purity_opts),
            "description": "Description", "date": dcol("Bought on"),
            "grams": st.column_config.NumberColumn("Grams", min_value=0, format="%.3f", required=True),
            "rate_per_g": money_cfg("Rate paid (₹/g)", required=True), "making_charges": money_cfg("Making (₹)"),
            "gst": money_cfg("GST (₹)"), "other_charges": money_cfg("Other (₹)"), "note": "Note"},
            ("metal", "grams", "rate_per_g"), "metals")

# ================================================================== 8. REAL ESTATE
RE_KINDS = ["Flat", "House", "Plot", "Land", "Commercial", "Other"]
with tabs[7]:
    rented = re_f["rent_yielding"].astype(bool)
    c = st.columns(3)
    kpi(c[0], "Current value", re_f["value"].sum(),
        f"{re_f['gain'].sum() / re_f['total_cost'].sum() * 100:+.1f}% vs total paid" if re_f["total_cost"].sum() else None,
        help="Current price per sq ft × area. Uses purchase cost until both are filled in")
    kpi(c[1], "Total paid (incl. charges)", re_f["total_cost"].sum())
    kpi(c[2], "Gain / loss", re_f["gain"].sum())
    st.write("")
    c = st.columns(3)
    kpi(c[0], "Properties", str(len(re_f)))
    kpi(c[1], "Extra charges paid", re_f["extra_charges"].sum(), help="Registration, stamp duty, brokerage, other")
    kpi(c[2], "Rent per month", re_f.loc[rented, "monthly_rent"].sum())
    st.write("")

    with st.expander("Add a property", icon=":material/add_home:", expanded=re_f.empty):
        with st.form("add_re", clear_on_submit=True, border=False):
            st.markdown("##### The property")
            c = st.columns(3)
            nm = c[0].text_input("Name", placeholder="e.g. 2BHK Whitefield")
            kind = c[1].selectbox("Type", RE_KINDS)
            loc = c[2].text_input("Location")
            c = st.columns(3)
            pd_ = dinput(c[0], "Purchased on", value=None)
            area = c[1].number_input("Area (sq ft)", min_value=0.0, format="%.0f")
            cur = c[2].number_input("Current price per sq ft (₹)", min_value=0.0, step=100.0, format="%.0f",
                                    help="Current value = this × area")
            st.markdown("##### What you paid")
            c = st.columns(4)
            cost = c[0].number_input("Cost of purchase (₹)", min_value=0.0, step=100000.0, format="%.0f")
            reg = c[1].number_input("Registration + stamp duty (₹)", min_value=0.0, format="%.0f")
            brk = c[2].number_input("Brokerage (₹)", min_value=0.0, format="%.0f")
            oth = c[3].number_input("Other charges (₹)", min_value=0.0, format="%.0f")
            st.markdown("##### Rent")
            c = st.columns(3, vertical_alignment="bottom")
            ry = c[0].checkbox("Rent yielding")
            rent = c[1].number_input("Rent per month (₹)", min_value=0.0, step=1000.0, format="%.0f")
            st.write("")
            if st.form_submit_button("Add property", type="primary"):
                if nm.strip() and cost > 0:
                    db.insert("real_estate", {"name": nm, "kind": kind, "location": loc, "purchase_date": pd_,
                                              "cost": cost, "area_sqft": area or None, "registration": reg or None,
                                              "brokerage": brk or None, "other_charges": oth or None,
                                              "rent_yielding": ry, "monthly_rent": rent or None,
                                              "current_per_sqft": cur or None})
                    flash("Property added.")
                    st.rerun()
                else:
                    st.error("A name and a purchase cost above zero are needed.")

    for _, r in re_f.iterrows():
        with st.container(border=True):
            bits = [b for b in (r["kind"], r["location"], f"{r['area_sqft']:,.0f} sq ft" if r["area_sqft"] else None,
                                f"bought {r['purchase_date']}" if pd.notna(r["purchase_date"]) else None) if b]
            st.markdown(f"#### {r['name']}")
            st.caption(" · ".join(bits) or "No details yet")
            c = st.columns(4)
            c[0].metric("Cost of purchase", an.inr(r["cost"]), help="Before registration, brokerage and other charges")
            c[1].metric("Extra charges", an.inr(r["extra_charges"]))
            c[2].metric("Total paid", an.inr(r["total_cost"]))
            c[3].metric("Cost per sq ft", an.inr(r["cost_per_sqft"]) if r["cost_per_sqft"] else "–")
            st.write("")
            c = st.columns(4)
            c[0].metric("Value now", an.inr(r["value"]),
                        f"{r['gain'] / r['total_cost'] * 100:+.1f}%" if r["total_cost"] else None)
            c[1].metric("Price per sq ft now", an.inr(r["current_per_sqft"]) if r["current_per_sqft"] else "–",
                        help="Set it in 'Edit or delete properties' below to value by area")
            c[2].metric("Gain / loss", an.inr(r["gain"]))
            if r["rent_yielding"]:
                c[3].metric("Rent per month", an.inr(r["monthly_rent"]), f"{r['rent_yield_pct']:.2f}% yearly yield",
                            delta_color="off")
            else:
                c[3].metric("Rent per month", "Not rented")
            if r["note"]:
                st.caption(r["note"])
    if re_f.empty:
        st.info("No properties yet. Use Add a property above.")

    with st.expander("Edit or delete properties", icon=":material/edit:"):
        edit_table("real_estate", real_estate, real_estate, {
            "name": st.column_config.TextColumn("Property", required=True),
            "kind": st.column_config.SelectboxColumn("Type", options=RE_KINDS),
            "location": "Location", "purchase_date": dcol("Purchased on"),
            "cost": money_cfg("Cost (₹)", required=True), "area_sqft": st.column_config.NumberColumn("Sq ft", min_value=0),
            "registration": money_cfg("Registration + stamp (₹)"), "brokerage": money_cfg("Brokerage (₹)"),
            "other_charges": money_cfg("Other charges (₹)"),
            "rent_yielding": st.column_config.CheckboxColumn("Rent yielding"),
            "monthly_rent": money_cfg("Rent / month (₹)"), "current_per_sqft": money_cfg("Price / sq ft now (₹)"),
            "note": "Note"}, ("name", "cost"), "real_estate")

# ================================================================== 9. NOTES
with tabs[8]:
    with st.form("add_note", clear_on_submit=True):
        c = st.columns([3, 1], vertical_alignment="bottom")
        title = c[0].text_input("Title")
        kind = c[1].selectbox("Kind", db.NOTE_KINDS)
        body = st.text_area("Details", height=90)
        if st.form_submit_button("Add note", type="primary"):
            if title.strip():
                now = datetime.now().strftime("%Y-%m-%d %H:%M")
                db.insert("notes", {"title": title, "body": body, "kind": kind, "done": False,
                                    "created": now, "updated": now})
                flash("Note added.")
                st.rerun()
            else:
                st.error("Give the note a title.")
    f1, f2 = st.columns([2, 1])
    kinds = f1.multiselect("Show", db.NOTE_KINDS, key="note_kinds", placeholder="All kinds")
    hide_done = f2.toggle("Hide completed", key="note_hide")
    shown = notes.iloc[::-1]
    if kinds:
        shown = shown[shown["kind"].isin(kinds)]
    if hide_done:
        shown = shown[~shown["done"]]
    if shown.empty:
        st.info("Nothing here yet.")
    editing = st.session_state.get("_edit_note")
    for _, n in shown.iterrows():
        nid = int(n["id"])
        with st.container(border=True):
            if editing == nid:
                with st.form(f"edit_note_{nid}"):
                    t = st.text_input("Title", n["title"])
                    k = st.selectbox("Kind", db.NOTE_KINDS,
                                     index=db.NOTE_KINDS.index(n["kind"]) if n["kind"] in db.NOTE_KINDS else 0)
                    b = st.text_area("Details", n["body"] or "", height=120)
                    s1, s2, _ = st.columns([1, 1, 6])
                    if s1.form_submit_button("Save", type="primary") and t.strip():
                        db.update_row("notes", nid, {"title": t, "kind": k, "body": b,
                                                     "updated": datetime.now().strftime("%Y-%m-%d %H:%M")})
                        st.session_state.pop("_edit_note")
                        flash("Note updated.")
                        st.rerun()
                    if s2.form_submit_button("Cancel"):
                        st.session_state.pop("_edit_note")
                        st.rerun()
            else:
                h1, h2 = st.columns([6, 2], vertical_alignment="center")
                title_md = f"~~{n['title']}~~" if n["done"] else f"**{n['title']}**"
                h1.markdown(f"{title_md} · :gray[{n['kind'] or 'Note'} · updated {n['updated'] or n['created']}]")
                if n["body"]:
                    st.markdown(n["body"])
                b1, b2, b3, _ = st.columns([1.2, 1, 1, 6])
                if b1.button("Mark open" if n["done"] else "Mark done", key=f"done_{nid}",
                             icon=":material/undo:" if n["done"] else ":material/check:"):
                    db.update_row("notes", nid, {"done": not n["done"]})
                    st.rerun()
                if b2.button("Edit", key=f"edit_{nid}", icon=":material/edit:"):
                    st.session_state["_edit_note"] = nid
                    st.rerun()
                if b3.button("Delete", key=f"del_{nid}", icon=":material/delete:"):
                    db.backup()
                    db.delete_row("notes", nid)
                    flash("Note deleted.")
                    st.rerun()
