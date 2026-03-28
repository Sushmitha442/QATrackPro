import streamlit as st
import pandas as pd
import requests
from requests.auth import HTTPBasicAuth
import plotly.express as px
from streamlit_autorefresh import st_autorefresh
from datetime import datetime
import smtplib
from email.message import EmailMessage
from reportlab.platypus import SimpleDocTemplate, Table


st.set_page_config(layout="wide")

st.markdown("""
<style>
body {
    background-color: #0F1C2E;
    color: white;
}

.metric-card {
    background-color: #1F2A40;
    padding: 20px;
    border-radius: 12px;
    text-align: center;
    box-shadow: 0px 4px 10px rgba(0,0,0,0.5);
}

.section-title {
    font-size: 22px;
    font-weight: bold;
    margin-top: 20px;
}
</style>
""", unsafe_allow_html=True)
# ----------------------------------
# 🔐 LOGIN SYSTEM
# ----------------------------------
users = {
    "team1": {"password": "123", "runid": "1"},
    "team2": {"password": "123", "runid": "2"},
}

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if not st.session_state.logged_in:
    st.title("🔐 Team Login")
    user = st.text_input("Username")
    pwd = st.text_input("Password", type="password")

    if st.button("Login"):
        if user in users and users[user]["password"] == pwd:
            st.session_state.logged_in = True
            st.session_state.runid = users[user]["runid"]
            st.success("Login Successful")
        else:
            st.error("Invalid Credentials")
    st.stop()

# ----------------------------------
# 🔁 AUTO REFRESH
# ----------------------------------
st_autorefresh(interval=300000, key="refresh")

st.title("🚀 QATrackPro Dashboard")

run_id = st.session_state.runid

# ----------------------------------
# 🔹 TESTRAIL API
# ----------------------------------
def get_testrail(run_id):
    url = f"{st.secrets['testrail_url']}/index.php?/api/v2/get_tests/{run_id}"
    res = requests.get(url, auth=(st.secrets["testrail_email"], st.secrets["testrail_api"]))
    data = res.json()

    records = []
    for t in data:
        records.append({
            "TestCaseID": t["case_id"],
            "RunID": run_id,
            "TesterName": t.get("assignedto_id"),
            "Status": "Pass" if t["status_id"] == 1 else "Fail",
            "DefectID": t.get("refs", ""),
            "ExecutionDate": datetime.today()
        })

    df = pd.DataFrame(records)

    # Handle missing tester
    df["TesterName"] = df["TesterName"].fillna("Unknown").replace("", "Unknown")

    return df

# ----------------------------------
# 🔹 JIRA API
# ----------------------------------
def get_jira():
    url = f"https://{st.secrets['jira_domain']}/rest/api/3/search"

    res = requests.get(
        url,
        auth=HTTPBasicAuth(st.secrets["jira_email"], st.secrets["jira_token"]),
        params={"jql": "project=YOURPROJECT", "maxResults": 50}
    )

    data = res.json()

    issues, comments = [], []

    for issue in data["issues"]:
        bug = issue["key"]
        status = issue["fields"]["status"]["name"]

        issues.append({"BugID": bug, "BugStatus": status})

        c_url = f"https://{st.secrets['jira_domain']}/rest/api/3/issue/{bug}/comment"
        c_res = requests.get(c_url, auth=HTTPBasicAuth(st.secrets["jira_email"], st.secrets["jira_token"]))

        if c_res.json()["comments"]:
            last = c_res.json()["comments"][-1]

            comments.append({
                "BugID": bug,
                "LatestComment": last["body"]["content"][0]["content"][0]["text"],
                "Author": last["author"]["displayName"],
                "CommentDate": last["created"]
            })

    jira_df = pd.DataFrame(issues)
    comments_df = pd.DataFrame(comments)

    return pd.merge(jira_df, comments_df, on="BugID", how="left")

# ----------------------------------
# 🔹 LOAD DATA
# ----------------------------------
test_df = get_testrail(run_id)
jira_full = get_jira()

final_df = pd.merge(
    test_df,
    jira_full,
    left_on="DefectID",
    right_on="BugID",
    how="left"
)

# ----------------------------------
# 🔹 KPIs
# ----------------------------------
col1, col2, col3, col4, col5 = st.columns(5)

def card(title, value, color):
    st.markdown(f"""
    <div class="metric-card">
        <h4>{title}</h4>
        <h2 style="color:{color};">{value}</h2>
    </div>
    """, unsafe_allow_html=True)

with col1:
    card("Total Tests", total, "#3498DB")

with col2:
    card("Passed", passed, "#2ECC71")

with col3:
    card("Failed", failed, "#E74C3C")

with col4:
    card("Pass Rate", f"{pass_rate}%", "#3498DB")

with col5:
    card("Total Bugs", bugs, "#E67E22")

# ----------------------------------
# 📈 EXECUTION TREND
# ----------------------------------
st.markdown('<p class="section-title">📈 Execution Trend</p>', unsafe_allow_html=True)

fig = px.line(
    trend,
    x="ExecutionDate",
    y="Count",
    color="Status",
    markers=True,
    color_discrete_map={
        "Pass": "#2ECC71",
        "Fail": "#E74C3C"
    }
)

fig.update_layout(
    plot_bgcolor="#1F2A40",
    paper_bgcolor="#1F2A40",
    font=dict(color="white")
)

st.plotly_chart(fig, use_container_width=True)

# ----------------------------------
# 🐞 BUG STATUS
# ----------------------------------
st.markdown('<p class="section-title">🐞 Bug Status Overview</p>', unsafe_allow_html=True)

fig = px.bar(
    jira_full,
    x="BugStatus",
    color="BugStatus",
    color_discrete_sequence=px.colors.qualitative.Bold
)

fig.update_layout(
    plot_bgcolor="#1F2A40",
    paper_bgcolor="#1F2A40",
    font=dict(color="white")
)

st.plotly_chart(fig, use_container_width=True)

# ----------------------------------
# 👨‍💻 TESTER PERFORMANCE
# ----------------------------------
st.markdown('<p class="section-title">👨‍💻 Tester Performance</p>', unsafe_allow_html=True)

fig = px.bar(
    perf,
    x="TesterName",
    y="TotalTests",
    color="TesterName"
)

fig.update_layout(
    plot_bgcolor="#1F2A40",
    paper_bgcolor="#1F2A40",
    font=dict(color="white")
)

st.plotly_chart(fig, use_container_width=True)

# ----------------------------------
# 💬 JIRA COMMENTS VISUAL
# ----------------------------------
st.markdown('<p class="section-title">💬 Developer Activity</p>', unsafe_allow_html=True)

fig = px.bar(
    comment_activity,
    x="Author",
    y="Count",
    color="Author"
)

fig.update_layout(
    plot_bgcolor="#1F2A40",
    paper_bgcolor="#1F2A40",
    font=dict(color="white")
)

st.plotly_chart(fig, use_container_width=True)

# ----------------------------------
# 📅 COMMENT TIMELINE
# ----------------------------------
st.subheader("📅 Comment Timeline")

jira_full["CommentDate"] = pd.to_datetime(jira_full["CommentDate"], errors="coerce")

timeline = jira_full.groupby(jira_full["CommentDate"].dt.date).size().reset_index(name="Count")

fig = px.line(timeline, x="CommentDate", y="Count", markers=True)
st.plotly_chart(fig, use_container_width=True)

# ----------------------------------
# 🚨 FAILED TESTS
# ----------------------------------
st.markdown('<p class="section-title">🚨 Critical Failures</p>', unsafe_allow_html=True)

st.dataframe(failed_df, use_container_width=True)
# ----------------------------------
# 💬 LATEST JIRA TABLE
# ----------------------------------
st.markdown('<p class="section-title">💬 Latest Jira Updates</p>', unsafe_allow_html=True)

st.dataframe(jira_full.sort_values("CommentDate", ascending=False), use_container_width=True)

# ----------------------------------
# 📄 PDF REPORT
# ----------------------------------
def generate_pdf(df):
    file = "report.pdf"
    doc = SimpleDocTemplate(file)
    table = Table([df.columns.tolist()] + df.values.tolist())
    doc.build([table])
    return file

if st.button("📄 Download Report"):
    pdf = generate_pdf(perf)
    with open(pdf, "rb") as f:
        st.download_button("Download", f, file_name="report.pdf")

# ----------------------------------
# 📧 EMAIL REPORT
# ----------------------------------
def send_email(file):
    msg = EmailMessage()
    msg["Subject"] = "QA Report"
    msg["From"] = st.secrets["email_sender"]
    msg["To"] = st.secrets["email_receiver"]

    with open(file, "rb") as f:
        msg.add_attachment(f.read(), maintype="application", subtype="pdf", filename="report.pdf")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(st.secrets["email_sender"], st.secrets["email_password"])
        smtp.send_message(msg)

if st.button("📧 Send Report"):
    pdf = generate_pdf(perf)
    send_email(pdf)
    st.success("Email Sent!")
