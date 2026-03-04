def analyze_logs(log_file):
    results = {
        "total_errors": 0,
        "db_errors": 0,
        "exceptions": []  # New: List to store actual error lines
    }

    with open(log_file, "r") as file:
        for line in file:
            if "ERROR" in line:
                results["total_errors"] += 1
                results["exceptions"].append(line.strip())
                if "DB_CONN_FAIL" in line or "Database" in line:
                    results["db_errors"] += 1

    return results