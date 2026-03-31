import streamlit as st
import pandas as pd
import requests
import re
from requests.auth import HTTPBasicAuth
import plotly.express as px
from streamlit_autorefresh import st_autorefresh
from datetime import datetime, timedelta
import smtplib
from email.message import EmailMessage
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
from reportlab.lib import colors as rl_colors   # ✅ FIX: renamed to avoid clash with plotly colors dict
from reportlab.lib.pagesizes import letter
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ✅ SESSION WITH RETRY
session = requests.Session()
retries = Retry(total=3, backoff_factor=2, status_forcelist=[500, 502, 503, 504])
session.mount("https://", HTTPAdapter(max_retries=retries))

st.set_page_config(layout="wide")

# ----------------------------------
# 🎨 UI STYLE
# ----------------------------------
st.markdown("""
<style>
.metric-card {
    background-color: #1F2A40;
    padding: 20px;
    border-radius: 12px;
    text-align: center;
}
</style>
""", unsafe_allow_html=True)

# ----------------------------------
# 🔐 LOGIN
# ----------------------------------
users = {"team1": {"password": "123"}, "team2": {"password": "123"}}

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if not st.session_state.logged_in:
    st.title("🔐 Login")
    u = st.text_input("User")
    p = st.text_input("Password", type="password")
    if st.button("Login"):
        if u in users and users[u]["password"] == p:
            st.session_state.logged_in = True
            st.session_state.username = u
            st.rerun()
        else:
            st.error("Invalid ❌")
    st.stop()

# ----------------------------------
# 👤 USER BAR + LOGOUT
# ----------------------------------
c1, c2 = st.columns([8, 2])
with c1:
    st.title(f"🚀 QATrackPro — {st.session_state.username}")
with c2:
    if st.button("Logout"):
        st.session_state.clear()
        st.rerun()

st_autorefresh(interval=300000)

# ----------------------------------
# 🔢 RUN IDS
# ----------------------------------
run_input = st.text_input("Run IDs (comma separated)", placeholder="e.g. 1073095,1073096")
if not run_input:
    st.warning("⚠️ Please enter at least one Run ID")
    st.stop()

run_ids = [r.strip() for r in run_input.split(",") if r.strip()]

# ----------------------------------
# 🔹 HELPERS
# ----------------------------------
def extract_tester(comment):
    if not comment:
        return "Unknown"
    match = re.search(r"(Tester|Tested by)\s*:\s*([A-Za-z ]+)", comment, re.IGNORECASE)
    return match.group(2).strip() if match else "Unknown"

# ----------------------------------
# 🔹 STATUS MAP
# ✅ FIX: normalize to title case so "passed" matches "Passed"
# ----------------------------------
@st.cache_data(ttl=3600)
def get_status_map():
    try:
        url = f"{st.secrets['testrail_url']}/index.php?/api/v2/get_statuses"
        res = session.get(
            url,
            auth=(st.secrets["testrail_email"], st.secrets["testrail_api"]),
            verify=False, timeout=30
        )
        return {s["id"]: s["name"].strip().title() for s in res.json()}
    except:
        return {}

# ----------------------------------
# 🔹 TESTRAIL
# ✅ FIX: added verify=False + timeout, safe error handling
# ----------------------------------
@st.cache_data(ttl=600)
def get_testrail(run_id):
    try:
        url = f"{st.secrets['testrail_url']}/index.php?/api/v2/get_results_for_run/{run_id}"
        res = session.get(
            url,
            auth=(st.secrets["testrail_email"], st.secrets["testrail_api"]),
            verify=False, timeout=30
        )
        if res.status_code != 200:
            st.error(f"❌ Run {run_id} failed: {res.status_code} {res.text[:150]}")
            return pd.DataFrame()
        data = res.json()
    except Exception as e:
        st.error(f"❌ Run {run_id} error: {e}")
        return pd.DataFrame()

    results = data.get("results", []) if isinstance(data, dict) else data
    if not results:
        st.warning(f"⚠️ No results in Run ID {run_id}")
        return pd.DataFrame()

    status_map = get_status_map()
    allowed    = {"Passed", "Failed", "Blocked"}
    rows       = []

    for r in results:
        status = status_map.get(r.get("status_id"), "Unknown")
        if status not in allowed:
            continue  # ✅ skip custom statuses

        ts = r.get("created_on")
        exec_date = datetime.fromtimestamp(ts).date() if ts else datetime.today().date()

        rows.append({
            "TestCaseID":    r.get("test_id"),
            "TesterName":    extract_tester(r.get("comment", "")),
            "Status":        status,
            "DefectID":      r.get("defects", ""),
            "ExecutionDate": exec_date,
        })

    return pd.DataFrame(rows)

# ----------------------------------
# 🔹 JIRA
# ✅ FIX: auto-prefix https://, safe merge when no comments exist
# ----------------------------------
@st.cache_data(ttl=600)
def get_jira():
    domain = st.secrets["jira_domain"].strip().rstrip("/")
    if not domain.startswith("http"):
        domain = f"https://{domain}"

    try:
        url = f"{domain}/rest/api/2/search"
        res = session.get(
            url,
            auth=HTTPBasicAuth(st.secrets["jira_email"], st.secrets["jira_token"]),
            params={"jql": f"project in ({st.secrets['jira_project_key']})", "maxResults": 50},
            verify=False, timeout=20
        )
        if res.status_code != 200:
            st.error(f"❌ Jira API Error {res.status_code}: {res.text[:200]}")
            return pd.DataFrame()
        data = res.json()
    except Exception as e:
        st.error(f"❌ Jira connection failed: {e}")
        return pd.DataFrame()

    issues, comments = [], []

    for issue in data.get("issues", []):
        bug    = issue["key"]
        status = issue["fields"]["status"]["name"]
        issues.append({"BugID": bug, "BugStatus": status})

        try:
            c_url = f"{domain}/rest/api/2/issue/{bug}/comment"
            c_res = session.get(
                c_url,
                auth=HTTPBasicAuth(st.secrets["jira_email"], st.secrets["jira_token"]),
                verify=False, timeout=20
            )
            if c_res.status_code != 200:
                continue
            c_data = c_res.json()
            if c_data.get("comments"):
                last = c_data["comments"][-1]
                try:
                    body = last.get("body", "")
                    if isinstance(body, dict):                          # ✅ fixed indent
                        txt = body["content"][0]["content"][0]["text"]  # ✅ fixed indent
                    else:                                               # ✅ fixed indent
                        txt = str(body).strip() if body else "No Comment"  # ✅ fixed indent
                except:
                    txt = "No Comment"
                comments.append({
                    "BugID":         bug,
                    "LatestComment": txt,
                    "Author":        last["author"]["displayName"],
                    "CommentDate":   last["created"]
                })
        except:
            continue

    if not issues:
        return pd.DataFrame()

    issues_df   = pd.DataFrame(issues)
    comments_df = pd.DataFrame(comments) if comments else pd.DataFrame(
        columns=["BugID", "LatestComment", "Author", "CommentDate"]
    )
    return pd.merge(issues_df, comments_df, on="BugID", how="left")
# ----------------------------------
# 🔹 LOAD DATA
# ----------------------------------
df_list = [get_testrail(r) for r in run_ids]
df_list = [d for d in df_list if not d.empty]

if not df_list:
    st.error("❌ No data found for given Run IDs")
    st.stop()

test_df = pd.concat(df_list, ignore_index=True)
jira_df = get_jira()

if jira_df.empty or "BugID" not in jira_df.columns:
    st.warning("⚠️ Jira not available — showing TestRail data only")
    final_df = test_df.copy()
    for col in ["BugID", "BugStatus", "LatestComment", "Author", "CommentDate"]:
        final_df[col] = "N/A"
else:
    final_df = pd.merge(test_df, jira_df, left_on="DefectID", right_on="BugID", how="left")

# ----------------------------------
# 🔹 KPI
# ----------------------------------
total     = len(final_df)
passed    = len(final_df[final_df["Status"] == "Passed"])
failed    = len(final_df[final_df["Status"] == "Failed"])
blocked   = len(final_df[final_df["Status"] == "Blocked"])
rate      = round((passed / total) * 100, 2) if total else 0
# ✅ FIX: exclude empty strings from bug count
bugs      = final_df["DefectID"].replace("", pd.NA).dropna().nunique()

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Total",   total)
c2.metric("Passed",  passed)
c3.metric("Failed",  failed)
c4.metric("Blocked", blocked)
c5.metric("Pass %",  f"{rate}%")
c6.metric("Bugs",    bugs)

status_colors = {"Passed": "#2ECC71", "Failed": "#E74C3C", "Blocked": "#F39C12"}

# ----------------------------------
# 📈 EXECUTION TREND
# ✅ FIX: real dates, correct count, only Pass/Fail/Blocked
# ----------------------------------
st.subheader("📈 Execution Trend")

final_df["ExecutionDate"] = pd.to_datetime(final_df["ExecutionDate"])

trend = (
    final_df.groupby([final_df["ExecutionDate"].dt.date, "Status"])
    .size()
    .reset_index(name="Count")
)
trend.rename(columns={"ExecutionDate": "Date"}, inplace=True)
trend["Date"] = pd.to_datetime(trend["Date"])
trend = trend.sort_values("Date")

fig = px.line(
    trend,
    x="Date", y="Count", color="Status",
    markers=True,
    color_discrete_map=status_colors,
    labels={"Count": "Test Count"}
)
fig.update_layout(
    plot_bgcolor="#1F2A40", paper_bgcolor="#1F2A40",
    font_color="white",
    xaxis=dict(showgrid=False),
    yaxis=dict(showgrid=True, gridcolor="#2C3E50")
)
st.plotly_chart(fig, use_container_width=True)

# ----------------------------------
# 👨‍💻 TESTER PERFORMANCE
# ✅ FIX: count tests per tester grouped by status (not word count)
# ----------------------------------
st.subheader("👨‍💻 Tester Performance")

perf = (
    final_df.groupby(["TesterName", "Status"])
    .size()
    .reset_index(name="TestCount")
)

fig_perf = px.bar(
    perf,
    x="TesterName", y="TestCount",
    color="Status",
    barmode="group",
    text="TestCount",
    color_discrete_map=status_colors,
    labels={"TestCount": "Tests Executed", "TesterName": "Tester"}
)
fig_perf.update_layout(
    plot_bgcolor="#1F2A40", paper_bgcolor="#1F2A40",
    font_color="white",
    xaxis=dict(showgrid=False),
    yaxis=dict(showgrid=True, gridcolor="#2C3E50")
)
st.plotly_chart(fig_perf, use_container_width=True)

# ----------------------------------
# 🚨 FAILED TESTS
# ----------------------------------
st.subheader("🚨 Failed Tests")
failed_df = final_df[final_df["Status"] == "Failed"]
cols = ["TestCaseID", "TesterName", "Status", "DefectID"]
if "BugStatus" in failed_df.columns:
    cols.append("BugStatus")
st.dataframe(failed_df[cols], use_container_width=True)

# ----------------------------------
# 💬 JIRA ACTIVITY
# ----------------------------------
st.subheader("💬 Jira Activity")
if jira_df.empty:
    st.info("Jira not connected")
else:
    st.dataframe(jira_df.sort_values("CommentDate", ascending=False), use_container_width=True)

# ----------------------------------
# 📄 PDF REPORT
# ✅ FIX: renamed reportlab colors import to rl_colors to avoid clash
# ----------------------------------
def generate_pdf(df):
    file = "report.pdf"
    doc  = SimpleDocTemplate(file, pagesize=letter)
    data = [df.columns.tolist()] + df.astype(str).values.tolist()

    table = Table(data)
    style = TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0),  rl_colors.grey),
        ("TEXTCOLOR",     (0, 0), (-1, 0),  rl_colors.whitesmoke),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("BOTTOMPADDING", (0, 0), (-1, 0),  12),
        ("GRID",          (0, 0), (-1, -1), 0.5, rl_colors.black),
    ])
    table.setStyle(style)
    doc.build([table])
    return file

def weekly(df):
    df = df.copy()
    df["ExecutionDate"] = pd.to_datetime(df["ExecutionDate"])
    return df[df["ExecutionDate"] >= (datetime.today() - timedelta(days=7))]

if st.button("📄 Weekly Report"):
    pdf = generate_pdf(weekly(final_df))
    with open(pdf, "rb") as f:
        st.download_button("Download", f, file_name="weekly_report.pdf")

# ----------------------------------
# 📧 EMAIL REPORT
# ----------------------------------
def send_email(file):
    msg            = EmailMessage()
    msg["Subject"] = "QA Weekly Report"
    msg["From"]    = st.secrets["email_sender"]
    msg["To"]      = st.secrets["email_receiver"]
    with open(file, "rb") as f:
        msg.add_attachment(f.read(), maintype="application", subtype="pdf", filename="report.pdf")
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(st.secrets["email_sender"], st.secrets["email_password"])
            smtp.send_message(msg)
        st.success("Sent ✅")
    except Exception as e:
        st.error(f"Email failed: {e}")

if st.button("📧 Send Weekly"):
    pdf = generate_pdf(weekly(final_df))
    send_email(pdf)
