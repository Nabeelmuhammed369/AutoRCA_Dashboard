import streamlit as st
import yaml
import pandas as pd
from Monitors.api_monitor import check_api_health
from Monitors.log_analyzer import analyze_logs
from Monitors.db_validator import validate_data
from Core.rca_engine import classify_issue

st.set_page_config(page_title="AutoRCA Dashboard", layout="wide")

st.title("🚀 AutoRCA: Intelligent Incident Analyzer")
st.markdown("---")

# Load Config
with open("config.yaml") as f:
    config = yaml.safe_load(f)

# Sidebar for Trigger
if st.sidebar.button("Run Full System Diagnostic"):
    # Run all monitors
    api = check_api_health(config["api"]["url"], config["api"]["timeout"])
    logs = analyze_logs(config["log"]["file"])
    db = validate_data(config["database"]["path"])
    classification = classify_issue(api, logs, db)

    # UI Layout
   # 1. TOP ROW: HIGH-LEVEL METRICS
    st.markdown("### 📊 System Health Overview")
    m_col1, m_col2, m_col3, m_col4 = st.columns(4)
    
    with m_col1:
        st.metric("API Status", f"{api.get('status_code')}", delta="Healthy" if api.get('status_code') == 200 else "Issue")
    with m_col2:
        st.metric("Latency", f"{api.get('response_time'):.3f}s")
    with m_col3:
        st.metric("Total Errors", logs.get("total_errors"))
    with m_col4:
        st.metric("DB Anomalies", db.get("null_email_count"), delta_color="inverse")

    st.markdown("---")

    # 2. MIDDLE ROW: ERROR CLASSIFICATION & DETAILS
    col_left, col_right = st.columns([1, 1])

    with col_left:
        st.subheader("⚠️ Error Type Distribution")
        # Logic to count types
        err_list = logs.get("exceptions", [])
        if err_list:
            # Simple grouping logic
            counts = {
                "DB Issues": sum(1 for e in err_list if "DB" in e or "Database" in e),
                "Null Pointers": sum(1 for e in err_list if "NullPointer" in e),
                "Connection": sum(1 for e in err_list if "timeout" in e.lower()),
                "Other": 0
            }
            counts["Other"] = len(err_list) - sum(counts.values())
            
            # Display as a clean Bar Chart
            df_err = pd.DataFrame(list(counts.items()), columns=['Error Type', 'Count'])
            st.bar_chart(df_err.set_index('Error Type'))
        else:
            st.success("No errors detected in current log cycle.")

    with col_right:
        st.subheader("🔍 Deep Dive: Log Filter")
        search = st.text_input("Search logs for specific keywords...", placeholder="e.g. timeout")
        if err_list:
            filtered = [e for e in err_list if search.lower() in e.lower()]
            st.caption(f"Showing {len(filtered)} instances")
            st.code("\n".join(filtered[-10:]), language="log") # Show last 10 for speed

    st.markdown("---")

    # 3. BOTTOM ROW: FINAL RCA (THE "BRAINS")
    st.subheader("🧠 Automated Root Cause Analysis")
    
    # Visual Alert based on classification
    if "Healthy" in classification:
        st.success(f"✅ {classification}")
    else:
        st.error(f"🚨 {classification}")
        
    with st.expander("See Recommendation"):
        if "Data" in classification:
            st.write("**Action:** Check upstream data validation in the User Module. 1 record found with missing email.")
        elif "Infrastructure" in classification:
            st.write("**Action:** Check Network Security Groups or API Gateway logs.")