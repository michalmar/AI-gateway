#!/usr/bin/env python3
"""
AI Gateway Scenario Verification Script
Queries App Insights and Log Analytics to verify the 4 key scenarios,
generates a markdown report with embedded chart images.

Scenarios:
  1. Load balancing across OpenAI models/backends
  2. Department access via product subscriptions
  3. Usage monitoring per product/department/model
  4. Cost monitoring per product/department/model

Usage:
    python3 verify-scenarios.py
    python3 verify-scenarios.py --timespan PT30M   # look back 30 min
"""

import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEPLOYMENT_NAME = "multi-model-failover"
RESOURCE_GROUP = f"lab-{DEPLOYMENT_NAME}"
INFERENCE_API_PATH = "inference"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CHARTS_DIR = os.path.join(SCRIPT_DIR, "scenario-charts")


def iso_to_kql_timespan(iso_ts):
    """Convert ISO 8601 duration (PT1H, PT30M) to KQL timespan (1h, 30m)."""
    import re
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso_ts, re.IGNORECASE)
    if not m:
        return iso_ts
    parts = []
    if m.group(1):
        parts.append(f"{m.group(1)}h")
    if m.group(2):
        parts.append(f"{m.group(2)}m")
    if m.group(3):
        parts.append(f"{m.group(3)}s")
    return "".join(parts) if parts else iso_ts
REPORT_PATH = os.path.join(SCRIPT_DIR, "scenario-report.md")

PRODUCTS = ["finance", "marketing", "hr"]
PRODUCT_COLORS = {"finance": "#2196F3", "marketing": "#FF9800", "hr": "#4CAF50",
                  "subscription-finance": "#2196F3", "subscription-marketing": "#FF9800", "subscription-hr": "#4CAF50"}
MODEL_COLORS = {"gpt-4.1-nano": "#9C27B0", "gpt-4.1": "#E91E63", "gpt-5.2": "#00BCD4"}


def get_deployment_outputs():
    """Fetch deployment outputs from Azure, or fall back to APIM REST API."""
    print("🔍 Discovering deployment outputs...")

    # Try deployment outputs first
    result = subprocess.run(
        ["az", "deployment", "group", "show",
         "--name", DEPLOYMENT_NAME, "-g", RESOURCE_GROUP, "-o", "json"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        try:
            deployment = json.loads(result.stdout)
            outputs = deployment.get("properties", {}).get("outputs", {})
            if outputs and "apimResourceGatewayURL" in outputs:
                # Get workspace name (need it for ARM query)
                ws_result = subprocess.run(
                    ["az", "resource", "list", "-g", RESOURCE_GROUP,
                     "--resource-type", "Microsoft.OperationalInsights/workspaces",
                     "--query", "[0].name", "-o", "tsv"],
                    capture_output=True, text=True
                )
                ws_name = ws_result.stdout.strip()
                if not ws_name:
                    # fallback
                    sub_id = subprocess.run(["az", "account", "show", "--query", "id", "-o", "tsv"],
                                            capture_output=True, text=True).stdout.strip()
                    ws_resp = subprocess.run(
                        ["az", "rest", "--method", "get",
                         "--url", f"https://management.azure.com/subscriptions/{sub_id}/resourceGroups/{RESOURCE_GROUP}/providers/Microsoft.OperationalInsights/workspaces?api-version=2023-09-01",
                         "--query", "value[0].name", "-o", "tsv"],
                        capture_output=True, text=True
                    )
                    ws_name = ws_resp.stdout.strip()
                return {
                    "gateway_url": outputs["apimResourceGatewayURL"]["value"],
                    "app_insights": outputs["appInsightsName"]["value"],
                    "log_analytics_workspace": ws_name,
                    "subscriptions": outputs["apimSubscriptions"]["value"],
                }
        except (json.JSONDecodeError, KeyError):
            pass

    print("  ⚠️  Deployment outputs not available, discovering via REST API...")

    # Discover APIM
    result = subprocess.run(
        ["az", "resource", "list", "-g", RESOURCE_GROUP,
         "--resource-type", "Microsoft.ApiManagement/service", "--query", "[0]", "-o", "json"],
        capture_output=True, text=True
    )
    apim = json.loads(result.stdout)
    gateway_url = f"https://{apim['name']}.azure-api.net"

    # Get subscription keys
    result = subprocess.run(
        ["az", "rest", "--method", "get",
         "--url", f"https://management.azure.com{apim['id']}/subscriptions?api-version=2024-05-01"],
        capture_output=True, text=True
    )
    subs_data = json.loads(result.stdout)
    subscriptions = []
    for sub in subs_data.get("value", []):
        name = sub["name"]
        if name == "master":
            continue
        result2 = subprocess.run(
            ["az", "rest", "--method", "post",
             "--url", f"https://management.azure.com{sub['id']}/listSecrets?api-version=2024-05-01"],
            capture_output=True, text=True
        )
        secrets = json.loads(result2.stdout)
        subscriptions.append({
            "name": name,
            "displayName": sub["properties"]["displayName"],
            "key": secrets["primaryKey"],
        })

    # Get App Insights
    result = subprocess.run(
        ["az", "resource", "list", "-g", RESOURCE_GROUP,
         "--resource-type", "Microsoft.Insights/components", "--query", "[0].name", "-o", "tsv"],
        capture_output=True, text=True
    )
    app_insights = result.stdout.strip()

    # Get Log Analytics workspace name
    sub_id_val = subprocess.run(
        ["az", "account", "show", "--query", "id", "-o", "tsv"],
        capture_output=True, text=True
    ).stdout.strip()
    result = subprocess.run(
        ["az", "rest", "--method", "get",
         "--url", f"https://management.azure.com/subscriptions/{sub_id_val}/resourceGroups/{RESOURCE_GROUP}/providers/Microsoft.OperationalInsights/workspaces?api-version=2023-09-01",
         "--query", "value[0].name", "-o", "tsv"],
        capture_output=True, text=True
    )
    ws_name = result.stdout.strip()

    return {
        "gateway_url": gateway_url,
        "app_insights": app_insights,
        "log_analytics_workspace": ws_name,
        "subscriptions": subscriptions,
    }


def run_app_insights_query(app_insights_name, query, label=""):
    """Run a KQL query against App Insights."""
    print(f"  📊 Querying: {label}...")
    result = subprocess.run(
        ["az", "monitor", "app-insights", "query",
         "--app", app_insights_name, "-g", RESOURCE_GROUP,
         "--analytics-query", query],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"  ⚠️  Query failed: {result.stderr[:200]}")
        return None
    try:
        data = json.loads(result.stdout)
        table = data["tables"][0]
        columns = [c["name"] for c in table["columns"]]
        rows = table.get("rows", [])
        df = pd.DataFrame(rows, columns=columns)
        print(f"  ✅ Got {len(df)} rows")
        return df
    except Exception as e:
        print(f"  ⚠️  Parse error: {e}")
        return None


def run_log_analytics_query(workspace_name, query, label=""):
    """Run a KQL query against Log Analytics workspace via ARM API."""
    print(f"  📊 Querying LAW: {label}...")

    if not workspace_name:
        print(f"  ⚠️  No workspace name provided, skipping query")
        return None

    sub_id = subprocess.run(
        ["az", "account", "show", "--query", "id", "-o", "tsv"],
        capture_output=True, text=True
    ).stdout.strip()

    url = (f"https://management.azure.com/subscriptions/{sub_id}/resourceGroups/{RESOURCE_GROUP}"
           f"/providers/Microsoft.OperationalInsights/workspaces/{workspace_name}"
           f"/api/query?api-version=2017-01-01-preview")

    # Flatten query to single line to avoid shell escaping issues
    flat_query = " ".join(query.strip().split())
    body = json.dumps({"query": flat_query})

    # Write body to temp file to avoid shell escaping issues with az rest --body
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write(body)
        tmp_path = f.name

    try:
        result = subprocess.run(
            ["az", "rest", "--method", "post", "--url", url, "--body", f"@{tmp_path}"],
            capture_output=True, text=True
        )
    finally:
        os.unlink(tmp_path)

    if result.returncode != 0:
        print(f"  ⚠️  Query failed: {result.stderr[:300]}")
        return None
    try:
        data = json.loads(result.stdout)
        tables = data.get("Tables") or data.get("tables", [])
        if not tables:
            print(f"  ⚠️  No tables in response")
            return None
        table = tables[0]
        if "Columns" in table:
            columns = [c["ColumnName"] for c in table["Columns"]]
            rows = table.get("Rows", [])
        else:
            columns = [c["name"] for c in table["columns"]]
            rows = table.get("rows", [])
        df = pd.DataFrame(rows, columns=columns)
        print(f"  ✅ Got {len(df)} rows")
        return df
    except Exception as e:
        print(f"  ⚠️  Parse error: {e}")
        return None


def ensure_charts_dir():
    os.makedirs(CHARTS_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Scenario 1: Load Balancing
# ---------------------------------------------------------------------------
BACKEND_COLORS = {"foundry1": "#2196F3", "foundry2": "#FF9800"}

def verify_load_balancing(config, timespan):
    print("\n📋 Scenario 1: Load Balancing Across Models/Backends")
    findings = {"pass": False, "details": "", "chart": None, "charts": []}

    ws = config["log_analytics_workspace"]

    # Backend distribution
    query_backends = f"""
ApiManagementGatewayLogs
| where TimeGenerated >= ago({timespan})
| where OperationId != ''
| where BackendId != ''
| summarize RequestCount = count() by BackendId
| order by RequestCount desc
"""
    df_backends = run_log_analytics_query(ws, query_backends, "Backend distribution")

    # Backend + response code (shows 429 retries that hit another backend)
    query_backend_status = f"""
ApiManagementGatewayLogs
| where TimeGenerated >= ago({timespan})
| where OperationId != ''
| where BackendId != ''
| summarize
    TotalRequests = count(),
    SuccessCount = countif(ResponseCode >= 200 and ResponseCode < 300),
    ThrottledCount = countif(ResponseCode == 429),
    ErrorCount = countif(ResponseCode >= 500)
    by BackendId
| order by TotalRequests desc
"""
    df_backend_status = run_log_analytics_query(ws, query_backend_status, "Backend status breakdown")

    # Backend timeline (to show failover moments)
    query_backend_time = f"""
ApiManagementGatewayLogs
| where TimeGenerated >= ago({timespan})
| where OperationId != ''
| where BackendId != ''
| summarize RequestCount = count() by BackendId, bin(TimeGenerated, 1m)
| order by TimeGenerated asc
"""
    df_backend_time = run_log_analytics_query(ws, query_backend_time, "Backend timeline")

    # Model distribution
    query_models = f"""
ApiManagementGatewayLlmLog
| where TimeGenerated >= ago({timespan})
| where DeploymentName != ''
| summarize RequestCount = count(), TotalTokens = sum(TotalTokens) by DeploymentName
| order by RequestCount desc
"""
    df_models = run_log_analytics_query(ws, query_models, "Model distribution")

    # Backend × Model cross-reference
    query_backend_model = f"""
ApiManagementGatewayLlmLog
| where TimeGenerated >= ago({timespan})
| where DeploymentName != ''
| join kind=leftouter ApiManagementGatewayLogs on CorrelationId
| where BackendId != ''
| summarize RequestCount = count(), TotalTokens = sum(TotalTokens) by BackendId, DeploymentName
| order by BackendId, DeploymentName
"""
    df_backend_model = run_log_analytics_query(ws, query_backend_model, "Backend × Model")

    details_parts = []
    charts = []

    # --- Backend distribution ---
    if df_backend_status is not None and len(df_backend_status) > 0:
        for col in ["TotalRequests", "SuccessCount", "ThrottledCount", "ErrorCount"]:
            df_backend_status[col] = pd.to_numeric(df_backend_status[col], errors="coerce").fillna(0).astype(int)
        total_req = df_backend_status["TotalRequests"].sum()
        multi_backend = len(df_backend_status) > 1
        findings["pass"] = True

        details_parts.append("**Backend Distribution (Load Balancing Evidence):**\n")
        if multi_backend:
            details_parts.append("> ✅ **Failover confirmed!** Requests were distributed across multiple backends.\n")
        else:
            details_parts.append("> ℹ️ All requests went to a single backend (no failover triggered).\n")

        details_parts.append("| Backend | Requests | % | Success | Throttled (429) | Errors |")
        details_parts.append("|---------|----------|---|---------|----------------|--------|")
        for _, row in df_backend_status.iterrows():
            pct = row["TotalRequests"] / total_req * 100 if total_req > 0 else 0
            details_parts.append(
                f"| {row['BackendId']} | {row['TotalRequests']} | {pct:.1f}% "
                f"| {row['SuccessCount']} | {row['ThrottledCount']} | {row['ErrorCount']} |"
            )

        # Backend bar chart
        fig, ax = plt.subplots(figsize=(8, 4))
        colors = [BACKEND_COLORS.get(b, "#999") for b in df_backend_status["BackendId"]]
        x_pos = range(len(df_backend_status))
        width = 0.3
        ax.bar([p - width/2 for p in x_pos], df_backend_status["SuccessCount"], width,
               label="Success (2xx)", color=colors, alpha=0.9)
        ax.bar([p + width/2 for p in x_pos], df_backend_status["ThrottledCount"], width,
               label="Throttled (429)", color="#FFC107", alpha=0.9)
        ax.set_xticks(x_pos)
        ax.set_xticklabels(df_backend_status["BackendId"])
        ax.set_title("Backend Load Distribution (Failover Evidence)", fontweight="bold")
        ax.set_ylabel("Request Count")
        ax.legend()
        for i, (s, t) in enumerate(zip(df_backend_status["SuccessCount"], df_backend_status["ThrottledCount"])):
            ax.text(i - width/2, s + 0.5, str(s), ha="center", fontsize=9, fontweight="bold")
            if t > 0:
                ax.text(i + width/2, t + 0.5, str(t), ha="center", fontsize=9, fontweight="bold")
        plt.tight_layout()
        cp = os.path.join(CHARTS_DIR, "scenario1_backend_distribution.png")
        fig.savefig(cp, dpi=150); plt.close(fig)
        charts.append(cp)

    # --- Backend timeline ---
    if df_backend_time is not None and len(df_backend_time) > 0:
        df_backend_time["RequestCount"] = pd.to_numeric(df_backend_time["RequestCount"], errors="coerce").fillna(0)
        df_backend_time["TimeGenerated"] = pd.to_datetime(df_backend_time["TimeGenerated"])

        fig, ax = plt.subplots(figsize=(10, 4))
        for backend in df_backend_time["BackendId"].unique():
            sub_df = df_backend_time[df_backend_time["BackendId"] == backend].sort_values("TimeGenerated")
            color = BACKEND_COLORS.get(backend, "#999")
            ax.plot(sub_df["TimeGenerated"], sub_df["RequestCount"],
                    marker="o", label=backend, color=color, markersize=4, linewidth=2)
        ax.set_title("Backend Usage Over Time (Failover Timeline)", fontweight="bold")
        ax.set_ylabel("Requests per minute")
        ax.set_xlabel("Time")
        ax.legend()
        fig.autofmt_xdate()
        plt.tight_layout()
        cp = os.path.join(CHARTS_DIR, "scenario1_backend_timeline.png")
        fig.savefig(cp, dpi=150); plt.close(fig)
        charts.append(cp)

    # --- Model distribution ---
    if df_models is not None and len(df_models) > 0:
        df_models["RequestCount"] = pd.to_numeric(df_models["RequestCount"], errors="coerce").fillna(0).astype(int)
        df_models["TotalTokens"] = pd.to_numeric(df_models["TotalTokens"], errors="coerce").fillna(0).astype(int)
        total_req = df_models["RequestCount"].sum()

        details_parts.append("\n**Model Distribution:**\n")
        details_parts.append("| Model | Requests | % of Total | Total Tokens |")
        details_parts.append("|-------|----------|-----------|--------------|")
        for _, row in df_models.iterrows():
            pct = row["RequestCount"] / total_req * 100 if total_req > 0 else 0
            details_parts.append(f"| {row['DeploymentName']} | {row['RequestCount']} | {pct:.1f}% | {row['TotalTokens']:,} |")

        fig, ax = plt.subplots(figsize=(8, 4))
        colors = [MODEL_COLORS.get(m, "#999") for m in df_models["DeploymentName"]]
        ax.bar(df_models["DeploymentName"], df_models["RequestCount"], color=colors)
        ax.set_title("Request Distribution Across Models", fontweight="bold")
        ax.set_ylabel("Request Count")
        for i, v in enumerate(df_models["RequestCount"]):
            ax.text(i, v + 0.5, str(v), ha="center", fontweight="bold")
        plt.tight_layout()
        cp = os.path.join(CHARTS_DIR, "scenario1_model_distribution.png")
        fig.savefig(cp, dpi=150); plt.close(fig)
        charts.append(cp)

    # --- Backend × Model ---
    if df_backend_model is not None and len(df_backend_model) > 0:
        for col in ["RequestCount", "TotalTokens"]:
            df_backend_model[col] = pd.to_numeric(df_backend_model[col], errors="coerce").fillna(0).astype(int)
        details_parts.append("\n**Backend × Model (Failover Detail):**\n")
        details_parts.append("| Backend | Model | Requests | Total Tokens |")
        details_parts.append("|---------|-------|----------|-------------|")
        for _, row in df_backend_model.iterrows():
            details_parts.append(f"| {row['BackendId']} | {row['DeploymentName']} | {row['RequestCount']} | {row['TotalTokens']:,} |")

    if not details_parts:
        details_parts.append("⚠️ No data found. Ensure load test was run and metrics have propagated (2-5 min delay).")

    findings["details"] = "\n".join(details_parts)
    findings["charts"] = charts
    return findings


# ---------------------------------------------------------------------------
# Scenario 2: Department Access (Product/Subscription)
# ---------------------------------------------------------------------------
def verify_department_access(config, timespan):
    print("\n📋 Scenario 2: Department Access via Product Subscriptions")
    findings = {"pass": False, "details": "", "chart": None}

    query = f"""
ApiManagementGatewayLogs
| where TimeGenerated >= ago({timespan})
| where OperationId != ''
| summarize
    TotalRequests = count(),
    SuccessCount = countif(ResponseCode >= 200 and ResponseCode < 300),
    RateLimitedCount = countif(ResponseCode == 429),
    ErrorCount = countif(ResponseCode >= 500)
    by ApimSubscriptionId
| order by TotalRequests desc
"""
    df = run_log_analytics_query(config["log_analytics_workspace"], query, "Product subscription access")

    if df is not None and len(df) > 0:
        for col in ["TotalRequests", "SuccessCount", "RateLimitedCount", "ErrorCount"]:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

        findings["pass"] = len(df) >= 2
        parts = ["**Per-Subscription Access Summary:**\n"]
        parts.append("| Subscription | Total | Success | Rate Limited (429) | Errors (5xx) |")
        parts.append("|-------------|-------|---------|-------------------|-------------|")
        for _, row in df.iterrows():
            parts.append(f"| {row['ApimSubscriptionId']} | {row['TotalRequests']} | {row['SuccessCount']} | {row['RateLimitedCount']} | {row['ErrorCount']} |")

        # Chart
        fig, ax = plt.subplots(figsize=(9, 5))
        x_labels = df["ApimSubscriptionId"].tolist()
        x_pos = range(len(x_labels))
        width = 0.25
        colors_s = [PRODUCT_COLORS.get(s, "#999") for s in x_labels]
        bars1 = ax.bar([p - width for p in x_pos], df["SuccessCount"], width, label="Success", color=colors_s, alpha=0.9)
        bars2 = ax.bar(x_pos, df["RateLimitedCount"], width, label="Rate Limited (429)", color="#FFC107", alpha=0.9)
        bars3 = ax.bar([p + width for p in x_pos], df["ErrorCount"], width, label="Errors (5xx)", color="#F44336", alpha=0.9)
        ax.set_title("Department Access: Requests by Subscription", fontweight="bold")
        ax.set_ylabel("Request Count")
        ax.set_xticks(x_pos)
        ax.set_xticklabels(x_labels, rotation=15, ha="right")
        ax.legend()
        plt.tight_layout()
        chart_path = os.path.join(CHARTS_DIR, "scenario2_department_access.png")
        fig.savefig(chart_path, dpi=150)
        plt.close(fig)
        findings["chart"] = chart_path
        findings["details"] = "\n".join(parts)
    else:
        findings["details"] = "⚠️ No subscription access data found."

    return findings


# ---------------------------------------------------------------------------
# Scenario 3: Usage Monitoring per Product/Department/Model
# ---------------------------------------------------------------------------
def verify_usage_monitoring(config, timespan):
    print("\n📋 Scenario 3: Usage Monitoring per Product/Department/Model")
    findings = {"pass": False, "details": "", "charts": []}

    # Token usage by subscription
    query_by_sub = f"""
ApiManagementGatewayLlmLog
| where TimeGenerated >= ago({timespan})
| where DeploymentName != ''
| join kind=leftouter ApiManagementGatewayLogs on CorrelationId
| summarize
    PromptTokens = sum(PromptTokens),
    CompletionTokens = sum(CompletionTokens),
    TotalTokens = sum(TotalTokens),
    RequestCount = count()
    by ApimSubscriptionId
| order by TotalTokens desc
"""
    df_sub = run_log_analytics_query(config["log_analytics_workspace"], query_by_sub, "Token usage by subscription")

    # Token usage by model
    query_by_model = f"""
ApiManagementGatewayLlmLog
| where TimeGenerated >= ago({timespan})
| where DeploymentName != ''
| summarize
    PromptTokens = sum(PromptTokens),
    CompletionTokens = sum(CompletionTokens),
    TotalTokens = sum(TotalTokens),
    RequestCount = count()
    by DeploymentName
| order by TotalTokens desc
"""
    df_model = run_log_analytics_query(config["log_analytics_workspace"], query_by_model, "Token usage by model")

    # Token usage by subscription × model
    query_cross = f"""
ApiManagementGatewayLlmLog
| where TimeGenerated >= ago({timespan})
| where DeploymentName != ''
| join kind=leftouter ApiManagementGatewayLogs on CorrelationId
| summarize TotalTokens = sum(TotalTokens), RequestCount = count() by ApimSubscriptionId, DeploymentName
| order by ApimSubscriptionId, DeploymentName
"""
    df_cross = run_log_analytics_query(config["log_analytics_workspace"], query_cross, "Token usage by subscription × model")

    # Usage over time
    query_time = f"""
ApiManagementGatewayLlmLog
| where TimeGenerated >= ago({timespan})
| where DeploymentName != ''
| join kind=leftouter ApiManagementGatewayLogs on CorrelationId
| summarize TotalTokens = sum(TotalTokens) by ApimSubscriptionId, bin(TimeGenerated, 1m)
| order by TimeGenerated asc
"""
    df_time = run_log_analytics_query(config["log_analytics_workspace"], query_time, "Token usage over time")

    parts = []
    charts = []

    # --- By Subscription ---
    if df_sub is not None and len(df_sub) > 0:
        for col in ["PromptTokens", "CompletionTokens", "TotalTokens", "RequestCount"]:
            df_sub[col] = pd.to_numeric(df_sub[col], errors="coerce").fillna(0).astype(int)
        findings["pass"] = True
        parts.append("**Token Usage by Subscription (Department):**\n")
        parts.append("| Subscription | Requests | Prompt Tokens | Completion Tokens | Total Tokens |")
        parts.append("|-------------|----------|--------------|------------------|-------------|")
        for _, row in df_sub.iterrows():
            parts.append(f"| {row['ApimSubscriptionId']} | {row['RequestCount']} | {row['PromptTokens']:,} | {row['CompletionTokens']:,} | {row['TotalTokens']:,} |")

        fig, ax = plt.subplots(figsize=(8, 4))
        colors = [PRODUCT_COLORS.get(s, "#999") for s in df_sub["ApimSubscriptionId"]]
        bars = ax.bar(df_sub["ApimSubscriptionId"], df_sub["TotalTokens"], color=colors)
        ax.set_title("Total Token Usage by Department", fontweight="bold")
        ax.set_ylabel("Total Tokens")
        for bar, val in zip(bars, df_sub["TotalTokens"]):
            ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 10,
                    f"{val:,}", ha="center", fontsize=9, fontweight="bold")
        plt.tight_layout()
        cp = os.path.join(CHARTS_DIR, "scenario3_usage_by_subscription.png")
        fig.savefig(cp, dpi=150); plt.close(fig)
        charts.append(cp)

    # --- By Model ---
    if df_model is not None and len(df_model) > 0:
        for col in ["PromptTokens", "CompletionTokens", "TotalTokens", "RequestCount"]:
            df_model[col] = pd.to_numeric(df_model[col], errors="coerce").fillna(0).astype(int)
        findings["pass"] = True
        parts.append("\n**Token Usage by Model:**\n")
        parts.append("| Model | Requests | Prompt Tokens | Completion Tokens | Total Tokens |")
        parts.append("|-------|----------|--------------|------------------|-------------|")
        for _, row in df_model.iterrows():
            parts.append(f"| {row['DeploymentName']} | {row['RequestCount']} | {row['PromptTokens']:,} | {row['CompletionTokens']:,} | {row['TotalTokens']:,} |")

        fig, ax = plt.subplots(figsize=(8, 4))
        colors = [MODEL_COLORS.get(m, "#999") for m in df_model["DeploymentName"]]
        x_pos = range(len(df_model))
        width = 0.35
        ax.bar([p - width/2 for p in x_pos], df_model["PromptTokens"], width, label="Prompt", color=colors, alpha=0.7)
        ax.bar([p + width/2 for p in x_pos], df_model["CompletionTokens"], width, label="Completion", color=colors, alpha=1.0)
        ax.set_xticks(x_pos)
        ax.set_xticklabels(df_model["DeploymentName"])
        ax.set_title("Token Usage by Model (Prompt vs Completion)", fontweight="bold")
        ax.set_ylabel("Tokens")
        ax.legend()
        plt.tight_layout()
        cp = os.path.join(CHARTS_DIR, "scenario3_usage_by_model.png")
        fig.savefig(cp, dpi=150); plt.close(fig)
        charts.append(cp)

    # --- Cross: Subscription × Model ---
    if df_cross is not None and len(df_cross) > 0:
        for col in ["TotalTokens", "RequestCount"]:
            df_cross[col] = pd.to_numeric(df_cross[col], errors="coerce").fillna(0).astype(int)
        parts.append("\n**Token Usage by Subscription × Model:**\n")
        parts.append("| Subscription | Model | Requests | Total Tokens |")
        parts.append("|-------------|-------|----------|-------------|")
        for _, row in df_cross.iterrows():
            parts.append(f"| {row['ApimSubscriptionId']} | {row['DeploymentName']} | {row['RequestCount']} | {row['TotalTokens']:,} |")

        # Stacked bar chart
        pivot = df_cross.pivot_table(index="ApimSubscriptionId", columns="DeploymentName",
                                      values="TotalTokens", aggfunc="sum", fill_value=0)
        fig, ax = plt.subplots(figsize=(9, 5))
        pivot.plot(kind="bar", stacked=True, ax=ax,
                   color=[MODEL_COLORS.get(c, "#999") for c in pivot.columns])
        ax.set_title("Token Usage: Department × Model (Stacked)", fontweight="bold")
        ax.set_ylabel("Total Tokens")
        ax.set_xlabel("")
        ax.legend(title="Model")
        plt.xticks(rotation=15, ha="right")
        plt.tight_layout()
        cp = os.path.join(CHARTS_DIR, "scenario3_usage_cross.png")
        fig.savefig(cp, dpi=150); plt.close(fig)
        charts.append(cp)

    # --- Time series ---
    if df_time is not None and len(df_time) > 0:
        df_time["TotalTokens"] = pd.to_numeric(df_time["TotalTokens"], errors="coerce").fillna(0)
        df_time["TimeGenerated"] = pd.to_datetime(df_time["TimeGenerated"])
        fig, ax = plt.subplots(figsize=(10, 4))
        for sub in df_time["ApimSubscriptionId"].unique():
            sub_df = df_time[df_time["ApimSubscriptionId"] == sub].sort_values("TimeGenerated")
            color = PRODUCT_COLORS.get(sub, "#999")
            ax.plot(sub_df["TimeGenerated"], sub_df["TotalTokens"], marker="o", label=sub, color=color, markersize=4)
        ax.set_title("Token Usage Over Time by Department", fontweight="bold")
        ax.set_ylabel("Tokens per minute")
        ax.set_xlabel("Time")
        ax.legend()
        fig.autofmt_xdate()
        plt.tight_layout()
        cp = os.path.join(CHARTS_DIR, "scenario3_usage_timeline.png")
        fig.savefig(cp, dpi=150); plt.close(fig)
        charts.append(cp)

    if not parts:
        parts.append("⚠️ No usage data found. Ensure load test was run and metrics have propagated.")

    findings["details"] = "\n".join(parts)
    findings["charts"] = charts
    return findings


# ---------------------------------------------------------------------------
# Scenario 4: Cost Monitoring per Product/Department/Model
# ---------------------------------------------------------------------------
def verify_cost_monitoring(config, timespan):
    print("\n📋 Scenario 4: Cost Monitoring per Product/Department/Model")
    findings = {"pass": False, "details": "", "charts": []}

    # Cost by subscription (uses the same KQL as the workbook)
    query_cost = f"""
let llmHeaderLogs = ApiManagementGatewayLlmLog
| where TimeGenerated >= ago({timespan})
| where DeploymentName != '';
let llmLogsWithSubscriptionId = llmHeaderLogs
| join kind=leftouter ApiManagementGatewayLogs on CorrelationId
| project SubscriptionName = ApimSubscriptionId, DeploymentName, PromptTokens, CompletionTokens, TotalTokens;
llmLogsWithSubscriptionId
| join kind=inner (
    PRICING_CL
    | summarize arg_max(TimeGenerated, *) by Model
    | project Model, InputTokensPrice, OutputTokensPrice
) on $left.DeploymentName == $right.Model
| extend InputCost = PromptTokens * InputTokensPrice
| extend OutputCost = CompletionTokens * OutputTokensPrice
| summarize InputCost = sum(InputCost), OutputCost = sum(OutputCost) by SubscriptionName
| extend TotalCost = (InputCost + OutputCost) / 1000
| project SubscriptionName, InputCost = InputCost / 1000, OutputCost = OutputCost / 1000, TotalCost
"""
    df_cost_sub = run_log_analytics_query(config["log_analytics_workspace"], query_cost, "Cost by subscription")

    # Cost by model
    query_cost_model = f"""
let llmHeaderLogs = ApiManagementGatewayLlmLog
| where TimeGenerated >= ago({timespan})
| where DeploymentName != '';
llmHeaderLogs
| join kind=inner (
    PRICING_CL
    | summarize arg_max(TimeGenerated, *) by Model
    | project Model, InputTokensPrice, OutputTokensPrice
) on $left.DeploymentName == $right.Model
| extend InputCost = PromptTokens * InputTokensPrice / 1000
| extend OutputCost = CompletionTokens * OutputTokensPrice / 1000
| summarize InputCost = sum(InputCost), OutputCost = sum(OutputCost), TotalTokens = sum(TotalTokens) by DeploymentName
| extend TotalCost = InputCost + OutputCost
| order by TotalCost desc
"""
    df_cost_model = run_log_analytics_query(config["log_analytics_workspace"], query_cost_model, "Cost by model")

    # Cost by subscription × model
    query_cost_cross = f"""
let llmHeaderLogs = ApiManagementGatewayLlmLog
| where TimeGenerated >= ago({timespan})
| where DeploymentName != '';
let llmLogsWithSubscriptionId = llmHeaderLogs
| join kind=leftouter ApiManagementGatewayLogs on CorrelationId
| project SubscriptionName = ApimSubscriptionId, DeploymentName, PromptTokens, CompletionTokens;
llmLogsWithSubscriptionId
| join kind=inner (
    PRICING_CL
    | summarize arg_max(TimeGenerated, *) by Model
    | project Model, InputTokensPrice, OutputTokensPrice
) on $left.DeploymentName == $right.Model
| extend TotalCost = (PromptTokens * InputTokensPrice + CompletionTokens * OutputTokensPrice) / 1000
| summarize TotalCost = sum(TotalCost) by SubscriptionName, DeploymentName
| order by SubscriptionName, DeploymentName
"""
    df_cost_cross = run_log_analytics_query(config["log_analytics_workspace"], query_cost_cross, "Cost by subscription × model")

    # Quota comparison
    query_quota = f"""
let llmHeaderLogs = ApiManagementGatewayLlmLog
| where TimeGenerated >= ago({timespan})
| where DeploymentName != '';
let llmLogsWithSubscriptionId = llmHeaderLogs
| join kind=leftouter ApiManagementGatewayLogs on CorrelationId
| project SubscriptionName = ApimSubscriptionId, DeploymentName, PromptTokens, CompletionTokens;
llmLogsWithSubscriptionId
| join kind=inner (
    PRICING_CL
    | summarize arg_max(TimeGenerated, *) by Model
    | project Model, InputTokensPrice, OutputTokensPrice
) on $left.DeploymentName == $right.Model
| extend TotalCost = (PromptTokens * InputTokensPrice + CompletionTokens * OutputTokensPrice) / 1000
| summarize TotalCost = sum(TotalCost) by SubscriptionName
| join kind=inner (
    SUBSCRIPTION_QUOTA_CL
    | summarize arg_max(TimeGenerated, *) by Subscription
    | project Subscription, CostQuota
) on $left.SubscriptionName == $right.Subscription
| project SubscriptionName, TotalCost, CostQuota, UsagePct = TotalCost / CostQuota * 100
"""
    df_quota = run_log_analytics_query(config["log_analytics_workspace"], query_quota, "Cost vs quota")

    parts = []
    charts = []

    # --- Cost by Subscription ---
    if df_cost_sub is not None and len(df_cost_sub) > 0:
        for col in ["InputCost", "OutputCost", "TotalCost"]:
            df_cost_sub[col] = pd.to_numeric(df_cost_sub[col], errors="coerce").fillna(0)
        findings["pass"] = True
        parts.append("**Cost by Subscription (Department):**\n")
        parts.append("| Subscription | Input Cost ($) | Output Cost ($) | Total Cost ($) |")
        parts.append("|-------------|---------------|----------------|---------------|")
        for _, row in df_cost_sub.iterrows():
            parts.append(f"| {row['SubscriptionName']} | ${row['InputCost']:.6f} | ${row['OutputCost']:.6f} | ${row['TotalCost']:.6f} |")

        fig, ax = plt.subplots(figsize=(8, 4))
        colors = [PRODUCT_COLORS.get(s, "#999") for s in df_cost_sub["SubscriptionName"]]
        x_pos = range(len(df_cost_sub))
        width = 0.35
        ax.bar([p - width/2 for p in x_pos], df_cost_sub["InputCost"], width, label="Input Cost", color=colors, alpha=0.7)
        ax.bar([p + width/2 for p in x_pos], df_cost_sub["OutputCost"], width, label="Output Cost", color=colors, alpha=1.0)
        ax.set_xticks(x_pos)
        ax.set_xticklabels(df_cost_sub["SubscriptionName"], rotation=15, ha="right")
        ax.set_title("Cost by Department (Input vs Output)", fontweight="bold")
        ax.set_ylabel("Cost ($)")
        ax.legend()
        plt.tight_layout()
        cp = os.path.join(CHARTS_DIR, "scenario4_cost_by_subscription.png")
        fig.savefig(cp, dpi=150); plt.close(fig)
        charts.append(cp)

    # --- Cost by Model ---
    if df_cost_model is not None and len(df_cost_model) > 0:
        for col in ["InputCost", "OutputCost", "TotalCost"]:
            df_cost_model[col] = pd.to_numeric(df_cost_model[col], errors="coerce").fillna(0)
        findings["pass"] = True
        parts.append("\n**Cost by Model:**\n")
        parts.append("| Model | Input Cost ($) | Output Cost ($) | Total Cost ($) |")
        parts.append("|-------|---------------|----------------|---------------|")
        for _, row in df_cost_model.iterrows():
            parts.append(f"| {row['DeploymentName']} | ${row['InputCost']:.6f} | ${row['OutputCost']:.6f} | ${row['TotalCost']:.6f} |")

        fig, ax = plt.subplots(figsize=(8, 4))
        colors = [MODEL_COLORS.get(m, "#999") for m in df_cost_model["DeploymentName"]]
        bars = ax.bar(df_cost_model["DeploymentName"], df_cost_model["TotalCost"], color=colors)
        ax.set_title("Total Cost by Model", fontweight="bold")
        ax.set_ylabel("Cost ($)")
        for bar, val in zip(bars, df_cost_model["TotalCost"]):
            ax.text(bar.get_x() + bar.get_width()/2, bar.get_height(),
                    f"${val:.6f}", ha="center", fontsize=9, fontweight="bold", va="bottom")
        plt.tight_layout()
        cp = os.path.join(CHARTS_DIR, "scenario4_cost_by_model.png")
        fig.savefig(cp, dpi=150); plt.close(fig)
        charts.append(cp)

    # --- Cost: Subscription × Model ---
    if df_cost_cross is not None and len(df_cost_cross) > 0:
        df_cost_cross["TotalCost"] = pd.to_numeric(df_cost_cross["TotalCost"], errors="coerce").fillna(0)
        parts.append("\n**Cost by Subscription × Model:**\n")
        parts.append("| Subscription | Model | Total Cost ($) |")
        parts.append("|-------------|-------|---------------|")
        for _, row in df_cost_cross.iterrows():
            parts.append(f"| {row['SubscriptionName']} | {row['DeploymentName']} | ${row['TotalCost']:.6f} |")

        pivot = df_cost_cross.pivot_table(index="SubscriptionName", columns="DeploymentName",
                                           values="TotalCost", aggfunc="sum", fill_value=0)
        fig, ax = plt.subplots(figsize=(9, 5))
        pivot.plot(kind="bar", stacked=True, ax=ax,
                   color=[MODEL_COLORS.get(c, "#999") for c in pivot.columns])
        ax.set_title("Cost: Department × Model (Stacked)", fontweight="bold")
        ax.set_ylabel("Cost ($)")
        ax.set_xlabel("")
        ax.legend(title="Model")
        plt.xticks(rotation=15, ha="right")
        plt.tight_layout()
        cp = os.path.join(CHARTS_DIR, "scenario4_cost_cross.png")
        fig.savefig(cp, dpi=150); plt.close(fig)
        charts.append(cp)

    # --- Quota comparison ---
    if df_quota is not None and len(df_quota) > 0:
        for col in ["TotalCost", "CostQuota", "UsagePct"]:
            df_quota[col] = pd.to_numeric(df_quota[col], errors="coerce").fillna(0)
        parts.append("\n**Cost vs Quota:**\n")
        parts.append("| Subscription | Cost ($) | Quota ($) | Usage % |")
        parts.append("|-------------|---------|----------|---------|")
        for _, row in df_quota.iterrows():
            emoji = "🟢" if row["UsagePct"] < 80 else ("🟡" if row["UsagePct"] < 100 else "🔴")
            parts.append(f"| {row['SubscriptionName']} | ${row['TotalCost']:.6f} | ${row['CostQuota']:.2f} | {emoji} {row['UsagePct']:.4f}% |")

        fig, ax = plt.subplots(figsize=(8, 4))
        x_pos = range(len(df_quota))
        colors = [PRODUCT_COLORS.get(s, "#999") for s in df_quota["SubscriptionName"]]
        ax.bar(x_pos, df_quota["CostQuota"], color=colors, alpha=0.3, label="Quota")
        ax.bar(x_pos, df_quota["TotalCost"], color=colors, alpha=0.9, label="Actual Cost")
        ax.set_xticks(x_pos)
        ax.set_xticklabels(df_quota["SubscriptionName"], rotation=15, ha="right")
        ax.set_title("Cost vs Quota by Department", fontweight="bold")
        ax.set_ylabel("Cost ($)")
        ax.legend()
        plt.tight_layout()
        cp = os.path.join(CHARTS_DIR, "scenario4_cost_vs_quota.png")
        fig.savefig(cp, dpi=150); plt.close(fig)
        charts.append(cp)

    if not parts:
        parts.append("⚠️ No cost data found. Ensure PRICING_CL table has been populated and load test was run.")

    findings["details"] = "\n".join(parts)
    findings["charts"] = charts
    return findings


# ---------------------------------------------------------------------------
# Scenario 5: Product TPM Quota Enforcement
# ---------------------------------------------------------------------------
PRODUCT_TPM_LIMITS = {
    "hr": 500,
    "marketing": 1000,
    "finance": 2000,
}


def verify_quota_enforcement(config, timespan, load_test_results):
    """Verify per-product TPM quota enforcement using load-test-results.json data
    and corroborate with gateway logs showing 429 responses."""
    print(f"\n📋 Scenario 5: Product TPM Quota Enforcement")

    findings = {"pass": False, "details": "", "charts": []}
    parts = []
    charts = []

    # ── 1. Analyse client-side results from the quota test ──────────────
    quota_results = None
    product_summaries = {}
    if load_test_results and load_test_results.get("test_mode") == "quota":
        quota_results = load_test_results["results"]
        product_summaries = load_test_results.get("product_summaries", {})

    if not quota_results:
        parts.append(
            "⚠️ No quota-mode results found. Run `python3 load-test.py --mode quota` first."
        )
        findings["details"] = "\n".join(parts)
        return findings

    # Overall quota test summary table
    parts.append("**Quota Test Results (client-side):**\n")
    parts.append("| Product | TPM Limit | Requests | ✅ Success | ⚡ 429 | Tokens Used | First 429 After |")
    parts.append("|---------|-----------|----------|-----------|-------|-------------|-----------------|")

    products_hit = 0
    for product in sorted(PRODUCT_TPM_LIMITS, key=lambda p: PRODUCT_TPM_LIMITS[p]):
        s = product_summaries.get(product, {})
        if not s:
            continue
        tpm = s.get("tpm_limit", PRODUCT_TPM_LIMITS[product])
        total = s.get("total_requests", 0)
        success = s.get("success", 0)
        rl = s.get("rate_limited", 0)
        tokens = s.get("total_tokens", 0)
        first_429 = s.get("first_429_at_request")
        tokens_before = s.get("tokens_before_first_429", "-")
        first_429_str = f"Request #{first_429} ({tokens_before} tokens)" if first_429 else "N/A"
        parts.append(
            f"| {product} | {tpm:,} | {total} | {success} | {rl} | {tokens:,} | {first_429_str} |"
        )
        if rl > 0:
            products_hit += 1

    parts.append("")
    if products_hit == len(PRODUCT_TPM_LIMITS):
        parts.append("> ✅ **All product TPM limits were enforced!** Every department hit its 429 ceiling.")
        findings["pass"] = True
    elif products_hit > 0:
        parts.append(f"> ⚠️ {products_hit}/{len(PRODUCT_TPM_LIMITS)} products hit their TPM limit.")
        findings["pass"] = True
    else:
        parts.append("> ❌ No product hit its TPM limit. Consider increasing traffic volume.")

    # ── 2. Per-product detail: token accumulation vs limit ──────────────
    parts.append("\n**Per-Product Token Accumulation:**\n")
    for product in sorted(PRODUCT_TPM_LIMITS, key=lambda p: PRODUCT_TPM_LIMITS[p]):
        tpm = PRODUCT_TPM_LIMITS[product]
        prod_results = [r for r in quota_results if r.get("test_phase") == product]
        if not prod_results:
            continue

        # Sort by run_id for temporal order
        prod_results.sort(key=lambda r: r["run_id"])

        success_results = [r for r in prod_results if r["status"] == "success"]
        rl_results = [r for r in prod_results if r["status"] == "rate_limited"]
        cumul_tokens = 0
        token_curve = []
        for r in prod_results:
            if r["status"] == "success":
                cumul_tokens += r["total_tokens"]
            token_curve.append({
                "req_idx": len(token_curve) + 1,
                "cumul_tokens": cumul_tokens,
                "status": r["status"],
            })

        parts.append(f"**{product.upper()}** (TPM limit: {tpm:,})")
        parts.append(f"- Requests sent: {len(prod_results)}")
        parts.append(f"- Successful: {len(success_results)} (consumed {cumul_tokens:,} tokens)")
        parts.append(f"- Rate-limited (429): {len(rl_results)}")
        if rl_results:
            # Find first 429
            for i, tc in enumerate(token_curve):
                if tc["status"] == "rate_limited":
                    parts.append(f"- First 429 at request #{tc['req_idx']} after {tc['cumul_tokens']:,} tokens")
                    ratio = tc['cumul_tokens'] / tpm * 100 if tpm > 0 else 0
                    parts.append(f"- Token usage at first 429: **{ratio:.0f}%** of TPM limit")
                    break
        parts.append("")

        # ── Chart: token accumulation curve ──
        fig, ax = plt.subplots(figsize=(10, 4))
        req_indices = [tc["req_idx"] for tc in token_curve]
        cumul_values = [tc["cumul_tokens"] for tc in token_curve]
        statuses = [tc["status"] for tc in token_curve]

        # Color the line segments
        colors = {"success": "#2ecc71", "rate_limited": "#e74c3c", "error": "#f39c12"}
        for i in range(len(req_indices)):
            ax.scatter(req_indices[i], cumul_values[i],
                       color=colors.get(statuses[i], "#999"),
                       s=20, zorder=3)
        ax.plot(req_indices, cumul_values, color="#3498db", alpha=0.5, linewidth=1, zorder=2)

        # TPM limit line
        ax.axhline(y=tpm, color="#e74c3c", linestyle="--", linewidth=2, label=f"TPM Limit ({tpm:,})")

        ax.set_xlabel("Request #")
        ax.set_ylabel("Cumulative Tokens")
        ax.set_title(f"{product.upper()} — Token Accumulation vs TPM Limit ({tpm:,})")
        ax.legend()
        ax.grid(True, alpha=0.3)

        # Add legend for dot colors
        from matplotlib.lines import Line2D
        legend_items = [
            Line2D([0], [0], marker='o', color='w', markerfacecolor='#2ecc71', markersize=8, label='Success'),
            Line2D([0], [0], marker='o', color='w', markerfacecolor='#e74c3c', markersize=8, label='429 Rate Limited'),
            Line2D([0], [0], color='#e74c3c', linestyle='--', linewidth=2, label=f'TPM Limit ({tpm:,})'),
        ]
        ax.legend(handles=legend_items, loc='upper left')

        fig.tight_layout()
        cp = os.path.join(CHARTS_DIR, f"scenario5_quota_{product}.png")
        fig.savefig(cp, dpi=150)
        plt.close(fig)
        charts.append(cp)

    # ── 3. Combined comparison chart ────────────────────────────────────
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    for idx, product in enumerate(sorted(PRODUCT_TPM_LIMITS, key=lambda p: PRODUCT_TPM_LIMITS[p])):
        ax = axes[idx]
        tpm = PRODUCT_TPM_LIMITS[product]
        s = product_summaries.get(product, {})
        success = s.get("success", 0)
        rl = s.get("rate_limited", 0)
        tokens = s.get("total_tokens", 0)

        # Bar chart: success vs 429
        bars = ax.bar(["Success", "429"], [success, rl],
                      color=["#2ecc71", "#e74c3c"], edgecolor="white", linewidth=0.5)
        ax.set_title(f"{product.upper()}\nTPM Limit: {tpm:,}", fontsize=11, fontweight='bold')
        ax.set_ylabel("Requests")
        for bar in bars:
            height = bar.get_height()
            if height > 0:
                ax.text(bar.get_x() + bar.get_width()/2., height + 0.5,
                        f'{int(height)}', ha='center', va='bottom', fontsize=10)

        # Add token info as text
        ax.text(0.5, 0.95, f"Tokens: {tokens:,}",
                transform=ax.transAxes, ha='center', va='top', fontsize=9,
                bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

    fig.suptitle("Product TPM Quota Enforcement — Success vs 429 Responses",
                 fontsize=13, fontweight='bold')
    fig.tight_layout()
    cp = os.path.join(CHARTS_DIR, "scenario5_quota_summary.png")
    fig.savefig(cp, dpi=150)
    plt.close(fig)
    charts.append(cp)

    # ── 4. Corroborate with gateway logs ────────────────────────────────
    ws_name = config.get("workspace_name", "")
    if ws_name:
        query = f"""
ApiManagementGatewayLogs
| where TimeGenerated >= ago({timespan})
| where ResponseCode == 429
| extend sub_name = tostring(ApimSubscriptionId)
| summarize count() by sub_name
| order by count_ desc
"""
        print(f"  📊 Querying LAW: 429 responses by subscription...")
        rows = run_log_analytics_query(ws_name, query, "429 by subscription")
        if rows:
            parts.append("\n**Gateway Log Corroboration (429 responses in APIM logs):**\n")
            parts.append("| Subscription | 429 Count |")
            parts.append("|-------------|-----------|")
            for row in rows:
                parts.append(f"| {row.get('sub_name', '')} | {row.get('count_', 0)} |")
            parts.append("")

    findings["details"] = "\n".join(parts)
    findings["charts"] = charts
    return findings


# ---------------------------------------------------------------------------
# Report Generation
# ---------------------------------------------------------------------------
def generate_report(scenarios, load_test_results):
    """Generate the markdown report with embedded chart references."""
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        f"# AI Gateway Scenario Verification Report",
        f"",
        f"**Generated:** {now}  ",
        f"**Resource Group:** `{RESOURCE_GROUP}`  ",
        f"**Deployment:** `{DEPLOYMENT_NAME}`",
        f"",
        f"## Summary",
        f"",
        f"| # | Scenario | Status |",
        f"|---|----------|--------|",
    ]

    scenario_names = [
        "Load Balancing Across Models/Backends",
        "Department Access via Product Subscriptions",
        "Usage Monitoring per Product/Department/Model",
        "Cost Monitoring per Product/Department/Model",
        "Product TPM Quota Enforcement",
    ]
    for i, (name, findings) in enumerate(zip(scenario_names, scenarios)):
        status = "✅ PASS" if findings["pass"] else "⚠️ NEEDS DATA"
        lines.append(f"| {i+1} | {name} | {status} |")

    # Load test summary from local results
    if load_test_results:
        lines.extend(["", "## Load Test Summary", ""])
        total = load_test_results["total_requests"]
        results = load_test_results["results"]
        success = sum(1 for r in results if r["status"] == "success")
        rate_limited = sum(1 for r in results if r["status"] == "rate_limited")
        errors = sum(1 for r in results if r["status"] == "error")
        lines.append(f"- **Total Requests:** {total}")
        lines.append(f"- **Successful:** {success} ({success/total*100:.1f}%)")
        lines.append(f"- **Rate Limited:** {rate_limited} ({rate_limited/total*100:.1f}%)")
        lines.append(f"- **Errors:** {errors} ({errors/total*100:.1f}%)")
        if success > 0:
            tokens = sum(r["total_tokens"] for r in results if r["status"] == "success")
            latencies = [r["latency_ms"] for r in results if r["status"] == "success"]
            lines.append(f"- **Total Tokens:** {tokens:,}")
            lines.append(f"- **Avg Latency:** {sum(latencies)/len(latencies):.0f} ms")

    # Detailed scenario sections
    for i, (name, findings) in enumerate(zip(scenario_names, scenarios)):
        status = "✅ PASS" if findings["pass"] else "⚠️ NEEDS DATA"
        lines.extend([
            f"",
            f"---",
            f"",
            f"## Scenario {i+1}: {name}",
            f"",
            f"**Status:** {status}",
            f"",
            findings["details"],
        ])

        # Add chart references
        chart = findings.get("chart")
        charts = findings.get("charts", [])
        all_charts = ([chart] if chart else []) + charts
        for cp in all_charts:
            rel_path = os.path.relpath(cp, SCRIPT_DIR)
            lines.append(f"\n![{name}]({rel_path})")

    lines.extend(["", "---", f"", f"*Report generated by `verify-scenarios.py`*"])
    report = "\n".join(lines)
    with open(REPORT_PATH, "w") as f:
        f.write(report)
    print(f"\n📄 Report saved to {REPORT_PATH}")
    return REPORT_PATH


def main():
    parser = argparse.ArgumentParser(description="AI Gateway Scenario Verification")
    parser.add_argument("--timespan", default="PT1H",
                        help="KQL timespan for queries (default: PT1H = 1 hour)")
    args = parser.parse_args()

    ensure_charts_dir()
    config = get_deployment_outputs()

    # Load test results if available
    results_path = os.path.join(SCRIPT_DIR, "load-test-results.json")
    load_test_results = None
    if os.path.exists(results_path):
        with open(results_path) as f:
            load_test_results = json.load(f)
        print(f"📁 Loaded {load_test_results['total_requests']} results from load-test-results.json")

    # Run all scenario verifications
    ts = iso_to_kql_timespan(args.timespan)
    s1 = verify_load_balancing(config, ts)
    s2 = verify_department_access(config, ts)
    s3 = verify_usage_monitoring(config, ts)
    s4 = verify_cost_monitoring(config, ts)
    s5 = verify_quota_enforcement(config, ts, load_test_results)

    report_path = generate_report([s1, s2, s3, s4, s5], load_test_results)

    print("\n" + "=" * 60)
    print("🏁 VERIFICATION COMPLETE")
    print("=" * 60)
    for i, (name, findings) in enumerate(zip(
        ["Load Balancing", "Department Access", "Usage Monitoring", "Cost Monitoring", "Quota Enforcement"],
        [s1, s2, s3, s4, s5]
    )):
        status = "✅" if findings["pass"] else "⚠️"
        print(f"  {status} Scenario {i+1}: {name}")
    print(f"\n📄 Full report: {report_path}")
    chart_count = sum(len(f.get("charts", [])) + (1 if f.get("chart") else 0) for f in [s1, s2, s3, s4, s5])
    print(f"📊 Charts generated: {chart_count} (in {CHARTS_DIR})")


if __name__ == "__main__":
    main()
